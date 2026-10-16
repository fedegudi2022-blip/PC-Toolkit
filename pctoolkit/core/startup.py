r"""Programas y tareas que arrancan con el equipo.

Lee las mismas fuentes que usa Windows:

* las claves `Run` y `RunOnce` del usuario y del sistema (las de 32 bits de
  `HKLM` se leen aparte, en su vista `WOW6432Node`, que es donde las instalan
  los programas antiguos),
* las carpetas de inicio del usuario y del sistema,
* las tareas programadas con disparador «al iniciar sesión» (fuera de las de
  Microsoft, que son parte del propio Windows),
* las tareas de inicio de las aplicaciones de la Microsoft Store, que Windows guarda en
  el registro del usuario y que ninguna clave `Run` menciona.

**Desactivar no borra nada.** Se escribe la misma marca que escribe el
Administrador de tareas en `...\\Explorer\\StartupApproved\\...`: la entrada sigue
donde estaba, Windows deja de ejecutarla y se puede volver a activar. Las
entradas `RunOnce` se muestran sin botón porque Windows no las aprueba ni las
desaprueba: se ejecutan una vez y desaparecen solas.

Las aplicaciones de la Store también se pueden activar y desactivar desde aquí: se
escribe el estado que usa Windows (`Windows.ApplicationModel.StartupTaskState`) en
`...\AppModel\SystemAppData\<paquete>\<tarea>`, que es exactamente lo que cambia el
Administrador de tareas al desactivarlas, y se relee para comprobar que quedó escrito.

Lo que este módulo **no** puede ver: los servicios, que se administran en la página de
herramientas, en «Servicios».
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import undo
from .tools import powershell_environment

IS_WINDOWS = os.name == "nt"

try:  # el registro solo existe en Windows
    import winreg
except ImportError:  # pragma: no cover - solo en otros sistemas
    winreg = None  # type: ignore[assignment]

# --- Orígenes -------------------------------------------------------------
SOURCE_USUARIO = "Tu usuario"
SOURCE_SISTEMA = "Todos los usuarios"
SOURCE_CARPETA_USUARIO = "Carpeta de inicio tuya"
SOURCE_CARPETA_COMUN = "Carpeta de inicio común"
SOURCE_TAREA = "Tarea al iniciar sesión"
SOURCE_STORE = "Aplicación de la Store"

KIND_REGISTRO = "registro"
KIND_CARPETA = "carpeta"
KIND_TAREA = "tarea"
KIND_STORE = "store"

# --- Estimación del coste al arrancar ------------------------------------
# Windows no publica la cifra que muestra el Administrador de tareas y la
# calcula por su cuenta, así que aquí no se inventa un número: se usa el tipo de
# entrada, que es un dato real y explicable. El nombre lo dice: es una
# estimación, y la interfaz lo aclara con todas las letras.
COST_LIGERO = "Ligero"
COST_MEDIO = "Medio"
COST_NULO = "No arranca"

COST_ORDER = {COST_MEDIO: 0, COST_LIGERO: 1, COST_NULO: 2}

RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_ONCE_PATH = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
APPROVED_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
APPROVED_RUN = APPROVED_ROOT + r"\Run"
APPROVED_RUN32 = APPROVED_ROOT + r"\Run32"
APPROVED_FOLDER = APPROVED_ROOT + r"\StartupFolder"

# Claves en las que PC Toolkit escribe marcas de activado/desactivado. La lista es cerrada
# a propósito: de dónde se escribe llega en los datos de la entrada y esos datos vienen
# también de `deshacer.jsonl`, un archivo de texto que cualquier proceso del usuario puede
# editar. Sin esta lista, un apunte retocado a mano haría que PC Toolkit escribiera en
# otra rama del registro (por ejemplo en `Run`, donde arrancaría otra cosa).
APPROVED_PATHS = (APPROVED_RUN, APPROVED_RUN32, APPROVED_FOLDER)

# Las aplicaciones de la Store no dicen que arrancan en ninguna clave `Run`: declaran una
# «tarea de inicio» en su manifiesto y Windows guarda su estado aquí, en el registro del
# usuario. La clave lleva el paquete (`Nombre_Publicador`) y, dentro, la tarea.
STORE_PATH = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
    r"\AppModel\SystemAppData"
)
STORE_STATE_VALUE = "State"

# Estados de `Windows.ApplicationModel.StartupTaskState`, publicados por Microsoft:
#   0 desactivada · 1 desactivada por el usuario · 2 activada ·
#   3 desactivada por directiva · 4 activada por directiva
# Un valor fuera de esa lista devuelve `None`: no se adivina el estado de algo que además
# se puede escribir.
STORE_STATES = {0: False, 1: False, 2: True, 3: False, 4: True}
STORE_ENABLED = 2
STORE_DISABLED_BY_USER = 1

STARTUP_FOLDER = r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
COMMON_STARTUP_FOLDER = r"%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup"

# Marcas de `StartupApproved`: el primer byte del valor. Windows escribe 2
# (activada) o 3 (desactivada) y, al desactivar, añade la fecha en los ocho
# bytes siguientes. Aquí solo se lee el primer byte, que es lo que respeta
# Windows al iniciar la sesión.
DISABLED_MARKS = (3, 7)

# Segundos entre el 1/1/1601 y el 1/1/1970 (formato FILETIME de Windows).
_FILETIME_EPOCH = 11_644_473_600

_EXECUTABLE_EXTENSIONS = (".exe", ".com", ".bat", ".cmd", ".lnk", ".ps1", ".vbs", ".js", ".url", ".msc")

_IGNORED_FOLDER_NAMES = {"desktop.ini", "thumbs.db"}

# Carpetas que no dicen nada del programa: si el ejecutable está dentro de una
# de ellas se sube un nivel para enseñar el nombre que el usuario reconoce
# (`...\Discord\Update.exe` → Discord, no «Update»; `...\Edge\Application\msedge.exe`
# → Edge, no «Application»).
_GENERIC_FOLDERS = {
    "application",
    "app",
    "bin",
    "current",
    "plugins",
    "resources",
    "system_tray",
    "versions",
    "version_x86",
}


def is_elevated() -> bool:
    """¿La aplicación se está ejecutando como administrador?"""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


@dataclass(frozen=True)
class RegistrySource:
    """Una clave del registro que Windows ejecuta al iniciar la sesión."""

    id: str
    label: str
    location: str
    run_path: str
    hive: Any
    approved_path: str = ""
    view: int = 0
    needs_admin: bool = False
    can_toggle: bool = True
    note: str = ""


@dataclass(frozen=True)
class FolderSource:
    """Una carpeta de inicio: lo que hay dentro se abre al entrar en Windows."""

    id: str
    label: str
    folder: str
    approved_path: str
    hive: Any
    needs_admin: bool = False
    can_toggle: bool = True

    @property
    def path(self) -> str:
        return os.path.expandvars(self.folder)


def _view_flag(name: str) -> int:
    if winreg is None:
        return 0
    return int(getattr(winreg, name, 0))


# Nombres de las raíces del registro, para guardarlos en los apuntes de deshacer.
HIVE_USER = "usuario"
HIVE_MACHINE = "sistema"


def _hive_value(hive: Any) -> str:
    """Nombre corto de la raíz del registro, para el apunte de deshacer."""
    return HIVE_USER if hive == winreg.HKEY_CURRENT_USER else HIVE_MACHINE


def registry_sources() -> tuple[RegistrySource, ...]:
    """Las claves Run y RunOnce reales de este equipo."""
    user = winreg.HKEY_CURRENT_USER
    machine = winreg.HKEY_LOCAL_MACHINE
    view64 = _view_flag("KEY_WOW64_64KEY")
    view32 = _view_flag("KEY_WOW64_32KEY")
    return (
        RegistrySource(
            "usuario-run",
            SOURCE_USUARIO,
            "HKEY_CURRENT_USER\\" + RUN_PATH,
            RUN_PATH,
            user,
            APPROVED_RUN,
        ),
        RegistrySource(
            "usuario-runonce",
            SOURCE_USUARIO,
            "HKEY_CURRENT_USER\\" + RUN_ONCE_PATH,
            RUN_ONCE_PATH,
            user,
            can_toggle=False,
            note="Se ejecuta una sola vez, la siguiente vez que entres en Windows.",
        ),
        RegistrySource(
            "sistema-run",
            SOURCE_SISTEMA,
            "HKEY_LOCAL_MACHINE\\" + RUN_PATH,
            RUN_PATH,
            machine,
            APPROVED_RUN,
            view=view64,
            needs_admin=True,
        ),
        RegistrySource(
            "sistema-run32",
            SOURCE_SISTEMA,
            "HKEY_LOCAL_MACHINE\\...\\WOW6432Node\\" + RUN_PATH,
            RUN_PATH,
            machine,
            APPROVED_RUN32,
            view=view32,
            needs_admin=True,
        ),
        RegistrySource(
            "sistema-runonce",
            SOURCE_SISTEMA,
            "HKEY_LOCAL_MACHINE\\" + RUN_ONCE_PATH,
            RUN_ONCE_PATH,
            machine,
            view=view64,
            can_toggle=False,
            note="Se ejecuta una sola vez, antes de entrar en Windows.",
        ),
    )


def folder_sources() -> tuple[FolderSource, ...]:
    """Las dos carpetas de inicio: la del usuario y la común."""
    return (
        FolderSource(
            "carpeta-usuario",
            SOURCE_CARPETA_USUARIO,
            STARTUP_FOLDER,
            APPROVED_FOLDER,
            winreg.HKEY_CURRENT_USER,
        ),
        FolderSource(
            "carpeta-comun",
            SOURCE_CARPETA_COMUN,
            COMMON_STARTUP_FOLDER,
            APPROVED_FOLDER,
            winreg.HKEY_LOCAL_MACHINE,
            needs_admin=True,
        ),
    )


# --- Interpretación de un comando ----------------------------------------
def _argv_windows(text: str) -> list[str]:
    """Parte la línea con `CommandLineToArgvW`, la misma función que usa Windows."""
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        cuenta = ctypes.c_int(0)
        argv = shell32.CommandLineToArgvW(str(text), ctypes.byref(cuenta))
        if not argv:
            return []
        try:
            return [str(argv[indice]) for indice in range(max(0, cuenta.value))]
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return []


def _split_like_windows(text: str) -> list[str]:
    """Separación de reserva (sin Windows): por espacios, respetando las comillas."""
    partes: list[str] = []
    actual = ""
    entre_comillas = False
    for caracter in text:
        if caracter == '"':
            entre_comillas = not entre_comillas
            continue
        if caracter == " " and not entre_comillas:
            if actual:
                partes.append(actual)
                actual = ""
            continue
        actual += caracter
    if actual:
        partes.append(actual)
    return partes


def command_arguments(command: str) -> list[str]:
    """Parte una línea de comando como lo haría el propio Windows.

    Con esto una ruta entre comillas que lleva espacios sigue siendo un solo argumento,
    sin tener que adivinar dónde termina el programa.
    """
    text = (command or "").strip()
    if not text:
        return []
    argv = _argv_windows(text) if IS_WINDOWS else []
    return argv or _split_like_windows(text)


def _is_executable(path: str) -> bool:
    r"""¿Termina en una extensión de programa?

    Se compara el **final** de la cadena. Buscar la extensión por dentro daba falsos
    positivos: `.com` encaja dentro de `.company`, así que una ruta como
    `C:\Program Files\App.Company\app.exe` se cortaba en `C:\Program Files\App.com` y
    la entrada parecía apuntar a un archivo que ya no existe.
    """
    return path.lower().endswith(_EXECUTABLE_EXTENSIONS)


def command_target(command: str) -> str:
    """Ruta del programa que hay dentro de una línea de comando.

    Puede venir entre comillas (`"C:\\Program Files\\...\\app.exe" --flag`) o suelta.
    Las líneas sin comillas con espacios son el caso difícil: Windows las corta por el
    primer espacio, así que el primer argumento no dice dónde está el programa. Para
    esas se prueban prefijos cada vez más largos —primero los que existen como archivo,
    que es la respuesta segura, y si no el primero con pinta de programa, que es lo que
    hay que enseñar cuando el archivo ya no está—.

    No siempre es un ejecutable: `rundll32`, `cmd /c` o un `shell:` devuelven algo que no
    es un archivo, y eso también es información.
    """
    partes = command_arguments(command)
    if not partes:
        return ""
    primero = partes[0]
    if len(partes) == 1 or _is_executable(primero):
        return primero
    candidatos: list[str] = []
    for cantidad in range(2, len(partes) + 1):
        candidato = " ".join(partes[:cantidad])
        if not _is_executable(candidato) and not os.path.isfile(candidato):
            continue
        if os.path.isfile(candidato):
            return candidato
        candidatos.append(candidato)
    if candidatos:
        return candidatos[0]
    # No existe ningún prefijo con pinta de programa: se devuelve la ruta entera sin los
    # argumentos (lo que empieza por guion o barra), que es la lectura más razonable.
    for indice in range(1, len(partes)):
        if partes[indice].startswith(("-", "/")):
            return " ".join(partes[:indice])
    return " ".join(partes)


def _target_state(command: str) -> tuple[str, bool | None]:
    """(ruta, ¿existe?). `None` cuando no se puede saber sin inventar."""
    path = command_target(command)
    if not path:
        return "", None
    expanded = os.path.expandvars(path).strip('"')
    if expanded.startswith("shell:") or expanded.startswith("%"):
        return expanded, None
    if not os.path.isabs(expanded):
        return expanded, None
    try:
        return expanded, os.path.exists(expanded)
    except OSError:
        return expanded, None


@dataclass
class StartupEntry:
    """Una cosa que arranca con Windows."""

    id: str
    name: str
    command: str
    source: str
    location: str
    kind: str
    enabled: bool
    needs_admin: bool = False
    can_toggle: bool = True
    note: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    # Ruta y existencia del programa, ya resueltas (no se serializan: se recalculan).
    _target: tuple[str, bool | None] | None = field(default=None, init=False, repr=False, compare=False)

    # --- Datos derivados (se calculan a partir del comando) --------------
    def resolve_target(self) -> tuple[str, bool | None]:
        """Ruta del programa y si existe, calculadas **una sola vez**.

        Se llama al leer las entradas, que es cuando corre el hilo de lectura: preguntar
        por un archivo en cada acceso (y se accede en cada repintado de la lista) puede
        quedarse esperando por una unidad de red o un disco apagado, y eso se notaría en
        la ventana.
        """
        if self._target is None:
            self._target = _target_state(self.command)
        return self._target

    @property
    def target_path(self) -> str:
        return self.resolve_target()[0]

    @property
    def target_exists(self) -> bool | None:
        return self.resolve_target()[1]

    @property
    def origin_folder(self) -> str:
        """Carpeta reconocible del programa."""
        path = self.target_path
        if not path:
            return ""
        folder = os.path.dirname(path.rstrip("\\/"))
        name = os.path.basename(folder)
        for _ in range(3):
            lowered = name.lower()
            if name and lowered not in _GENERIC_FOLDERS and not lowered.startswith("version-"):
                return name
            parent = os.path.dirname(folder)
            if not parent or parent == folder:
                break
            folder = parent
            name = os.path.basename(folder)
        return name or os.path.basename(path)

    @property
    def status_label(self) -> str:
        return "Activada" if self.enabled else "Desactivada"

    @property
    def cost(self) -> tuple[str, str]:
        """Estimación propia del coste al arrancar: (nivel, motivo).

        No es una medida: es el tipo de entrada, que sí es un dato real. Las
        tareas de inicio de sesión llevan más trabajo (Windows las lanza como
        una tarea con su propio contexto) que una línea del registro.
        """
        if self.target_exists is False:
            return COST_NULO, "El archivo al que apunta ya no está en el equipo: no arranca nada."
        if self.kind in (KIND_TAREA, KIND_STORE):
            return COST_MEDIO, (
                "Es una tarea programada: Windows la lanza aparte al entrar."
                if self.kind == KIND_TAREA
                else "Es una aplicación de la Store: Windows la abre al entrar en la sesión."
            )
        return COST_LIGERO, "Es una entrada del registro o un acceso directo: se abre al entrar."

    @property
    def display_command(self) -> str:
        """Comando corto para la lista (el completo se lee en el consejo)."""
        text = " ".join((self.command or "").split())
        return text if len(text) <= 120 else text[:117] + "…"

    @property
    def problem(self) -> str:
        """Aviso en una línea, solo si hay algo que el usuario deba saber."""
        if self.target_exists is False:
            return "El archivo al que apunta ya no existe"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.name,
            "comando": self.command,
            "origen": self.source,
            "ubicacion": self.location,
            "tipo": self.kind,
            "activada": self.enabled,
            "admin": self.needs_admin,
            "conmutable": self.can_toggle,
            "nota": self.note,
            "datos": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StartupEntry":
        payload = data if isinstance(data, dict) else {}
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("nombre", "")),
            command=str(payload.get("comando", "")),
            source=str(payload.get("origen", "")),
            location=str(payload.get("ubicacion", "")),
            kind=str(payload.get("tipo", KIND_REGISTRO)),
            enabled=bool(payload.get("activada", True)),
            needs_admin=bool(payload.get("admin", False)),
            can_toggle=bool(payload.get("conmutable", True)),
            note=str(payload.get("nota", "")),
            data=dict(payload.get("datos") or {}),
        )


# --- Lectura --------------------------------------------------------------
def approval_bytes(enabled: bool, moment: float | None = None) -> bytes:
    """El valor que escribe Windows en `StartupApproved` (2 activada / 3 desactivada)."""
    when = time.time() if moment is None else float(moment)
    stamp = int((when + _FILETIME_EPOCH) * 10_000_000)
    first = b"\x02" if enabled else b"\x03"
    extra = (0).to_bytes(8, "little") if enabled else stamp.to_bytes(8, "little")
    return first + extra


def approval_enabled(value: Any) -> bool:
    """Interpreta un valor de `StartupApproved`; sin dato, la entrada está activa."""
    if isinstance(value, (bytes, bytearray)) and value:
        return value[0] not in DISABLED_MARKS
    return True


def _approved_view(hive: Any) -> int:
    """Vista con la que se lee `StartupApproved`.

    Las marcas de activado/desactivado de las entradas del sistema viven en la
    vista de 64 bits (es ahí donde las escribe Windows), aunque la entrada que
    describen esté en la vista de 32 bits. Por eso no se usa la vista de la
    clave `Run`, sino la suya propia.
    """
    if winreg is None or hive != winreg.HKEY_LOCAL_MACHINE:
        return 0
    return _view_flag("KEY_WOW64_64KEY")


def _read_values(hive: Any, path: str, view: int = 0) -> dict[str, Any]:
    """Valores de una clave del registro; vacío si no existe o no se puede leer."""
    if winreg is None:
        return {}
    flags = winreg.KEY_READ | view
    try:
        with winreg.OpenKey(hive, path, 0, flags) as handle:
            values: dict[str, Any] = {}
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(handle, index)
                except OSError:
                    break
                values[str(name)] = value
                index += 1
            return values
    except (FileNotFoundError, OSError):
        return {}


def _write_approval(hive: Any, path: str, value_name: str, enabled: bool, view: int = 0) -> None:
    """Escribe la marca de activada/desactivada (crea la clave si hace falta)."""
    if winreg is None:
        raise OSError("El registro solo existe en Windows.")
    flags = winreg.KEY_SET_VALUE | winreg.KEY_READ | view
    with winreg.CreateKeyEx(hive, path, 0, flags) as handle:
        winreg.SetValueEx(handle, value_name, 0, winreg.REG_BINARY, approval_bytes(enabled))
        stored, _kind = winreg.QueryValueEx(handle, value_name)  # se relee para comprobar
    if approval_enabled(stored) != enabled:
        raise OSError("Windows no guardó el cambio.")


def read_registry_entries(sources: Sequence[RegistrySource] | None = None) -> list[StartupEntry]:
    """Entradas de las claves Run y RunOnce."""
    if winreg is None:
        return []
    entries: list[StartupEntry] = []
    for source in sources if sources is not None else registry_sources():
        values = _read_values(source.hive, source.run_path, source.view)
        approved = (
            _read_values(source.hive, source.approved_path, _approved_view(source.hive))
            if source.approved_path
            else {}
        )
        for name, command in values.items():
            clean = str(name).strip()
            if not clean:
                continue
            enabled = approval_enabled(approved.get(clean)) if source.approved_path else True
            entrada = StartupEntry(
                id=f"{source.id}|{clean}",
                name=clean,
                command=str(command or "").strip(),
                source=source.label,
                location=source.location,
                kind=KIND_REGISTRO,
                enabled=enabled,
                needs_admin=source.needs_admin,
                can_toggle=source.can_toggle,
                note=source.note
                or (
                    "La desactiva el propio Windows; la entrada se queda donde está."
                    if source.approved_path
                    else ""
                ),
                data={
                    "clave": source.id,
                    "hive": _hive_value(source.hive),
                    "run_path": source.run_path,
                    "approved_path": source.approved_path,
                    "valor": clean,
                },
            )
            # La ruta y su existencia se resuelven ahora, en el hilo que lee el registro,
            # y no cada vez que la lista se repinta.
            entrada.resolve_target()
            entries.append(entrada)
    return entries


def read_folder_entries(sources: Sequence[FolderSource] | None = None) -> list[StartupEntry]:
    """Entradas de las carpetas de inicio (accesos directos y programas sueltos)."""
    if winreg is None:
        return []
    entries: list[StartupEntry] = []
    for source in sources if sources is not None else folder_sources():
        path = source.path
        if not path or not os.path.isdir(path):
            continue
        try:
            names = sorted(os.listdir(path))
        except OSError:
            continue
        approved = (
            _read_values(source.hive, source.approved_path, _approved_view(source.hive))
            if source.can_toggle
            else {}
        )
        for name in names:
            if name.lower() in _IGNORED_FOLDER_NAMES or name.startswith("."):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                continue
            display = os.path.splitext(name)[0]
            entrada = StartupEntry(
                id=f"{source.id}|{name}",
                name=display,
                command=full,
                source=source.label,
                location=path,
                kind=KIND_CARPETA,
                enabled=approval_enabled(approved.get(name)) if source.can_toggle else True,
                needs_admin=source.needs_admin,
                can_toggle=source.can_toggle,
                note="Es un acceso directo de la carpeta de inicio.",
                data={
                    "clave": source.id,
                    "hive": _hive_value(source.hive),
                    "approved_path": source.approved_path,
                    "valor": name,
                },
            )
            entrada.resolve_target()
            entries.append(entrada)
    return entries


TASKS_SCRIPT = (
    # La salida se lee como UTF-8: sin esto, PowerShell escribe en la página del sistema
    # y los nombres y rutas con acentos llegan corruptos.
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='SilentlyContinue';"
    "$lista = Get-ScheduledTask | Where-Object {"
    "$_.TaskPath -notlike '\\Microsoft\\*' -and "
    "($_.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' })} | "
    "ForEach-Object {"
    "$a = $_.Actions | Select-Object -First 1;"
    "[pscustomobject]@{"
    "nombre=[string]$_.TaskName;"
    "ruta=[string]$_.TaskPath;"
    "estado=[string]$_.State;"
    "programa=[string]$a.Execute;"
    "argumentos=[string]$a.Arguments"
    "}};"
    "ConvertTo-Json -InputObject @($lista) -Compress -Depth 3"
)


STORE_SCRIPT = (
    # La salida se lee como UTF-8: sin esto, PowerShell escribe en la página del sistema
    # y los nombres con acentos llegan corruptos.
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='SilentlyContinue';"
    "$base='HKCU:\\" + STORE_PATH + "';"
    # El nombre del paquete da la identidad y el del menú Inicio, el nombre que reconoce
    # el usuario: en la lista tiene que poner «PC Manager», no «Microsoft.MicrosoftPCManager».
    "$paquetes=@{};"
    "foreach($p in (Get-AppxPackage)){ $paquetes[$p.PackageFamilyName]=[string]$p.Name };"
    "$inicio=@(Get-StartApps);"
    "$lista=@();"
    "foreach($clave in (Get-ChildItem -LiteralPath $base)){"
    "foreach($tarea in (Get-ChildItem -LiteralPath $clave.PSPath | Where-Object { $_.PSChildName -ne 'Schemas' })){"
    "$props=Get-ItemProperty -LiteralPath $tarea.PSPath;"
    "if($props.PSObject.Properties.Name -contains 'State'){"
    "$paquete=$clave.PSChildName;"
    "$identidad=[string]$paquetes[$paquete];"
    "if(-not $identidad){ $identidad=($paquete -split '_')[0] };"
    "$nombre='';"
    "foreach($app in $inicio){ if([string]$app.AppID -like ($paquete + '!*')){ $nombre=[string]$app.Name; break } };"
    "$lista += [pscustomobject]@{paquete=$paquete;tarea=[string]$tarea.PSChildName;"
    "estado=[int]$props.State;nombre=$nombre;identidad=$identidad}"
    "}}}" + ";"
    "ConvertTo-Json -InputObject @($lista) -Compress -Depth 3"
)


def task_entries_from_output(output: str) -> list[StartupEntry]:
    """Convierte la salida del Programador de tareas en entradas (función pura)."""
    payload = (output or "").strip()
    if not payload.startswith("[") and not payload.startswith("{"):
        return []
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return []
    rows = data if isinstance(data, list) else [data]
    entries: list[StartupEntry] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("nombre"):
            continue
        folder = str(row.get("ruta") or "\\")
        name = str(row["nombre"])
        program = str(row.get("programa") or "")
        arguments = str(row.get("argumentos") or "")
        command = " ".join(part for part in (program, arguments) if part).strip()
        entries.append(
            StartupEntry(
                id=f"tarea|{folder}{name}",
                name=name,
                command=command,
                source=SOURCE_TAREA,
                location=f"Programador de tareas · {(folder.strip(chr(92)) or 'raíz')}\\{name}",
                kind=KIND_TAREA,
                enabled=str(row.get("estado") or "").lower() != "disabled",
                needs_admin=False,
                can_toggle=True,
                note="La controla el Programador de tareas de Windows.",
                data={"clave": "tarea", "tarea": f"{folder}{name}"},
            )
        )
    return entries


def read_task_entries(timeout: int = 60) -> list[StartupEntry]:
    """Tareas de inicio de sesión que no son de Microsoft (tarda unos segundos)."""
    if not IS_WINDOWS:
        return []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", TASKS_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=powershell_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    entradas = task_entries_from_output(result.stdout or "")
    for entrada in entradas:
        # La ruta del programa y su existencia se resuelven aquí, en el hilo que consulta
        # el Programador de tareas, y no al pintar la lista.
        entrada.resolve_target()
    return entradas


def store_state_enabled(value: Any) -> bool | None:
    """¿La tarea de inicio está activa? `None` si Windows informa de un estado desconocido.

    Los cinco estados que publica Microsoft están en `STORE_STATES`. Cualquier otro valor
    devuelve `None` a propósito: no se adivina el estado de algo que además se escribe.
    """
    try:
        estado = int(value)
    except (TypeError, ValueError):
        return None
    return STORE_STATES.get(estado)


def store_entries_from_output(output: str) -> list[StartupEntry]:
    """Convierte la salida del guion de la Store en entradas (función pura).

    Las tareas cuyo estado Windows no documenta se dejan fuera: son las únicas que no se
    pueden enseñar ni cambiar sin adivinar.
    """
    payload = (output or "").strip()
    if not payload.startswith("[") and not payload.startswith("{"):
        return []
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return []
    rows = data if isinstance(data, list) else [data]
    entries: list[StartupEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # El paquete y la tarea van al registro al cambiar el estado, así que se validan
        # aquí, al leerlos: un nombre con separadores escribiría en otra rama.
        paquete = _safe_value_name(row.get("paquete") or "")
        tarea = _safe_value_name(row.get("tarea") or "")
        if not paquete or not tarea:
            continue
        activa = store_state_enabled(row.get("estado"))
        if activa is None:
            continue
        identidad = str(row.get("identidad") or "").strip() or paquete.split("_")[0]
        entries.append(
            StartupEntry(
                id=f"store|{paquete}|{tarea}",
                name=str(row.get("nombre") or "").strip() or identidad,
                # Sin comando: una aplicación de la Store no es un archivo que se pueda
                # enseñar ni buscar en el disco, y devolver una ruta inventada haría
                # creer que la entrada apunta a algo que no está.
                command="",
                source=SOURCE_STORE,
                location=f"Aplicación de la Store · {identidad}",
                kind=KIND_STORE,
                enabled=activa,
                needs_admin=False,
                can_toggle=True,
                note=(
                    "La arranca Windows como aplicación de la Store; aquí se cambia su "
                    "tarea de inicio, igual que en el Administrador de tareas."
                ),
                data={"clave": "store", "paquete": paquete, "tarea": tarea},
            )
        )
    return entries


def read_store_entries(timeout: int = 90) -> list[StartupEntry]:
    """Tareas de inicio de las aplicaciones de la Store (tarda unos segundos).

    Son tres consultas a Windows —el estado en el registro, los paquetes y los nombres del
    menú Inicio— dentro del **mismo** proceso de PowerShell: separarlas costaría el
    arranque de PowerShell (~2 s en un equipo real) cada una.
    """
    if not IS_WINDOWS:
        return []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", STORE_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=powershell_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return store_entries_from_output(result.stdout or "")


def read_slow_entries() -> list[StartupEntry]:
    """Las dos lecturas lentas de la página, en una sola llamada de fondo.

    Las tareas de inicio de sesión y las aplicaciones de la Store se consultan juntas
    porque las dos tardan segundos y las dos alimentan la misma lista: así la página lanza
    un solo trabajo en segundo plano en vez de esperar a dos.
    """
    return read_task_entries() + read_store_entries()


SOURCES_ORDER = (
    SOURCE_USUARIO,
    SOURCE_SISTEMA,
    SOURCE_CARPETA_USUARIO,
    SOURCE_CARPETA_COMUN,
    SOURCE_TAREA,
    SOURCE_STORE,
)


def source_order(source: str) -> int:
    """Posición de un origen en el orden natural de la lista."""
    try:
        return SOURCES_ORDER.index(source)
    except ValueError:
        return len(SOURCES_ORDER)


def sort_entries(entries: Iterable[StartupEntry]) -> list[StartupEntry]:
    """Activadas primero y, dentro de cada grupo, por origen y nombre."""
    return sorted(
        entries,
        key=lambda entry: (not entry.enabled, source_order(entry.source), entry.name.lower()),
    )


def read_entries(
    sources: Sequence[RegistrySource] | None = None,
    folders: Sequence[FolderSource] | None = None,
) -> list[StartupEntry]:
    """Entradas del registro y de las carpetas de inicio (lectura rápida)."""
    return sort_entries(
        list(read_registry_entries(sources)) + list(read_folder_entries(folders))
    )


# --- Cambios --------------------------------------------------------------
def _hive_from(value: Any) -> Any:
    """Recupera la raíz del registro guardada en los datos de la entrada.

    Solo se aceptan los dos nombres que escribe la propia aplicación (`usuario` y
    `sistema`): lo que no esté en esa lista no se escribe, aunque venga en un apunte.
    """
    if winreg is None:
        return None
    if value == HIVE_USER:
        return winreg.HKEY_CURRENT_USER
    if value == HIVE_MACHINE:
        return winreg.HKEY_LOCAL_MACHINE
    return None


def _safe_value_name(name: str) -> str:
    """Nombre de valor del registro, si se puede usar dentro de su clave.

    Una barra invertida en el nombre escribiría en otra rama del registro, así que se
    rechaza: lo que se espera aquí es el nombre de la entrada (`Discord`, `PruebaApp`…).
    """
    limpio = str(name or "").strip()
    if not limpio or limpio in (".", ".."):
        return ""
    if any(separador in limpio for separador in ("\\", "/", ":")):
        return ""
    return limpio


def _safe_task_name(name: str) -> str:
    """Nombre de tarea del Programador, si PC Toolkit puede tocarla.

    Devuelve el nombre con su ruta (como lo espera `schtasks`) o vacío cuando no se debe
    tocar: las tareas de Microsoft son parte del propio Windows y aquí no se desactivan.
    """
    limpio = str(name or "").strip().replace("/", "\\").lstrip("\\")
    if not limpio or ".." in limpio:
        return ""
    if limpio.lower() == "microsoft" or limpio.lower().startswith("microsoft\\"):
        return ""
    return "\\" + limpio


def _validated_registry_target(entry: StartupEntry) -> tuple[Any, str, str, str]:
    """Clave, nombre y raíz del registro de una entrada, comprobados de antemano.

    Devuelve `(hive, approved_path, valor, problema)`: el problema va vacío cuando todo
    está en orden. Los datos se validan antes de escribir porque no se sabe de dónde
    vienen (la lista de entradas y los apuntes de deshacer se guardan en archivos de
    texto que el usuario puede editar).
    """
    hive = _hive_from(entry.data.get("hive"))
    approved = str(entry.data.get("approved_path") or "")
    valor = _safe_value_name(entry.data.get("valor") or entry.name)
    if hive is None:
        return None, "", "", "El apunte no dice de qué parte del registro se trata."
    if approved not in APPROVED_PATHS:
        return (
            None,
            "",
            "",
            "El apunte manda escribir en una clave que PC Toolkit no usa "
            f"({approved or 'sin clave'}); no se toca nada.",
        )
    if not valor:
        return None, "", "", "El apunte trae un nombre de valor que no se puede usar."
    return hive, approved, valor, ""


def set_enabled(entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
    """Activa o desactiva una entrada. Devuelve (éxito, mensaje real de Windows)."""
    if winreg is None and entry.kind != KIND_TAREA:
        return False, "El registro solo existe en Windows."
    if entry.kind == KIND_TAREA:
        return _set_task_enabled(entry, enabled)
    if entry.kind == KIND_STORE:
        return _set_store_enabled(entry, enabled)
    if not entry.can_toggle:
        return False, f"«{entry.name}» no se puede cambiar desde aquí. {entry.note}".strip()
    hive, approved, value_name, problema = _validated_registry_target(entry)
    if problema:
        return False, problema
    try:
        _write_approval(hive, approved, value_name, enabled, _approved_view(hive))
    except PermissionError:
        return False, "Windows denegó el cambio: hace falta ejecutar como administrador."
    except OSError as exc:
        return False, f"No se pudo cambiar «{entry.name}»: {exc}"
    accion = "activada" if enabled else "desactivada"
    return True, f"«{entry.name}» {accion}. Windows lo tendrá en cuenta en el próximo arranque."


def _set_task_enabled(entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
    """Activa o desactiva una tarea programada con `schtasks`."""
    if not IS_WINDOWS:
        return False, "El Programador de tareas solo existe en Windows."
    task = _safe_task_name(entry.data.get("tarea") or entry.name)
    if not task:
        return False, (
            f"«{entry.name}» es una tarea de Windows o trae un nombre que no se puede usar: "
            "PC Toolkit no toca las tareas de Microsoft."
        )
    arguments = ["/Change", "/TN", task, "/ENABLE" if enabled else "/DISABLE"]
    try:
        result = subprocess.run(
            ["schtasks", *arguments],
            capture_output=True,
            text=True,
            # `schtasks` responde en la página OEM de la consola (la 850 en español); leerla
            # como cp1252 corrompía los nombres con acentos.
            encoding="oem",
            errors="replace",
            timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"No se pudo hablar con el Programador de tareas: {exc}"
    if result.returncode == 0:
        accion = "activada" if enabled else "desactivada"
        return True, f"«{entry.name}» {accion} en el Programador de tareas."
    detail = " ".join((result.stderr or result.stdout or "").split())
    return False, (
        f"Windows no dejó cambiar «{entry.name}»"
        + (f" ({detail})" if detail else ".")
        + " Puede requerir permisos de administrador."
    )


def _set_store_enabled(entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
    """Activa o desactiva la tarea de inicio de una aplicación de la Store.

    Se escribe el estado en la clave del paquete —2 activada y 1 desactivada por el
    usuario, los mismos valores que deja el Administrador de tareas— y se **relee**: un
    cambio que Windows no guarde no se puede contar como hecho.
    """
    if winreg is None:
        return False, "El registro solo existe en Windows."
    paquete = _safe_value_name(entry.data.get("paquete") or "")
    tarea = _safe_value_name(entry.data.get("tarea") or "")
    if not paquete or not tarea:
        return False, (
            f"«{entry.name}» no dice de qué paquete de la Store es: mejor cambiarlo desde "
            "el Administrador de tareas, que sí lo sabe."
        )
    ruta = f"{STORE_PATH}\\{paquete}\\{tarea}"
    valor = STORE_ENABLED if enabled else STORE_DISABLED_BY_USER
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, ruta, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ
        ) as clave:
            winreg.SetValueEx(clave, STORE_STATE_VALUE, 0, winreg.REG_DWORD, valor)
            guardado, _tipo = winreg.QueryValueEx(clave, STORE_STATE_VALUE)
    except FileNotFoundError:
        return False, (
            f"Windows ya no tiene la tarea de inicio de «{entry.name}» "
            "(¿se desinstaló la aplicación?)."
        )
    except PermissionError:
        return False, "Windows denegó el cambio: hace falta ejecutar como administrador."
    except OSError as exc:
        return False, f"No se pudo cambiar «{entry.name}»: {exc}"
    if store_state_enabled(guardado) != enabled:
        return False, "Windows no guardó el cambio."
    accion = "activada" if enabled else "desactivada"
    return True, f"«{entry.name}» {accion}. Windows lo tendrá en cuenta en el próximo arranque."


def move(
    entry: StartupEntry,
    enabled: bool,
    store: undo.UndoStore | None = None,
    origin: str = undo.ORIGIN_MANUAL,
) -> tuple[bool, str]:
    """Cambia el estado de una entrada y, si hay almacén, lo anota para deshacer."""
    ok, message = set_enabled(entry, enabled)
    if not ok or store is None:
        return ok, message
    store.add(
        kind=undo.KIND_ARRANQUE,
        title=entry.name,
        before=entry.status_label,
        after="Activada" if enabled else "Desactivada",
        data={"entrada": entry.to_dict(), "antes": bool(entry.enabled), "despues": bool(enabled)},
        origin=origin,
    )
    return True, message


def revert(action: undo.UndoAction, store: undo.UndoStore) -> tuple[bool, str]:
    """Vuelve a dejar una entrada como estaba (lo llama el panel de deshacer)."""
    payload = action.data if isinstance(action.data, dict) else {}
    entry = StartupEntry.from_dict(payload.get("entrada"))
    if not entry.id:
        return False, "El apunte no guarda qué entrada se cambió, así que no se puede deshacer."
    wanted = bool(payload.get("antes", not entry.enabled))
    ok, message = set_enabled(entry, wanted)
    if not ok:
        return False, message
    estado = "activada" if wanted else "desactivada"
    return True, f"«{entry.name}» vuelve a estar {estado}."


undo.register(undo.KIND_ARRANQUE, revert)


# --- Resumen --------------------------------------------------------------
def summary(entries: Sequence[StartupEntry]) -> dict[str, Any]:
    """Cifras para la cabecera de la página."""
    active = [entry for entry in entries if entry.enabled]
    return {
        "total": len(entries),
        "activas": len(active),
        "desactivadas": len(entries) - len(active),
        "sin_archivo": sum(1 for entry in entries if entry.target_exists is False and entry.enabled),
        "con_admin": sum(1 for entry in entries if entry.needs_admin),
        "por_origen": {origen: sum(1 for entry in entries if entry.source == origen) for origen in SOURCES_ORDER},
    }


def heavy_entries(entries: Sequence[StartupEntry]) -> list[StartupEntry]:
    """Entradas que trabajan de verdad al arrancar y se pueden desactivar sin borrar nada.

    Es la misma selección que sostiene el aviso de la portada («tienes N programas de inicio
    pesados»): las que ya están activas, cuyo coste estimado es medio (las tareas de inicio de
    sesión y las aplicaciones de la Store, que Windows lanza aparte) y que además se pueden
    cambiar desde aquí —lo que pide administrador se queda fuera, porque intentarlo solo serviría
    para fallar delante de quien lo pidió—. Se devuelven en el orden de la página para que el
    aviso y la lista digan lo mismo y en el mismo orden.
    """
    return sort_entries(
        [
            entry
            for entry in entries
            if entry.enabled
            and entry.can_toggle
            and not entry.needs_admin
            and entry.cost[0] == COST_MEDIO
        ]
    )


def by_source(entries: Sequence[StartupEntry]) -> dict[str, list[StartupEntry]]:
    """Agrupa las entradas por su origen, en el orden natural de la página."""
    grouped: dict[str, list[StartupEntry]] = {origen: [] for origen in SOURCES_ORDER}
    for entry in entries:
        grouped.setdefault(entry.source, []).append(entry)
    return {origen: items for origen, items in grouped.items() if items}
