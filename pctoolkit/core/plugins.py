"""Plugins: ajustes de Windows que aporta el usuario en un archivo JSON.

El catálogo de PC Toolkit es una lista cerrada: para añadir un ajuste hay que tocar el
programa. Un plugin abre esa puerta sin abrirla del todo: se deja un archivo `.json` en
`%APPDATA%\\PCToolkit\\plugins` y sus ajustes aparecen en la página «Ajustes de Windows» como
los demás, con su análisis, su riesgo y su vuelta atrás.

Tres reglas, porque un archivo que viene de fuera no es de fiar:

* **Validación estricta.** Se comprueba clave por clave: una categoría que no existe, un
  `riesgo` mal escrito, un `tipo` que el registro no sabe escribir o una clave del registro
  fuera de HKCU/HKLM rechazan **el archivo entero** con el motivo. Nada se carga a medias.
* **Nada se ejecuta sin que la persona lo confirme.** Los ajustes de un plugin aparecen
  marcados como «plugin sin confirmar» y no se pueden aplicar hasta que se revisan y se
  aceptan; si el archivo cambia después, la huella deja de coincidir y se vuelve a pedir.
* **Se puede deshacer.** Los ajustes de un plugin entran por el mismo camino que los del
  catálogo (`windows_settings`), así que el respaldo `.reg`, el historial de «Deshacer» y el
  registro de cambios funcionan igual.

El formato está documentado en DOCUMENTACION.md y hay una plantilla lista para copiar en
`example_text()`, que es la que escribe el botón «Crear un plugin de ejemplo».
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import logs, windows_settings
from .config import data_dir

# Variable de entorno con la que las pruebas apuntan a una carpeta propia.
PLUGINS_DIR_ENV = "PCTOOLKIT_RAIZ_PLUGINS"

# Límites: un plugin es una lista de ajustes, no un lugar donde esconder un catálogo entero.
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 50
MAX_SETTINGS_PER_FILE = 200
MAX_TEXT = 600
MAX_DOCUMENT_TEXT = 200

ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,60}$")

# Variables del documento y de cada ajuste: lo que no esté aquí es un error, para que una
# errata («rriesgo») se vea en vez de pasar desapercibida.
DOCUMENT_KEYS = {"esquema", "id", "nombre", "autor", "version", "descripcion", "ajustes"}
SETTING_KEYS = {
    "id",
    "nombre",
    "categoria",
    "descripcion",
    "por_que",
    "riesgo",
    "requiere_admin",
    "claves",
    "crea_claves",
    "nota",
    "buscar",
    "reinicio",
}
VALUE_KEYS = {"hive", "path", "nombre", "tipo", "recomendado"}
SCHEMA_VERSION = 1

_EXAMPLE = """{
  "esquema": 1,
  "id": "ejemplo",
  "nombre": "Mis ajustes",
  "autor": "Tu nombre",
  "version": "1.0",
  "descripcion": "Plantilla de plugin. Copia lo que quieras y borra el resto.",
  "ajustes": [
    {
      "id": "sin-anuncios-inicio",
      "nombre": "Quitar los anuncios del menú Inicio",
      "categoria": "anuncios",
      "descripcion": "Deja de mostrar aplicaciones sugeridas y promociones en el menú Inicio.",
      "por_que": "Es publicidad que Windows coloca dentro del propio menú de inicio.",
      "riesgo": "bajo",
      "requiere_admin": false,
      "claves": [
        {
          "hive": "HKCU",
          "path": "Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\ContentDeliveryManager",
          "nombre": "SubscribedContent-338388Enabled",
          "tipo": "dword",
          "recomendado": 0
        }
      ],
      "nota": "Si lo vuelves a poner, verás otra vez las sugerencias.",
      "reinicio": "Se nota al abrir el menú Inicio."
    }
  ]
}
"""


def plugins_dir() -> Path:
    """Carpeta donde se dejan los plugins (`%APPDATA%\\PCToolkit\\plugins`).

    Se puede cambiar con `PCTOOLKIT_RAIZ_PLUGINS`: es lo que usan las pruebas para leer y
    escribir en una carpeta suya, sin tocar la del usuario.
    """
    indicada = (os.environ.get(PLUGINS_DIR_ENV) or "").strip()
    return Path(indicada) if indicada else data_dir() / "plugins"


def ensure_dir() -> Path:
    """Crea la carpeta de plugins si no está (y la devuelve)."""
    carpeta = plugins_dir()
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - sin permisos no hay nada que hacer
        logs.warning(f"No se pudo crear la carpeta de plugins ({carpeta})", exc, name="plugins")
    return carpeta


def trust_file() -> Path:
    """Archivo con las huellas de los plugins que el usuario ya revisó y aceptó."""
    return plugins_dir() / "confianza.json"


def example_text() -> str:
    """Plantilla comentada, tal cual se escribe con «Crear un plugin de ejemplo»."""
    return _EXAMPLE


def fingerprint(text: str) -> str:
    """Huella del archivo: si el plugin cambia, deja de estar aceptado."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Fichas
