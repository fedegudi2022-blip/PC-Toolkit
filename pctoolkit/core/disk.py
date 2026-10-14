"""Analizador de espacio en disco.

Recorre un directorio o unidad completa, agrega el tamaño de cada elemento y
devuelve el desglose por carpeta, por categoría de archivo y los archivos más
grandes. El escaneo corre en un hilo aparte para no bloquear la interfaz.
"""

from __future__ import annotations

import heapq
import os
import string
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

import psutil
from PySide6.QtCore import QThread, Signal

from . import logs

CATEGORIES: dict[str, tuple[str, ...]] = {
    "Vídeo": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts"),
    "Imágenes": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".raw", ".svg", ".psd"),
    "Audio": (".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".aiff"),
    "Documentos": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt", ".csv", ".md"),
    "Comprimidos": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".cab"),
    "Programas": (".exe", ".msi", ".dll", ".appx", ".msix", ".bat", ".cmd", ".sys", ".bin"),
    "Código": (".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".html", ".css", ".json", ".xml", ".sql", ".sh", ".ipynb"),
}
OTHER_CATEGORY = "Otros"

SKIP_NAMES = {"$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws"}


@dataclass
class DriveInfo:
    mount: str
    device: str
    fstype: str
    total: int = 0
    used: int = 0
    free: int = 0
    percent: float = 0.0
    label: str = ""

    @property
    def display_name(self) -> str:
        base = f"{self.mount.rstrip(chr(92))}"
        if self.label:
            return f"{base}  ·  {self.label}"
        return base


@dataclass
class Node:
    path: str
    name: str
    size: int
    is_dir: bool
    file_count: int = 0
    children: list["Node"] = field(default_factory=list)


@dataclass
class ScanResult:
    root: str
    total_size: int = 0
    total_files: int = 0
    dirs_scanned: int = 0
    errors: int = 0
    duration: float = 0.0
    children: list[Node] = field(default_factory=list)
    categories: list[tuple[str, int]] = field(default_factory=list)
    largest_files: list[Node] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total_file_count(self) -> int:
        return self.total_files


def list_drives() -> list[DriveInfo]:
    """Unidades disponibles con su uso de espacio."""
    drives: list[DriveInfo] = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except (OSError, AttributeError):
        partitions = []
    for part in partitions:
        mount = part.mountpoint
        if not mount:
            continue
        info = DriveInfo(mount=mount, device=part.device, fstype=part.fstype or "—")
        try:
            usage = psutil.disk_usage(mount)
            info.total = usage.total
            info.used = usage.used
            info.free = usage.free
            info.percent = usage.percent
        except (OSError, PermissionError):
            continue
        info.label = _volume_label(mount)
        drives.append(info)
    if not drives:
        fallback = os.environ.get("SystemDrive", "C:") + "\\"
        info = DriveInfo(mount=fallback, device=fallback, fstype="NTFS")
        try:
            usage = psutil.disk_usage(fallback)
            info.total, info.used, info.free, info.percent = (
                usage.total,
                usage.used,
                usage.free,
                usage.percent,
            )
        except OSError as exc:
            # Sin la medida de la unidad del sistema el informe sale sin datos de disco:
            # conviene poder saber después por qué.
            logs.warning(f"No se pudo medir la unidad {fallback}", exc)
        info.label = _volume_label(fallback)
        drives.append(info)
    drives.sort(key=lambda d: d.mount)
    return drives


def _volume_label(mount: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        drive = mount.rstrip("\\/") + "\\"
        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(drive), buf, 260, None, None, None, None, 0
        )
        if ok:
            return buf.value
    except (OSError, AttributeError):
        pass
    return ""


def category_for(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if not ext:
        return OTHER_CATEGORY
    for label, extensions in CATEGORIES.items():
        if ext in extensions:
            return label
    return OTHER_CATEGORY


def quick_candidates(root: str | None = None) -> list[str]:
    r"""Carpetas recomendadas para analizar rápidamente.

    Son las carpetas personales **reales** del usuario: se preguntan a Windows
    (`config.user_folder`) porque con OneDrive o una política de empresa están
    redirigidas y `%USERPROFILE%\Downloads` no es la carpeta que el usuario ve.
    """
    from .config import user_folder

    local = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    options = [
        str(user_folder("Downloads")),
        str(user_folder("Desktop")),
        str(user_folder("Documents")),
        str(user_folder("Videos")),
        str(user_folder("Pictures")),
        os.path.join(local, "Temp"),
    ]
    return [path for path in options if os.path.isdir(path)]


class DiskScanner(QThread):
    """Escanea un directorio en segundo plano."""

    progress = Signal(str, int)
    finished_scan = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, parent=None, min_large_file: int = 100 * 1024 * 1024) -> None:
        super().__init__(parent)
        self.path = path
        self.min_large_file = min_large_file
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D102 (QThread)
        try:
            result = scan_directory(
                self.path,
                progress=lambda path, count: self.progress.emit(path, count),
                should_cancel=self._cancel.is_set,
                min_large_file=self.min_large_file,
            )
        except Exception as exc:  # pragma: no cover - defensivo
            self.failed.emit(str(exc))
            return
        self.finished_scan.emit(result)


