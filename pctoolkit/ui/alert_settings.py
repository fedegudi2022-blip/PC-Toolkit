"""Diálogo de ajustes: umbrales, apariencia, ventana, historial y actualizaciones.

Diseño: barra lateral con las cinco secciones, un buscador que deja solo las opciones que
coinciden —y dice en qué secciones están las demás— y el contenido repartido en tarjetas por
tema, cada una con su explicación. Se conservan los mismos nombres de widget (`theme_combo`,
`enabled_check`, `updates_button`…) porque la suite y el resto de la aplicación cuentan con
ellos.

Lo que cambia respecto del diálogo anterior, y por qué:

* **Cada opción se explica donde se ve.** La explicación estaba en el `toolTip`: había que
  pasar el ratón por encima de cada fila para saber qué hacía. Ahora va debajo del nombre, en
  tono apagado, y el toolTip se mantiene para quien lo busque ahí.
* **Los umbrales enseñan lo que marca el equipo ahora.** Un «90 %» no dice nada por sí solo:
  al lado va la lectura del momento, y en ámbar cuando el equipo ya está por encima del umbral
  que se acaba de escribir. La lectura llega de la ventana (la muestra que ya tiene) y, si no
  la hay, se lee de `psutil` al abrir.
* **Los ajustes tienen valores recomendados.** Un botón devuelve todo a como viene de fábrica
  sin guardar nada; se ve qué cambiaría y se confirma con «Guardar».
* **Se sabe si hay cambios sin guardar.** El pie lo dice, y «Restaurar» deja de estar activo
  cuando ya está todo como viene de fábrica.
* **El tamaño de la ventana se puede devolver al de fábrica** (es un ajuste que no tiene
  control visible porque se decide usando la aplicación).

Los valores se guardan con `apply()`, que escribe en las preferencias de Windows
(HKCU\\Software\\PCToolkit) y deja la configuración de alertas lista para el motor.
"""

from __future__ import annotations

import psutil
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAccessible, QDesktopServices, QKeySequence, QShortcut
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
from ..core.appearance import MODE_DARK, MODE_LABELS, MODES, describe_mode
from ..core.config import APP_VERSION, COLORS, format_bytes, format_integer
from ..core.history import DEFAULT_INTERVAL_SECONDS, HistoryStore
from ..core.metrics import system_drive
from ..core.settings import AppSettings
from . import accessibility, icons
from .widgets import Card, Chip, IconBadge, Muted, SectionTitle, hline

# Secciones del diálogo: identificador, título, explicación y icono de la barra lateral.
SECTIONS = (
    ("alertas", "Alertas y avisos", "Cuándo avisar de que el equipo va justo.", "warn"),
    ("apariencia", "Apariencia", "Cómo se ve la aplicación y cómo se abre la ventana.", "theme"),
    ("ventana", "Ventana y bandeja", "Qué hace al cerrar y qué avisa por notificación.", "window"),
    ("consumo", "Consumo e historial", "Cuánto mide y cuánto guarda.", "cpu"),
    ("actualizaciones", "Actualizaciones", "Si busca versiones nuevas por su cuenta.", "download"),
)

# Lo que viene de fábrica: es a lo que devuelve «Restaurar los recomendados». Los umbrales y
# los tiempos se preguntan a `AlertConfig()`, que es quien los define de verdad, para no tener
# los mismos números escritos en dos sitios que se puedan separar.
_FABRICA = AlertConfig()
RECOMENDADOS: dict[str, object] = {
    "alertas": _FABRICA.enabled,
    "avisos_portada": True,
    "sostenido": _FABRICA.sustained_samples,
    "enfriamiento": _FABRICA.cooldown_minutes,
    "tema": MODE_DARK,
    "bandeja": True,
    "notificar": True,
    "cerrar_a_bandeja": True,
    "eco": True,
    "eco_oculto": True,
    "avisos_historial": True,
    "intervalo": DEFAULT_INTERVAL_SECONDS,
    "actualizaciones": True,
    "ventana_de_fabrica": False,
    **{f"umbral_{metric}": int(round(_FABRICA.threshold_for(metric))) for metric in (METRIC_CPU, METRIC_RAM, METRIC_DISK)},
}

