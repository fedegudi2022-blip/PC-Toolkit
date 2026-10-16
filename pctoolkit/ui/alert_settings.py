"""Diálogo de ajustes: umbrales, apariencia, ventana, historial y actualizaciones.

Diseño nuevo: antes era **una columna larga de tarjetas**, con todo a la vista a la vez, el
diálogo altísimo y sin forma de llegar a lo que buscabas. Ahora hay una barra lateral con las
cinco secciones, un buscador que deja solo las opciones que coinciden y el contenido repartido
en tarjetas, cada una con su explicación. Se conservan los mismos nombres de widget
(`theme_combo`, `enabled_check`, `updates_button`…) porque la suite y el resto de la
aplicación cuentan con ellos.

Los valores se siguen guardando con `apply()`, que escribe en las preferencias de Windows
(HKCU\\Software\\PCToolkit) y deja la configuración de alertas lista para el motor.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAccessible, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import actualizador
from ..core.alerts import METRIC_CPU, METRIC_DISK, METRIC_RAM, AlertConfig
from ..core.appearance import MODE_LABELS, MODES, describe_mode
from ..core.config import APP_VERSION, COLORS, format_bytes, format_integer
from ..core.history import HistoryStore
from ..core.settings import AppSettings
from . import accessibility
from .widgets import Card, IconBadge, Muted, SectionTitle, hline

# Secciones del diálogo: identificador, título y explicación de para qué sirven.
SECTIONS = (
    ("alertas", "Alertas y avisos", "Cuándo avisar de que el equipo va justo."),
    ("apariencia", "Apariencia", "Cómo se ve la aplicación."),
    ("ventana", "Ventana y bandeja", "Qué hace al cerrar y qué avisa por notificación."),
    ("consumo", "Consumo e historial", "Cuánto mide y cuánto guarda."),
    ("actualizaciones", "Actualizaciones", "Si busca versiones nuevas por su cuenta."),
)


class SettingsRow(QWidget):
    """Una fila del diálogo: etiqueta a la izquierda y control a la derecha."""

    def __init__(self, label: str, control: QWidget, help_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.label = QLabel(label)
        self.label.setWordWrap(True)
        if help_text:
            self.label.setToolTip(help_text)
            self.setToolTip(help_text)
        layout.addWidget(self.label, 1)
        layout.addWidget(control, 0, Qt.AlignRight)
        control.setAccessibleName(label)
        if help_text:
            control.setAccessibleDescription(help_text)


class AlertSettingsDialog(QDialog):
    """Permite ajustar umbrales, apariencia, bandeja, historial y actualizaciones."""

    def __init__(
        self,
        settings: AppSettings,
        store: HistoryStore,
        tray_available: bool = True,
        parent: QWidget | None = None,
        updates=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.config = settings.alert_config
        #: Controlador de actualizaciones de la ventana (puede no haberlo, en las pruebas).
        self.updates = updates
        self._rows: list[tuple[QWidget, str, str]] = []  # (fila, sección, texto para buscar)
        self._buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("Ajustes y alertas")
        self.setMinimumSize(780, 560)
        self.resize(840, 620)
        self._build(tray_available)

    # ------------------------------------------------------------------
    def _build(self, tray_available: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(14)

        layout.addLayout(self._header())
        layout.addLayout(self._body(tray_available), 1)
        layout.addWidget(self._footer())

    def _header(self) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("shield", 38, 20, COLORS["accent_glow"]))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel("Ajustes y alertas")
        title.setObjectName("PageTitle")
        accessibility.set_role(title, QAccessible.Role.Heading)
        subtitle = QLabel(
            "Avisos por umbral, apariencia, bandeja, consumo e historial. Todo se guarda en "
            "tus preferencias, sin permisos de administrador."
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        texts.addWidget(title)
        texts.addWidget(subtitle)
        head.addLayout(texts, 1)
        return head

    def _body(self, tray_available: bool) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(16)

        # --- Barra lateral de secciones ------------------------------------
        nav = QWidget()
        nav.setFixedWidth(196)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)
        for key, title, _ in SECTIONS:
            button = QPushButton(title)
            button.setObjectName("SidebarItem")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(38)
            button.clicked.connect(lambda _=False, name=key: self.show_section(name))
            nav_layout.addWidget(button)
            self._buttons[key] = button
        nav_layout.addStretch(1)
        body.addWidget(nav)

        # --- Contenido -----------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(IconBadge("analyze", 26, 14, COLORS["text_dim"]))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar una opción (por ejemplo: umbral, tema, historial)…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search, 1)
        right_layout.addLayout(search_row)

        self.no_results = Muted("No hay ninguna opción que coincida con esa búsqueda.")
        self.no_results.setWordWrap(True)
        self.no_results.setVisible(False)
        right_layout.addWidget(self.no_results)

        self.stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.stack)
        right_layout.addWidget(scroll, 1)
        body.addWidget(right, 1)

        self._page_alertas()
        self._page_apariencia()
        self._page_ventana(tray_available)
        self._page_consumo()
        self._page_actualizaciones()
        self.show_section(SECTIONS[0][0])
        return body

    # --- Páginas --------------------------------------------------------
    def _page(self) -> QVBoxLayout:
        """Crea la página de una sección y la añade al cuadro."""
        page = QWidget()
        scroll_layout = QVBoxLayout(page)
        scroll_layout.setContentsMargins(0, 0, 12, 8)
        scroll_layout.setSpacing(14)
        scroll_layout.addStretch(1)
        self.stack.addWidget(page)
        return scroll_layout

    def _card(self, page_layout: QVBoxLayout, name: str, description: str) -> QVBoxLayout:
        card = Card(soft=True, name=name)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 16)
        card_layout.setSpacing(10)
        card_layout.addWidget(SectionTitle(name))
        if description:
            note = Muted(description)
            note.setWordWrap(True)
            card_layout.addWidget(note)
        card_layout.addWidget(hline())
        page_layout.insertWidget(page_layout.count() - 1, card)
        return card_layout

    def _row(self, card_layout: QVBoxLayout, section: str, row: QWidget, keywords: str) -> None:
        """Añade una fila y la apunta para el buscador."""
        card_layout.addWidget(row)
        self._rows.append((row, section, keywords.lower()))

    def _page_alertas(self) -> None:
        page = self._page()
        card_layout = self._card(
            page,
            "Avisos por umbral",
            "PC Toolkit vigila el equipo cada segundo: avisa cuando una métrica se mantiene por "
            "encima del umbral, no cuando da un pico suelto.",
        )
        self.enabled_check = QCheckBox("Avisar cuando se supere un umbral")
        self._row(card_layout, "alertas", self.enabled_check, "activar desactivar avisos alertas umbral")
        # Los avisos propios de la portada: unen lo que la aplicación ya mide (CPU sostenida,
        # disco, programas de inicio y limpieza) en frases concretas que se pueden descartar.
        self.insights_check = QCheckBox("Mostrar avisos propios en la portada")
        self.insights_check.setToolTip(
            "Recomendaciones sacadas de tus propios datos («este programa consume el 40 % de "
            "CPU», «quedan 4 GB libres», «tienes 12 programas de inicio pesados»). Cada una se "
            "puede descartar desde la portada."
        )
        self.insights_check.setChecked(self.settings.insights_enabled)
        self._row(
            card_layout,
            "alertas",
            self.insights_check,
            "avisos inteligentes recomendaciones portada consejos descartar",
        )
        self.spins: dict[str, QSpinBox] = {}
        for metric, label, help_text in (
            (METRIC_CPU, "Umbral de CPU", "Porcentaje de uso del procesador a partir del cual se avisa."),
            (METRIC_RAM, "Umbral de memoria RAM", "Porcentaje de memoria en uso a partir del cual se avisa."),
            (METRIC_DISK, "Umbral del disco del sistema", "Porcentaje de ocupación del disco donde está Windows."),
        ):
            spin = QSpinBox()
            spin.setRange(10, 100)
            spin.setSuffix(" %")
            spin.setValue(int(round(self.config.threshold_for(metric))))
            spin.setFixedWidth(110)
            self.spins[metric] = spin
            fila = SettingsRow(label, spin, help_text)
            self._row(card_layout, "alertas", fila, f"umbral {label.lower()} aviso")
        # Los dos tiempos del aviso, en su propia fila y con la etiqueta a la izquierda:
        # antes iban en un formulario y al buscar quedaban sueltos.
        self.sustained_spin = QSpinBox()
        self.sustained_spin.setRange(1, 30)
        self.sustained_spin.setValue(self.config.sustained_samples)
        self.sustained_spin.setSuffix(" muestras")
        self.sustained_spin.setFixedWidth(140)
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(1, 240)
        self.cooldown_spin.setValue(self.config.cooldown_minutes)
        self.cooldown_spin.setSuffix(" min")
        self.cooldown_spin.setFixedWidth(120)
        self._row(
            card_layout,
            "alertas",
            SettingsRow(
                "Sostenido durante",
                self.sustained_spin,
                "Cuántas medidas seguidas tienen que superar el umbral antes de avisar.",
            ),
            "sostenido muestras seguidas aviso",
        )
        self._row(
            card_layout,
            "alertas",
            SettingsRow(
                "No repetir el aviso en",
                self.cooldown_spin,
                "Tiempo mínimo entre dos avisos de la misma métrica.",
            ),
            "enfriamiento repetir aviso minutos",
        )

    def _page_apariencia(self) -> None:
        page = self._page()
        card_layout = self._card(
            page,
            "Tema de la aplicación",
            "«Seguir a Windows» usa el mismo modo que el sistema y cambia con él.",
        )
        self.theme_combo = QComboBox()
        for mode in MODES:
            self.theme_combo.addItem(MODE_LABELS[mode], mode)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.settings.theme_mode)))
        self.theme_combo.setMinimumWidth(190)
        self.theme_combo.currentIndexChanged.connect(self._update_theme_note)
        self._row(card_layout, "apariencia", SettingsRow("Modo de apariencia", self.theme_combo), "tema apariencia claro oscuro")
        self.theme_note = Muted(describe_mode(self.settings.theme_mode))
        self.theme_note.setWordWrap(True)
        self.theme_note.setAccessibleName("Qué hace el modo elegido")
        card_layout.addWidget(self.theme_note)

    def _page_ventana(self, tray_available: bool) -> None:
        page = self._page()
        card_layout = self._card(
            page,
            "Ventana, bandeja y notificaciones",
            "Qué pasa al cerrar la ventana y por dónde avisa la aplicación.",
        )
        self.tray_check = QCheckBox("Mostrar el icono en la bandeja del sistema")
        self.tray_check.setChecked(self.settings.tray_enabled and tray_available)
        self.tray_check.setEnabled(tray_available)
        self._row(card_layout, "ventana", SettingsRow("Icono en la bandeja", self.tray_check, "Área de notificaciones, junto al reloj."), "bandeja icono notificaciones reloj")
        self.notify_check = QCheckBox("Avisar con una notificación al superar un umbral")
        self.notify_check.setChecked(self.settings.notify_on_alert)
        self._row(card_layout, "ventana", self.notify_check, "notificación aviso emergente")
        self.close_check = QCheckBox("Al cerrar la ventana, seguir en la bandeja")
        self.close_check.setChecked(self.settings.close_to_tray)
        self.close_check.setEnabled(tray_available)
        self._row(card_layout, "ventana", self.close_check, "cerrar ventana bandeja segundo plano")
        if not tray_available:
            card_layout.addWidget(Muted("Este sistema no tiene bandeja disponible."))

    def _page_consumo(self) -> None:
        page = self._page()
        card_layout = self._card(
            page,
            "Consumo de recursos",
            "El modo eco mide más despacio y gasta menos CPU y memoria. Las mediciones siguen "
            "siendo válidas: se actualizan menos.",
        )
        self.eco_check = QCheckBox("Modo eco: muestrear más despacio")
        self.eco_check.setChecked(self.settings.eco_mode)
        self._row(card_layout, "consumo", self.eco_check, "eco cpu memoria consumo lento")
        self.eco_hidden_check = QCheckBox("Aplicar el modo eco mientras la ventana está oculta")
        self.eco_hidden_check.setChecked(self.settings.eco_when_hidden)
        self._row(card_layout, "consumo", self.eco_hidden_check, "eco oculta bandeja ahorro")

        history_card = self._card(
            page,
            "Historial de métricas",
            "Se guardan las últimas 24 horas de cada serie (CPU, memoria, disco, red y la gráfica "
            "cuando se puede leer) para dibujar la gráfica de la página de Inicio.",
        )
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self.history_check = QCheckBox("Guardar una muestra cada")
        self.history_check.setChecked(self.settings.history_enabled)
        self.history_spin = QSpinBox()
        self.history_spin.setRange(5, 3600)
        self.history_spin.setSuffix(" s")
        self.history_spin.setValue(self.settings.history_interval_seconds)
        self.history_spin.setFixedWidth(110)
        row_layout.addWidget(self.history_check)
        row_layout.addWidget(self.history_spin)
        row_layout.addStretch(1)
        self._row(history_card, "consumo", row, "historial guardar intervalo muestras")
        muestras = self.store.count()
        tamano = self.store.size_bytes()
        history_card.addWidget(
            Muted(
                f"Historial actual: {format_integer(muestras)} muestras ({format_bytes(tamano)}) en "
                f"{self.store.path.name}."
            )
        )

    def _page_actualizaciones(self) -> None:
        page = self._page()
        card_layout = self._card(
            page,
            "Actualizaciones",
            "La comprobación automática solo avisa (como mucho, una vez al día). Descargar e "
            "instalar es siempre una decisión tuya, y todo se verifica con su SHA-256.",
        )
        self.updates_check = QCheckBox("Buscar actualizaciones al arrancar")
        self.updates_check.setChecked(self.settings.check_updates)
        self._row(card_layout, "actualizaciones", self.updates_check, "actualizaciones arrancar buscar versión nueva")
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        self.updates_button = QPushButton("Buscar actualizaciones")
        self.updates_button.setObjectName("Ghost")
        self.updates_button.clicked.connect(self._search_updates)
        row_layout.addWidget(self.updates_button)
        # Salida de emergencia: si la consulta no puede llegar a GitHub (sin red, un
        # cortafuegos, una copia antigua sin https…), el navegador sí llega y aquí se puede
        # ver qué versiones hay. Sin esto, un fallo de red dejaba la tarjeta en un callejón.
        self.releases_button = QPushButton("Ver las publicaciones")
        self.releases_button.setObjectName("Ghost")
        self.releases_button.setToolTip("Abre la página de versiones del proyecto en el navegador")
        self.releases_button.clicked.connect(self._open_releases)
        row_layout.addWidget(self.releases_button)
        row_layout.addStretch(1)
        card_layout.addWidget(row)
        self.updates_status = Muted(f"Versión instalada: {APP_VERSION}")
        self.updates_status.setWordWrap(True)
        self.updates_status.setAccessibleName("Estado de las actualizaciones")
        card_layout.addWidget(self.updates_status)
        if self.updates is None:
            self.updates_button.setEnabled(False)
            self.updates_button.setToolTip("Sin controlador de actualizaciones (en las pruebas).")
        else:
            # El diálogo escucha al controlador: así el botón puede contar qué pasó
            # (al día, versión nueva o error) sin bloquear la ventana mientras se consulta.
            self.updates.status.connect(self.updates_status.setText)
            self.updates.checked.connect(self._on_checked)
            self.updates.failed.connect(self._on_failed)

    def _footer(self) -> QWidget:
        footer = QWidget()
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.version_label = Muted(f"{APP_VERSION}")
        layout.addWidget(self.version_label)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.button(QDialogButtonBox.Save).setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        return footer

    # --- Navegación y búsqueda ------------------------------------------
    def show_section(self, key: str) -> None:
        """Enseña una sección (y marca su botón)."""
        indice = next((i for i, (name, _, _) in enumerate(SECTIONS) if name == key), 0)
        self.stack.setCurrentIndex(indice)
        for name, button in self._buttons.items():
            button.setChecked(name == key)

    def current_section(self) -> str:
        indice = self.stack.currentIndex()
        return SECTIONS[indice][0] if 0 <= indice < len(SECTIONS) else SECTIONS[0][0]

    def _filter(self, text: str) -> None:
        """Deja a la vista solo lo que coincide con la búsqueda.

        Se busca primero por **principio de palabra** («tema» encuentra «tema», no
        «sistema»), que es lo que espera quien escribe; si así no hay nada, se busca
        dentro de las palabras («istorial» encuentra «historial») en vez de dejar la
        pantalla vacía.
        """
        buscado = text.strip().lower()
        if not buscado:
            for fila, _, _ in self._rows:
                fila.setVisible(True)
            self.no_results.setVisible(False)
            return
        coincidencias = {i for i, (_, _, claves) in enumerate(self._rows) if buscado in claves.split()}
        if not coincidencias:
            coincidencias = {i for i, (_, _, claves) in enumerate(self._rows) if buscado in claves}
        encontrados = 0
        primera: str | None = None
        for i, (fila, seccion, _) in enumerate(self._rows):
            coincide = i in coincidencias
            fila.setVisible(coincide)
            if coincide:
                encontrados += 1
                primera = primera or seccion
        self.no_results.setVisible(encontrados == 0)
        if primera is not None:
            self.show_section(primera)

    # ------------------------------------------------------------------
    @property
    def theme_mode(self) -> str:
        """Modo de apariencia elegido en el diálogo («auto», «dark» o «light»)."""
        return str(self.theme_combo.currentData() or "auto")

    def _update_theme_note(self) -> None:
        """Explica qué supone el modo elegido, mirando cómo está Windows."""
        self.theme_note.setText(describe_mode(self.theme_mode))

    def apply(self) -> AlertConfig:
        """Guarda los valores en las preferencias y devuelve la configuración."""
        config = AlertConfig(
            enabled=self.enabled_check.isChecked(),
            sustained_samples=self.sustained_spin.value(),
            cooldown_minutes=self.cooldown_spin.value(),
        )
        for metric, spin in self.spins.items():
            config.set_threshold(metric, spin.value())
        self.config = config

        self.settings.alert_config = config
        self.settings.theme_mode = self.theme_mode
        self.settings.tray_enabled = self.tray_check.isChecked()
        self.settings.notify_on_alert = self.notify_check.isChecked()
        self.settings.close_to_tray = self.close_check.isChecked() and self.tray_check.isChecked()
        self.settings.eco_mode = self.eco_check.isChecked()
        self.settings.eco_when_hidden = self.eco_hidden_check.isChecked()
        self.settings.insights_enabled = self.insights_check.isChecked()
        self.settings.history_enabled = self.history_check.isChecked()
        self.settings.history_interval_seconds = self.history_spin.value()
        self.settings.check_updates = self.updates_check.isChecked()
        self.settings.sync()
        return config

    # ------------------------------------------------------------------
    def _search_updates(self) -> None:
        """Pide una comprobación de actualizaciones (sin bloquear la ventana)."""
        if self.updates is None:
            return
        self.updates_status.setText("Buscando actualizaciones…")
        self.updates.check_now()

    def _open_releases(self) -> None:
        """Abre en el navegador la lista de versiones publicadas."""
        QDesktopServices.openUrl(QUrl(actualizador.RELEASES_PAGE))

    def _on_checked(self, release) -> None:
        """Resultado de una comprobación pedida a mano."""
        if release is None:
            self.updates_status.setText(
                f"Ya tienes la última versión ({APP_VERSION}). Se vuelve a comprobar dentro de "
                "un día."
            )

    def _on_failed(self, message: str) -> None:
        self.updates_status.setText(message)

    def accept(self) -> None:  # noqa: D102 (Qt)
        # Guardar **y cerrar**: antes se guardaba sin cerrar el diálogo, y parecía que el
        # botón no hacía nada (el usuario tenía que pulsar la cruz para salir).
        self.apply()
        super().accept()
