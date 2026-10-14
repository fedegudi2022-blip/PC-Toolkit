"""Accesos rápidos a herramientas de Windows y utilidades de mantenimiento."""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

IS_WINDOWS = os.name == "nt"

CATEGORY_SISTEMA = "Sistema"
CATEGORY_MANTENIMIENTO = "Mantenimiento"
CATEGORY_ALMACENAMIENTO = "Almacenamiento"
CATEGORY_RED = "Red"
CATEGORY_AVANZADO = "Avanzado"

CATEGORY_ORDER = [
    CATEGORY_SISTEMA,
    CATEGORY_MANTENIMIENTO,
    CATEGORY_ALMACENAMIENTO,
    CATEGORY_RED,
    CATEGORY_AVANZADO,
]


@dataclass(frozen=True)
class ToolShortcut:
    """Acceso directo a una herramienta del sistema."""

    id: str
    name: str
    description: str
    target: str
    category: str
    icon: str = "tool"
    args: str = ""
    admin: bool = False

    @property
    def available(self) -> bool:
        return IS_WINDOWS


TOOLS: list[ToolShortcut] = [
    # --- Sistema ---------------------------------------------------------
    ToolShortcut("taskmgr", "Administrador de tareas", "Procesos, rendimiento e inicio", "taskmgr.exe", CATEGORY_SISTEMA, "gauge"),
    ToolShortcut("msinfo32", "Información del sistema", "Resumen completo del hardware", "msinfo32.exe", CATEGORY_SISTEMA, "info"),
    ToolShortcut("sysdm", "Propiedades del sistema", "Nombre del equipo y variables", "sysdm.cpl", CATEGORY_SISTEMA, "info"),
    ToolShortcut("devmgmt", "Administrador de dispositivos", "Controladores y hardware", "devmgmt.msc", CATEGORY_SISTEMA, "chip"),
    ToolShortcut("services", "Servicios", "Servicios de Windows y su estado", "services.msc", CATEGORY_SISTEMA, "gear", admin=True),
    ToolShortcut("eventvwr", "Visor de eventos", "Registro de errores del sistema", "eventvwr.msc", CATEGORY_SISTEMA, "list"),
    ToolShortcut("settings", "Configuración de Windows", "Panel de configuración moderno", "ms-settings:", CATEGORY_SISTEMA, "gear"),
    ToolShortcut("control", "Panel de control", "Herramientas clásicas", "control.exe", CATEGORY_SISTEMA, "gear"),
    # --- Mantenimiento ---------------------------------------------------
    ToolShortcut("cleanmgr", "Liberador de espacio en disco", "Elimina archivos innecesarios", "cleanmgr.exe", CATEGORY_MANTENIMIENTO, "broom"),
    ToolShortcut("dfrgui", "Optimizar unidades", "Desfragmentar y recortar discos", "dfrgui.exe", CATEGORY_MANTENIMIENTO, "disk", admin=True),
    ToolShortcut("windowsupdate", "Buscar actualizaciones", "Windows Update", "ms-settings:windowsupdate", CATEGORY_MANTENIMIENTO, "download"),
    ToolShortcut("defender", "Seguridad de Windows", "Antivirus y protección", "windowsdefender:", CATEGORY_MANTENIMIENTO, "shield"),
    ToolShortcut("restore", "Restaurar sistema", "Puntos de restauración", "rstrui.exe", CATEGORY_MANTENIMIENTO, "restore", admin=True),
    ToolShortcut("taskschd", "Programador de tareas", "Tareas automáticas de Windows", "taskschd.msc", CATEGORY_MANTENIMIENTO, "calendar", admin=True),
    ToolShortcut("perfmon", "Monitor de rendimiento", "Gráficas de rendimiento", "perfmon.msc", CATEGORY_MANTENIMIENTO, "gauge"),
    # --- Almacenamiento --------------------------------------------------
    ToolShortcut("diskmgmt", "Administrador de discos", "Particiones y volúmenes", "diskmgmt.msc", CATEGORY_ALMACENAMIENTO, "disk", admin=True),
    ToolShortcut("temp", "Carpeta temporal", "Archivos temporales del usuario", "%TEMP%", CATEGORY_ALMACENAMIENTO, "folder"),
    ToolShortcut("recycle", "Papelera de reciclaje", "Elementos eliminados", "shell:RecycleBinFolder", CATEGORY_ALMACENAMIENTO, "trash"),
    ToolShortcut("downloads", "Descargas", "Carpeta de descargas", "shell:Downloads", CATEGORY_ALMACENAMIENTO, "folder"),
    ToolShortcut("storage", "Sensores de almacenamiento", "Uso de disco y recomendaciones", "ms-settings:storagesense", CATEGORY_ALMACENAMIENTO, "disk"),
    # --- Red -------------------------------------------------------------
    ToolShortcut("ncpa", "Conexiones de red", "Adaptadores de red", "ncpa.cpl", CATEGORY_RED, "network"),
    ToolShortcut("firewall", "Firewall de Windows", "Reglas de entrada y salida", "firewall.cpl", CATEGORY_RED, "shield"),
    ToolShortcut("netusage", "Uso de datos de red", "Consumo por aplicación", "ms-settings:datausage", CATEGORY_RED, "network"),
    ToolShortcut("resmon", "Monitor de recursos", "CPU, disco, red y memoria en vivo", "resmon.exe", CATEGORY_RED, "gauge"),
    # --- Avanzado --------------------------------------------------------
    ToolShortcut("regedit", "Editor del Registro", "Configuración avanzada de Windows", "regedit.exe", CATEGORY_AVANZADO, "warn", admin=True),
    ToolShortcut("cmd", "Símbolo del sistema", "Consola de comandos", "cmd.exe", CATEGORY_AVANZADO, "terminal"),
    ToolShortcut("powershell", "PowerShell", "Consola avanzada de administración", "powershell.exe", CATEGORY_AVANZADO, "terminal"),
    ToolShortcut("msconfig", "Configuración del sistema", "Arranque y servicios de inicio", "msconfig.exe", CATEGORY_AVANZADO, "gear", admin=True),
]