# ---------------------------------------------------------------------------


@dataclass
class PluginInfo:
    """Un plugin cargado: de dónde salió y qué trae."""

    id: str
    name: str = ""
    author: str = ""
    version: str = ""
    description: str = ""
    path: Path | None = None
    fingerprint: str = ""
    count: int = 0
    needs_admin: int = 0
    high_risk: int = 0
    trusted: bool = False

    @property
    def dangerous(self) -> bool:
        """¿Trae algo que merece un aviso aparte? (administrador o riesgo alto)"""
        return bool(self.needs_admin or self.high_risk)

    @property
    def when(self) -> float:
        """Fecha del archivo, para poder ordenar la lista."""
        try:
            return self.path.stat().st_mtime if self.path else 0.0
        except OSError:
            return 0.0


@dataclass
class PluginLoad:
    """Resultado de leer la carpeta de plugins."""

    settings: list[windows_settings.Setting] = field(default_factory=list)
    plugins: list[PluginInfo] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (archivo, motivo)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        """Frase corta para la tarjeta de la página."""
        if not self.plugins and not self.errors:
            return "No hay ningún plugin instalado."
        partes = [f"{len(self.plugins)} plugin(s), {len(self.settings)} ajuste(s)"]
        pendientes = [info for info in self.plugins if not info.trusted]
        if pendientes:
            partes.append(f"{len(pendientes)} sin confirmar")
        if self.errors:
            partes.append(f"{len(self.errors)} con errores")
        return " · ".join(partes)


# ---------------------------------------------------------------------------
# Validación (funciones puras: se prueban con textos fijos)
# ---------------------------------------------------------------------------


def _texto(valor: Any, *, limite: int = MAX_TEXT) -> str:
    """Texto recortado y sin espacios sobrantes (nunca `None`)."""
    if valor is None:
        return ""
    return str(valor).strip()[:limite]


def _es_clave_valida(valor: Any) -> bool:
    return isinstance(valor, str) and bool(ID_PATTERN.match(valor.strip()))


