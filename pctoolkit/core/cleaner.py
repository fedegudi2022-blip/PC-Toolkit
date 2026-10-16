"""Catálogo de limpieza con vista previa: qué se puede liberar y cuánto, antes de borrar.

Hasta ahora la aplicación limpiaba una sola carpeta (`%TEMP%`, en `tools.py`). Esto
generaliza esa idea a un **catálogo de objetivos**: cada uno sabe qué carpetas le
corresponden, qué antigüedad exige, si necesita permisos de administrador, si hay que
cerrar algo antes y si lo que borra **no se puede recuperar** (la papelera). Quien usa la
aplicación ve la lista con lo que liberaría cada uno y decide; nada se borra al analizar.

Los tres compromisos que se mantienen del limpiador de temporales, porque son los que
hacen que esto se pueda ofrecer sin miedo:

* **La vista previa sale del mismo código que el borrado.** `scan()` y `clean()` recorren
  las carpetas con la misma función (`_walk_folder`), así que el número que se enseña es
  el de los archivos que se van a tocar de verdad; lo que cambie entre la medida y el
  borrado (un archivo que se abra en el medio, por ejemplo) se cuenta y se dice.
* **No se sigue ningún enlace.** Los *junctions* y accesos simbólicos que aparezcan dentro
  de una carpeta se dejan donde están: seguirlos llevaría el borrado a un destino ajeno
  (la misma regla que usa `tools.py`, y la misma función).
* **Nunca una carpeta peligrosa.** Antes de tocar nada se comprueba que la carpeta no sea
  una raíz de unidad, la del sistema o la del perfil (`tools.is_safe_temp_folder`); si el
  catálogo se equivocara (o alguien reescribiera una lista de rutas), la limpieza se
  planta en vez de arrasar.

Y una cosa que se dice en voz alta en vez de esconderse: un archivo **en uso** no se puede
borrar. El análisis lo detecta —intenta abrirlo para escribir, que es lo mismo que hace
falta para borrarlo— y lo aparta del total liberable, de modo que la vista previa no
promete lo que no va a poder cumplir (las miniaturas de Windows, por ejemplo, están en uso
mientras el Explorador las tiene abiertas).
"""

from __future__ import annotations

import ctypes
import fnmatch
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

import psutil

from .config import format_bytes
from .startup import is_elevated
from .tools import is_reparse_dir, is_safe_temp_folder, min_temp_age_hours

IS_WINDOWS = os.name == "nt"

# --- Tipos de objetivo -----------------------------------------------------
KIND_FOLDER = "carpeta"
KIND_RECYCLE = "papelera"

# Objetivos que limpia el mantenimiento desatendido si nadie dice otra cosa: solo los
# temporales del usuario, que es exactamente lo que hacía antes de que existiera el
# catálogo. Lo demás se activa a mano, porque una tarea programada borrando la papelera
# sin que nadie lo haya pedido es justo lo que no se quiere.
DEFAULT_TARGETS = ("temp",)

# Categorías que la portada mide para su aviso «se pueden liberar X»: las que no piden
# administrador y no destruyen nada que no se recupere solo (la papelera **no** está: eso no
# se puede deshacer, y un aviso no debe marcar por su cuenta algo irreversible).
RECOMMENDED_TARGETS = ("temp", "browsers", "thumbnails", "dumps")

# Subcarpetas de caché de los navegadores basados en Chromium. Se limpian **solo** cachés:
# el perfil (cookies, sesiones, marcadores) no se toca nunca.
CHROMIUM_CACHES = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "ShaderCache",
    "GrShaderCache",
    "Service Worker\\CacheStorage",
)

# (etiqueta, procesos, carpeta raíz dentro de %LOCALAPPDATA%, subcarpetas de caché, ¿tiene
# perfiles?). Opera guarda todo en su carpeta; Firefox usa `cache2` dentro de cada perfil.
BROWSERS = (
    ("Edge", ("msedge.exe",), "Microsoft\\Edge\\User Data", CHROMIUM_CACHES, True),
    ("Chrome", ("chrome.exe",), "Google\\Chrome\\User Data", CHROMIUM_CACHES, True),
    ("Brave", ("brave.exe",), "BraveSoftware\\Brave-Browser\\User Data", CHROMIUM_CACHES, True),
    ("Vivaldi", ("vivaldi.exe",), "Vivaldi\\User Data", CHROMIUM_CACHES, True),
    ("Opera", ("opera.exe",), "Opera Software\\Opera Stable", CHROMIUM_CACHES, False),
    ("Firefox", ("firefox.exe",), "Mozilla\\Firefox\\Profiles", ("cache2",), True),
)