def tools_by_category() -> dict[str, list[ToolShortcut]]:
    grouped: dict[str, list[ToolShortcut]] = {category: [] for category in CATEGORY_ORDER}
    for tool in TOOLS:
        grouped.setdefault(tool.category, []).append(tool)
    return grouped


def find_tool(tool_id: str) -> ToolShortcut | None:
    for tool in TOOLS:
        if tool.id == tool_id:
            return tool
    return None


def windows_powershell_modules() -> list[Path]:
    """Carpetas de módulos del Windows PowerShell 5.1 (las que usa al abrirse a mano)."""
    sistema = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    carpetas = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsPowerShell" / "Modules",
        sistema / "system32" / "WindowsPowerShell" / "v1.0" / "Modules",
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "WindowsPowerShell" / "Modules",
    ]
    return [carpeta for carpeta in carpetas if carpeta.is_dir()]


def powershell_environment() -> dict[str, str]:
    """Entorno para lanzar Windows PowerShell 5.1 desde Python.

    Si el proceso padre es PowerShell 7 (el paso de un flujo de GitHub ejecuta `pwsh`),
    los hijos heredan su `PSModulePath`, y Windows PowerShell busca sus módulos en el
    sitio equivocado: `New-SelfSignedCertificate` falla con «No existe ninguna unidad
    con el nombre 'Cert'» y `ConvertTo-SecureString` con «the module could not be
    loaded». Los dos engañan durante un buen rato.

    Quitar la variable no basta en un servidor: sin ella, Windows PowerShell la
    reconstruye con las del sistema y del usuario, y ahí puede estar metida la de
    PowerShell 7. Se define, pues, con las carpetas del Windows PowerShell de siempre
    (es el arreglo que documenta Microsoft para los procesos intermedios como Python).
    """
    entorno = dict(os.environ)
    carpetas = windows_powershell_modules()
    if carpetas:
        entorno["PSModulePath"] = os.pathsep.join(str(carpeta) for carpeta in carpetas)
    else:  # sin las carpetas del sistema no hay nada seguro que poner
        entorno.pop("PSModulePath", None)
    return entorno


