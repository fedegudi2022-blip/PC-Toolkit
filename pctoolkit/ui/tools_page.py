"""Página de accesos rápidos a herramientas de Windows."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.config import COLORS, format_bytes
from ..core import tools as tool_core
from . import accessibility, icons
from .widgets import Card, IconBadge, Muted, PageHeader, SectionTitle


class ToolTile(Card):
    """Botón amplio que abre una herramienta del sistema."""

    clicked = Signal(object)

    def __init__(self, tool: tool_core.ToolShortcut, parent: QWidget | None = None) -> None:
        super().__init__(parent, soft=True)
        self.tool = tool
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(74)
        # Abre una herramienta del sistema al pulsarla: para el lector es un botón
        # con el nombre de la herramienta y su explicación.
        accessibility.mark(self, QAccessible.Role.Button, tool.name, tool.description)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(IconBadge(tool.icon, 30, 16, COLORS["text_muted"]))
        name = QLabel(tool.name)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        name.setWordWrap(True)
        top.addWidget(name, 1)
        if tool.admin:
            badge = QLabel()
            badge.setPixmap(icons.pixmap_for("shield", 14, COLORS["warn"]))
            badge.setToolTip("Solicita permisos de administrador (UAC)")
            top.addWidget(badge)
        layout.addLayout(top)

        description = QLabel(tool.description)
        description.setObjectName("Muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addStretch(1)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.tool)


class ToolsPage(QWidget):
    """Catálogo de herramientas del sistema agrupadas por categoría."""

    status = Signal(str)

    def __init__(self, launcher, task_logger=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.launch_tool = launcher
        self.task_logger = task_logger
        self._tiles: list[ToolTile] = []
        self._build()

    # ------------------------------------------------------------------
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
            "Herramientas de Windows",
            "Abre en un clic las utilidades del sistema que más se usan.",
        )
        layout.addWidget(header)

        layout.addWidget(self._maintenance_card())

        filters = Card(name="Filtros")
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(16, 12, 16, 12)
        filters_layout.setSpacing(10)
        filters_layout.addWidget(IconBadge("analyze", 28, 15, COLORS["text_dim"]))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar herramienta (por ejemplo: disco, red, actualizaciones)…")
        self.search.textChanged.connect(self._filter)
        filters_layout.addWidget(self.search, 1)
        layout.addWidget(filters)

        self.categories_container = QWidget()
        self.categories_layout = QVBoxLayout(self.categories_container)
        self.categories_layout.setContentsMargins(0, 0, 0, 0)
        self.categories_layout.setSpacing(18)
        layout.addWidget(self.categories_container)
        layout.addStretch(1)

        self._populate()

    def _maintenance_card(self) -> Card:
        card = Card(name="Mantenimiento rápido")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        layout.addWidget(IconBadge("broom", 40, 20, COLORS["accent_glow"]))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel("Mantenimiento rápido")
        title.setObjectName("CardTitle")
        self.temp_info = QLabel("Calcula cuánto espacio ocupan los archivos temporales del usuario.")
        self.temp_info.setObjectName("Muted")
        self.temp_info.setWordWrap(True)
        texts.addWidget(title)
        texts.addWidget(self.temp_info)
        layout.addLayout(texts, 1)

        self.scan_temp_button = QPushButton("Calcular temporales")
        self.scan_temp_button.setObjectName("Ghost")
        self.scan_temp_button.clicked.connect(self._scan_temp)
        layout.addWidget(self.scan_temp_button)

        self.clean_temp_button = QPushButton("Liberar espacio")
        self.clean_temp_button.setObjectName("Primary")
        self.clean_temp_button.setIcon(icons.icon("broom", 15, COLORS["on_accent"]))
        self.clean_temp_button.setEnabled(False)
        self.clean_temp_button.clicked.connect(self._clean_temp)
        layout.addWidget(self.clean_temp_button)
        return card

    def _populate(self) -> None:
        while self.categories_layout.count():
            item = self.categories_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._tiles.clear()

        for category, tools in tool_core.tools_by_category().items():
            if not tools:
                continue
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            block_layout.setSpacing(10)
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(SectionTitle(category))
            count = QLabel(f"{len(tools)} herramientas")
            count.setObjectName("Dim")
            head.addWidget(count)
            head.addStretch(1)
            block_layout.addLayout(head)

            grid = QGridLayout()
            grid.setSpacing(12)
            for index, tool in enumerate(tools):
                tile = ToolTile(tool)
                tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                tile.clicked.connect(self._run_tool)
                tile.matches = True
                tile.category_name = category
                grid.addWidget(tile, index // 3, index % 3)
                self._tiles.append(tile)
            block_layout.addLayout(grid)
            self.categories_layout.addWidget(block)

    def _filter(self, text: str) -> None:
        query = text.strip().lower()
        for tile in self._tiles:
            haystack = f"{tile.tool.name} {tile.tool.description} {tile.tool.category}".lower()
            # Se guarda la coincidencia porque isVisible() depende de los padres.
            tile.matches = not query or query in haystack
            tile.setVisible(tile.matches)
        for index in range(self.categories_layout.count()):
            widget = self.categories_layout.itemAt(index).widget()
            if widget is None:
                continue
            widget.setVisible(any(tile.matches for tile in widget.findChildren(ToolTile)))
        self.status.emit(
            f"{sum(1 for tile in self._tiles if tile.isVisible())} herramientas disponibles"
            if query
            else "Mostrando todas las herramientas."
        )

    def _run_tool(self, tool: tool_core.ToolShortcut) -> None:
        ok, message = self.launch_tool(tool.id)
        self.status.emit(message)
        if not ok:
            QMessageBox.warning(self, "No se pudo abrir", message)
        elif self.task_logger is not None:
            self.task_logger(tool)

    # --- Temporales -------------------------------------------------------
    def _scan_temp(self) -> None:
        report = tool_core.scan_temp_folder()
        if report.file_count == 0:
            self.temp_info.setText("No hay archivos temporales antiguos que se puedan borrar.")
            self.clean_temp_button.setEnabled(False)
        else:
            self.temp_info.setText(
                f"{format_bytes(report.total_size)} en {report.file_count:,} archivos temporales "
                f"(más de 1 hora de antigüedad) en {report.folders[0] if report.folders else ''}"
            )
            self.clean_temp_button.setEnabled(True)
        self._temp_report = report
        self.status.emit("Cálculo de archivos temporales completado.")

    def _clean_temp(self) -> None:
        report = getattr(self, "_temp_report", None) or tool_core.scan_temp_folder()
        if not report.file_count:
            return
        answer = QMessageBox.question(
            self,
            "Liberar espacio",
            "Se eliminarán los archivos temporales con más de una hora de antigüedad:\n\n"
            f"{format_bytes(report.total_size)} en {report.file_count:,} archivos\n"
            f"Carpeta: {report.folders[0] if report.folders else ''}\n\n"
            "Los archivos en uso se omitirán automáticamente. ¿Continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        result = tool_core.clean_temp_folder()
        self.temp_info.setText(
            f"Se liberaron {format_bytes(result.freed)} "
            f"({result.deleted:,} archivos borrados, {result.skipped:,} omitidos)."
        )
        self.clean_temp_button.setEnabled(False)
        self.status.emit(f"Espacio liberado: {format_bytes(result.freed)}")
        if self.task_logger is not None:
            self.task_logger("temp_cleanup", f"Liberados {format_bytes(result.freed)}")