# Volcados de error: los que deja un programa al caerse y los informes de error de Windows.
WER_FOLDERS = ("ReportQueue", "ReportArchive")

# Flags de `SHEmptyRecycleBinW`: sin preguntar otra vez (ya se confirma en la ventana), sin
# barra de progreso (la pone la aplicación) y sin sonido.
SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


def _local_appdata() -> str:
    return os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )


def _system_root() -> str:
    return os.environ.get("SystemRoot") or r"C:\Windows"


def _user_temp() -> str:
    return os.environ.get("TEMP") or os.environ.get("TMP") or ""


# --- Rutas de cada objetivo ------------------------------------------------

def temp_folders() -> tuple[str, ...]:
    """Carpetas temporales del usuario (lo que limpiaba la aplicación hasta ahora)."""
    carpeta = _user_temp()
    return (carpeta,) if carpeta else ()


def windows_temp_folders() -> tuple[str, ...]:
    return (os.path.join(_system_root(), "Temp"),)


def update_cache_folders() -> tuple[str, ...]:
    return (os.path.join(_system_root(), "SoftwareDistribution", "Download"),)


def thumbnail_folders() -> tuple[str, ...]:
    return (os.path.join(_local_appdata(), "Microsoft", "Windows", "Explorer"),)


def dump_folders() -> tuple[str, ...]:
    base = _local_appdata()
    carpetas = [os.path.join(base, "CrashDumps")]
    wer = os.path.join(base, "Microsoft", "Windows", "WER")
    carpetas.extend(os.path.join(wer, nombre) for nombre in WER_FOLDERS)
    return tuple(carpetas)


def _browser_profiles(root: str, caches: Sequence[str], with_profiles: bool) -> list[str]:
    """Subcarpetas de caché que existen de verdad dentro de una raíz de navegador."""
    encontradas: list[str] = []
    perfiles = [root]
    if with_profiles and os.path.isdir(root):
        try:
            with os.scandir(root) as iterator:
                perfiles = [
                    entry.path
                    for entry in iterator
                    if entry.is_dir(follow_symlinks=False)
                    and (entry.name == "Default" or entry.name.startswith("Profile "))
                ] or [root]
        except OSError:
            perfiles = [root]
    for perfil in perfiles:
        for cache in caches:
            ruta = os.path.join(perfil, cache)
            if os.path.isdir(ruta):
                encontradas.append(ruta)
    return encontradas


def browser_cache_folders() -> tuple[str, ...]:
    """Cachés de los navegadores instalados (solo cachés, nunca el perfil)."""
    base = _local_appdata()
    carpetas: list[str] = []
    for _label, _processes, relative, caches, with_profiles in BROWSERS:
        root = os.path.join(base, relative)
        if not os.path.isdir(root):
            continue
        carpetas.extend(_browser_profiles(root, caches, with_profiles))
    return tuple(dict.fromkeys(carpetas))


def open_browser_labels() -> tuple[str, ...]:
    """Navegadores que **tienen caché en este equipo** y están abiertos ahora mismo.

    Se pregunta por los procesos del equipo, no por la lista entera: si alguien tiene
    Firefox instalado pero cerrado y Edge abierto, el aviso habla solo de Edge.
    """
    base = _local_appdata()
    con_cache = {
        label: processes
        for label, processes, relative, _caches, _with in BROWSERS
        if os.path.isdir(os.path.join(base, relative))
    }
    if not con_cache:
        return ()
    abiertos: list[str] = []
    for proceso in psutil.process_iter(["name"]):
        nombre = (proceso.info.get("name") or "").lower()
        for label, processes in con_cache.items():
            if nombre in processes and label not in abiertos:
                abiertos.append(label)
    return tuple(abiertos)


def _browser_blocker() -> str:
    abiertos = open_browser_labels()
    if not abiertos:
        return ""
    nombres = ", ".join(abiertos)
    return (
        f"{nombres} está abierto: ciérralo y vuelve a analizar. Un navegador en marcha "
        "reescribe su caché mientras se borra."
    )