def launch(tool: ToolShortcut) -> tuple[bool, str]:
    """Abre una herramienta del sistema. Devuelve (éxito, mensaje)."""
    if not IS_WINDOWS:
        return False, "Los accesos rápidos están disponibles únicamente en Windows."
    target = tool.target
    if "%" in target:
        target = os.path.expandvars(target)
    try:
        if target.startswith("shell:"):
            os.startfile(target)  # type: ignore[attr-defined]
            return True, f"Abriendo {tool.name.lower()}…"
        verb = "runas" if tool.admin else "open"
        result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, verb, target, tool.args or None, None, 1
        )
        if result <= 32:
            if result == 5:  # acceso denegado (UAC cancelado)
                return False, "Permiso de administrador cancelado."
            return False, f"No se pudo abrir {tool.name} (código {result})."
        return True, f"Abriendo {tool.name.lower()}…"
    except (OSError, AttributeError) as exc:
        return False, f"No se pudo abrir {tool.name}: {exc}"


# --- Limpieza de archivos temporales -------------------------------------

TEMP_FOLDERS = ("TEMP", "TMP")

# Por debajo de esta antigüedad no se borra nada, aunque quien llame pida cero:
# un temporal recién escrito suele pertenecer a algo que se está instalando o a un
# programa abierto, y llevárselo por delante rompe la instalación o la sesión.
MIN_TEMP_AGE_HOURS = 1.0

# FILE_ATTRIBUTE_REPARSE_POINT: marca las carpetas que en realidad son un enlace
# (junction) o un acceso simbólico. `os.path.isjunction` solo existe desde Python
# 3.12, así que se mira el atributo, que además viene en el propio `stat`.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass
class TempReport:
    folders: list[str] = field(default_factory=list)
    total_size: int = 0
    file_count: int = 0
    skipped: int = 0
    freed: int = 0
    deleted: int = 0
    # Motivo por el que no se tocó nada (carpeta rechazada); vacío si se pudo limpiar.
    rejected: str = ""


def default_temp_folder() -> str:
    return os.environ.get("TEMP") or os.environ.get("TMP") or ""


def min_temp_age_hours(min_age_hours: float) -> float:
    """Antigüedad mínima efectiva, nunca por debajo del suelo de seguridad."""
    try:
        pedida = float(min_age_hours)
    except (TypeError, ValueError):
        pedida = MIN_TEMP_AGE_HOURS
    return max(MIN_TEMP_AGE_HOURS, pedida)


