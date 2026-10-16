"""Página de inicio: salud del equipo, gauges, detalles y accesos rápidos."""

from __future__ import annotations

import psutil
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.alerts import METRIC_CPU, METRIC_DISK, METRIC_RAM, AlertConfig
from ..core.config import COLORS, format_bytes, format_integer
from ..core.history import HistoryStore
from ..core import gpu, insights as insights_core
from . import accessibility, icons, theme
from .chart import SERIES, HistoryChart
from .widgets import (
    ActionCard,
    ArcGauge,
    Card,
    HealthMeter,
    IconBadge,
    Muted,
    PageHeader,
    SectionTitle,
    hline,
    kv_row,
)


class InsightRow(Card):
    """Una fila de aviso de la portada.

    Por dentro es una tarjeta (mismo aspecto que el resto de la ventana), pero para un lector
    de pantalla es un **elemento de lista**: lleva dentro la gravedad, el título, la
    explicación y sus botones, y se lee como una fila más de la lista de avisos. Es el mismo
    criterio que siguen las filas del catálogo de ajustes de Windows.
    """

    def __init__(self, insight, parent: QWidget | None = None) -> None:
        super().__init__(parent, soft=True, name=insight.title)
        accessibility.mark(
            self,
            QAccessible.Role.ListItem,
            insight.title,
            f"{insight.severity_label}. {insight.detail}",
        )


def _speed(value: float) -> str:
    return f"{format_bytes(value)}/s" if value else "0 B/s"