def _admin_blocker() -> str:
    return "Necesita permisos de administrador (el análisis también: sin ellos Windows no deja ni leer)."


# --- Objetivos -------------------------------------------------------------

@dataclass(frozen=True)
class CleanTarget:
    """Un objetivo de limpieza: qué carpetas, con qué reglas y qué se pierde."""

    key: str
    name: str
    description: str
    detail: str
    folders: Callable[[], tuple[str, ...]]
    kind: str = KIND_FOLDER
    # Solo se tocan los archivos que casan con estos patrones (vacío: todos).
    patterns: tuple[str, ...] = ()
    # Antigüedad mínima propia en horas; `None` usa la que pida quien limpia (con el
    # suelo de una hora de `tools.min_temp_age_hours`).
    age_hours: float | None = None
    requires_admin: bool = False
    # Lo que borra no se puede recuperar (ni desde la Papelera de Windows).
    irreversible: bool = False
    # Se marca al abrir la ventana (lo irreversible y lo que necesita administrador se
    # dejan en manos de quien lo marque a propósito).
    preselected: bool = True
    # Devuelve el motivo por el que ahora mismo no se puede limpiar ("" si se puede).
    blocker: Callable[[], str] = lambda: ""


def _catalog() -> tuple[CleanTarget, ...]:
    """El catálogo, en el orden en que se enseña (de lo más seguro a lo más delicado)."""
    return (
        CleanTarget(
            key="temp",
            name="Archivos temporales del usuario",
            description="Lo que dejan los programas al instalarse o al trabajar.",
            detail=(
                "Se borran solo los archivos con más antigüedad que la indicada: uno recién "
                "escrito puede ser de una instalación en curso. Los que están en uso se omiten."
            ),
            folders=temp_folders,
        ),
        CleanTarget(
            key="browsers",
            name="Cachés de los navegadores",
            description="Copias de páginas, scripts y recursos que se vuelven a descargar.",
            detail=(
                "Solo las carpetas de caché: las contraseñas, las cookies, las sesiones y los "
                "marcadores no se tocan. Se limpia únicamente con el navegador cerrado."
            ),
            folders=browser_cache_folders,
            age_hours=0.0,
            blocker=_browser_blocker,
        ),
        CleanTarget(
            key="thumbnails",
            name="Miniaturas e iconos de Windows",
            description="Las vistas en miniatura que Windows guarda de tus imágenes y vídeos.",
            detail=(
                "Windows las vuelve a crear cuando hacen falta (tardará un poco la primera vez "
                "que abras una carpeta con muchas fotos). Mientras el Explorador está abierto "
                "suele tenerlas en uso, así que es normal que se omitan algunas."
            ),
            folders=thumbnail_folders,
            patterns=("thumbcache_*.db", "iconcache_*.db"),
            age_hours=0.0,
        ),
        CleanTarget(
            key="dumps",
            name="Volcados de error",
            description="Archivos que deja un programa al cerrarse de golpe y los informes de error de Windows.",
            detail=(
                "Se pierde el rastro de los fallos: si un programa se cae y quieres averiguar por "
                "qué, esos archivos son la prueba. Ayudan a diagnosticar, no ocupan por gusto."
            ),
            folders=dump_folders,
            age_hours=0.0,
        ),
        CleanTarget(
            key="recycle",
            name="Papelera de reciclaje",
            description="Todo lo que has eliminado y todavía no se ha borrado del disco.",
            detail=(
                "Esto no se puede deshacer. Es lo que más espacio libera de una vez y lo más "
                "fácil de lamentar: revisa la Papelera antes de vaciarla desde aquí."
            ),
            folders=lambda: (),
            kind=KIND_RECYCLE,
            irreversible=True,
            preselected=False,
        ),
        CleanTarget(
            key="wintemp",
            name="Archivos temporales de Windows",
            description="La carpeta temporal del propio sistema (C:\\Windows\\Temp).",
            detail=(
                "Suele tener archivos de instaladores y de actualizaciones. Necesita permisos de "
                "administrador, y lo que esté en uso se omite."
            ),
            folders=windows_temp_folders,
            requires_admin=True,
            preselected=False,
        ),
        CleanTarget(
            key="update_cache",
            name="Caché de Windows Update",
            description="Los paquetes que Windows ya descargó para actualizarse.",
            detail=(
                "Windows la administra solo y normalmente está casi vacía. Si borras esta carpeta "
                "con una actualización a medias, esa actualización puede quedar a medio instalar: "
                "por eso no se marca sola y pide permisos de administrador."
            ),
            folders=update_cache_folders,
            age_hours=0.0,
            requires_admin=True,
            preselected=False,
        ),
    )