def scan_directory(
    root: str,
    progress: Callable[[str, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    min_large_file: int = 100 * 1024 * 1024,
    top_children: int = 60,
    top_files: int = 20,
) -> ScanResult:
    """Calcula el tamaño de cada elemento dentro de `root`."""
    result = ScanResult(root=root)
    started = time.time()
    counter = {"files": 0, "dirs": 0, "errors": 0}
    categories: dict[str, int] = {}
    largest: list[tuple[int, int, Node]] = []
    largest_seq = [0]
    last_emit = [0.0]

    def report(path: str) -> None:
        now = time.time()
        if progress and (now - last_emit[0] > 0.08 or counter["files"] % 200 == 0):
            last_emit[0] = now
            progress(path, counter["files"])

    def walk(path: str, depth: int) -> tuple[int, int]:
        """Devuelve (tamaño total, número de archivos) de `path`."""
        if should_cancel and should_cancel():
            return 0, 0
        size_total = 0
        file_total = 0
        try:
            with os.scandir(path) as iterator:
                entries = list(iterator)
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            counter["errors"] += 1
            return 0, 0

        counter["dirs"] += 1
        for entry in entries:
            if should_cancel and should_cancel():
                return size_total, file_total
            name = entry.name
            if name.lower() in SKIP_NAMES:
                continue
            try:
                if entry.is_symlink() or _is_junction(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    sub_size, sub_files = walk(entry.path, depth + 1)
                    size_total += sub_size
                    file_total += sub_files
                else:
                    stat = entry.stat(follow_symlinks=False)
                    size = stat.st_size
                    size_total += size
                    file_total += 1
                    counter["files"] += 1
                    label = category_for(name)
                    categories[label] = categories.get(label, 0) + size
                    if size >= min_large_file:
                        node = Node(path=entry.path, name=name, size=size, is_dir=False)
                        largest_seq[0] += 1
                        item = (size, largest_seq[0], node)
                        if len(largest) < top_files:
                            heapq.heappush(largest, item)
                        elif size > largest[0][0]:
                            heapq.heapreplace(largest, item)
            except (PermissionError, FileNotFoundError, OSError):
                counter["errors"] += 1
                continue
            report(entry.path)
        return size_total, file_total

    result.children = _children_with_sizes(
        root, walk, report, should_cancel, top_children, counter, categories
    )
    result.total_size = sum(child.size for child in result.children)
    result.total_files = counter["files"]
    result.dirs_scanned = counter["dirs"]
    result.errors = counter["errors"]
    result.categories = sorted(categories.items(), key=lambda item: item[1], reverse=True)
    result.largest_files = [node for _, _, node in sorted(largest, key=lambda item: item[0], reverse=True)]
    result.duration = time.time() - started
    result.cancelled = bool(should_cancel and should_cancel())
    return result


def _children_with_sizes(
    root: str,
    walk: Callable[[str, int], tuple[int, int]],
    report: Callable[[str], None],
    should_cancel: Callable[[], bool] | None,
    limit: int,
    counter: dict[str, int],
    categories: dict[str, int],
) -> list[Node]:
    nodes: list[Node] = []
    try:
        with os.scandir(root) as iterator:
            entries = list(iterator)
    except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
        return nodes

    for entry in entries:
        if should_cancel and should_cancel():
            break
        name = entry.name
        if name.lower() in SKIP_NAMES:
            continue
        try:
            if entry.is_symlink() or _is_junction(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                size, files = walk(entry.path, 1)
                nodes.append(
                    Node(path=entry.path, name=name, size=size, is_dir=True, file_count=files)
                )
                report(entry.path)
            else:
                size = entry.stat(follow_symlinks=False).st_size
                # Los archivos que cuelgan directamente de la raíz también cuentan.
                counter["files"] += 1
                label = category_for(name)
                categories[label] = categories.get(label, 0) + size
                nodes.append(Node(path=entry.path, name=name, size=size, is_dir=False, file_count=1))
        except (PermissionError, FileNotFoundError, OSError):
            continue

    nodes.sort(key=lambda node: node.size, reverse=True)
    if len(nodes) > limit:
        rest = nodes[limit:]
        nodes = nodes[:limit]
        nodes.append(
            Node(
                path=root,
                name=f"Otros {len(rest)} elementos",
                size=sum(node.size for node in rest),
                is_dir=False,
                file_count=len(rest),
            )
        )
    return nodes


def _is_junction(entry: os.DirEntry) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is None:
        return False
    try:
        return bool(isjunction(entry.path))
    except OSError:
        return False


def available_drives_letters() -> Iterable[str]:
    """Letras de unidad presentes en el sistema (para sugerencias)."""
    for letter in string.ascii_uppercase:
        candidate = f"{letter}:\\"
        if os.path.exists(candidate):
            yield candidate