# Lo que se dice de cada umbral, y con qué se compara la lectura de ahora.
METRIC_ROWS = (
    (METRIC_CPU, "Umbral de CPU", "Porcentaje de uso del procesador a partir del cual se avisa."),
    (METRIC_RAM, "Umbral de memoria RAM", "Porcentaje de memoria en uso a partir del cual se avisa."),
    (METRIC_DISK, "Umbral del disco del sistema", "Porcentaje de ocupación del disco donde está Windows."),
)


def _lecturas(snapshot=None) -> dict[str, float]:
    """Qué marca el equipo ahora mismo, en porcentaje, por métrica.

    Se aprovecha la muestra que ya tiene la ventana (no cuesta nada y es la que el usuario está
    viendo en la portada); si no hay ninguna —el diálogo se abre desde la bandeja antes de la
    primera muestra, o desde las pruebas—, se lee de `psutil` una vez. Sin datos se devuelve un
    diccionario vacío y el umbral se queda sin lectura al lado, que es mejor que enseñar un cero
    que no es verdad.
    """
    if snapshot is not None:
        return {
            METRIC_CPU: float(getattr(snapshot, "cpu_percent", 0.0)),
            METRIC_RAM: float(getattr(snapshot, "ram_percent", 0.0)),
            METRIC_DISK: float(getattr(snapshot, "disk_percent", 0.0)),
        }
    try:
        return {
            METRIC_CPU: float(psutil.cpu_percent(interval=None)),
            METRIC_RAM: float(psutil.virtual_memory().percent),
            METRIC_DISK: float(psutil.disk_usage(system_drive()).percent),
        }
    except (OSError, AttributeError, ValueError):  # pragma: no cover - sin contadores
        return {}