CATALOG: tuple[CleanTarget, ...] = _catalog()


def targets() -> tuple[CleanTarget, ...]:
    """El catálogo completo, en el orden en que se enseña."""
    return CATALOG


def target_keys() -> tuple[str, ...]:
    return tuple(target.key for target in CATALOG)


def find_target(key: str) -> CleanTarget | None:
    for target in CATALOG:
        if target.key == key:
            return target
    return None


def normalize_keys(keys: Sequence[str] | None) -> tuple[str, ...]:
    """Claves válidas del catálogo, sin repetir y en el orden del catálogo.

    Lo que no se reconoce se descarta en vez de fallar: las categorías se guardan en las
    preferencias y un catálogo que cambie de versión no debe dejar la limpieza sin hacer.
    """
    if keys is None:
        return DEFAULT_TARGETS
    pedidas = {str(key) for key in keys}
    elegidas = tuple(target.key for target in CATALOG if target.key in pedidas)
    return elegidas


def keys_label(keys: Sequence[str] | None) -> str:
    """Nombres legibles de las categorías, para mensajes y etiquetas."""
    nombres = [target.name for target in CATALOG if target.key in set(normalize_keys(keys))]
    return " · ".join(nombres) if nombres else "ninguna"


# --- Medición y borrado ----------------------------------------------------

@dataclass
class TargetScan:
    """Lo que se sabe de un objetivo en una vista previa."""

    key: str
    name: str
    description: str
    detail: str
    # `carpeta` o `papelera`: la papelera cuenta elementos, no archivos, y la interfaz lo
    # dice con la palabra que toca.
    kind: str = KIND_FOLDER
    present: bool = False
    available: bool = False
    blocked: str = ""
    size: int = 0
    files: int = 0
    locked: int = 0
    locked_size: int = 0
    recent: int = 0
    skipped: int = 0
    folders: tuple[str, ...] = ()
    irreversible: bool = False
    requires_admin: bool = False
    preselected: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "categoria": self.key,
            "nombre": self.name,
            "presente": self.present,
            "disponible": self.available,
            "motivo": self.blocked,
            "bytes": self.size,
            "archivos": self.files,
            "en_uso": self.locked,
            "recientes": self.recent,
            "omitidos": self.skipped,
            "carpetas": list(self.folders),
        }


@dataclass
class CleanScan:
    """Vista previa completa: lo que se liberaría con lo que se ha pedido."""

    items: list[TargetScan] = field(default_factory=list)
    min_age_hours: float = 24.0

    def item(self, key: str) -> TargetScan | None:
        for entry in self.items:
            if entry.key == key:
                return entry
        return None

    @property
    def available_items(self) -> list[TargetScan]:
        return [item for item in self.items if item.available]

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.available_items)

    @property
    def total_files(self) -> int:
        return sum(item.files for item in self.available_items)

    @property
    def total_locked(self) -> int:
        return sum(item.locked for item in self.available_items)

    def summary(self) -> str:
        if not self.available_items:
            return "No hay nada que se pueda liberar ahora mismo."
        partes = [
            f"se liberarían {format_bytes(self.total_size)} en {self.total_files} elemento(s)",
            f"{len(self.available_items)} categoría(s)",
        ]
        if self.total_locked:
            partes.append(f"{self.total_locked} en uso (no se pueden borrar ahora)")
        return " · ".join(partes) + "."

    def to_dict(self) -> dict[str, object]:
        return {
            "antiguedad_horas": self.min_age_hours,
            "liberable": self.total_size,
            "archivos": self.total_files,
            "categorias": [item.to_dict() for item in self.items],
        }