def _ajuste_from_dict(
    datos: Any,
    *,
    plugin_id: str,
    plugin_name: str,
    huella: str,
) -> tuple[windows_settings.Setting | None, list[str]]:
    """Convierte el diccionario de un ajuste en la ficha del catálogo (o en sus errores)."""
    errores: list[str] = []
    if not isinstance(datos, dict):
        return None, ["cada ajuste tiene que ser un objeto con sus claves"]
    sobran = sorted(set(datos) - SETTING_KEYS)
    if sobran:
        errores.append(f"claves que no existen en un ajuste: {', '.join(sobran)}")
    identificador = _texto(datos.get("id"), limite=80)
    if not _es_clave_valida(identificador):
        errores.append("«id» tiene que ser un identificador corto (letras, números, punto, guion o guion bajo)")
    nombre = _texto(datos.get("nombre"), limite=120)
    if not nombre:
        errores.append("falta «nombre»")
    descripcion = _texto(datos.get("descripcion"))
    if not descripcion:
        errores.append("falta «descripcion»")
    categoria = _texto(datos.get("categoria"), limite=40).lower()
    if categoria not in windows_settings.CATEGORIES:
        permitidas = ", ".join(windows_settings.CATEGORY_ORDER)
        errores.append(f"«categoria» tiene que ser una de: {permitidas}")
    riesgo = _texto(datos.get("riesgo"), limite=20).lower()
    if riesgo not in windows_settings.RISK_ORDER:
        errores.append("«riesgo» tiene que ser «bajo», «medio» o «alto»")
    requiere_admin = datos.get("requiere_admin", False)
    if not isinstance(requiere_admin, bool):
        errores.append("«requiere_admin» tiene que ser verdadero o falso")

    valores: list[windows_settings.ValueSpec] = []
    claves = datos.get("claves")
    if not isinstance(claves, list) or not claves:
        errores.append("«claves» tiene que ser una lista con al menos una clave del registro")
    else:
        for indice, bruta in enumerate(claves, start=1):
            if not isinstance(bruta, dict):
                errores.append(f"la clave {indice} tiene que ser un objeto")
                continue
            sobran_clave = sorted(set(bruta) - VALUE_KEYS)
            if sobran_clave:
                errores.append(f"clave {indice}: campos que no existen: {', '.join(sobran_clave)}")
            hive = _texto(bruta.get("hive") or windows_settings.HIVE_USER, limite=10).upper()
            if hive not in (windows_settings.HIVE_USER, windows_settings.HIVE_MACHINE):
                errores.append(f"clave {indice}: «hive» tiene que ser HKCU o HKLM")
                continue
            ruta = _texto(bruta.get("path"), limite=MAX_TEXT)
            if not ruta or ruta.startswith(("\\\\", "/")) or ".." in ruta:
                errores.append(f"clave {indice}: «path» no es una ruta de registro válida")
                continue
            if hive == windows_settings.HIVE_MACHINE and not requiere_admin:
                errores.append(
                    f"clave {indice}: toca HKEY_LOCAL_MACHINE, así que el ajuste tiene que llevar "
                    "«requiere_admin» en verdadero"
                )
                continue
            tipo = _texto(bruta.get("tipo") or windows_settings.KIND_DWORD, limite=10).lower()
            if tipo not in (windows_settings.KIND_DWORD, windows_settings.KIND_STRING, windows_settings.KIND_DELETE):
                errores.append(f"clave {indice}: «tipo» tiene que ser dword, string o delete")
                continue
            if tipo == windows_settings.KIND_DELETE:
                recomendado: Any = None
            else:
                if "recomendado" not in bruta:
                    errores.append(f"clave {indice}: falta «recomendado» (lo que tiene que valer)")
                    continue
                recomendado = bruta["recomendado"]
                if tipo == windows_settings.KIND_DWORD and not isinstance(recomendado, int):
                    errores.append(f"clave {indice}: con «tipo» dword, «recomendado» tiene que ser un número")
                    continue
                if tipo == windows_settings.KIND_STRING and not isinstance(recomendado, str):
                    errores.append(f"clave {indice}: con «tipo» string, «recomendado» tiene que ser texto")
                    continue
            valores.append(
                windows_settings.ValueSpec(
                    path=ruta,
                    name=_texto(bruta.get("nombre"), limite=MAX_TEXT),
                    recommended=recomendado,
                    kind=tipo,
                    hive=hive,
                )
            )

    creadas: list[windows_settings.KeyRef] = []
    for indice, bruta in enumerate(datos.get("crea_claves") or [], start=1):
        if not isinstance(bruta, dict):
            errores.append(f"crea_claves {indice}: tiene que ser un objeto")
            continue
        sobran_clave = sorted(set(bruta) - {"path", "hive"})
        if sobran_clave:
            errores.append(f"crea_claves {indice}: campos que no existen: {', '.join(sobran_clave)}")
        hive = _texto(bruta.get("hive") or windows_settings.HIVE_USER, limite=10).upper()
        ruta = _texto(bruta.get("path"), limite=MAX_TEXT)
        if hive not in (windows_settings.HIVE_USER, windows_settings.HIVE_MACHINE) or not ruta:
            errores.append(f"crea_claves {indice}: hacen falta «path» y un «hive» válido")
            continue
        creadas.append(windows_settings.KeyRef(path=ruta, hive=hive))

    if errores:
        return None, errores
    ajuste = windows_settings.Setting(
        id=f"plugin.{plugin_id}.{identificador}",
        name=nombre,
        category=categoria,
        description=descripcion,
        why=_texto(datos.get("por_que")),
        risk=riesgo,
        needs_admin=bool(requiere_admin),
        values=tuple(valores),
        created_keys=tuple(creadas),
        note=_texto(datos.get("nota")),
        search=_texto(datos.get("buscar")),
        requires_restart=_texto(datos.get("reinicio")),
        plugin=plugin_id,
        plugin_name=plugin_name or plugin_id,
        plugin_fingerprint=huella,
    )
    return ajuste, []