class SettingsRow(QWidget):
    """Una fila del diálogo: nombre y explicación a la izquierda, control a la derecha."""

    def __init__(
        self,
        label: str,
        control: QWidget,
        help_text: str = "",
        parent: QWidget | None = None,
        extras: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        textos = QVBoxLayout()
        textos.setSpacing(2)
        self.label = QLabel(label)
        self.label.setWordWrap(True)
        textos.addWidget(self.label)
        # La explicación se ve, no se esconde en un toolTip: es lo que hace que el diálogo se
        # entienda sin pasar el ratón por cada fila. El toolTip se deja igualmente, porque
        # quien ya lo buscaba ahí no debería perderlo.
        if help_text:
            self.help_label = QLabel(help_text)
            self.help_label.setObjectName("Dim")
            self.help_label.setWordWrap(True)
            textos.addWidget(self.help_label)
            self.setToolTip(help_text)
            control.setAccessibleDescription(help_text)
        else:
            self.help_label = None
        layout.addLayout(textos, 1)

        # Lo que acompaña al control antes de él (la lectura de ahora, por ejemplo): va pegado
        # al control, y así el ojo lee «ahora 23 % → umbral 90 %» de un tirón.
        if extras is not None:
            layout.addWidget(extras, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        control.setAccessibleName(label)


class CheckRow(QWidget):
    """Una opción que se marca o se desmarca, con su explicación debajo."""

    def __init__(self, control: QCheckBox, help_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(control)
        self.help_label = None
        if help_text:
            self.help_label = Muted(help_text)
            self.help_label.setWordWrap(True)
            # Sangría a la altura del texto de la casilla, para que se lea como su explicación.
            margen = self.help_label.contentsMargins()
            self.help_label.setContentsMargins(22 + margen.left(), margen.top(), margen.right(), margen.bottom())
            layout.addWidget(self.help_label)
            control.setToolTip(help_text)
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
        snapshot=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.config = settings.alert_config
        #: Controlador de actualizaciones de la ventana (puede no haberlo, en las pruebas).
        self.updates = updates
        #: Última muestra del equipo, para enseñar la lectura al lado de cada umbral.
        self.lecturas = _lecturas(snapshot)
        self._rows: list[tuple[QWidget, str, str]] = []  # (fila, sección, texto para buscar)
        self._buttons: dict[str, QPushButton] = {}
        self._reset_geometry = False  # se pide devolver la ventana a su tamaño de fábrica
        self.setWindowTitle("Ajustes y alertas")
        self.setMinimumSize(800, 580)
        self.resize(860, 640)
        self._build(tray_available)
        # Lo que había al abrir: con esto se sabe si hay cambios sin guardar (y «Restaurar»
        # vuelve a los valores de fábrica, que son otros).
        self._inicial = self.values()
        # Cualquier control vuelve a mirar si hay algo sin guardar: es lo que alimenta el aviso
        # del pie y el botón de restaurar, y se conecta aquí, de una vez, en vez de en cada
        # opción (añadir una nueva no obliga a acordarse de conectarla).
        for control in self.findChildren(QWidget):
            if isinstance(control, QCheckBox):
                control.toggled.connect(self._refresh_dirty)
            elif isinstance(control, QSpinBox):
                control.valueChanged.connect(self._refresh_dirty)
            elif isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._refresh_dirty)
        self._refresh_dirty()
        # Ctrl+F lleva al buscador, como en cualquier ventana con uno.
        QShortcut(QKeySequence.Find, self, self.search.setFocus)

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
        head.addWidget(IconBadge("gear", 38, 20, COLORS["accent_glow"]))
        textos = QVBoxLayout()
        textos.setSpacing(2)
        title = QLabel("Ajustes y alertas")
        title.setObjectName("PageTitle")
        accessibility.set_role(title, QAccessible.Role.Heading)
        subtitle = QLabel(
            "Avisos por umbral, apariencia, bandeja, consumo e historial. Todo se guarda en "
            "tus preferencias, sin permisos de administrador."
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        textos.addWidget(title)
        textos.addWidget(subtitle)
        head.addLayout(textos, 1)
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
        for key, title, _description, icon_name in SECTIONS:
            button = QPushButton(f"  {title}")
            button.setObjectName("SidebarItem")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(38)
            # El icono lleva el mismo tono que el texto del botón y se dibuja con la paleta
            # activa: la barra lateral se lee de un vistazo por la forma antes que por el texto.
            button.setIcon(icons.icon(icon_name, 16))
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

        # Dónde están las coincidencias. El buscador deja a la vista lo que coincide en todas
        # las secciones, pero solo se puede mirar una sección a la vez: sin esto, encontrar
        # «historial» en la primera y no ver los dos aciertos de la otra parecía un fallo.
        self.matches_box = QWidget()
        self.matches_row = QHBoxLayout(self.matches_box)
        self.matches_row.setContentsMargins(0, 0, 0, 0)
        self.matches_row.setSpacing(6)
        self.matches_box.setVisible(False)
        right_layout.addWidget(self.matches_box)

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

    def _check_row(self, card_layout, section: str, check: QCheckBox, help_text: str, keywords: str) -> None:
        """Añade una opción de casilla con su explicación, ya apuntada para el buscador."""
        self._row(card_layout, section, CheckRow(check, help_text), keywords)

    def _page_alertas(self) -> None:
        page = self._page()

        # 1. Qué se vigila.
        card = self._card(
            page,
            "Qué se vigila",
            "PC Toolkit mira el equipo cada segundo y avisa cuando una métrica se mantiene por "
            "encima del umbral, no cuando da un pico suelto.",
        )
        self.enabled_check = QCheckBox("Avisar cuando se supere un umbral")
        # Con su estado de verdad: sin esto la casilla salía siempre desmarcada y guardar desde
        # el diálogo apagaba las alertas sin que nadie lo hubiera pedido.
        self.enabled_check.setChecked(self.config.enabled)
        self._check_row(
            card,
            "alertas",
            self.enabled_check,
            "La aplicación sigue midiendo y dibujando aunque esté desactivado; lo que no hace "
            "es avisar.",
            "activar desactivar avisos alertas umbral",
        )
        # Los avisos propios de la portada: unen lo que la aplicación ya mide (CPU sostenida,
        # disco, programas de inicio y limpieza) en frases concretas que se pueden descartar.
        self.insights_check = QCheckBox("Mostrar avisos propios en la portada")
        self.insights_check.setChecked(self.settings.insights_enabled)
        self._check_row(
            card,
            "alertas",
            self.insights_check,
            "Recomendaciones sacadas de tus propios datos («este programa consume el 40 % de "
            "CPU», «quedan 4 GB libres», «tienes 12 programas de inicio pesados»). Cada una se "
            "puede descartar desde la portada.",
            "avisos inteligentes recomendaciones portada consejos descartar",
        )

        # 2. Los umbrales, con la lectura de ahora al lado.
        card = self._card(
            page,
            "Umbrales",
            "El porcentaje a partir del cual se avisa. Al lado de cada uno está lo que marca el "
            "equipo en este momento, y se pone en ámbar cuando ya lo supera.",
        )
        self.spins: dict[str, QSpinBox] = {}
        self.reading_chips: dict[str, Chip] = {}
        for metric, label, help_text in METRIC_ROWS:
            spin = QSpinBox()
            spin.setRange(10, 100)
            spin.setSuffix(" %")
            spin.setValue(int(round(self.config.threshold_for(metric))))
            spin.setFixedWidth(110)
            spin.valueChanged.connect(lambda _=0, name=metric: self._refresh_reading(name))
            self.spins[metric] = spin
            chip = Chip("", "text_muted")
            chip.setAccessibleName(f"Lectura actual de {label}")
            self.reading_chips[metric] = chip
            fila = SettingsRow(label, spin, help_text, extras=chip)
            self._row(card, "alertas", fila, f"umbral {label.lower()} aviso ahora")
        self._refresh_readings()

        # 3. Cuándo avisar.
        card = self._card(
            page,
            "Cuándo avisar",
            "Un pico de un segundo no es un problema: se avisa de lo que se sostiene, y no más a "
            "menudo de lo que se pide.",
        )
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
            card,
            "alertas",
            SettingsRow(
                "Sostenido durante",
                self.sustained_spin,
                "Cuántas medidas seguidas tienen que superar el umbral antes de avisar.",
            ),
            "sostenido muestras seguidas aviso tiempo",
        )
        self._row(
            card,
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
        card = self._card(
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
        self._row(
            card,
            "apariencia",
            SettingsRow("Modo de apariencia", self.theme_combo),
            "tema apariencia claro oscuro modo",
        )
        self.theme_note = Muted(describe_mode(self.settings.theme_mode))
        self.theme_note.setWordWrap(True)
        self.theme_note.setAccessibleName("Qué hace el modo elegido")
        card.addWidget(self.theme_note)

        # El tamaño y el sitio de la ventana se apuntan solos al cerrarla: es un ajuste que no
        # tiene control visible (se decide usando la aplicación), así que lo que se ofrece aquí
        # es volver al de fábrica.
        card = self._card(
            page,
            "Ventana",
            "Lo que se recuerda solo: tamaño, sitio, pantalla y si estaba maximizada.",
        )
        self.geometry_reset_button = QPushButton("Abrir con el tamaño de fábrica")
        self.geometry_reset_button.setObjectName("Ghost")
        self.geometry_reset_button.clicked.connect(self._reset_window_geometry)
        fila = SettingsRow(
            "Tamaño y posición guardados",
            self.geometry_reset_button,
            "Se apuntan al cerrar la ventana y se recuperan al abrirla, en la misma pantalla. "
            "Este botón los olvida para que la próxima vez se abra como la primera.",
        )
        self._row(
            card,
            "apariencia",
            fila,
            "ventana tamaño sitio posición maximizada recordar geometría pantalla",
        )
        self.geometry_note = Muted(self._geometry_note())
        self.geometry_note.setWordWrap(True)
        card.addWidget(self.geometry_note)

    def _page_ventana(self, tray_available: bool) -> None:
        page = self._page()
        card = self._card(
            page,
            "Ventana, bandeja y notificaciones",
            "Qué pasa al cerrar la ventana y por dónde avisa la aplicación.",
        )
        self.tray_check = QCheckBox("Mostrar el icono en la bandeja del sistema")
        self.tray_check.setChecked(self.settings.tray_enabled and tray_available)
        self.tray_check.setEnabled(tray_available)
        self._check_row(
            card,
            "ventana",
            self.tray_check,
            "Área de notificaciones, junto al reloj. Desde ahí se abre la ventana, se libera "
            "memoria y se sale del programa.",
            "bandeja icono notificaciones reloj área",
        )
        self.notify_check = QCheckBox("Avisar con una notificación al superar un umbral")
        self.notify_check.setChecked(self.settings.notify_on_alert)
        self._check_row(
            card,
            "ventana",
            self.notify_check,
            "La notificación sale por la bandeja de Windows; el aviso también queda en la "
            "ventana.",
            "notificación aviso emergente bandeja",
        )
        self.close_check = QCheckBox("Al cerrar la ventana, seguir en la bandeja")
        self.close_check.setChecked(self.settings.close_to_tray)
        self.close_check.setEnabled(tray_available)
        self._check_row(
            card,
            "ventana",
            self.close_check,
            "Cerrar solo esconde la ventana: las mediciones y los avisos siguen. Para salir del "
            "todo, «Salir» en el menú de la bandeja.",
            "cerrar ventana bandeja segundo plano salir",
        )
        if not tray_available:
            card.addWidget(Muted("Este sistema no tiene bandeja disponible."))

    def _page_consumo(self) -> None:
        page = self._page()
        card = self._card(
            page,
            "Consumo de recursos",
            "El modo eco mide más despacio y gasta menos CPU y memoria. Las mediciones siguen "
            "siendo válidas: se actualizan menos.",
        )
        self.eco_check = QCheckBox("Modo eco: muestrear más despacio")
        self.eco_check.setChecked(self.settings.eco_mode)
        self._check_row(
            card,
            "consumo",
            self.eco_check,
            "Un muestreo cada 2 s en vez de cada segundo, y sin recorrer la lista de procesos.",
            "eco cpu memoria consumo lento ahorro",
        )
        self.eco_hidden_check = QCheckBox("Aplicar el modo eco mientras la ventana está oculta")
        self.eco_hidden_check.setChecked(self.settings.eco_when_hidden)
        self._check_row(
            card,
            "consumo",
            self.eco_hidden_check,
            "Con la ventana en la bandeja no hay nada que repintar: ahí el ahorro se nota más.",
            "eco oculta bandeja ahorro",
        )

        card = self._card(
            page,
            "Historial de métricas",
            "Se guardan las últimas 24 horas de cada serie (CPU, memoria, disco, red y la "
            "gráfica cuando se puede leer) para dibujar la gráfica de la página de Inicio.",
        )
        fila = QWidget()
        fila_layout = QHBoxLayout(fila)
        fila_layout.setContentsMargins(0, 0, 0, 0)
        fila_layout.setSpacing(8)
        self.history_check = QCheckBox("Guardar una muestra cada")
        self.history_check.setChecked(self.settings.history_enabled)
        self.history_spin = QSpinBox()
        self.history_spin.setRange(5, 3600)
        self.history_spin.setSuffix(" s")
        self.history_spin.setValue(self.settings.history_interval_seconds)
        self.history_spin.setFixedWidth(110)
        fila_layout.addWidget(self.history_check)
        fila_layout.addWidget(self.history_spin)
        fila_layout.addStretch(1)
        self._row(card, "consumo", fila, "historial guardar intervalo muestras segundos")
        muestras = self.store.count()
        tamano = self.store.size_bytes()
        self.history_note = Muted(
            f"Historial actual: {format_integer(muestras)} muestras ({format_bytes(tamano)}) en "
            f"{self.store.path.name}."
        )
        self.history_note.setWordWrap(True)
        card.addWidget(self.history_note)

    def _page_actualizaciones(self) -> None:
        page = self._page()
        card = self._card(
            page,
            "Actualizaciones",
            "La comprobación automática solo avisa (como mucho, una vez al día). Descargar e "
            "instalar es siempre una decisión tuya, y todo se verifica con su SHA-256.",
        )
        self.updates_check = QCheckBox("Buscar actualizaciones al arrancar")
        self.updates_check.setChecked(self.settings.check_updates)
        self._check_row(
            card,
            "actualizaciones",
            self.updates_check,
            "La consulta va en segundo plano y no retrasa el arranque.",
            "actualizaciones arrancar buscar versión nueva",
        )
        fila = QWidget()
        fila_layout = QHBoxLayout(fila)
        fila_layout.setContentsMargins(0, 0, 0, 0)
        fila_layout.setSpacing(10)
        self.updates_button = QPushButton("Buscar actualizaciones")
        self.updates_button.setObjectName("Ghost")
        self.updates_button.clicked.connect(self._search_updates)
        fila_layout.addWidget(self.updates_button)
        # Salida de emergencia: si la consulta no puede llegar a GitHub (sin red, un
        # cortafuegos, una copia antigua sin https…), el navegador sí llega y aquí se puede
        # ver qué versiones hay. Sin esto, un fallo de red dejaba la tarjeta en un callejón.
        self.releases_button = QPushButton("Ver las publicaciones")
        self.releases_button.setObjectName("Ghost")
        self.releases_button.setToolTip("Abre la página de versiones del proyecto en el navegador")
        self.releases_button.clicked.connect(self._open_releases)
        fila_layout.addWidget(self.releases_button)
        fila_layout.addStretch(1)
        card.addWidget(fila)
        self.updates_status = Muted(f"Versión instalada: {APP_VERSION}")
        self.updates_status.setWordWrap(True)
        self.updates_status.setAccessibleName("Estado de las actualizaciones")
        card.addWidget(self.updates_status)
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
        # Volver a lo recomendado es una decisión de un momento: se prueba, se ve qué cambia y
        # se confirma con «Guardar» (o se descarta cerrando el diálogo).
        self.restore_button = QPushButton("Restaurar recomendados")
        self.restore_button.setObjectName("Ghost")
        self.restore_button.setToolTip("Devuelve todas las opciones a como vienen de fábrica, sin guardar")
        self.restore_button.clicked.connect(self.restore_recommended)
        layout.addWidget(self.restore_button)
        # Si hay cambios sin guardar se dice aquí: cerrar con la cruz los descarta, y eso no
        # debería pillar a nadie por sorpresa.
        self.dirty_label = Muted("")
        layout.addWidget(self.dirty_label)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.button(QDialogButtonBox.Save).setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        return footer

    # --- Lo que marca el equipo ------------------------------------------
    def _refresh_reading(self, metric: str) -> None:
        """Pinta la lectura de una métrica, en ámbar si ya pasa del umbral escrito."""
        chip = self.reading_chips.get(metric)
        if chip is None:
            return
        ahora = self.lecturas.get(metric)
        if ahora is None:
            chip.setText("sin lectura")
            chip.set_color("text_muted")
            return
        supera = ahora >= self.spins[metric].value()
        chip.setText(f"ahora {ahora:.0f} %")
        chip.set_color("warn" if supera else "text_muted")
        chip.setToolTip(
            "El equipo ya está por encima de este umbral: con este valor, avisaría ahora mismo."
            if supera
            else "Umbral por encima de lo que marca el equipo ahora."
        )

    def _refresh_readings(self) -> None:
        for metric in self.reading_chips:
            self._refresh_reading(metric)

    # --- Del cuadro a las preferencias ------------------------------------
    def values(self) -> dict:
        """Todo lo que deja elegir el diálogo, en un solo sitio.

        Se lee una vez al construirlo —es lo que había antes de tocar nada— y otra al guardar:
        así «¿hay cambios sin guardar?» es comparar dos diccionarios, y añadir una opción nueva
        no obliga a acordarse de nada en tres sitios distintos.
        """
        datos: dict[str, object] = {
            "alertas": self.enabled_check.isChecked(),
            "avisos_portada": self.insights_check.isChecked(),
            "sostenido": self.sustained_spin.value(),
            "enfriamiento": self.cooldown_spin.value(),
            "tema": self.theme_mode,
            "bandeja": self.tray_check.isChecked(),
            "notificar": self.notify_check.isChecked(),
            "cerrar_a_bandeja": self.close_check.isChecked() and self.tray_check.isChecked(),
            "eco": self.eco_check.isChecked(),
            "eco_oculto": self.eco_hidden_check.isChecked(),
            "avisos_historial": self.history_check.isChecked(),
            "intervalo": self.history_spin.value(),
            "actualizaciones": self.updates_check.isChecked(),
            "ventana_de_fabrica": self._reset_geometry,
        }
        for metric, spin in self.spins.items():
            datos[f"umbral_{metric}"] = spin.value()
        return datos

    def apply(self) -> AlertConfig:
        """Guarda los valores en las preferencias y devuelve la configuración."""
        datos = self.values()
        config = AlertConfig(
            enabled=bool(datos["alertas"]),
            sustained_samples=int(datos["sostenido"]),
            cooldown_minutes=int(datos["enfriamiento"]),
        )
        for metric in self.spins:
            config.set_threshold(metric, int(datos[f"umbral_{metric}"]))
        self.config = config

        self.settings.alert_config = config
        self.settings.theme_mode = str(datos["tema"])
        self.settings.tray_enabled = bool(datos["bandeja"])
        self.settings.notify_on_alert = bool(datos["notificar"])
        self.settings.close_to_tray = bool(datos["cerrar_a_bandeja"])
        self.settings.eco_mode = bool(datos["eco"])
        self.settings.eco_when_hidden = bool(datos["eco_oculto"])
        self.settings.insights_enabled = bool(datos["avisos_portada"])
        self.settings.history_enabled = bool(datos["avisos_historial"])
        self.settings.history_interval_seconds = int(datos["intervalo"])
        self.settings.check_updates = bool(datos["actualizaciones"])
        if datos["ventana_de_fabrica"]:
            # Vaciar la geometría guardada es lo que hace que la próxima vez la ventana se abra
            # con su tamaño de fábrica, centrada: la pone `_restore_window_geometry`.
            self.settings.window_geometry = ""
        self.settings.sync()
        self._inicial = datos
        self._refresh_dirty()
        return config

    def restore_recommended(self) -> None:
        """Devuelve los controles a los valores de fábrica, sin guardar."""
        self._set_values(RECOMENDADOS)
        self._refresh_dirty()

    def _set_values(self, datos: dict) -> None:
        """Pone los controles en los valores de un diccionario como el de `values()`."""
        self.enabled_check.setChecked(bool(datos["alertas"]))
        self.insights_check.setChecked(bool(datos["avisos_portada"]))
        for metric, spin in self.spins.items():
            spin.setValue(int(datos.get(f"umbral_{metric}", spin.value())))
        self.sustained_spin.setValue(int(datos["sostenido"]))
        self.cooldown_spin.setValue(int(datos["enfriamiento"]))
        indice = self.theme_combo.findData(str(datos["tema"]))
        if indice >= 0:
            self.theme_combo.setCurrentIndex(indice)
        self.tray_check.setChecked(bool(datos["bandeja"]))
        self.notify_check.setChecked(bool(datos["notificar"]))
        self.close_check.setChecked(bool(datos["cerrar_a_bandeja"]))
        self.eco_check.setChecked(bool(datos["eco"]))
        self.eco_hidden_check.setChecked(bool(datos["eco_oculto"]))
        self.history_check.setChecked(bool(datos["avisos_historial"]))
        self.history_spin.setValue(int(datos["intervalo"]))
        self.updates_check.setChecked(bool(datos["actualizaciones"]))
        self._reset_geometry = bool(datos.get("ventana_de_fabrica", False))
        self._refresh_readings()
        self._refresh_geometry()

    def _refresh_dirty(self) -> None:
        """Dice si hay cambios sin guardar y deja «Restaurar» solo cuando sirve de algo."""
        datos = self.values()
        hay_cambios = datos != self._inicial
        self.dirty_label.setText("Cambios sin guardar." if hay_cambios else "")
        self.dirty_label.setVisible(hay_cambios)
        self.restore_button.setEnabled(datos != RECOMENDADOS)

    # --- Ventana ----------------------------------------------------------
    def _geometry_note(self) -> str:
        if self._reset_geometry:
            return "Se olvidará el tamaño y el sitio guardados al guardar."
        if self.settings.window_geometry:
            return "Ya hay un tamaño y un sitio guardados; se pueden olvidar."
        return "Todavía no hay ninguno guardado: se usará el de fábrica."

    def _refresh_geometry(self) -> None:
        self.geometry_note.setText(self._geometry_note())
        self.geometry_reset_button.setEnabled(not self._reset_geometry)

    def _reset_window_geometry(self) -> None:
        self._reset_geometry = True
        self._refresh_geometry()
        self._refresh_dirty()

    # --- Navegación y búsqueda ------------------------------------------
    def show_section(self, key: str) -> None:
        """Enseña una sección (y marca su botón)."""
        indice = next((i for i, (name, *_) in enumerate(SECTIONS) if name == key), 0)
        self.stack.setCurrentIndex(indice)
        for name, button in self._buttons.items():
            button.setChecked(name == key)

    def current_section(self) -> str:
        indice = self.stack.currentIndex()
        return SECTIONS[indice][0] if 0 <= indice < len(SECTIONS) else SECTIONS[0][0]

    def _matches_by_section(self, buscado: str) -> dict[str, int]:
        """Cuántas opciones coinciden en cada sección, con la misma regla que el filtro."""
        exactas: dict[str, int] = {}
        for _fila, seccion, claves in self._rows:
            if buscado in claves.split():
                exactas[seccion] = exactas.get(seccion, 0) + 1
        if exactas:
            return exactas
        parciales: dict[str, int] = {}
        for _fila, seccion, claves in self._rows:
            if buscado in claves:
                parciales[seccion] = parciales.get(seccion, 0) + 1
        return parciales

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
            self._show_matches({})
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
        self._show_matches(self._matches_by_section(buscado) if encontrados else {})
        if primera is not None:
            self.show_section(primera)

    def _show_matches(self, por_seccion: dict[str, int]) -> None:
        """Enseña en qué secciones hay coincidencias y deja saltar a cualquiera de ellas."""
        while self.matches_row.count():
            elemento = self.matches_row.takeAt(0)
            widget = elemento.widget()
            if widget is not None:
                widget.deleteLater()
        if not por_seccion:
            self.matches_box.setVisible(False)
            return
        self.matches_box.setVisible(True)
        actual = self.current_section()
        for key, title, _descripcion, _icono in SECTIONS:
            cuantas = por_seccion.get(key, 0)
            if not cuantas:
                continue
            boton = QPushButton(f"{title} · {cuantas}")
            boton.setObjectName("Ghost")
            boton.setToolTip("Ir a esa sección")
            boton.setCursor(Qt.PointingHandCursor)
            boton.setChecked(key == actual)
            boton.clicked.connect(lambda _=False, name=key: self.show_section(name))
            self.matches_row.addWidget(boton)
        self.matches_row.addStretch(1)

    # ------------------------------------------------------------------
    @property
    def theme_mode(self) -> str:
        """Modo de apariencia elegido en el diálogo («auto», «dark» o «light»)."""
        return str(self.theme_combo.currentData() or "auto")

    def _update_theme_note(self) -> None:
        """Explica qué supone el modo elegido, mirando cómo está Windows."""
        self.theme_note.setText(describe_mode(self.theme_mode))

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