@dataclass
class TargetClean:
    """Resultado del borrado de un objetivo."""

    key: str
    name: str
    ok: bool = True
    freed: int = 0
    deleted: int = 0
    skipped: int = 0
    blocked: str = ""
    detail: str = ""
    irreversible: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "categoria": self.key,
            "nombre": self.name,
            "correcto": self.ok,
            "liberado": self.freed,
            "archivos": self.deleted,
            "omitidos": self.skipped,
            "motivo": self.blocked,
            "detalle": self.detail,
        }


@dataclass
class CleanResult:
    """Resumen de una limpieza (real o simulada)."""

    items: list[TargetClean] = field(default_factory=list)
    dry_run: bool = False
    cancelled: bool = False
    duration: float = 0.0

    def item(self, key: str) -> TargetClean | None:
        for entry in self.items:
            if entry.key == key:
                return entry
        return None

    @property
    def freed(self) -> int:
        return sum(item.freed for item in self.items)

    @property
    def deleted(self) -> int:
        return sum(item.deleted for item in self.items)

    @property
    def skipped(self) -> int:
        return sum(item.skipped for item in self.items)

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.items) if self.items else False

    def summary(self) -> str:
        head = "Simulación" if self.dry_run else "Limpieza"
        if not self.items:
            return f"{head}: no había ningún paso que ejecutar."
        hechos = [item for item in self.items if not item.blocked]
        if not hechos:
            return f"{head}: nada que limpiar ({len(self.items)} categoría(s) no disponibles)."
        partes = [f"{self.deleted} archivo(s) · {format_bytes(self.freed)}"]
        if self.skipped:
            partes.append(f"{self.skipped} omitido(s) por estar en uso")
        return f"{head}: " + " · ".join(partes) + "."

    def to_dict(self) -> dict[str, object]:
        return {
            "simulacion": self.dry_run,
            "cancelada": self.cancelled,
            "duracion_segundos": round(self.duration, 2),
            "correcto": self.ok,
            "liberado": self.freed,
            "archivos": self.deleted,
            "omitidos": self.skipped,
            "resumen": self.summary(),
            "categorias": [item.to_dict() for item in self.items],
        }


def _is_locked(path: str) -> bool:
    """¿El archivo está en uso (o no se puede borrar)?

    Borrar un archivo en Windows exige poder abrirlo con permiso de escritura: si eso
    falla, el borrado también va a fallar. Se usa justo eso como prueba, así que un
    archivo de solo lectura o bloqueado por otro programa no entra en el total que se
    promete liberar.
    """
    if not IS_WINDOWS:
        return False
    try:
        descriptor = os.open(path, os.O_RDWR)
    except OSError:
        return True
    except ValueError:  # pragma: no cover - ruta con un carácter imposible
        return True
    else:
        os.close(descriptor)
        return False