class DashboardPage(QWidget):
    """Vista general del rendimiento del equipo."""

    navigate = Signal(str)
    status = Signal(str)
    export_requested = Signal()
    # Avisos propios de la portada: ver algo, descartarlo o volver a mostrar los descartados.
    # La acción y, cuando la tiene, las categorías de limpieza que señala el aviso.
    insight_action = Signal(str, object)
    insight_dismiss = Signal(object)
    insights_restore = Signal()

    def __init__(
        self,
        metrics,
        tool_launcher,
        history: HistoryStore | None = None,
        alert_config: AlertConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.metrics = metrics
        self.launch_tool = tool_launcher
        self.history = history
        self._samples: list = []
        self._alerts = alert_config if alert_config is not None else AlertConfig()
        # Muestras seguidas por encima del umbral, por métrica: la misma regla (y los
        # mismos números) con la que el motor de alertas decide cuándo avisa.
        self._streaks: dict[str, int] = {METRIC_CPU: 0, METRIC_RAM: 0, METRIC_DISK: 0}
        self._net_peak = 0.0
        self.chips: dict[str, QLabel] = {}
        self.gpu_rows: dict[str, QLabel] = {}
        self.insight_rows: list[QWidget] = []
        self._build()
        self.reload_history()
        self._sync_threshold_marks()

    # ------------------------------------------------------------------
    def set_alert_config(self, config: AlertConfig) -> None:
        """Umbrales vigentes (los cambia el diálogo de «Ajustes y alertas»)."""
        self._alerts = config if config is not None else AlertConfig()
        self._sync_threshold_marks()

    def _sync_threshold_marks(self) -> None:
        """Marca en cada medidor el umbral de aviso que tenga configurado."""
        if not self.chips:
            return  # todavía no se ha construido la interfaz
        for metric, medidor in (
            (METRIC_CPU, self.cpu_gauge),
            (METRIC_RAM, self.ram_gauge),
            (METRIC_DISK, self.disk_gauge),
        ):
            medidor.set_threshold(self._alerts.threshold_for(metric))

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

        # --- Encabezado ---------------------------------------------------
        header = PageHeader("Bienvenido de nuevo", "¿Cómo está funcionando tu PC hoy?")
        refresh = QPushButton("Actualizar ahora")
        refresh.setIcon(icons.icon("restore", 16))
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self._refresh)
        header.add_action(refresh)
        export = QPushButton("Exportar informe")
        export.setIcon(icons.icon("report", 16, COLORS["on_accent"]))
        export.setObjectName("Primary")
        export.clicked.connect(self.export_requested.emit)
        header.add_action(export)
        layout.addWidget(header)

        # --- Fila superior: salud + gauges ---------------------------------
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(self._health_card())

        grid = QGridLayout()
        grid.setSpacing(16)
        self.cpu_card, self.cpu_gauge = self._metric_card(
            "CPU", "cpu", "Rendimiento del procesador", "Ver detalle", "series_cpu"
        )
        self.ram_card, self.ram_gauge = self._metric_card(
            "Memoria", "memory", "Uso de memoria RAM", "Ver detalle", "series_ram"
        )
        self.disk_card, self.disk_gauge = self._metric_card(
            "Almacenamiento", "disk", "Uso del disco del sistema", "Ver detalle", "series_disk"
        )
        self.net_card, self.net_gauge = self._metric_card(
            "Red", "network", "Tráfico de red actual", "Ver detalle", "series_net"
        )
        grid.addWidget(self.cpu_card, 0, 0)
        grid.addWidget(self.ram_card, 0, 1)
        grid.addWidget(self.disk_card, 1, 0)
        grid.addWidget(self.net_card, 1, 1)
        # La tarjeta gráfica ocupa la fila entera: necesita más sitio que un medidor
        # (lleva el nombre, el controlador y el motor que más trabaja) y así no queda
        # un hueco al lado de los cuatro medidores.
        self.gpu_card = self._gpu_card()
        grid.addWidget(self.gpu_card, 2, 0, 1, 2)
        top.addLayout(grid, 1)
        layout.addLayout(top)

        layout.addWidget(self._history_card())

        # Los avisos van justo debajo del historial: son la lectura de todo lo de arriba
        # (y de lo que la aplicación sabe por otras páginas) en una frase.
        layout.addWidget(self._insights_card())

        # --- Fila inferior: acciones + detalles -----------------------------
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(self._actions_card(), 3)
        bottom.addWidget(self._details_card(), 2)
        layout.addLayout(bottom)

        layout.addStretch(1)

    # ------------------------------------------------------------------
    def _health_card(self) -> Card:
        card = Card(name="Salud")
        card.setFixedWidth(178)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(IconBadge("heart", 30, 16, COLORS["text_muted"]))
        texts = QVBoxLayout()
        texts.setSpacing(0)
        title = QLabel("Salud")
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        texts.addWidget(title)
        top.addLayout(texts)
        top.addStretch(1)
        layout.addLayout(top)

        self.health_badge = QLabel("—")
        self.health_badge.setObjectName("HealthBadge")
        self.health_badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.health_badge)

        self.health_meter = HealthMeter()
        layout.addWidget(self.health_meter, 1)

        self.health_score = QLabel("Puntuación —")
        self.health_score.setObjectName("Muted")
        self.health_score.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.health_score)
        return card

    def _metric_card(
        self, title: str, icon_name: str, subtitle: str, button_text: str, color_key: str
    ):
        card = Card(name=title)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        # El icono y el título llevan el color de la serie de la tarjeta: el mismo del
        # medidor, la barra y la leyenda de la gráfica.
        head.addWidget(IconBadge(icon_name, 32, 17, COLORS[color_key]))
        texts = QVBoxLayout()
        texts.setSpacing(0)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        title_label.setStyleSheet(f"color: {COLORS[color_key]};")
        self_subtitle = QLabel(subtitle)
        self_subtitle.setObjectName("CardSubtitle")
        texts.addWidget(title_label)
        texts.addWidget(self_subtitle)
        head.addLayout(texts)
        head.addStretch(1)
        # El aviso por umbral va con un chip de texto: el color solo no basta ni para
        # quien no distingue colores ni para saber qué pasa de un vistazo.
        chip = QLabel()
        chip.setObjectName("HealthBadge")
        chip.setWordWrap(True)
        chip.setVisible(False)
        head.addWidget(chip)
        self.chips[color_key] = chip
        layout.addLayout(head)

        gauge = ArcGauge(title, "%", "")
        layout.addWidget(gauge)

        footer = QHBoxLayout()
        button = QPushButton(button_text)
        button.setObjectName("Ghost")
        button.clicked.connect(lambda _=False, name=title: self._open_detail(name))
        footer.addWidget(button)
        footer.addStretch(1)
        layout.addLayout(footer)
        return card, gauge

    def _gpu_card(self) -> Card:
        """Tarjeta gráfica: la ficha que `psutil` no puede dar y Windows sí.

        El medidor va con el **acento neutro** y no con un color de serie, y no es un
        descuido: la paleta D2 no tiene un quinto color de datos. Se buscó midiendo como
        mide la suite (Machado y CIEDE2000 contra las cuatro series y los tres estados) y
        en la paleta clara no hay ningún tono que cumpla, mientras que en la oscura solo
        quedaban azules que se confunden con la CPU. Un color propio saldría de pedir
        prestado el de otra serie, y eso engañaría sobre lo que significa cada color. La
        tarjeta gráfica tampoco tiene serie propia en el historial de 24 h.
        """
        card = Card(name="Gráfica")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(18)

        self.gpu_gauge = ArcGauge("Gráfica", "%", "Leyendo la tarjeta gráfica…")
        self.gpu_gauge.setFixedWidth(210)
        layout.addWidget(self.gpu_gauge)

        details = QVBoxLayout()
        details.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconBadge("chip", 32, 17, COLORS["text_muted"]))
        texts = QVBoxLayout()
        texts.setSpacing(0)
        title = QLabel("Tarjeta gráfica")
        title.setObjectName("CardTitle")
        self.gpu_state = QLabel("Leyendo la tarjeta gráfica…")
        self.gpu_state.setObjectName("CardSubtitle")
        self.gpu_state.setWordWrap(True)
        texts.addWidget(title)
        texts.addWidget(self.gpu_state)
        head.addLayout(texts)
        head.addStretch(1)
        details.addLayout(head)

        for key, label in (("controlador", "Controlador"), ("motor", "Motor más cargado")):
            row, value = kv_row(label, "—")
            self.gpu_rows[key] = value
            details.addWidget(row)
        # El cómo importa: la GPU se lee aparte del muestreo normal, así que sin esto un
        # «15 %» que no se mueve cada segundo parecería un dato viejo o roto. La cadencia y
        # el alcance **no** se escriben aquí a mano: los dice el vigía (`set_gpu_cadence`),
        # porque en eco —el ajuste de fábrica— es un minuto, y un aviso fijo de «cada 15 s»
        # sería mentira justo en el caso normal.
        self.gpu_cadence = Muted("")
        details.addWidget(self.gpu_cadence)
        self.set_gpu_cadence(gpu.USAGE_INTERVAL_MS, continua=False)
        details.addStretch(1)
        layout.addLayout(details, 1)
        return card

    def set_gpu_cadence(self, interval_ms: int, continua: bool = False) -> None:
        """Escribe cada cuánto se consulta la tarjeta, con la cadencia de verdad.

        `continua` dice que la lectura no depende de estar mirando la tarjeta (sale de los
        contadores de Windows, sin abrir ningún proceso): entonces también se consulta con
        la ventana oculta y el aviso lo tiene que decir, porque es justo lo que llena la
        serie de la GPU del historial.
        """
        segundos = max(1, int(round(int(interval_ms) / 1000)))
        if segundos < 60:
            cada = f"cada {segundos} s"
        elif segundos < 120:
            cada = "cada minuto"
        else:
            cada = f"cada {round(segundos / 60)} min"
        alcance = (
            "también con esta ventana oculta."
            if continua
            else "solo con esta ventana a la vista."
        )
        self.gpu_cadence.setText(f"Se consulta {cada}, {alcance}")

    def _history_card(self) -> Card:
        card = Card(name="Historial de las últimas 24 horas")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(SectionTitle("Historial de las últimas 24 horas"))
        self.history_info = QLabel("Recopilando datos…")
        self.history_info.setObjectName("Dim")
        head.addWidget(self.history_info)
        head.addStretch(1)

        self.legend_buttons: dict[str, QPushButton] = {}
        for key, label, color_key in SERIES:
            button = QPushButton(f"  {label}")
            button.setObjectName("Legend")
            button.setCheckable(True)
            button.setChecked(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(icons.icon_from_pixmap(icons.dot_pixmap(COLORS[color_key], 9)))
            button.toggled.connect(lambda checked, name=key: self._toggle_series(name, checked))
            self.legend_buttons[key] = button
            head.addWidget(button)
        layout.addLayout(head)

        self.chart = HistoryChart(24)
        layout.addWidget(self.chart)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.history_stats: dict[str, QLabel] = {}
        for key, label, color_key in SERIES:
            holder = QHBoxLayout()
            holder.setSpacing(6)
            dot = QLabel()
            dot.setPixmap(icons.dot_pixmap(COLORS[color_key], 8))
            holder.addWidget(dot)
            text = QLabel(f"{label}: sin datos")
            text.setObjectName("Muted")
            self.history_stats[key] = text
            holder.addWidget(text)
            stats.addLayout(holder)
        stats.addStretch(1)
        self.history_range = QLabel("")
        self.history_range.setObjectName("Dim")
        stats.addWidget(self.history_range)
        layout.addLayout(stats)
        return card

    def _toggle_series(self, key: str, checked: bool) -> None:
        self.chart.set_series_visible({key: checked})

    # --- Historial --------------------------------------------------------
    def reload_history(self) -> None:
        """Recarga las muestras guardadas y refresca la gráfica."""
        if self.history is None:
            return
        self._samples = self.history.load(24)
        self._render_history()

    def append_history_sample(self, sample) -> None:
        """Añade una muestra nueva sin volver a leer el archivo."""
        self._samples.append(sample)
        cutoff = sample.timestamp - 24 * 3600
        if self._samples and self._samples[0].timestamp < cutoff:
            self._samples = [item for item in self._samples if item.timestamp >= cutoff]
        self._render_history()

    def _render_history(self) -> None:
        self.chart.set_samples(self._samples)
        count = len(self._samples)
        if not count:
            self.history_info.setText("Sin datos todavía")
            for key, label, _color_key in SERIES:
                self.history_stats[key].setText(f"{label}: sin datos")
            self.history_range.setText("")
            return

        span_minutes = max(0.0, (self._samples[-1].timestamp - self._samples[0].timestamp) / 60.0)
        self.history_info.setText(f"{format_integer(count)} muestras")
        span = (
            f"Datos de los últimos {span_minutes:.0f} min"
            if span_minutes < 120
            else f"Datos de las últimas {span_minutes / 60:.1f} h"
        )
        self.history_range.setText(span)
        for key, label, _color_key in SERIES:
            values = [valor for valor in (getattr(sample, key, None) for sample in self._samples) if valor is not None]
            if not values:
                # La GPU solo trae dato mientras se consulta la tarjeta: sin ninguna lectura,
                # «sin datos» es la verdad (un 0 % diría que no trabajó).
                self.history_stats[key].setText(f"{label}: sin datos")
                continue
            self.history_stats[key].setText(
                f"{label}: media {sum(values) / len(values):.0f}% · máx {max(values):.0f}%"
            )

    # ------------------------------------------------------------------
    def _insights_card(self) -> Card:
        """Avisos propios: lo que la aplicación ya mide, unido y dicho en una frase."""
        card = Card(name="Qué conviene mirar")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconBadge("warn", 30, 16, COLORS["text_muted"]))
        textos = QVBoxLayout()
        textos.setSpacing(1)
        textos.addWidget(SectionTitle("Qué conviene mirar"))
        subtitulo = Muted(
            "Avisos sacados de lo que la aplicación ya mide (métricas, historial, programas de "
            "inicio y limpieza). Cada uno se puede descartar y no vuelve a molestar."
        )
        subtitulo.setWordWrap(True)
        textos.addWidget(subtitulo)
        head.addLayout(textos, 1)
        # El botón lleva nombre propio («restore_insights_button») para no tapar la señal
        # `insights_restore` de esta misma clase: una señal y un atributo con el mismo
        # nombre en el mismo objeto es un error que solo aparece al conectarla.
        self.restore_insights_button = QPushButton("Volver a mostrar los descartados")
        self.restore_insights_button.setObjectName("Ghost")
        self.restore_insights_button.setToolTip(
            "Olvida los avisos que has descartado y los evalúa otra vez"
        )
        self.restore_insights_button.setVisible(False)
        self.restore_insights_button.clicked.connect(self.insights_restore.emit)
        head.addWidget(self.restore_insights_button)
        layout.addLayout(head)

        self.insights_container = QWidget()
        self.insights_layout = QVBoxLayout(self.insights_container)
        self.insights_layout.setContentsMargins(0, 0, 0, 0)
        self.insights_layout.setSpacing(8)
        layout.addWidget(self.insights_container)

        self.insights_empty = Muted("Nada que requiera tu atención ahora mismo.")
        self.insights_empty.setWordWrap(True)
        layout.addWidget(self.insights_empty)
        self.insights_footer = Muted("")
        self.insights_footer.setWordWrap(True)
        self.insights_footer.setVisible(False)
        layout.addWidget(self.insights_footer)
        return card

    @staticmethod
    def _insight_color(severity: str) -> str:
        return {
            insights_core.SEVERITY_BAD: COLORS["bad"],
            insights_core.SEVERITY_WARN: COLORS["warn"],
        }.get(severity, COLORS["text_muted"])

    def _insight_row(self, insight) -> QWidget:
        """Una fila de aviso: gravedad, qué pasa, por qué y qué se puede hacer."""
        row = InsightRow(insight)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        color = self._insight_color(insight.severity)
        top = QHBoxLayout()
        top.setSpacing(8)
        # La gravedad va en texto además de en color: el color solo no basta para quien no
        # lo distingue, y el nombre del aviso ya lo lleva el lector de pantalla.
        etiqueta = QLabel(insight.severity_label)
        etiqueta.setStyleSheet(f"color: {color}; font-size: 11.5px; font-weight: 600;")
        top.addWidget(etiqueta)
        titulo = QLabel(insight.title)
        titulo.setStyleSheet("font-size: 13px; font-weight: 600;")
        titulo.setWordWrap(True)
        top.addWidget(titulo, 1)
        if insight.action_label:
            accion = QPushButton(insight.action_label)
            accion.setObjectName("Ghost")
            # Se lleva consigo las categorías que señala el aviso: la limpieza se abre con
            # ellas marcadas y sigue sin borrar nada hasta que se confirme.
            accion.clicked.connect(
                lambda _=False, clave=insight.action, objetivos=insight.targets: (
                    self.insight_action.emit(clave, tuple(objetivos))
                )
            )
            top.addWidget(accion)
        if insight.secondary_label:
            # Una segunda salida para el mismo aviso (mirar qué ocupa el disco, por ejemplo):
            # no libera nada, así que no marca ninguna categoría.
            segunda = QPushButton(insight.secondary_label)
            segunda.setObjectName("Ghost")
            segunda.clicked.connect(
                lambda _=False, clave=insight.secondary_action: self.insight_action.emit(clave, ())
            )
            top.addWidget(segunda)
        descartar = QPushButton("Descartar")
        descartar.setObjectName("Ghost")
        descartar.setToolTip("No volver a mostrar este aviso mientras la situación siga igual")
        descartar.clicked.connect(lambda _=False, aviso=insight: self.insight_dismiss.emit(aviso))
        top.addWidget(descartar)
        layout.addLayout(top)

        detalle = Muted(insight.detail)
        detalle.setWordWrap(True)
        layout.addWidget(detalle)
        return row

    def update_insights(self, activos, descartados: int = 0) -> None:
        """Pinta los avisos vigentes (y cuántos quedan por descartes)."""
        for fila in self.insight_rows:
            fila.setParent(None)
            fila.deleteLater()
        self.insight_rows = []

        visibles, ocultos = insights_core.visible(list(activos or []))
        for aviso in visibles:
            fila = self._insight_row(aviso)
            self.insight_rows.append(fila)
            self.insights_layout.addWidget(fila)

        self.insights_empty.setVisible(not visibles)
        if not visibles:
            self.insights_empty.setText(
                "Nada que requiera tu atención ahora mismo. "
                + (f"Tienes {descartados} aviso(s) descartado(s)." if descartados else "")
            )
        partes = []
        if ocultos:
            partes.append(f"Y {ocultos} aviso(s) más esperando.")
        if descartados:
            partes.append(f"{descartados} descartado(s) por ti.")
        self.insights_footer.setText(" ".join(partes))
        self.insights_footer.setVisible(bool(partes))
        self.restore_insights_button.setVisible(descartados > 0)

    def _actions_card(self) -> Card:
        card = Card(name="Acciones rápidas")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(SectionTitle("Acciones rápidas"))
        layout.addWidget(Muted("Atajos a las tareas más habituales de mantenimiento."))

        grid = QGridLayout()
        grid.setSpacing(12)
        actions = [
            ("analyze", "Analizar el disco", "Descubre qué ocupa tu espacio", lambda: self.navigate.emit("disk")),
            ("broom", "Optimizar mi PC", "Abre el Liberador de espacio", lambda: self.launch_tool("cleanmgr")),
            ("calendar", "Registro de tareas", "Planifica el mantenimiento", lambda: self.navigate.emit("tasks")),
            ("report", "Exportar informe", "Informe en HTML, CSV o JSON", self.export_requested.emit),
        ]
        for index, (icon_name, title, subtitle, handler) in enumerate(actions):
            action = ActionCard(icon_name, title, subtitle)
            action.clicked.connect(handler)
            grid.addWidget(action, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        return card

    def _details_card(self) -> Card:
        card = Card(name="Detalles del sistema")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(SectionTitle("Detalles del sistema"))

        self.detail_rows: dict[str, QLabel] = {}
        for key, label in (
            ("equipo", "Equipo"),
            ("sistema", "Sistema"),
            ("procesador", "Procesador"),
            ("nucleos", "Núcleos / hilos"),
            ("memoria", "Memoria RAM"),
            ("procesos", "Procesos activos"),
            ("uptime", "Encendido desde"),
            ("bateria", "Batería"),
        ):
            row, value_label = kv_row(label, "—")
            self.detail_rows[key] = value_label
            layout.addWidget(row)

        layout.addWidget(hline())
        layout.addWidget(SectionTitle("Procesos con más CPU"))
        self.process_list = QListWidget()
        self.process_list.setFixedHeight(150)
        self.process_list.setSelectionMode(QListWidget.NoSelection)
        self.process_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.process_list.setTextElideMode(Qt.ElideMiddle)
        layout.addWidget(self.process_list)
        return card

    # ------------------------------------------------------------------
    def _open_detail(self, title: str) -> None:
        # CPU y RAM se detallan con herramientas del sistema; el almacenamiento
        # se resuelve en la página del analizador de disco.
        if title == "Almacenamiento":
            self.navigate.emit("disk")
            return
        target = {"CPU": "resmon", "Memoria": "taskmgr", "Gráfica": "taskmgr", "Red": "ncpa"}.get(
            title, "taskmgr"
        )
        self.launch_tool(target)

    def _refresh(self) -> None:
        self.metrics.poll()
        self.status.emit("Métricas actualizadas.")

    # ------------------------------------------------------------------
    def update_snapshot(self, snap, system_info: dict) -> None:
        # Cada medidor lleva el color de **su** serie de datos (los mismos que usa la
        # gráfica del historial), así que la CPU se reconoce por el azul, la memoria por
        # el cian y el disco por el naranja sin mirar el título. El ámbar y el rojo
        # quedan para cuando el valor alcanza el umbral de aviso configurado.
        self._update_streaks(snap)
        cpu_acc = self._accent_for(snap.cpu_percent, "series_cpu")
        freq = f"{snap.cpu_freq_mhz / 1000:.2f} GHz" if snap.cpu_freq_mhz else "—"
        self.cpu_gauge.set_value(
            snap.cpu_percent,
            cpu_acc,
            f"{snap.cpu_cores} núcleos · {snap.cpu_threads} hilos · {freq}",
        )

        self.ram_gauge.set_value(
            snap.ram_percent,
            self._accent_for(snap.ram_percent, "series_ram"),
            f"{format_bytes(snap.ram_used)} de {format_bytes(snap.ram_total)}",
        )

        disk_subtitle = f"{format_bytes(snap.disk_free)} libres en {snap.disk_mount.rstrip(chr(92))}"
        self.disk_gauge.set_value(
            snap.disk_percent, self._accent_for(snap.disk_percent, "series_disk"), disk_subtitle
        )
        self._update_chip("series_cpu", snap.cpu_percent)
        self._update_chip("series_ram", snap.ram_percent)
        self._update_chip("series_disk", snap.disk_percent)

        throughput = snap.net_down_rate + snap.net_up_rate
        self._net_peak = max(self._net_peak, throughput)
        escala = self._net_scale()
        net_ratio = min(100.0, throughput / escala * 100.0)
        # La red no se mide en porcentaje: enseña la velocidad real, porque un «0 %» con
        # tráfico no dice nada. El abanico se llena contra la velocidad del enlace (o
        # contra el pico reciente si Windows no la declara), así que significa algo.
        self.net_gauge.set_value(
            net_ratio,
            COLORS["series_net"],
            f"↓ {_speed(snap.net_down_rate)}   ↑ {_speed(snap.net_up_rate)} · enlace {_speed(escala)}",
        )
        self.net_gauge.set_display(_speed(throughput))

        # --- Tarjeta gráfica -------------------------------------------------
        # El dato no viene del muestreo sino del vigía de la GPU, así que la tarjeta dice
        # de dónde sale y, si no hay lectura, por qué no la hay.
        if snap.gpu_percent is None:
            espera = "La última lectura no trajo datos" if self.metrics.gpu_failed else "Leyendo la tarjeta gráfica…"
            self.gpu_gauge.set_value(0.0, COLORS["accent_soft"])
            self.gpu_gauge.set_display("—", espera)
            self.gpu_state.setText(espera)
            self.gpu_rows["controlador"].setText("—")
            self.gpu_rows["motor"].setText("—")
        else:
            motor = f" · motor {snap.gpu_engine}" if snap.gpu_engine else ""
            self.gpu_gauge.set_value(snap.gpu_percent, COLORS["accent_soft"])
            self.gpu_gauge.set_display(
                f"{snap.gpu_percent:.0f} %", f"{snap.gpu_name or 'Tarjeta gráfica'}{motor}"
            )
            self.gpu_state.setText(snap.gpu_name or "Tarjeta gráfica")
            self.gpu_rows["controlador"].setText(snap.gpu_driver or "—")
            self.gpu_rows["motor"].setText(snap.gpu_engine or "—")

        score = snap.health_score
        self.health_meter.set_score(score, snap.health_label)
        self.health_badge.setText(snap.health_label)
        color = COLORS["good"] if score >= 70 else (COLORS["warn"] if score >= 45 else COLORS["bad"])
        # El relleno y el borde salen del mismo color de estado con transparencia: el
        # ayudante evita el formato de ocho dígitos, que Qt interpreta al revés.
        self.health_badge.setStyleSheet(
            f"#HealthBadge {{ background: {theme.with_alpha(color, 34)}; color: {color}; "
            f"border: 1px solid {theme.with_alpha(color, 85)}; }}"
        )
        self.health_score.setText(f"Puntuación {score:.0f}/100")

        rows = self.detail_rows
        rows["equipo"].setText(system_info.get("nombre_equipo", "—"))
        rows["sistema"].setText(f"{system_info.get('sistema', '—')} ({system_info.get('arquitectura', '')})")
        cpu_name = system_info.get("procesador", "") or "—"
        rows["procesador"].setText(cpu_name if len(cpu_name) <= 28 else cpu_name[:27] + "…")
        rows["nucleos"].setText(f"{snap.cpu_cores} / {snap.cpu_threads}")
        rows["memoria"].setText(format_bytes(snap.ram_total))
        rows["procesos"].setText(str(snap.process_count))
        rows["uptime"].setText(self._uptime(snap.uptime_seconds))
        if snap.battery_percent is None:
            rows["bateria"].setText("No disponible")
        else:
            state = "cargando" if snap.battery_plugged else "en uso"
            rows["bateria"].setText(f"{snap.battery_percent:.0f}% ({state})")

        self.process_list.clear()
        for name, cpu, mem in snap.top_processes:
            short = name if len(name) <= 22 else name[:21] + "…"
            item = QListWidgetItem(
                icons.icon("cpu", 14, COLORS["text_dim"]), f"{short}   {cpu:.0f}% · {mem:.0f} MB"
            )
            item.setToolTip(name)
            self.process_list.addItem(item)

    _METRIC_BY_SERIES = {
        "series_cpu": METRIC_CPU,
        "series_ram": METRIC_RAM,
        "series_disk": METRIC_DISK,
    }

    def _accent_for(self, percent: float, series: str = "accent") -> str:
        """Color del medidor: el de su serie, o el de aviso al pasar el umbral real.

        El umbral es el de las alertas (`AlertConfig`), el que se configura en «Ajustes
        y alertas»; aquí no hay ningún número inventado. El rojo se reserva para cuando
        la racha sostenida supera el umbral, igual que el motor de alertas: un pico
        suelto se queda en ámbar.
        """
        metric = self._METRIC_BY_SERIES.get(series)
        if metric is None:
            return COLORS[series]
        if percent >= self._alerts.threshold_for(metric):
            sostenido = self._streaks.get(metric, 0) >= max(1, self._alerts.sustained_samples)
            return COLORS["bad"] if sostenido else COLORS["warn"]
        return COLORS[series]

    def _update_streaks(self, snap) -> None:
        """Cuenta muestras seguidas por encima del umbral (misma regla que las alertas)."""
        for metric, percent in (
            (METRIC_CPU, snap.cpu_percent),
            (METRIC_RAM, snap.ram_percent),
            (METRIC_DISK, snap.disk_percent),
        ):
            if percent >= self._alerts.threshold_for(metric):
                self._streaks[metric] = self._streaks.get(metric, 0) + 1
            else:
                self._streaks[metric] = 0

    def _update_chip(self, color_key: str, percent: float) -> None:
        """Chip de texto junto al título en cuanto se alcanza el umbral de aviso."""
        chip = self.chips.get(color_key)
        metric = self._METRIC_BY_SERIES.get(color_key)
        if chip is None or metric is None:
            return
        threshold = self._alerts.threshold_for(metric)
        if percent < threshold:
            chip.setVisible(False)
            return
        sostenido = self._streaks.get(metric, 0) >= max(1, self._alerts.sustained_samples)
        color = COLORS["bad"] if sostenido else COLORS["warn"]
        chip.setText(
            f"{'Crítico' if sostenido else 'Aviso'} · {percent:.0f} % (umbral {threshold:.0f} %)"
        )
        chip.setStyleSheet(
            f"#HealthBadge {{ background: {theme.with_alpha(color, 34)}; color: {color}; "
            f"border: 1px solid {theme.with_alpha(color, 85)}; }}"
        )
        chip.setVisible(True)

    def _net_scale(self) -> float:
        """Velocidad de enlace de la red activa, en bytes por segundo.

        `psutil.net_if_stats` da la velocidad negociada en Mbps; si el sistema no la
        declara (0), se usa el pico reciente de esta sesión como escala, con un suelo de
        1 MB/s para que el abanico no se dispare con el primer paquete.
        """
        velocidad = 0.0
        try:
            for datos in psutil.net_if_stats().values():
                if datos.isup and datos.speed > 0:
                    velocidad = max(velocidad, float(datos.speed))
        except (AttributeError, OSError):
            velocidad = 0.0
        if velocidad > 0:
            return velocidad * 1000 * 1000 / 8.0
        return max(self._net_peak, 1024 * 1024)

    @staticmethod
    def _uptime(seconds: float) -> str:
        total = int(seconds)
        days, rest = divmod(total, 86400)
        hours, rest = divmod(rest, 3600)
        minutes = rest // 60
        if days:
            return f"{days} d {hours} h {minutes} min"
        if hours:
            return f"{hours} h {minutes} min"
        return f"{minutes} min"