def _normalized(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _protected_folders() -> set[str]:
    """Carpetas que nunca se limpian: sistema, perfil y raíces de unidad.

    Si `%TEMP%` apuntase a cualquiera de ellas por un error de configuración, la
    limpieza se llevaría por delante archivos que no son temporales, así que se
    rechazan de plano en vez de confiar en la variable de entorno.
    """
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    drive = os.environ.get("SystemDrive") or os.path.splitdrive(system_root)[0] or "C:"
    perfil = os.environ.get("USERPROFILE") or str(Path.home())
    candidatos = [
        system_root,
        os.path.join(system_root, "System32"),
        os.path.join(drive + os.sep),
        os.path.join(drive + os.sep, "Users"),
        perfil,
        os.path.join(perfil, "Desktop"),
        os.path.join(perfil, "Documents"),
        os.path.join(perfil, "Downloads"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramData"),
        os.environ.get("PUBLIC"),
    ]
    return {_normalized(item) for item in candidatos if item}


def is_safe_temp_folder(folder: str | os.PathLike[str] | None) -> bool:
    """¿Se puede borrar dentro de esa carpeta sin arriesgar archivos ajenos?"""
    if not folder:
        return False
    ruta = _normalized(folder)
    # Raíz de una unidad (C:\, D:\…): nunca.
    if os.path.splitdrive(ruta)[1] in ("", os.sep):
        return False
    if ruta in _protected_folders():
        return False
    # La carpeta tiene que ser una subcarpeta de algo, no el propio perfil ni C:\.
    padre = os.path.dirname(ruta)
    return bool(padre) and _normalized(padre) != ruta


def _is_reparse_dir(entry: os.DirEntry) -> bool:
    """¿La entrada es un directorio que en realidad apunta a otro sitio?

    Un junction dentro de `%TEMP%` llevaría el recorrido a un destino ajeno, así que
    no se sigue: ni se cuenta ni se borra lo que haya al otro lado.
    """
    try:
        if not entry.is_dir(follow_symlinks=False):
            return False
        stat = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    return bool(getattr(stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _prepare(folder: str | None, min_age_hours: float) -> tuple[str, float, TempReport]:
    """Valida la carpeta y devuelve (carpeta, antigüedad efectiva, informe)."""
    folder = folder or default_temp_folder()
    report = TempReport()
    edad = min_temp_age_hours(min_age_hours)
    if not folder or not os.path.isdir(folder):
        report.rejected = "La carpeta temporal no existe."
        return "", edad, report
    if not is_safe_temp_folder(folder):
        report.rejected = (
            f"Por seguridad no se limpia {folder}: es una raíz de unidad, la carpeta "
            "del sistema o la del perfil. Revisa la variable TEMP."
        )
        return "", edad, report
    report.folders = [folder]
    return folder, edad, report


def scan_temp_folder(folder: str | None = None, min_age_hours: float = 1.0) -> TempReport:
    """Suma el tamaño de los archivos temporales que se pueden borrar."""
    folder, edad, report = _prepare(folder, min_age_hours)
    if not folder:
        return report
    cutoff = time.time() - edad * 3600
    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except (PermissionError, FileNotFoundError, OSError):
            report.skipped += 1
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not _is_reparse_dir(entry):
                        stack.append(entry.path)
                    continue
                stat = entry.stat(follow_symlinks=False)
                if stat.st_mtime > cutoff:
                    report.skipped += 1
                    continue
                report.total_size += stat.st_size
                report.file_count += 1
            except (PermissionError, FileNotFoundError, OSError):
                report.skipped += 1
    return report


def clean_temp_folder(
    folder: str | None = None,
    min_age_hours: float = 1.0,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TempReport:
    """Borra archivos temporales antiguos; informa lo realmente liberado."""
    folder, edad, report = _prepare(folder, min_age_hours)
    if not folder:
        return report
    report = scan_temp_folder(folder, min_age_hours=edad)
    cutoff = time.time() - edad * 3600
    stack = [folder]
    while stack:
        if should_cancel and should_cancel():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except (PermissionError, FileNotFoundError, OSError):
            report.skipped += 1
            continue
        for entry in entries:
            if should_cancel and should_cancel():
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not _is_reparse_dir(entry):
                        stack.append(entry.path)
                    continue
                stat = entry.stat(follow_symlinks=False)
                if stat.st_mtime > cutoff:
                    continue
                os.remove(entry.path)
                report.freed += stat.st_size
                report.deleted += 1
                if progress:
                    progress(report.freed, report.total_size)
            except (PermissionError, FileNotFoundError, OSError):
                report.skipped += 1
    # Elimina las subcarpetas que hayan quedado vacías de verdad. Se usa `os.rmdir`
    # y no `shutil.rmtree`: `rmdir` solo borra carpetas vacías, así que una
    # subcarpeta que todavía tenga contenido (aunque no tenga archivos sueltos y
    # solo le queden subcarpetas) se queda como está en lugar de desaparecer con
    # todo lo que hay dentro.
    for root, _dirs, _files in os.walk(folder, topdown=False):
        if root == folder:
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass  # no está vacía o está en uso: se deja tal cual
    if progress:
        progress(report.freed, report.total_size)
    return report
