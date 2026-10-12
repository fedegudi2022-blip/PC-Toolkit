"""Página de inicio: salud del equipo, gauges, detalles y accesos rápidos."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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

from ..core.config import COLORS, format_bytes
from ..core.history import HistoryStore
from . import icons
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


def _speed(value: float) -> str:
    return f"{format_bytes(value)}/s" if value else "0 B/s"


class DashboardPage(QWidget):
    """Vista general del rendimiento del equipo."""

    navigate = Signal(str)
    status = Signal(str)
    export_requested = Signal()

    def __init__(
        self,
        metrics,
        tool_launcher,
        history: HistoryStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.metrics = metrics
        self.launch_tool = tool_launcher
        self.history = history
        self._samples: list = []
        self._build()
        self.reload_history()

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
            "CPU", "cpu", "Rendimiento del procesador", "Ver detalle"
        )
        self.ram_card, self.ram_gauge = self._metric_card(
            "Memoria", "memory", "Uso de memoria RAM", "Ver detalle"
        )
        self.disk_card, self.disk_gauge = self._metric_card(
            "Almacenamiento", "disk", "Uso del disco del sistema", "Ver detalle"
        )
        self.net_card, self.net_gauge = self._metric_card(
            "Red", "network", "Tráfico de red actual", "Ver detalle"
        )
        grid.addWidget(self.cpu_card, 0, 0)
        grid.addWidget(self.ram_card, 0, 1)
        grid.addWidget(self.disk_card, 1, 0)
        grid.addWidget(self.net_card, 1, 1)
        top.addLayout(grid, 1)
        layout.addLayout(top)

        layout.addWidget(self._history_card())

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

    def _metric_card(self, title: str, icon_name: str, subtitle: str, button_text: str):
        card = Card(name=title)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconBadge(icon_name, 32, 17, COLORS["text_dim"]))
        texts = QVBoxLayout()
        texts.setSpacing(0)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        self_subtitle = QLabel(subtitle)
        self_subtitle.setObjectName("CardSubtitle")
        texts.addWidget(title_label)
        texts.addWidget(self_subtitle)
        head.addLayout(texts)
        head.addStretch(1)
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
        for key, label, color in SERIES:
            button = QPushButton(f"  {label}")
            button.setObjectName("Legend")
            button.setCheckable(True)
            button.setChecked(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(icons.icon_from_pixmap(icons.dot_pixmap(color, 9)))
            button.toggled.connect(lambda checked, name=key: self._toggle_series(name, checked))
            self.legend_buttons[key] = button
            head.addWidget(button)
        layout.addLayout(head)

        self.chart = HistoryChart(24)
        layout.addWidget(self.chart)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.history_stats: dict[str, QLabel] = {}
        for key, label, color in SERIES:
            holder = QHBoxLayout()
            holder.setSpacing(6)
            dot = QLabel()
            dot.setPixmap(icons.dot_pixmap(color, 8))
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
            for key, label, _color in SERIES:
                self.history_stats[key].setText(f"{label}: sin datos")
            self.history_range.setText("")
            return

        span_minutes = max(0.0, (self._samples[-1].timestamp - self._samples[0].timestamp) / 60.0)
        self.history_info.setText(f"{count:,} muestras")
        span = (
            f"Datos de los últimos {span_minutes:.0f} min"
            if span_minutes < 120
            else f"Datos de las últimas {span_minutes / 60:.1f} h"
        )
        self.history_range.setText(span)
        for key, label, color in SERIES:
            values = [getattr(sample, key) for sample in self._samples]
            self.history_stats[key].setText(
                f"{label}: media {sum(values) / len(values):.0f}% · máx {max(values):.0f}%"
            )

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
        target = {"CPU": "resmon", "Memoria": "taskmgr", "Red": "ncpa"}.get(title, "taskmgr")
        self.launch_tool(target)

    def _refresh(self) -> None:
        self.metrics.poll()
        self.status.emit("Métricas actualizadas.")

    # ------------------------------------------------------------------
    def update_snapshot(self, snap, system_info: dict) -> None:
        cpu_acc = self._accent_for(snap.cpu_percent)
        freq = f"{snap.cpu_freq_mhz / 1000:.2f} GHz" if snap.cpu_freq_mhz else "—"
        self.cpu_gauge.set_value(
            snap.cpu_percent,
            cpu_acc,
            f"{snap.cpu_cores} núcleos · {snap.cpu_threads} hilos · {freq}",
        )

        self.ram_gauge.set_value(
            snap.ram_percent,
            self._accent_for(snap.ram_percent),
            f"{format_bytes(snap.ram_used)} de {format_bytes(snap.ram_total)}",
        )

        disk_subtitle = f"{format_bytes(snap.disk_free)} libres en {snap.disk_mount.rstrip(chr(92))}"
        self.disk_gauge.set_value(snap.disk_percent, self._accent_for(snap.disk_percent), disk_subtitle)

        throughput = snap.net_down_rate + snap.net_up_rate
        net_ratio = min(100.0, throughput / (125 * 1024 * 1024) * 100.0)  # relativo a 1 Gbps
        self.net_gauge.set_value(
            net_ratio,
            COLORS["accent"],
            f"↓ {_speed(snap.net_down_rate)}   ↑ {_speed(snap.net_up_rate)}",
        )

        score = snap.health_score
        self.health_meter.set_score(score, snap.health_label)
        self.health_badge.setText(snap.health_label)
        color = COLORS["good"] if score >= 70 else (COLORS["warn"] if score >= 45 else COLORS["bad"])
        self.health_badge.setStyleSheet(
            f"#HealthBadge {{ background: {color}22; color: {color}; "
            f"border: 1px solid {color}55; }}"
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

    def _accent_for(self, percent: float) -> str:
        if percent >= 85:
            return COLORS["bad"]
        if percent >= 65:
            return COLORS["warn"]
        return COLORS["accent"]

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