def _matches(name: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    minusculas = name.lower()
    return any(fnmatch.fnmatch(minusculas, pattern) for pattern in patterns)


def _walk_folder(
    folder: str,
    cutoff: float,
    *,
    remove: bool,
    patterns: Sequence[str] = (),
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """Recorre una carpeta y devuelve (o borra) lo que cumple las reglas.

    Devuelve un diccionario con `size`, `files`, `locked`, `locked_size`, `recent`,
    `skipped` y, si `remove` es cierto, `deleted` y `freed`. Escanea y borra con la
    **misma** función a propósito: así la vista previa y lo que se borra coinciden.
    """
    datos = {
        "size": 0,
        "files": 0,
        "locked": 0,
        "locked_size": 0,
        "recent": 0,
        "skipped": 0,
        "deleted": 0,
        "freed": 0,
    }
    pila = [folder]
    while pila:
        if should_cancel and should_cancel():
            break
        actual = pila.pop()
        try:
            with os.scandir(actual) as iterator:
                entradas = list(iterator)
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            datos["skipped"] += 1
            continue
        for entry in entradas:
            if should_cancel and should_cancel():
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    # Un enlace dentro de la carpeta no se sigue: llevaría el borrado a
                    # un destino que no es el que se está limpiando.
                    if not is_reparse_dir(entry):
                        pila.append(entry.path)
                    continue
                if not _matches(entry.name, patterns):
                    continue
                stat = entry.stat(follow_symlinks=False)
                if stat.st_mtime > cutoff:
                    datos["recent"] += 1
                    continue
                if _is_locked(entry.path):
                    datos["locked"] += 1
                    datos["locked_size"] += stat.st_size
                    continue
                datos["size"] += stat.st_size
                datos["files"] += 1
                if remove:
                    os.remove(entry.path)
                    datos["freed"] += stat.st_size
                    datos["deleted"] += 1
            except (PermissionError, FileNotFoundError, OSError):
                datos["skipped"] += 1
    if remove:
        # Solo se retiran las subcarpetas que quedan **vacías** de verdad (`rmdir` no
        # borra nada con contenido). Igual que en la limpieza de temporales.
        for root, _dirs, _files in os.walk(folder, topdown=False):
            if root == folder:
                continue
            try:
                os.rmdir(root)
            except OSError:
                pass
    return datos


# --- Papelera de reciclaje -------------------------------------------------
class _SHQUERYRBINFO(ctypes.Structure):
    """`SHQUERYRBINFO` de la API del shell: cuántos bytes y cuántos elementos.

    El orden de los campos es el de la estructura de Windows (`cbSize`, `i64Size`,
    `i64NumItems`) y no se puede cambiar: si se intercambian, la llamada responde «correcto»
    y deja los dos números en los campos cruzados (medido: el tamaño se leía como número de
    elementos, con un valor de mil seiscientos millones de archivos que no era verdad).
    """

    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def recycle_info() -> tuple[int, int] | None:
    """(bytes, elementos) de la papelera del usuario; `None` si Windows no lo cuenta."""
    if not IS_WINDOWS:
        return None
    try:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        resultado = ctypes.windll.shell32.SHQueryRecycleBinW(  # type: ignore[attr-defined]
            None, ctypes.byref(info)
        )
    except (AttributeError, OSError):
        return None
    if resultado != 0:
        return None
    return max(0, int(info.i64Size)), max(0, int(info.i64NumItems))



def empty_recycle() -> tuple[bool, str]:
    """Vacía la papelera del usuario. Devuelve (éxito, mensaje).

    Se usa la función del propio Windows (`SHEmptyRecycleBinW`) y no un borrado de
    `$Recycle.Bin` a mano: la papelera es por usuario y por unidad, y Windows lleva cuenta
    de los dos. Esto no se puede deshacer.
    """
    if not IS_WINDOWS:
        return False, "La papelera solo existe en Windows."
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    try:
        resultado = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)  # type: ignore[attr-defined]
    except (AttributeError, OSError) as exc:
        return False, f"No se pudo vaciar la papelera: {exc}"
    # 0 es «hecho»; S_FALSE (0x1) también aparece cuando no había nada que vaciar.
    if resultado in (0, 1):
        return True, "Papelera vaciada."
    return False, f"Windows no pudo vaciar la papelera (código {resultado})."


# --- Vista previa y limpieza ----------------------------------------------

def _effective_age(target: CleanTarget, min_age_hours: float) -> float:
    """Antigüedad mínima efectiva del objetivo, en horas."""
    if target.age_hours is None:
        return min_temp_age_hours(min_age_hours)
    return max(0.0, float(target.age_hours))


def _folders_for(target: CleanTarget, temp_folder: str | None) -> tuple[str, ...]:
    """Carpetas del objetivo que existen de verdad, ya filtradas por seguridad."""
    if target.key == "temp" and temp_folder:
        candidatas: tuple[str, ...] = (temp_folder,)
    else:
        candidatas = tuple(target.folders() or ())
    return tuple(folder for folder in candidatas if folder and os.path.isdir(folder))


def _blocked(target: CleanTarget, folders: tuple[str, ...]) -> str:
    """Motivo por el que el objetivo no se puede tocar ahora ("" si se puede)."""
    if target.requires_admin and not is_elevated():
        return _admin_blocker()
    motivo = target.blocker() if target.blocker is not None else ""
    if motivo:
        return motivo
    if target.kind == KIND_FOLDER:
        if not folders:
            return ""
        for folder in folders:
            if not is_safe_temp_folder(folder):
                return (
                    f"Por seguridad no se limpia {folder}: es una raíz de unidad, la carpeta "
                    "del sistema o la del perfil."
                )
    return ""


def scan(
    keys: Sequence[str] | None = None,
    *,
    min_age_hours: float = 24.0,
    temp_folder: str | None = None,
    progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> CleanScan:
    """Mide lo que se liberaría con las categorías pedidas. **No borra nada.**"""
    plan = CleanScan(min_age_hours=min_temp_age_hours(min_age_hours))
    for key in normalize_keys(keys):
        target = find_target(key)
        if target is None:  # pragma: no cover - normalize_keys ya lo garantiza
            continue
        if progress:
            progress(f"Analizando «{target.name}»…")
        if should_cancel and should_cancel():
            break
        item = TargetScan(
            key=target.key,
            name=target.name,
            description=target.description,
            detail=target.detail,
            kind=target.kind,
            irreversible=target.irreversible,
            requires_admin=target.requires_admin,
            preselected=target.preselected,
        )
        if target.kind == KIND_RECYCLE:
            info = recycle_info()
            if info is None:
                plan.items.append(item)
                continue
            item.present = True
            item.size, item.files = info
            item.available = info[1] > 0 and not target.blocker()
            item.blocked = _blocked(target, ()) if info[1] > 0 else ""
            plan.items.append(item)
            continue
        folders = _folders_for(target, temp_folder)
        item.folders = folders
        item.present = bool(folders)
        if not folders:
            plan.items.append(item)
            continue
        item.blocked = _blocked(target, folders)
        item.available = not item.blocked
        if item.available:
            cutoff = time.time() - _effective_age(target, min_age_hours) * 3600
            for folder in folders:
                datos = _walk_folder(
                    folder,
                    cutoff,
                    remove=False,
                    patterns=target.patterns,
                    should_cancel=should_cancel,
                )
                item.size += datos["size"]
                item.files += datos["files"]
                item.locked += datos["locked"]
                item.locked_size += datos["locked_size"]
                item.recent += datos["recent"]
                item.skipped += datos["skipped"]
        plan.items.append(item)
    return plan


def clean(
    keys: Sequence[str] | None = None,
    *,
    min_age_hours: float = 24.0,
    temp_folder: str | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> CleanResult:
    """Borra lo que hay en las categorías pedidas y devuelve lo que se liberó de verdad.

    Con `dry_run` no toca nada: mide y devuelve lo mismo que `scan()`, en forma de
    resultado, para que la simulación del mantenimiento y la limpieza real compartan
    forma y se puedan comparar.
    """
    arranque = time.perf_counter()
    resultado = CleanResult(dry_run=dry_run)
    for key in normalize_keys(keys):
        target = find_target(key)
        if target is None:  # pragma: no cover
            continue
        if should_cancel and should_cancel():
            resultado.cancelled = True
            break
        if progress:
            progress(f"Limpiando «{target.name}»…")
        entrada = TargetClean(
            key=target.key, name=target.name, irreversible=target.irreversible
        )
        if target.kind == KIND_RECYCLE:
            info = recycle_info()
            if info is None or info[1] == 0:
                entrada.blocked = "La papelera ya está vacía (o Windows no la cuenta)."
                resultado.items.append(entrada)
                continue
            if dry_run:
                entrada.freed, entrada.deleted = info
                entrada.detail = f"Se vaciarían {info[1]} elemento(s)."
                resultado.items.append(entrada)
                continue
            ok, mensaje = empty_recycle()
            entrada.ok = ok
            entrada.detail = mensaje
            if ok:
                entrada.freed, entrada.deleted = info
            resultado.items.append(entrada)
            continue
        folders = _folders_for(target, temp_folder)
        motivo = _blocked(target, folders)
        if not folders:
            entrada.blocked = "No hay carpetas de este tipo en este equipo."
            resultado.items.append(entrada)
            continue
        if motivo:
            entrada.blocked = motivo
            resultado.items.append(entrada)
            continue
        cutoff = time.time() - _effective_age(target, min_age_hours) * 3600
        for folder in folders:
            datos = _walk_folder(
                folder,
                cutoff,
                remove=not dry_run,
                patterns=target.patterns,
                should_cancel=should_cancel,
            )
            entrada.freed += datos["freed"] if not dry_run else datos["size"]
            entrada.deleted += datos["deleted"] if not dry_run else datos["files"]
            entrada.skipped += datos["skipped"] + datos["locked"]
        resultado.items.append(entrada)
    resultado.duration = time.perf_counter() - arranque
    return resultado
