"""Accesos rápidos a herramientas de Windows y utilidades de mantenimiento."""

from __future__ import annotations

import ctypes
import os
import shutil
import time
from dataclasses import dataclass, field
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


@dataclass
class TempReport:
    folders: list[str] = field(default_factory=list)
    total_size: int = 0
    file_count: int = 0
    skipped: int = 0
    freed: int = 0
    deleted: int = 0


def default_temp_folder() -> str:
    return os.environ.get("TEMP") or os.environ.get("TMP") or ""


def scan_temp_folder(folder: str | None = None, min_age_hours: float = 1.0) -> TempReport:
    """Suma el tamaño de los archivos temporales que se pueden borrar."""
    folder = folder or default_temp_folder()
    report = TempReport()
    if not folder or not os.path.isdir(folder):
        return report
    report.folders = [folder]
    # Con min_age_hours <= 0 no se filtra por antigüedad.
    cutoff = time.time() - min_age_hours * 3600 if min_age_hours > 0 else None
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
                    stack.append(entry.path)
                    continue
                stat = entry.stat(follow_symlinks=False)
                if cutoff is not None and stat.st_mtime > cutoff:
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
    folder = folder or default_temp_folder()
    report = scan_temp_folder(folder, min_age_hours=min_age_hours)
    if not folder or not os.path.isdir(folder):
        return report
    cutoff = time.time() - min_age_hours * 3600 if min_age_hours > 0 else None
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
                    stack.append(entry.path)
                    continue
                stat = entry.stat(follow_symlinks=False)
                if cutoff is not None and stat.st_mtime > cutoff:
                    continue
                os.remove(entry.path)
                report.freed += stat.st_size
                report.deleted += 1
                if progress:
                    progress(report.freed, report.total_size)
            except (PermissionError, FileNotFoundError, OSError):
                report.skipped += 1
    # Elimina subcarpetas vacías creadas por temporales
    for root, _dirs, files in os.walk(folder, topdown=False):
        if root == folder or files:
            continue
        try:
            shutil.rmtree(root, ignore_errors=False)
        except OSError:
            pass
    if progress:
        progress(report.freed, report.total_size)
    return report
