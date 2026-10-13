"""Icono de la bandeja del sistema con el estado del equipo."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core.alerts import METRIC_CPU, METRIC_DISK, METRIC_RAM, AlertConfig, AlertEvent
from ..core.config import APP_NAME, COLORS, SURFACES, format_bytes

TRAY_SIZES = (16, 24, 32, 48, 64)

METRIC_SHORT_LABELS = {METRIC_CPU: "CPU", METRIC_RAM: "Memoria", METRIC_DISK: "Disco"}


def status_pixmap(snapshot, size: int = 32) -> QPixmap:
    """Icono con tres barras: CPU, memoria y disco, coloreadas por carga."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    radius = size * 0.22
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(COLORS["bg_alt"]))
    painter.drawRoundedRect(0, 0, size, size, radius, radius)
    painter.setBrush(QColor(COLORS["panel_alt"]))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, radius, radius)

    # Los mismos tres colores de dato que la gráfica del historial: así el icono de
    # la bandeja y la leyenda de la ventana cuentan lo mismo.
    values = [
        (getattr(snapshot, "cpu_percent", 0.0) if snapshot else 0.0, COLORS["series_cpu"]),
        (getattr(snapshot, "ram_percent", 0.0) if snapshot else 0.0, COLORS["series_ram"]),
        (getattr(snapshot, "disk_percent", 0.0) if snapshot else 0.0, COLORS["series_disk"]),
    ]
    bar_width = size * 0.18
    gap = size * 0.10
    total = len(values) * bar_width + (len(values) - 1) * gap
    start_x = (size - total) / 2.0
    bottom = size - size * 0.2
    top = size * 0.2
    track_height = bottom - top

    for index, (percent, color) in enumerate(values):
        x = start_x + index * (bar_width + gap)
        painter.setBrush(QColor(SURFACES["track"]))
        painter.drawRoundedRect(
            int(x), int(top), int(bar_width), int(track_height), bar_width / 2, bar_width / 2
        )
        ratio = max(0.0, min(100.0, float(percent))) / 100.0
        height = max(bar_width, track_height * ratio)
        painter.setBrush(QColor(_color_for(percent, color)))
        painter.drawRoundedRect(
            int(x), int(bottom - height), int(bar_width), int(height), bar_width / 2, bar_width / 2
        )
    painter.end()
    return pixmap


def _color_for(percent: float, color: str) -> str:
    if percent >= 90:
        return COLORS["bad"]
    if percent >= 75:
        return COLORS["warn"]
    return color


class TrayController(QObject):
    """Gestiona el icono, su menú y los avisos."""

    open_requested = Signal()
    quit_requested = Signal()
    settings_requested = Signal()
    memory_requested = Signal()
    maintenance_requested = Signal()

    def __init__(self, settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray: QSystemTrayIcon | None = None
        self._last_tooltip = ""
        if self.available:
            self._build()
        self.set_visible(self.settings.tray_enabled and self.available)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.tray = QSystemTrayIcon(status_pixmap(None, 32), self)
        self.tray.setToolTip(f"{APP_NAME} · iniciando…")

        menu = QMenu()
        self.open_action = menu.addAction("Abrir PC Toolkit")
        self.open_action.triggered.connect(self.open_requested.emit)
        menu.addSeparator()

        self.status_actions = {}
        for key in (METRIC_CPU, METRIC_RAM, METRIC_DISK):
            action = menu.addAction(f"{METRIC_SHORT_LABELS[key]}: —")
            action.setEnabled(False)
            self.status_actions[key] = action
        menu.addSeparator()

        # Optimización a un clic, sin abrir la ventana.
        self.memory_action = menu.addAction("Liberar memoria ahora")
        self.memory_action.triggered.connect(self.memory_requested.emit)
        self.maintenance_action = menu.addAction("Ejecutar mantenimiento ahora")
        self.maintenance_action.triggered.connect(self.maintenance_requested.emit)
        menu.addSeparator()

        self.alert_action = menu.addAction("Alertas activadas")
        self.alert_action.setCheckable(True)
        self.alert_action.setChecked(self.settings.alert_config.enabled)
        self.alert_action.toggled.connect(self._toggle_alerts)

        self.settings_action = menu.addAction("Configurar alertas…")
        self.settings_action.triggered.connect(self.settings_requested.emit)
        menu.addSeparator()

        quit_action = menu.addAction("Salir")
        quit_action.triggered.connect(self.quit_requested.emit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def _toggle_alerts(self, checked: bool) -> None:
        config = self.settings.alert_config
        config.enabled = checked
        self.settings.alert_config = config
        self.settings.sync()

    def set_alerts_enabled(self, enabled: bool) -> None:
        if self.tray is not None:
            self.alert_action.setChecked(enabled)

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self.tray is not None and self.tray.isVisible())

    def set_visible(self, visible: bool) -> None:
        if self.tray is None:
            return
        if visible:
            self.tray.show()
        else:
            self.tray.hide()

    def update_status(self, snapshot) -> None:
        """Refresca icono y menú con la última instantánea."""
        if self.tray is None:
            return
        self.tray.setIcon(status_pixmap(snapshot, 32))
        tooltip = (
            f"{APP_NAME}\n"
            f"CPU {snapshot.cpu_percent:.0f}%  ·  RAM {snapshot.ram_percent:.0f}%  ·  "
            f"Disco {snapshot.disk_percent:.0f}%\n"
            f"Salud {snapshot.health_score:.0f}/100 · ↓ {format_bytes(snapshot.net_down_rate)}/s"
        )
        if tooltip != self._last_tooltip:
            self._last_tooltip = tooltip
            self.tray.setToolTip(tooltip)
        for key, action in self.status_actions.items():
            value = {
                METRIC_CPU: snapshot.cpu_percent,
                METRIC_RAM: snapshot.ram_percent,
                METRIC_DISK: snapshot.disk_percent,
            }[key]
            action.setText(f"{METRIC_SHORT_LABELS[key]}: {value:.0f}%")

    def notify(self, title: str, message: str, warning: bool = True) -> None:
        if self.tray is None:
            return
        icon = QSystemTrayIcon.Warning if warning else QSystemTrayIcon.Information
        self.tray.showMessage(title, message, icon, 9000)

    def notify_alert(self, event: AlertEvent) -> None:
        self.notify(event.title, event.message, warning=True)

    def refresh_config(self, config: AlertConfig) -> None:
        self.set_alerts_enabled(config.enabled)
        self.set_visible(self.settings.tray_enabled and self.available)


def tray_available() -> bool:
    return QSystemTrayIcon.isSystemTrayAvailable()
