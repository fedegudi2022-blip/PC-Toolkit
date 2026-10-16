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

from ..core.config import COLORS, format_bytes, format_integer
from ..core import cleaner
from ..core import tools as tool_core
from . import accessibility, icons, theme, workers
from .clean import CleanDialog
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
            theme.pixmap(badge, "shield", 14, "warn")
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

    def __init__(
        self,
        launcher,
        task_logger=None,
        settings=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.launch_tool = launcher
        self.task_logger = task_logger
        # Las preferencias son para la ventana de limpieza: es la que guarda qué categorías
        # limpia el mantenimiento automático. Sin ellas (pruebas sueltas) todo funciona
        # igual, solo que esa casilla no se enseña.
        self.settings = settings
        self._tiles: list[ToolTile] = []
        self._space_scan: cleaner.CleanScan | None = None
        self._space_running = False
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
        card = Card(name="Liberar espacio")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        layout.addWidget(IconBadge("broom", 40, 20, COLORS["accent_glow"]))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel("Liberar espacio")
        title.setObjectName("CardTitle")
        self.space_info = QLabel(
            "Mira cuánto se puede liberar antes de borrar nada: temporales del usuario y de "
            "Windows, cachés de los navegadores, miniaturas, volcados de error y la papelera."
        )
        self.space_info.setObjectName("Muted")
        self.space_info.setWordWrap(True)
        texts.addWidget(title)
        texts.addWidget(self.space_info)
        layout.addLayout(texts, 1)

        self.analyze_space_button = QPushButton("Analizar")
        self.analyze_space_button.setObjectName("Primary")
        self.analyze_space_button.setIcon(icons.icon("analyze", 15, COLORS["on_accent"]))
        self.analyze_space_button.setToolTip(
            "Mide todas las categorías sin borrar nada (tarda unos segundos)"
        )
        self.analyze_space_button.clicked.connect(self.analyze_space)
        layout.addWidget(self.analyze_space_button)

        self.space_detail_button = QPushButton("Ver detalle…")
        self.space_detail_button.setObjectName("Ghost")
        self.space_detail_button.setEnabled(False)
        self.space_detail_button.setToolTip("Abre la vista previa, categoría por categoría")
        self.space_detail_button.clicked.connect(self.open_clean_dialog)
        layout.addWidget(self.space_detail_button)
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

    # --- Liberar espacio --------------------------------------------------
    def analyze_space(self) -> None:
        """Mide todas las categorías en segundo plano (no borra nada)."""
        if self._space_running:
            return
        self._space_running = True
        self.analyze_space_button.setEnabled(False)
        self.space_info.setText("Analizando qué se puede liberar…")
        self.status.emit("Analizando el espacio que se puede liberar…")
        workers.run_in_background(
            self, lambda: cleaner.scan(cleaner.target_keys()), self._space_scanned
        )

    def _space_scanned(self, resultado) -> None:
        self._space_running = False
        self.analyze_space_button.setEnabled(True)
        if isinstance(resultado, Exception):
            self.space_info.setText(f"No se pudo analizar el espacio: {resultado}")
            return
        self._space_scan = resultado
        self.space_detail_button.setEnabled(True)
        self.space_info.setText(self.space_text())
        self.status.emit(self.space_text())

    def space_text(self) -> str:
        """Frase con lo que se liberaría, o la explicación de por qué no hay nada."""
        plan = self._space_scan
        if plan is None:
            return (
                "Mira cuánto se puede liberar antes de borrar nada: temporales del usuario y de "
                "Windows, cachés de los navegadores, miniaturas, volcados de error y la papelera."
            )
        disponibles = plan.available_items
        if not disponibles:
            return "Ahora mismo no hay nada que se pueda liberar de forma segura."
        texto = (
            f"Se pueden liberar {format_bytes(plan.total_size)} en "
            f"{format_integer(plan.total_files)} elemento(s), en {len(disponibles)} categoría(s). "
            "Abre el detalle para ver qué es cada cosa y borrar solo lo que quieras."
        )
        if plan.total_locked:
            texto += (
                f" Otros {format_integer(plan.total_locked)} archivo(s) están en uso y se "
                "omitirán."
            )
        return texto

    def open_clean_dialog(self, prechecked=()) -> None:
        """Abre la vista previa (reutiliza el último análisis si lo hay).

        `prechecked` son las categorías que llegan marcadas ya: las usa la portada cuando se
        pulsa la acción de un aviso («se pueden liberar 2 GB»), para no tener que buscarlas.
        """
        dialog = CleanDialog(self.settings, self._space_scan, self, prechecked=prechecked)
        dialog.done_changes.connect(self._space_cleaned)
        dialog.exec()
        # El análisis de la ventana es el más reciente: se queda para la tarjeta.
        self._space_scan = dialog.scan
        self.space_detail_button.setEnabled(True)
        self.space_info.setText(self.space_text())

    def _space_cleaned(self, mensaje: str) -> None:
        self.status.emit(mensaje)
        if self.task_logger is not None:
            self.task_logger("limpieza", mensaje)