def taken_ids(*, own_plugin: str = "", pending: Iterable[str] = ()) -> set[str]:
    """Identificadores ya usados: los del catálogo, los de otros plugins y los de esta carga.

    Los de **este** plugin no cuentan: al volver a leer la carpeta sus ajustes ya están
    registrados y, sin esto, la segunda lectura los rechazaría por repetidos.
    """
    ocupados = {setting.id for setting in windows_settings.CATALOG}
    ocupados.update(pending)
    ocupados.update(
        setting.id
        for setting in windows_settings.catalog()
        if setting.from_plugin and setting.plugin != own_plugin
    )
    return ocupados


def parse_document(
    text: str,
    *,
    source: str = "plugin.json",
    taken: Iterable[str] | None = None,
) -> tuple[PluginInfo | None, list[windows_settings.Setting], list[str]]:
    """Lee un plugin completo: devuelve su ficha, sus ajustes y los errores que tenga.

    Es una función pura: recibe el texto del archivo y no toca el disco ni el registro.
    """
    nombre_archivo = Path(source).name
    try:
        datos = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, [], [f"el archivo no es JSON válido ({exc})"]
    if not isinstance(datos, dict):
        return None, [], ["el archivo tiene que contener un objeto JSON"]

    errores: list[str] = []
    sobran = sorted(set(datos) - DOCUMENT_KEYS)
    if sobran:
        errores.append(f"claves que no existen: {', '.join(sobran)}")
    esquema = datos.get("esquema", SCHEMA_VERSION)
    if esquema != SCHEMA_VERSION:
        errores.append(f"«esquema» tiene que ser {SCHEMA_VERSION}")
    identificador = _texto(datos.get("id"), limite=80) or Path(nombre_archivo).stem
    if not _es_clave_valida(identificador):
        errores.append("«id» tiene que ser un identificador corto (letras, números, punto, guion o guion bajo)")
    nombre = _texto(datos.get("nombre"), limite=120)
    if not nombre:
        errores.append("falta «nombre»")
    crudos = datos.get("ajustes")
    if not isinstance(crudos, list) or not crudos:
        errores.append("«ajustes» tiene que ser una lista con al menos un ajuste")
        crudos = []
    if len(crudos) > MAX_SETTINGS_PER_FILE:
        errores.append(f"demasiados ajustes en un archivo (máximo {MAX_SETTINGS_PER_FILE})")
        crudos = crudos[:MAX_SETTINGS_PER_FILE]

    huella = fingerprint(text)
    ocupados = set(taken) if taken is not None else set(taken_ids(own_plugin=identificador))
    ajustes: list[windows_settings.Setting] = []
    ids: set[str] = set()
    for indice, crudo in enumerate(crudos, start=1):
        ajuste, fallos = _ajuste_from_dict(
            crudo, plugin_id=identificador, plugin_name=nombre, huella=huella
        )
        if fallos:
            errores.extend(f"ajuste {indice}: {motivo}" for motivo in fallos)
            continue
        assert ajuste is not None  # si no hay fallos, hay ajuste
        if ajuste.id in ids or ajuste.id in ocupados:
            errores.append(f"ajuste {indice}: el identificador «{ajuste.id}» ya está usado")
            continue
        ids.add(ajuste.id)
        ajustes.append(ajuste)

    if errores:
        return None, [], errores
    info = PluginInfo(
        id=identificador,
        name=nombre,
        author=_texto(datos.get("autor"), limite=120),
        version=_texto(datos.get("version"), limite=40),
        description=_texto(datos.get("descripcion"), limite=MAX_DOCUMENT_TEXT),
        path=Path(source),
        fingerprint=huella,
        count=len(ajustes),
        needs_admin=sum(1 for ajuste in ajustes if ajuste.needs_admin),
        high_risk=sum(1 for ajuste in ajustes if ajuste.risk == windows_settings.RISK_HIGH),
    )
    return info, ajustes, []


