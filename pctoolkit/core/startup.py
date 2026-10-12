"""Programas y tareas que arrancan con el equipo.

Lee las mismas fuentes que usa Windows:

* las claves `Run` y `RunOnce` del usuario y del sistema (las de 32 bits de
  `HKLM` se leen aparte, en su vista `WOW6432Node`, que es donde las instalan
  los programas antiguos),
* las carpetas de inicio del usuario y del sistema,
* las tareas programadas con disparador «al iniciar sesión» (fuera de las de
  Microsoft, que son parte del propio Windows).

**Desactivar no borra nada.** Se escribe la misma marca que escribe el
Administrador de tareas en `...\\Explorer\\StartupApproved\\...`: la entrada sigue
donde estaba, Windows deja de ejecutarla y se puede volver a activar. Las
entradas `RunOnce` se muestran sin botón porque Windows no las aprueba ni las
desaprueba: se ejecutan una vez y desaparecen solas.

Lo que este módulo **no** puede ver: las aplicaciones de la Microsoft Store, que
Windows arranca por otra vía sin una clave pública estable, y los servicios
(eso está en la página de herramientas, en «Servicios»).
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

KIND_REGISTRO = "registro"
KIND_CARPETA = "carpeta"
KIND_TAREA = "tarea"

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
def command_target(command: str) -> str:
    """Ruta del ejecutable que hay dentro de una línea de comando.

    Puede venir entre comillas (`"C:\\Program Files\\...\\app.exe" --flag`) o
    suelta, y no siempre es un ejecutable: `rundll32`, `cmd /c` o un `shell:`
    devuelven algo que no es un archivo, y eso también es información.
    """
    text = (command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return (text[1:end] if end > 0 else text.strip('"')).strip()
    lowered = text.lower()
    limit: int | None = None
    for extension in _EXECUTABLE_EXTENSIONS:
        index = lowered.find(extension)
        if index != -1:
            candidate = index + len(extension)
            if limit is None or candidate < limit:
                limit = candidate
    if limit is not None:
        return text[:limit].strip()
    return text.split(" ", 1)[0].strip()


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

    # --- Datos derivados (se calculan a partir del comando) --------------
    @property
    def target_path(self) -> str:
        return _target_state(self.command)[0]

    @property
    def target_exists(self) -> bool | None:
        return _target_state(self.command)[1]

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
        if self.kind == KIND_TAREA:
            return COST_MEDIO, "Es una tarea programada: Windows la lanza aparte al entrar."
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
            entries.append(
                StartupEntry(
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
            )
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
            entries.append(
                StartupEntry(
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
            )
    return entries


TASKS_SCRIPT = (
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
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return task_entries_from_output(result.stdout or "")


SOURCES_ORDER = (
    SOURCE_USUARIO,
    SOURCE_SISTEMA,
    SOURCE_CARPETA_USUARIO,
    SOURCE_CARPETA_COMUN,
    SOURCE_TAREA,
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
    """Recupera la raíz del registro guardada en los datos de la entrada."""
    if winreg is None:
        return None
    if value == HIVE_USER or value == 1:
        return winreg.HKEY_CURRENT_USER
    if value == HIVE_MACHINE or value == 2:
        return winreg.HKEY_LOCAL_MACHINE
    return None


def set_enabled(entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
    """Activa o desactiva una entrada. Devuelve (éxito, mensaje real de Windows)."""
    if winreg is None and entry.kind != KIND_TAREA:
        return False, "El registro solo existe en Windows."
    if entry.kind == KIND_TAREA:
        return _set_task_enabled(entry, enabled)
    if not entry.can_toggle:
        return False, f"«{entry.name}» no se puede cambiar desde aquí. {entry.note}".strip()
    hive = _hive_from(entry.data.get("hive"))
    approved = str(entry.data.get("approved_path") or "")
    value_name = str(entry.data.get("valor") or entry.name)
    if hive is None or not approved:
        return False, "Faltan datos para cambiar esta entrada."
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
    task = str(entry.data.get("tarea") or entry.name)
    arguments = ["/Change", "/TN", task, "/ENABLE" if enabled else "/DISABLE"]
    try:
        result = subprocess.run(
            ["schtasks", *arguments],
            capture_output=True,
            text=True,
            encoding="cp1252",
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


def by_source(entries: Sequence[StartupEntry]) -> dict[str, list[StartupEntry]]:
    """Agrupa las entradas por su origen, en el orden natural de la página."""
    grouped: dict[str, list[StartupEntry]] = {origen: [] for origen in SOURCES_ORDER}
    for entry in entries:
        grouped.setdefault(entry.source, []).append(entry)
    return {origen: items for origen, items in grouped.items() if items}
