"""Página del analizador de espacio en disco."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.config import COLORS, format_bytes
from ..core.disk import DiskScanner, DriveInfo, ScanResult, list_drives, quick_candidates
from . import icons
from .widgets import (
    ArcProgress,
    BarRow,
    Card,
    IconBadge,
    Muted,
    PageHeader,
    SectionTitle,
    hline,
)


class DriveTile(Card):
    """Resumen de una unidad con su barra de ocupación."""

    def __init__(self, drive: DriveInfo, on_analyze, parent: QWidget | None = None) -> None:
        # El grupo se llama como la unidad que resume (la etiqueta que se ve).
        super().__init__(parent, soft=True, name=f"Unidad {drive.mount.rstrip(chr(92))}")
        self.drive = drive
        self.setCursor(Qt.PointingHandCursor)
        self._on_analyze = on_analyze
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(IconBadge("disk", 26, 14, COLORS["text_dim"]))
        name = QLabel(f"Unidad {drive.mount.rstrip(chr(92))}")
        name.setStyleSheet("font-size: 12.5px; font-weight: 600;")
        top.addWidget(name)
        top.addStretch(1)
        percent = QLabel(f"{drive.percent:.0f}%")
        percent.setStyleSheet("font-size: 12.5px; font-weight: 600;")
        top.addWidget(percent)
        layout.addLayout(top)

        label = QLabel(drive.label or drive.fstype)
        label.setObjectName("Dim")
        layout.addWidget(label)

        bar = ArcProgress(self._color_for(drive.percent))
        bar.set_ratio(drive.percent / 100.0)
        bar.set_label(f"Ocupaci\u00f3n de {drive.mount}")
        layout.addWidget(bar)

        usage = QLabel(f"{format_bytes(drive.used)} usados · {format_bytes(drive.free)} libres")
        usage.setObjectName("Muted")
        layout.addWidget(usage)

    def _color_for(self, percent: float) -> str:
        if percent >= 90:
            return COLORS["bad"]
        if percent >= 75:
            return COLORS["warn"]
        return COLORS["accent"]

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() == Qt.LeftButton:
            self._on_analyze(self.drive.mount)


class DiskAnalyzerPage(QWidget):
    """Analiza qué ocupa el espacio y permite navegar por el árbol."""

    status = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scanner: DiskScanner | None = None
        self._current_path = os.path.expanduser("~")
        self._result: ScanResult | None = None
        self._build()
        self.refresh_drives()

    # --- Construcción ----------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 22, 28, 26)
        layout.setSpacing(18)

        header = PageHeader(
            "Analizador de espacio",
            "Descubre qué carpetas y archivos consumen tu disco.",
        )
        self.up_button = QPushButton("Subir un nivel")
        self.up_button.setIcon(icons.icon("up", 15))
        self.up_button.setObjectName("Ghost")
        self.up_button.clicked.connect(self._go_up)
        self.up_button.setEnabled(False)
        header.add_action(self.up_button)

        self.stop_button = QPushButton("Detener")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_scan)
        header.add_action(self.stop_button)

        self.analyze_button = QPushButton("Analizar")
        self.analyze_button.setIcon(icons.icon("analyze", 15, COLORS["on_accent"]))
        self.analyze_button.setObjectName("Primary")
        self.analyze_button.clicked.connect(lambda: self.start_scan(self._current_path))
        header.add_action(self.analyze_button)
        layout.addWidget(header)

        layout.addWidget(self._drive_strip())
        layout.addWidget(self._controls())

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.current_label = QLabel()
        self.current_label.setObjectName("Muted")
        layout.addWidget(self.current_label)

        results = QHBoxLayout()
        results.setSpacing(16)

        self.left_card = Card(name="Elementos más pesados")
        left_layout = QVBoxLayout(self.left_card)
        left_layout.setContentsMargins(18, 16, 18, 18)
        left_layout.setSpacing(10)
        left_layout.addWidget(SectionTitle("Elementos más pesados"))
        left_layout.addWidget(Muted("Haz clic en una carpeta para analizarla por dentro."))
        self.children_box = QVBoxLayout()
        self.children_box.setSpacing(6)
        left_layout.addLayout(self.children_box)
        left_layout.addStretch(1)
        results.addWidget(self.left_card, 3)

        self.right_card = Card(name="Uso por categoría")
        right_layout = QVBoxLayout(self.right_card)
        right_layout.setContentsMargins(18, 16, 18, 18)
        right_layout.setSpacing(10)
        right_layout.addWidget(SectionTitle("Uso por categoría"))
        self.categories_box = QVBoxLayout()
        self.categories_box.setSpacing(8)
        right_layout.addLayout(self.categories_box)
        right_layout.addWidget(hline())
        right_layout.addWidget(SectionTitle("Archivos más grandes"))
        self.files_box = QVBoxLayout()
        self.files_box.setSpacing(8)
        right_layout.addLayout(self.files_box)
        right_layout.addStretch(1)
        right_layout.addWidget(Muted("Se listan los archivos de más de 100 MB encontrados."))
        results.addWidget(self.right_card, 2)
        layout.addLayout(results)
        layout.addStretch(1)

        self._set_current_label(self._current_path)

    def _drive_strip(self) -> QWidget:
        self.drive_container = QWidget()
        self.drive_layout = QHBoxLayout(self.drive_container)
        self.drive_layout.setContentsMargins(0, 0, 0, 0)
        self.drive_layout.setSpacing(12)
        return self.drive_container

    def _controls(self) -> Card:
        card = Card(name="Ruta que analizar")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.quick_combo = QComboBox()
        self.quick_combo.addItem("Carpetas sugeridas…", "")
        for path in quick_candidates():
            self.quick_combo.addItem(path, path)
        self.quick_combo.setMinimumWidth(230)
        self.quick_combo.currentIndexChanged.connect(self._quick_selected)
        layout.addWidget(self.quick_combo)

        self.path_edit = QLineEdit(self._current_path)
        self.path_edit.setPlaceholderText("Ruta a analizar, por ejemplo C:\\Users\\TuUsuario")
        self.path_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.path_edit.returnPressed.connect(lambda: self.start_scan(self.path_edit.text().strip()))
        layout.addWidget(self.path_edit, 1)

        browse = QPushButton("Elegir carpeta…")
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._browse)
        layout.addWidget(browse)
        return card

    # --- Estado ---------------------------------------------------------
    def refresh_drives(self) -> None:
        while self.drive_layout.count():
            item = self.drive_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.drives = list_drives()
        for drive in self.drives[:4]:
            tile = DriveTile(drive, lambda path: self.start_scan(path))
            tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.drive_layout.addWidget(tile)

    def _quick_selected(self, index: int) -> None:
        path = self.quick_combo.itemData(index)
        if path:
            self.path_edit.setText(path)
            self.start_scan(path)

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Elige la carpeta a analizar", self._current_path
        )
        if selected:
            self.path_edit.setText(selected)
            self.start_scan(selected)

    def _go_up(self) -> None:
        parent = os.path.dirname(self._current_path.rstrip("\\/")) or self._current_path
        if parent and os.path.isdir(parent) and parent != self._current_path:
            self.start_scan(parent)

    def _set_current_label(self, path: str) -> None:
        self.current_label.setText(f"Ruta actual:  {path}")

    # --- Escaneo ---------------------------------------------------------
    def start_scan(self, path: str) -> None:
        path = (path or "").strip().strip('"')
        if not path:
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "Ruta no válida", f"La ruta no existe:\n{path}")
            return
        if self._scanner and self._scanner.isRunning():
            self.status.emit("Ya hay un análisis en curso.")
            return
        if not os.path.isdir(path):
            path = os.path.dirname(path)

        self._current_path = path
        self.path_edit.setText(path)
        self._set_current_label(path)
        self.progress.setVisible(True)
        self.analyze_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.emit(f"Analizando {path}…")

        self._scanner = DiskScanner(path, self)
        self._scanner.progress.connect(self._on_progress)
        self._scanner.finished_scan.connect(self._on_finished)
        self._scanner.failed.connect(self._on_failed)
        self._scanner.start()

    def _on_progress(self, path: str, count: int) -> None:
        short = path if len(path) < 78 else "…" + path[-77:]
        self.status.emit(f"{count:,} archivos analizados · {short}")

    def _on_failed(self, message: str) -> None:
        self._finish_ui()
        QMessageBox.critical(self, "Error al analizar", message)

    def _stop_scan(self) -> None:
        if self._scanner and self._scanner.isRunning():
            self._scanner.cancel()
            self.status.emit("Cancelando el análisis…")

    def _finish_ui(self) -> None:
        self.progress.setVisible(False)
        self.analyze_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.up_button.setEnabled(bool(os.path.dirname(self._current_path.rstrip("\\/"))) and
                                   os.path.dirname(self._current_path.rstrip("\\/")) != self._current_path)

    def _on_finished(self, result: ScanResult) -> None:
        self._result = result
        self._finish_ui()
        self._render(result)
        suffix = " (incompleto)" if result.cancelled else ""
        self.status.emit(
            f"Análisis terminado{suffix}: {format_bytes(result.total_size)} en "
            f"{result.total_files:,} archivos · {result.duration:.1f} s"
        )

    def _render(self, result: ScanResult) -> None:
        while self.children_box.count():
            item = self.children_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        while self.categories_box.count():
            item = self.categories_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        while self.files_box.count():
            item = self.files_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        total = max(1, result.total_size)
        palette = [
            COLORS["accent"],
            COLORS["magenta"],
            COLORS["amber"],
            COLORS["bad"],
            COLORS["good"],
            COLORS["accent_glow"],
            COLORS["text_dim"],
        ]

        if not result.children:
            empty = Muted("No se encontraron elementos accesibles en esta ubicación.")
            self.children_box.addWidget(empty)
        for node in result.children[:25]:
            row = BarRow(node, node.size / total, COLORS["accent"])
            row.activated.connect(self._on_node_activated)
            self.children_box.addWidget(row)

        for index, (name, size) in enumerate(result.categories[:8]):
            wrapper = QWidget()
            box = QVBoxLayout(wrapper)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(4)
            head = QHBoxLayout()
            label = QLabel(name)
            label.setStyleSheet("font-size: 12.5px;")
            value = QLabel(format_bytes(size))
            value.setStyleSheet("font-size: 12.5px; font-weight: 600;")
            head.addWidget(label)
            head.addStretch(1)
            head.addWidget(value)
            box.addLayout(head)
            bar = ArcProgress(palette[index % len(palette)])
            bar.set_ratio(size / total)
            bar.set_label(f"Peso de {label.text()}")
            box.addWidget(bar)
            self.categories_box.addWidget(wrapper)

        for node in result.largest_files[:8]:
            wrapper = QWidget()
            box = QVBoxLayout(wrapper)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            name = QLabel(node.name)
            name.setStyleSheet("font-size: 12.5px;")
            meta = QLabel(f"{format_bytes(node.size)} · {node.path}")
            meta.setObjectName("Dim")
            meta.setWordWrap(True)
            box.addWidget(name)
            box.addWidget(meta)
            self.files_box.addWidget(wrapper)

        self.up_button.setEnabled(
            bool(os.path.dirname(result.root.rstrip("\\/")))
            and os.path.dirname(result.root.rstrip("\\/")) != result.root
        )

    def _on_node_activated(self, node) -> None:
        if getattr(node, "is_dir", False):
            self.start_scan(node.path)
        else:
            folder = os.path.dirname(node.path)
            QMessageBox.information(
                self,
                "Archivo",
                f"{node.name}\n\nTamaño: {format_bytes(node.size)}\nCarpeta: {folder}",
            )

    # --- Datos para el informe ------------------------------------------
    def last_result(self) -> ScanResult | None:
        return self._result

    def drive_labels(self) -> dict[str, str]:
        return {drive.mount: drive.label for drive in getattr(self, "drives", [])}
