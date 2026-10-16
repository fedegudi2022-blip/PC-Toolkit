"""Página «Ajustes de Windows»: analizar, revisar y aplicar, sin tocar nada al analizar.

El flujo es el de una revisión médica: se **analiza** (solo lectura), se ve qué está
recomendado y qué ya está optimizado, se marcan los que se quieran y solo entonces se
**aplican**, con confirmación previa y posibilidad de volver atrás.

Cada ajuste lleva su ayuda (botón «?», clic derecho o `F1`), donde se explica qué cambia,
qué riesgo tiene, qué claves del registro toca y cómo se revierte. Nada se aplica sin que el
usuario lo haya marcado y confirmado.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAccessible, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import plugins as plugins_core
from ..core import windows_settings as tweaks
from ..core.config import COLORS, format_integer
from ..core.undo import UndoStore
from . import accessibility, theme
from .debloat import DebloatDialog
from .plugins_ui import PluginsDialog
from .widgets import Card, IconBadge, Muted, PageHeader, SectionTitle, hline


class HelpDialog(QDialog):
    """«¿Qué hace esto?»: qué cambia, riesgo, claves y cómo se revierte."""

    def __init__(self, setting: tweaks.Setting, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setting = setting
        self.setWindowTitle(f"¿Qué hace «{setting.name}»?")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge(tweaks.CATEGORIES.get(setting.category, ("", "gear"))[1], 36, 19, COLORS["accent_glow"]))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel(setting.name)
        title.setObjectName("SectionTitle")
        texts.addWidget(title)
        chips = QLabel(f"{setting.category_label} · {setting.risk_label}" + (" · necesita administrador" if setting.needs_admin else ""))
        chips.setObjectName("Muted")
        texts.addWidget(chips)
        head.addLayout(texts, 1)
        layout.addLayout(head)

        body = QLabel(setting.help_text())
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        body.setAccessibleName("Explicación del ajuste")
        layout.addWidget(body)

        buttons = QDialogButtonBox()
        self.search_button = QPushButton("Buscar en internet")
        self.search_button.setObjectName("Ghost")
        self.search_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.setting.search_url())))
        self.copy_button = QPushButton("Copiar las claves")
        self.copy_button.setObjectName("Ghost")
        self.copy_button.clicked.connect(self._copy_keys)
        close_button = QPushButton("Cerrar")
        close_button.setObjectName("Primary")
        close_button.clicked.connect(self.accept)
        buttons.addButton(self.search_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        buttons.addButton(close_button, QDialogButtonBox.AcceptRole)
        layout.addWidget(buttons)

    def _copy_keys(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join(self.setting.registry_lines))


class SettingRow(Card):
    """Una fila del catálogo: casilla, nombre, estado, riesgo y ayuda."""

    changed = Signal()
    help_requested = Signal(object)

    def __init__(self, state: tweaks.SettingState, parent: QWidget | None = None) -> None:
        super().__init__(parent, soft=True, name=state.setting.name)
        self.setting = state.setting
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.check = QCheckBox()
        self.check.setVisible(False)
        self.check.setAccessibleName(f"Aplicar «{self.setting.name}»")
        self.check.toggled.connect(lambda _=False: self.changed.emit())
        top.addWidget(self.check)
        self.name_label = QLabel(self.setting.name)
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.name_label.setWordWrap(True)
        top.addWidget(self.name_label, 1)

        self.status_label = QLabel()
        self.status_label.setObjectName("Muted")
        top.addWidget(self.status_label)

        self.risk_label = QLabel()
        self.risk_label.setObjectName("Muted")
        top.addWidget(self.risk_label)

        self.admin_label = QLabel()
        theme.pixmap(self.admin_label, "shield", 14, "warn")
        self.admin_label.setToolTip("Este ajuste toca HKEY_LOCAL_MACHINE y necesita permisos de administrador")
        self.admin_label.setVisible(False)
        top.addWidget(self.admin_label)

        self.plugin_label = QLabel(f"Plugin · {self.setting.plugin_name}")
        self.plugin_label.setObjectName("Dim")
        self.plugin_label.setVisible(self.setting.from_plugin)
        self.plugin_label.setToolTip("Este ajuste lo aporta un plugin, no PC Toolkit: revísalo antes de confiar en él")
        top.addWidget(self.plugin_label)

        self.help_button = QPushButton("?")
        self.help_button.setObjectName("Ghost")
        self.help_button.setFixedSize(26, 26)
        self.help_button.setToolTip("¿Qué hace esto?  ·  F1")
        self.help_button.setAccessibleName(f"¿Qué hace «{self.setting.name}»?")
        self.help_button.clicked.connect(lambda: self.help_requested.emit(self.setting))
        top.addWidget(self.help_button)
        layout.addLayout(top)

        self.description = QLabel(self.setting.description)
        self.description.setObjectName("Muted")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)

        self.detail = Muted("")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        accessibility.mark(self, QAccessible.Role.ListItem, self.setting.name, self.setting.description)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        # La fila recibe el foco al pulsarla: así `F1` explica el ajuste sobre el que se
        # acaba de hacer clic, que es lo que espera cualquiera que use el teclado.
        self.setFocusPolicy(Qt.StrongFocus)
        self._update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt)
        self.setFocus(Qt.MouseFocusReason)
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    def _menu(self, position) -> None:
        menu = QMenu(self)
        what = menu.addAction("¿Qué hace esto?")
        what.setShortcut(QKeySequence(Qt.Key_F1))
        what.triggered.connect(lambda: self.help_requested.emit(self.setting))
        copy_keys = menu.addAction("Copiar las claves del registro")
        copy_keys.triggered.connect(self._copy_keys)
        search = menu.addAction("Buscar en internet")
        search.triggered.connect(self._search)
        if self.check.isVisibleTo(self) and self.state.available:
            menu.addSeparator()
            toggle = menu.addAction("Marcar para aplicar" if not self.check.isChecked() else "Quitar la marca")
            toggle.triggered.connect(self._toggle)
        menu.exec(self.mapToGlobal(position))

    def _toggle(self) -> None:
        self.check.setChecked(not self.check.isChecked())

    def _copy_keys(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join(self.setting.registry_lines))

    def _search(self) -> None:
        QDesktopServices.openUrl(QUrl(self.setting.search_url()))

    def set_state(self, state: tweaks.SettingState) -> None:
        """Repinta la fila con el resultado del último análisis."""
        self.state = state
        self._update()

    def _update(self) -> None:
        estado = self.state.status
        self.status_label.setText(self.state.status_label)
        color = {
            tweaks.STATUS_PENDING: COLORS["warn"],
            tweaks.STATUS_OPTIMIZED: COLORS["good"],
            tweaks.STATUS_UNAVAILABLE: COLORS["text_dim"],
        }.get(estado, COLORS["text_muted"])
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.risk_label.setText(self.setting.risk_label)
        self.admin_label.setVisible(self.setting.needs_admin)
        aplicable = estado == tweaks.STATUS_PENDING
        self.check.setVisible(aplicable)
        if not aplicable:
            self.check.setChecked(False)
        self.detail.setText(self.state.detail)
        self.detail.setVisible(bool(self.state.detail))
        # La ayuda siempre está disponible: incluso en un ajuste ya optimizado interesa
        # saber qué se está dejando como está.
        self.help_button.setVisible(True)

    @property
    def marked(self) -> bool:
        # `isVisibleTo` y no `isVisible`: mientras la página no se ha enseñado todavía
        # ninguna casilla «es visible», y con eso no se podría marcar nada antes del
        # primer pintado (lo que pasa al construir la página y en las pruebas).
        return self.check.isVisibleTo(self) and self.check.isChecked()

    def mark(self, value: bool) -> None:
        if self.check.isVisibleTo(self):
            self.check.setChecked(value)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.key() == Qt.Key_F1:
            self.help_requested.emit(self.setting)
            return
        super().keyPressEvent(event)


class WindowsSettingsPage(QWidget):
    """Catálogo de ajustes recomendados de Windows, con análisis y aplicación."""

    status = Signal(str)

    def __init__(self, store: UndoStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._rows: list[SettingRow] = []
        self._states: list[tweaks.SettingState] = []
        # Los plugins se leen antes de construir las filas: sus ajustes son parte del
        # catálogo y tienen que salir en la misma lista, con las mismas reglas.
        self.plugins = plugins_core.load_into_catalog()
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
        layout.setSpacing(16)

        header = PageHeader(
            "Ajustes de Windows",
            "Revisa qué ajustes conviene cambiar y aplica solo los que quieras. Nada se toca "
            "hasta que lo confirmes, y todo se puede deshacer.",
        )
        self.analyze_button = QPushButton("Analizar")
        self.analyze_button.setObjectName("Primary")
        self.analyze_button.setToolTip("Mira el estado actual de cada ajuste. No cambia nada.  ·  F5")
        self.analyze_button.clicked.connect(self.analyse)
        header.add_action(self.analyze_button)
        layout.addWidget(header)

        # --- Acciones y resumen -------------------------------------------
        actions = Card(name="Acciones")
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(16, 14, 16, 14)
        actions_layout.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.mark_button = QPushButton("Marcar los recomendados")
        self.mark_button.setObjectName("Ghost")
        self.mark_button.clicked.connect(lambda: self.mark_recommended(True))
        row.addWidget(self.mark_button)
        self.unmark_button = QPushButton("Quitar las marcas")
        self.unmark_button.setObjectName("Ghost")
        self.unmark_button.clicked.connect(lambda: self.mark_recommended(False))
        row.addWidget(self.unmark_button)
        row.addStretch(1)
        self.summary_label = Muted("Sin analizar todavía: pulsa «Analizar».")
        self.summary_label.setAccessibleName("Resumen del análisis")
        row.addWidget(self.summary_label)
        actions_layout.addLayout(row)

        row_two = QHBoxLayout()
        row_two.setSpacing(8)
        self.apply_button = QPushButton("Aplicar los marcados")
        self.apply_button.setObjectName("Primary")
        self.apply_button.setEnabled(False)
        self.apply_button.setToolTip("Aplica solo los ajustes que hayas marcado, pidiendo confirmación")
        self.apply_button.clicked.connect(self.apply_marked)
        row_two.addWidget(self.apply_button)
        self.summary_button = QPushButton("Copiar resumen")
        self.summary_button.setObjectName("Ghost")
        self.summary_button.setToolTip("Copia el análisis y los últimos cambios, para pedir ayuda en un foro")
        self.summary_button.clicked.connect(self.copy_summary)
        row_two.addWidget(self.summary_button)
        row_two.addStretch(1)
        actions_layout.addLayout(row_two)
        layout.addWidget(actions)

        # --- Filtros -------------------------------------------------------
        filters = Card(name="Filtros")
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(16, 12, 16, 12)
        filters_layout.setSpacing(10)
        filters_layout.addWidget(IconBadge("analyze", 28, 15, COLORS["text_dim"]))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar ajuste (por ejemplo: anuncios, telemetría, barra)…")
        self.search.textChanged.connect(self._apply_filter)
        filters_layout.addWidget(self.search, 1)
        self.only_pending = QCheckBox("Solo los recomendados")
        self.only_pending.toggled.connect(self._apply_filter)
        filters_layout.addWidget(self.only_pending)
        layout.addWidget(filters)

        # --- Lo que trae Windows de fábrica y lo que aportan los plugins -------
        layout.addWidget(self._build_debloat_card())
        layout.addWidget(self._build_plugins_card())

        self.categories_container = QWidget()
        self.categories_layout = QVBoxLayout(self.categories_container)
        self.categories_layout.setContentsMargins(0, 0, 0, 0)
        self.categories_layout.setSpacing(16)
        layout.addWidget(self.categories_container)
        layout.addStretch(1)

        # F5 analiza otra vez y F1 explica el ajuste en el que estás: los dos se usan sin
        # soltar el teclado.
        QShortcut(QKeySequence("F5"), self, activated=self.analyse)
        QShortcut(QKeySequence(Qt.Key_F1), self, activated=self.help_for_focus)
        self._build_rows()

    def _build_debloat_card(self) -> QWidget:
        """Tarjeta del gestor de aplicaciones preinstaladas (debloat)."""
        tarjeta = Card(name="Aplicaciones preinstaladas")
        fila = QHBoxLayout(tarjeta)
        fila.setContentsMargins(16, 14, 16, 14)
        fila.setSpacing(12)
        fila.addWidget(IconBadge("tool", 34, 18, COLORS["text_muted"]))
        textos = QVBoxLayout()
        textos.setSpacing(2)
        titulo = QLabel("Aplicaciones preinstaladas")
        titulo.setStyleSheet("font-size: 13.5px; font-weight: 600;")
        textos.addWidget(titulo)
        detalle = Muted(
            "Windows trae decenas de aplicaciones de la tienda que nadie pidió. Aquí se ven con su "
            "editor, su versión y lo que ocupan, y se quitan solo de tu cuenta (sin administrador). "
            "Las piezas del sistema están en una lista blanca y no se pueden tocar."
        )
        detalle.setWordWrap(True)
        textos.addWidget(detalle)
        fila.addLayout(textos, 1)
        self.debloat_button = QPushButton("Revisar aplicaciones…")
        self.debloat_button.setObjectName("Ghost")
        self.debloat_button.setToolTip("Abre la lista de aplicaciones instaladas, con su tamaño y su riesgo")
        self.debloat_button.clicked.connect(self.open_debloat)
        fila.addWidget(self.debloat_button)
        return tarjeta

    def _build_plugins_card(self) -> QWidget:
        """Tarjeta de plugins: cuántos hay, cuántos esperan revisión y qué falló."""
        tarjeta = Card(name="Plugins")
        fila = QHBoxLayout(tarjeta)
        fila.setContentsMargins(16, 14, 16, 14)
        fila.setSpacing(12)
        fila.addWidget(IconBadge("gear", 34, 18, COLORS["text_muted"]))
        textos = QVBoxLayout()
        textos.setSpacing(2)
        titulo = QLabel("Plugins")
        titulo.setStyleSheet("font-size: 13.5px; font-weight: 600;")
        textos.addWidget(titulo)
        self.plugins_label = Muted("")
        self.plugins_label.setWordWrap(True)
        self.plugins_label.setAccessibleName("Estado de los plugins")
        textos.addWidget(self.plugins_label)
        self.plugins_aviso = QLabel("")
        self.plugins_aviso.setWordWrap(True)
        self.plugins_aviso.setVisible(False)
        textos.addWidget(self.plugins_aviso)
        fila.addLayout(textos, 1)
        self.plugins_button = QPushButton("Revisar plugins…")
        self.plugins_button.setObjectName("Ghost")
        self.plugins_button.setToolTip("Revisa qué trae cada plugin antes de dejar que toque el equipo")
        self.plugins_button.clicked.connect(self.open_plugins)
        fila.addWidget(self.plugins_button)
        self._update_plugins_card()
        return tarjeta

    def _update_plugins_card(self) -> None:
        """Repinta el resumen de plugins (y el aviso si hay algo pendiente)."""
        if not hasattr(self, "plugins_label"):
            return
        resultado = self.plugins
        self.plugins_label.setText(f"{resultado.summary()} · carpeta: {plugins_core.plugins_dir()}")
        pendientes = [info for info in resultado.plugins if not info.trusted]
        if resultado.errors or pendientes:
            partes = []
            if pendientes:
                partes.append(
                    f"{len(pendientes)} plugin(s) sin confirmar: sus ajustes aparecen como no disponibles "
                    "hasta que los revises."
                )
            if resultado.errors:
                partes.append(f"{len(resultado.errors)} archivo(s) con errores: revisa el motivo y corrige el JSON.")
            self.plugins_aviso.setText(" ".join(partes))
            self.plugins_aviso.setStyleSheet(
                f"color: {COLORS['bad'] if resultado.errors else COLORS['warn']}; font-size: 12px;"
            )
            self.plugins_aviso.setVisible(True)
        else:
            self.plugins_aviso.setVisible(False)
        self.plugins_button.setText(
            "Revisar plugins…" if not pendientes else f"Revisar plugins ({len(pendientes)} sin confirmar)…"
        )

    def open_debloat(self) -> None:
        """Abre el gestor de aplicaciones preinstaladas."""
        dialogo = DebloatDialog(self.store, self)
        dialogo.done_changes.connect(self.status.emit)
        dialogo.exec()

    def open_plugins(self) -> None:
        """Abre la revisión de plugins."""
        dialogo = PluginsDialog(self.plugins, self)
        dialogo.changed.connect(self._plugins_changed)
        dialogo.exec()
        self.plugins = plugins_core.loaded()
        self._update_plugins_card()

    def _plugins_changed(self, mensaje: str) -> None:
        """Un plugin se aceptó, se retiró o se volvió a leer: el catálogo cambia."""
        self.plugins = plugins_core.loaded()
        self._clear_rows()
        self._build_rows()
        self.analyse()
        self._update_plugins_card()
        self.status.emit(mensaje)

    def _clear_rows(self) -> None:
        """Vacía las tarjetas de categorías (al recargar plugins hay ajustes nuevos)."""
        while self.categories_layout.count():
            item = self.categories_layout.takeAt(0)
            tarjeta = item.widget()
            if tarjeta is not None:
                tarjeta.setParent(None)
                tarjeta.deleteLater()
        self._rows = []
        self._states = []

    @property
    def analysed(self) -> bool:
        """¿Se ha analizado ya este equipo? (la ventana lo mira al abrir la página)"""
        return any(fila.state.status != tweaks.STATUS_UNKNOWN for fila in self._rows)

    def _build_rows(self) -> None:
        """Crea las filas del catálogo (una vez: el análisis solo las repinta)."""
        self._states = [
            tweaks.SettingState(setting, tweaks.STATUS_UNKNOWN, "sin analizar todavía")
            for setting in tweaks.catalog()
        ]
        for _, nombre, del_grupo in tweaks.grouped(self._states):
            card = Card(name=nombre)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 16)
            card_layout.setSpacing(10)
            card_layout.addWidget(SectionTitle(nombre))
            card_layout.addWidget(hline())
            for estado in del_grupo:
                fila = SettingRow(estado)
                fila.changed.connect(self._update_buttons)
                fila.help_requested.connect(self.show_help)
                self._rows.append(fila)
                card_layout.addWidget(fila)
            self.categories_layout.addWidget(card)
        self._update_buttons()
        self._apply_filter()

    # ------------------------------------------------------------------
    def analyse(self) -> None:
        """Analiza el equipo y repinta el catálogo (no cambia nada)."""
        estados = tweaks.analyse()
        por_id = {estado.setting.id: estado for estado in estados}
        for fila in self._rows:
            estado = por_id.get(fila.setting.id)
            if estado is not None:
                fila.set_state(estado)
        self._states = estados
        cifras = tweaks.summary(estados)
        self.summary_label.setText(
            f"{format_integer(cifras['recomendados'])} recomendado(s) · "
            f"{format_integer(cifras['optimizados'])} ya optimizado(s) · "
            f"{format_integer(cifras['no_disponibles'])} no disponible(s)"
        )
        self._update_buttons()
        self._apply_filter()
        self.status.emit(
            f"Ajustes analizados: {cifras['recomendados']} por aplicar de {cifras['total']}."
        )

    def mark_recommended(self, value: bool = True) -> None:
        """Marca (o desmarca) todo lo que esté recomendado y se pueda aplicar."""
        for fila in self._rows:
            if fila.state.status == tweaks.STATUS_PENDING:
                fila.mark(value)
        self._update_buttons()

    def selected_settings(self) -> list[tweaks.Setting]:
        """Ajustes marcados, en el orden del catálogo."""
        return [fila.setting for fila in self._rows if fila.marked]

    def _update_buttons(self) -> None:
        marcados = len(self.selected_settings())
        self.apply_button.setEnabled(marcados > 0)
        self.apply_button.setText(
            "Aplicar los marcados" if marcados == 0 else f"Aplicar los marcados ({marcados})"
        )

    def _apply_filter(self) -> None:
        texto = self.search.text().strip().lower()
        solo_recomendados = self.only_pending.isChecked()
        visibles = 0
        for fila in self._rows:
            ajuste = fila.setting
            coincide = not texto or texto in ajuste.name.lower() or texto in ajuste.description.lower() or texto in ajuste.category_label.lower()
            if solo_recomendados and fila.state.status != tweaks.STATUS_PENDING:
                coincide = False
            fila.setVisible(coincide)
            visibles += int(coincide)
        # Las tarjetas de categoría se ocultan enteras cuando no queda nada dentro.
        for indice in range(self.categories_layout.count()):
            tarjeta = self.categories_layout.itemAt(indice).widget()
            if tarjeta is None:
                continue
            hijas = [hija for hija in tarjeta.findChildren(SettingRow)]
            if hijas:
                # `isVisibleTo(tarjeta)`: si la tarjeta está oculta, sus filas no «son
                # visibles» aunque ellas mismas no estén ocultas, y la tarjeta se volvería
                # a ocultar sola en cada pasada del filtro.
                tarjeta.setVisible(any(hija.isVisibleTo(tarjeta) for hija in hijas))

    # ------------------------------------------------------------------
    def show_help(self, setting: tweaks.Setting) -> None:
        """Abre la ayuda del ajuste (botón «?», clic derecho o F1)."""
        dialogo = HelpDialog(setting, self)
        dialogo.exec()

    def help_for_focus(self) -> None:
        """Abre la ayuda del ajuste con el foco (o del primero marcado, si no hay foco)."""
        foco = self.focusWidget()
        fila = foco if isinstance(foco, SettingRow) else None
        if fila is None:
            fila = next((candidata for candidata in self._rows if candidata.marked), None)
        if fila is None:
            fila = next(
                (candidata for candidata in self._rows if candidata.state.status == tweaks.STATUS_PENDING),
                None,
            )
        if fila is not None:
            self.show_help(fila.setting)

    def copy_summary(self) -> None:
        """Copia el resumen al portapapeles (para pedir ayuda)."""
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(tweaks.summary_text())
        self.status.emit("Resumen copiado al portapapeles.")

    def apply_marked(self) -> None:
        """Aplica los ajustes marcados, con confirmación previa."""
        ajustes = self.selected_settings()
        if not ajustes:
            return
        riesgos = [ajuste for ajuste in ajustes if ajuste.risk in (tweaks.RISK_MEDIUM, tweaks.RISK_HIGH)]
        lineas = [f"• {ajuste.name} ({ajuste.risk_label.lower()})" for ajuste in ajustes]
        aviso = ""
        if riesgos:
            aviso = (
                "\n\nEstos cambios son de riesgo medio o alto: se guarda un respaldo de las claves "
                "antes de tocarlas y se pueden deshacer desde la página de Inicio."
            )
        pregunta = QMessageBox(self)
        pregunta.setWindowTitle("Aplicar los ajustes marcados")
        pregunta.setIcon(QMessageBox.Question)
        pregunta.setText(f"¿Aplicar {len(ajustes)} ajuste(s)?\n\n" + "\n".join(lineas) + aviso)
        pregunta.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        pregunta.button(QMessageBox.Yes).setText("Aplicar")
        pregunta.button(QMessageBox.No).setText("Cancelar")
        punto = None
        if riesgos and tweaks.is_elevated() and tweaks.IS_WINDOWS:
            punto = QCheckBox("Crear un punto de restauración antes (puede tardar un minuto)")
            pregunta.setCheckBox(punto)
        if pregunta.exec() != QMessageBox.Yes:
            self.status.emit("No se aplicó nada.")
            return

        if punto is not None and punto.isChecked():
            ok, mensaje = tweaks.create_restore_point()
            self.status.emit(mensaje if ok else f"Aviso: {mensaje}")
            if not ok:
                # Sin punto de restauración se sigue, pero se dice: el respaldo .reg y el
                # sistema de deshacer siguen ahí.
                self.status.emit(f"{mensaje} Se aplica igual, con el respaldo de claves.")

        resultado = tweaks.apply_settings(ajustes, store=self.store)
        self.analyse()
        self.status.emit(resultado.message())
        if resultado.failed:
            QMessageBox.warning(
                self,
                "Algunos ajustes no se pudieron aplicar",
                "\n".join(f"• {nombre}: {motivo}" for nombre, motivo in resultado.failed),
            )