# ---------------------------------------------------------------------------
# Confianza: la huella del archivo que el usuario aceptó
# ---------------------------------------------------------------------------


def read_trust() -> dict[str, Any]:
    """Huellas aceptadas, tal cual están guardadas (dict vacío si no hay nada)."""
    destino = trust_file()
    if not destino.exists():
        return {}
    try:
        datos = json.loads(destino.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _write_trust(datos: dict[str, Any]) -> None:
    destino = trust_file()
    try:
        ensure_dir()
        destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logs.warning(f"No se pudo guardar la confianza de los plugins ({destino})", exc, name="plugins")


def is_trusted(plugin_id: str, huella: str) -> bool:
    """¿Este usuario ya revisó y aceptó **esta** versión del plugin?"""
    datos = read_trust()
    guardado = datos.get(plugin_id)
    return isinstance(guardado, dict) and str(guardado.get("huella") or "") == huella


def accept(info: PluginInfo, *, when: float | None = None) -> None:
    """Acepta un plugin (se guarda su huella: si el archivo cambia, habrá que revisarlo otra vez)."""
    datos = read_trust()
    datos[info.id] = {
        "nombre": info.name,
        "huella": info.fingerprint,
        "at": float(when if when is not None else time.time()),
        "ajustes": info.count,
    }
    _write_trust(datos)


def revoke(plugin_id: str) -> None:
    """Retira la confianza de un plugin: sus ajustes dejan de poder aplicarse."""
    datos = read_trust()
    if datos.pop(plugin_id, None) is not None:
        _write_trust(datos)


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


def _read_plugin_id(text: str) -> str:
    """Identificador del plugin, leído sin validar el resto (para saber qué identificadores
    son «suyos» al comprobar repeticiones)."""
    try:
        datos = json.loads(text)
    except (ValueError, TypeError):
        return ""
    if not isinstance(datos, dict):
        return ""
    identificador = _texto(datos.get("id"), limite=80)
    return identificador if _es_clave_valida(identificador) else ""


def load(folder: Path | None = None) -> PluginLoad:
    """Lee la carpeta de plugins y devuelve ajustes, fichas y errores.

    Un archivo con errores **no se carga** y no arrastra a los demás: los buenos siguen
    funcionando y el motivo del malo se enseña en la tarjeta de plugins.
    """
    carpeta = Path(folder) if folder else plugins_dir()
    resultado = PluginLoad()
    if not carpeta.is_dir():
        return resultado
    archivos = sorted(carpeta.glob("*.json"))
    if len(archivos) > MAX_FILES:
        resultado.errors.append(("(carpeta)", f"hay más de {MAX_FILES} archivos de plugin: solo se leen los primeros"))
        archivos = archivos[:MAX_FILES]
    cargados: dict[str, str] = {}  # identificador de plugin -> archivo que lo trajo
    por_plugin: dict[str, list[windows_settings.Setting]] = {}
    for archivo in archivos:
        if archivo.name == trust_file().name:
            continue
        try:
            if archivo.stat().st_size > MAX_FILE_BYTES:
                resultado.errors.append((archivo.name, f"el archivo pasa de {MAX_FILE_BYTES // 1024} KB"))
                continue
            texto = archivo.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            resultado.errors.append((archivo.name, f"no se pudo leer ({exc})"))
            continue
        info_previa = _read_plugin_id(texto) or Path(archivo).stem
        # De lo ya leído en esta pasada solo cuentan los ajustes de **otros** plugins: los de
        # este mismo identificador son suyos y no pueden chocar consigo mismos.
        pendientes_de_otros = {
            ajuste.id
            for otro_id, ajustes_previos in por_plugin.items()
            if otro_id != info_previa
            for ajuste in ajustes_previos
        }
        info, ajustes, errores = parse_document(
            texto,
            source=str(archivo),
            taken=taken_ids(own_plugin=info_previa, pending=pendientes_de_otros),
        )
        if info is None:
            resultado.errors.append((archivo.name, "; ".join(errores)))
            continue
        # Dos archivos con el mismo identificador de plugin (una copia del mismo, casi
        # siempre) traerían los mismos ajustes dos veces: se queda el primero y el otro se
        # rechaza con el motivo, en vez de duplicar filas en la página.
        previo = cargados.get(info.id)
        if previo is not None:
            resultado.errors.append((archivo.name, f"el plugin «{info.id}» ya está cargado desde {previo}"))
            continue
        cargados[info.id] = archivo.name
        por_plugin[info.id] = list(ajustes)
        info.trusted = is_trusted(info.id, info.fingerprint)
        resultado.plugins.append(info)
        resultado.settings.extend(ajustes)
    resultado.plugins.sort(key=lambda info: (not info.trusted, info.name.lower()))
    return resultado


_loaded: PluginLoad | None = None
_guard_installed = False


def apply_reason(setting: windows_settings.Setting) -> str:
    """Por qué **no** se puede aplicar este ajuste (vacío si sí se puede).

    Los ajustes del catálogo siempre pasan; los de un plugin, solo si ese plugin está
    aceptado. Y como la huella se compara con la del archivo, editarlo tumba el permiso.
    """
    if not setting.plugin:
        return ""
    if is_trusted(setting.plugin, setting.plugin_fingerprint):
        return ""
    return (
        f"«{setting.name}» viene del plugin «{setting.plugin_name}», que no has revisado todavía. "
        "Ábrelo desde la tarjeta de Plugins y acéptalo antes de aplicarlo."
    )


def install_guard() -> None:
    """Pide al catálogo que consulte el permiso de los plugins antes de aplicar nada."""
    global _guard_installed
    if _guard_installed:
        return
    windows_settings.set_apply_guard(apply_reason)
    _guard_installed = True


def loaded() -> PluginLoad:
    """Última carga hecha (vacía si todavía no se ha cargado nada)."""
    return _loaded or PluginLoad()


def load_into_catalog(folder: Path | None = None) -> PluginLoad:
    """Carga los plugins y registra sus ajustes en el catálogo (una sola vez)."""
    global _loaded
    install_guard()
    resultado = load(folder)
    windows_settings.register_settings(resultado.settings)
    _loaded = resultado
    if resultado.errors:
        for archivo, motivo in resultado.errors:
            logs.warning(f"Plugin rechazado ({archivo}): {motivo}", None, name="plugins")
    return resultado


def reload_into_catalog(folder: Path | None = None) -> PluginLoad:
    """Vuelve a leer la carpeta (lo usa el botón «Volver a leer» de la página)."""
    global _loaded
    if _loaded is not None:
        windows_settings.forget_plugin_settings()
    _loaded = None
    return load_into_catalog(folder)


def write_example(path: Path | None = None) -> Path | None:
    """Escribe la plantilla en la carpeta de plugins (sin pisar nada que exista)."""
    carpeta = ensure_dir()
    destino = Path(path) if path else carpeta / "ejemplo.json"
    if destino.exists():
        return destino
    try:
        destino.write_text(example_text(), encoding="utf-8")
    except OSError as exc:
        logs.warning(f"No se pudo escribir el plugin de ejemplo ({destino})", exc, name="plugins")
        return None
    return destino


def summary_text(load_result: PluginLoad | None = None) -> str:
    """Resumen legible de los plugins instalados (para «Copiar resumen»)."""
    resultado = load_result or loaded()
    lineas = ["Plugins de PC Toolkit", f"carpeta: {plugins_dir()}", ""]
    if not resultado.plugins:
        lineas.append("No hay ningún plugin instalado.")
    for info in resultado.plugins:
        estado = "aceptado" if info.trusted else "SIN CONFIRMAR"
        lineas.append(
            f"[{estado}] {info.name} ({info.id}) · {info.count} ajuste(s)"
            + (" · toca HKEY_LOCAL_MACHINE" if info.needs_admin else "")
            + (" · con riesgo alto" if info.high_risk else "")
        )
        if info.author:
            lineas.append(f"    autor: {info.author}")
        if info.path:
            lineas.append(f"    archivo: {info.path}")
    for archivo, motivo in resultado.errors:
        lineas.append(f"[error] {archivo}: {motivo}")
    return "\n".join(lineas)
