"""Ventana principal: barra lateral, páginas y estado."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import maintenance as maintenance_core
from ..core import memory as memory_core
from ..core import tools as tool_core
from ..core.alerts import AlertConfig, AlertEngine
from ..core.config import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    COLORS,
    THEME_LIGHT,
    active_palette,
    format_bytes,
)
from ..core.history import HistoryRecorder, HistoryStore
from ..core.memory import MemoryGuard
from ..core.metrics import PROFILE_ECO, PROFILE_FULL, MetricsService
from ..core.settings import AppSettings
from ..core.tasks import TaskStore
from ..core.undo import UndoStore
from . import accessibility, icons
from .alert_settings import AlertSettingsDialog
from .dashboard import DashboardPage
from .disk_page import DiskAnalyzerPage
from .optimize_page import OptimizeMemoryPage
from .reports_page import ReportsPage
from .schedule_page import MaintenanceSchedulePage
from .startup_page import StartupPage
from .tasks_page import TasksPage
from .tools_page import ToolsPage
from .theme import apply_theme
from .tray import TrayController, tray_available
from .workers import run_in_background, shutdown_background, wait_thread

# Herramienta -> categoría del registro de mantenimiento
AUTO_TASK_CATEGORY = {
    "cleanmgr": "Limpieza",
    "temp": "Limpieza",
    "trash": "Limpieza",
    "temp_cleanup": "Limpieza",
    "windowsupdate": "Actualizaciones",
    "defender": "Seguridad",
    "firewall": "Seguridad",
    "restore": "Copias de seguridad",
    "dfrgui": "Rendimiento",
    "perfmon": "Rendimiento",
    "resmon": "Rendimiento",
    "taskmgr": "Rendimiento",
    "devmgmt": "Hardware",
    "diskmgmt": "Hardware",
    "msinfo32": "Hardware",
}


class SidebarButton(QPushButton):
    def __init__(self, key: str, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(f"  {text}", parent)
        self.key = key
        self.setObjectName("SidebarItem")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setIcon(icons.icon(icon_name, 17))
        self.setMinimumHeight(38)


class MainWindow(QMainWindow):
    """Contenedor principal de PC Toolkit."""

    def __init__(self, settings: AppSettings | None = None, history: HistoryStore | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — {APP_TAGLINE}")
        self.setWindowIcon(QIcon(icons.pixmap_for("cpu", 64, COLORS["accent_glow"])))
        self.resize(1320, 840)
        self.setMinimumSize(1180, 740)

        self.settings = settings or AppSettings()
        self.task_store = TaskStore(self)
        self.undo_store = UndoStore()
        self.metrics = MetricsService(parent=self)
        self.history = history or HistoryStore()
        self.recorder = HistoryRecorder(
            self.metrics,
            store=self.history,
            interval_seconds=self.settings.history_interval_seconds,
            parent=self,
        )
        self.alerts = AlertEngine(self.settings.alert_config)
        self.memory_guard = MemoryGuard(self.settings.memory_options)
        self.tray = TrayController(self.settings, parent=self)
        self._alert_history: list = []
        self._force_quit = False
        self._system_info: dict = {}
        self._memory_busy = False
        self._maintenance_busy = False
        self._replacement = None  # ventana nueva al cambiar de tema (evita que se recoja)
        self._pages: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, SidebarButton] = {}
        self._shortcuts: list[QShortcut] = []

        self._build()
        self._wire()
        if self.settings.history_enabled:
            self.recorder.attach()
        self.metrics.start()
        self.tray.update_status(self.metrics.snapshot)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._sidebar())

        separator = QFrame()
        separator.setFixedWidth(1)
        separator.setStyleSheet(f"background: {COLORS['border_soft']};")
        layout.addWidget(separator)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.dashboard = DashboardPage(self.metrics, self._launch_tool, self.history)
        self.optimize_page = OptimizeMemoryPage(self.settings)
        self.startup_page = StartupPage(self.undo_store, self._launch_tool)
        self.schedule_page = MaintenanceSchedulePage(self.settings)
        self.disk_page = DiskAnalyzerPage()
        self.tools_page = ToolsPage(self._launch_tool, self._log_tool)
        self.tasks_page = TasksPage(self.task_store)
        self.reports_page = ReportsPage(self.metrics, self.task_store, self.disk_page)

        for key, page in (
            ("home", self.dashboard),
            ("memory", self.optimize_page),
            ("startup", self.startup_page),
            ("disk", self.disk_page),
            ("tools", self.tools_page),
            ("tasks", self.tasks_page),
            ("reports", self.reports_page),
            ("schedule", self.schedule_page),
        ):
            self._pages[key] = page
            self.stack.addWidget(page)

        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.status_message = QLabel("Listo.")
        self.status_message.setObjectName("Muted")
        bar.addWidget(self.status_message, 1)
        self.alert_indicator = QLabel()
        self.alert_indicator.setObjectName("Muted")
        self.alert_indicator.setVisible(False)
        bar.addPermanentWidget(self.alert_indicator)
        self.status_metrics = QLabel()
        self.status_metrics.setObjectName("Muted")
        # Las cifras van en el texto de la etiqueta; el nombre sirve para que un
        # lector de pantalla sepa qué está leyendo al llegar aquí.
        self.status_metrics.setAccessibleName("Métricas del equipo")
        bar.addPermanentWidget(self.status_metrics)

        self.navigate("home")

    def _sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(226)
        sidebar.setStyleSheet(f"background: {COLORS['bg_alt']};")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 18)
        layout.setSpacing(8)

        brand = QLabel("PC TOOLKIT")
        brand.setObjectName("BrandLabel")
        layout.addWidget(brand)
        subtitle = QLabel("MANTENIMIENTO")
        subtitle.setObjectName("BrandSub")
        layout.addWidget(subtitle)
        layout.addSpacing(14)

        items = [
            ("home", "Inicio", "home"),
            ("memory", "Optimizar memoria", "memory"),
            ("startup", "Programas de inicio", "power"),
            ("disk", "Analizar disco", "analyze"),
            ("tools", "Herramientas", "tool"),
            ("tasks", "Tareas", "list"),
            ("reports", "Informes", "report"),
            ("schedule", "Mantenimiento", "calendar"),
        ]
        for index, (key, text, icon_name) in enumerate(items, start=1):
            button = SidebarButton(key, text, icon_name)
            button.setToolTip(f"{text}  ·  Ctrl+{index}")
            button.clicked.connect(lambda _=False, name=key: self.navigate(name))
            layout.addWidget(button)
            self._nav_buttons[key] = button
            # Atajo de teclado por página, en el mismo orden que la barra lateral.
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda name=key: self.navigate(name))
            self._shortcuts.append(shortcut)

        layout.addSpacing(10)
        tip = QLabel(
            "Consejo: «Optimizar memoria» libera la RAM reservada y «Mantenimiento» la deja "
            "programada para que Windows la limpie por su cuenta."
        )
        tip.setObjectName("Dim")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)

        self.alert_button = QPushButton("  Ajustes y alertas")
        self.alert_button.setObjectName("SidebarItem")
        self.alert_button.setCursor(Qt.PointingHandCursor)
        self.alert_button.setIcon(icons.icon("shield", 17))
        self.alert_button.setMinimumHeight(38)
        self.alert_button.setToolTip(
            "Umbrales de CPU, memoria y disco, bandeja del sistema, modo eco e historial."
            "  ·  Ctrl+9"
        )
        self.alert_button.clicked.connect(self.open_alert_settings)
        layout.addWidget(self.alert_button)
        settings_shortcut = QShortcut(QKeySequence("Ctrl+9"), self)
        settings_shortcut.activated.connect(self.open_alert_settings)
        self._shortcuts.append(settings_shortcut)

        self.auto_log_check = QCheckBox("Registrar acciones automáticamente")
        self.auto_log_check.setToolTip(
            "Añade una entrada al registro de tareas cada vez que abres una herramienta de mantenimiento."
        )
        self.auto_log_check.setChecked(self.settings.auto_log_tools)
        self.auto_log_check.toggled.connect(self._save_auto_log)
        layout.addWidget(self.auto_log_check)

        version = QLabel(f"Versión {APP_VERSION}")
        version.setObjectName("Dim")
        layout.addWidget(version)
        return sidebar

    # ------------------------------------------------------------------
    def _wire(self) -> None:
        self.metrics.sampled.connect(self._on_sample)
        self.metrics.profile_changed.connect(self._on_profile_changed)
        self.recorder.recorded.connect(self._on_history_sample)
        self.tray.open_requested.connect(self.open_from_tray)
        self.tray.quit_requested.connect(self.quit_application)
        self.tray.settings_requested.connect(self.open_alert_settings)
        self.tray.memory_requested.connect(lambda: self.release_memory_now("bandeja"))
        self.tray.maintenance_requested.connect(self.run_maintenance_now)
        self.dashboard.navigate.connect(self.navigate)
        self.dashboard.export_requested.connect(lambda: self.navigate("reports"))
        self.dashboard.status.connect(self.set_status)
        self.optimize_page.status.connect(self.set_status)
        self.startup_page.status.connect(self.set_status)
        self.optimize_page.release_requested.connect(lambda: self.release_memory_now("manual"))
        self.optimize_page.settings_changed.connect(self._on_memory_settings)
        self.schedule_page.status.connect(self.set_status)
        self.disk_page.status.connect(self.set_status)
        self.tools_page.status.connect(self.set_status)
        self.tasks_page.status.connect(self.set_status)
        self.reports_page.status.connect(self.set_status)

    @property
    def nav_shortcuts(self) -> list[QShortcut]:
        """Atajos de teclado de las páginas (Ctrl+1…Ctrl+8) y de los ajustes (Ctrl+9)."""
        return list(self._shortcuts)

    def _save_auto_log(self, checked: bool) -> None:
        self.settings.auto_log_tools = checked
        self.settings.sync()

    # --- Bandeja, alertas e historial ------------------------------------
    def open_from_tray(self) -> None:
        """Muestra y enfoca la ventana desde la bandeja."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self) -> None:
        """Sale de verdad (desde la bandeja o al desactivarla)."""
        self._force_quit = True
        self.close()

    def open_alert_settings(self) -> None:
        paleta_antes = active_palette()
        dialog = AlertSettingsDialog(self.settings, self.history, tray_available(), self)
        if dialog.exec() == AlertSettingsDialog.Accepted:
            config = dialog.config
            self.alerts.config = config
            self.alerts.reset()
            self.tray.refresh_config(config)
            self.recorder.interval_seconds = max(5, self.settings.history_interval_seconds)
            self._apply_power_profile(refresh=True)
            resumen = _describe_alert_config(config)
            paleta_nueva = apply_theme(QApplication.instance(), self.settings.theme_mode)
            if paleta_nueva != paleta_antes:
                # Los colores se leen al construir cada widget (iconos, estilos en
                # línea, gráficas), así que el tema nuevo se aplica rehaciendo la
                # ventana en vez de repintar cientos de detalles a mano.
                self.set_status(resumen)
                nueva = self.reload_theme()
                nueva.set_status(
                    resumen
                    + "  ·  Tema "
                    + ("claro." if paleta_nueva == THEME_LIGHT else "oscuro.")
                )
                return
            self.set_status(resumen)
        else:
            # Si se cancela, los controles de la bandeja vuelven a su estado real.
            self.tray.refresh_config(self.settings.alert_config)

    def reload_theme(self) -> "MainWindow":
        """Rehace la ventana con la paleta ya aplicada y devuelve la nueva.

        Se conserva el tamaño y el sitio de la ventana, y si estaba oculta (solo
        en la bandeja) la nueva también lo queda.
        """
        nueva = MainWindow(self.settings, self.history)
        nueva.setGeometry(self.geometry())
        if self.isVisible():
            nueva.show()
        self._force_quit = True  # cerrar la anterior de verdad, no a la bandeja
        self._replacement = nueva  # se mantiene viva hasta que la vieja desaparezca
        QTimer.singleShot(0, self.close)
        return nueva

    def _on_history_sample(self, sample) -> None:
        self.dashboard.append_history_sample(sample)

    def _update_current_page(self, snap) -> None:
        """Pinta la página visible con una muestra (solo la que se está viendo)."""
        current = self.stack.currentWidget()
        if current is self.dashboard:
            if not self._system_info:
                self._system_info = self.metrics.system_info()
            self.dashboard.update_snapshot(snap, self._system_info)
        elif current is self.optimize_page:
            self.optimize_page.update_snapshot(snap)

    def _on_sample(self, snap) -> None:
        """Actualiza la interfaz, la bandeja y evalúa los umbrales."""
        # Con la ventana oculta no se repinta nada: es lo que mantiene bajo el
        # consumo cuando la aplicación solo vive en la bandeja.
        if self.isVisible():
            self._update_current_page(snap)
        self.status_metrics.setText(
            f"CPU {snap.cpu_percent:.0f}%  ·  RAM {snap.ram_percent:.0f}%  ·  "
            f"Salud {snap.health_score:.0f}/100  ·  ↓ {format_bytes(snap.net_down_rate)}/s"
        )
        self.tray.update_status(snap)
        for event in self.alerts.evaluate(snap):
            self._alert_history.append(event)
            if self.settings.notify_on_alert:
                self.tray.notify_alert(event)
            self.set_status(f"Alerta: {event.label} al {event.value:.0f}%")
            self._show_alert_badge(event)
        # Liberación automática de RAM: solo si el usuario la activó y la RAM
        # lleva varias muestras por encima del umbral.
        if not self._memory_busy and self.memory_guard.observe(snap.ram_percent):
            self.release_memory_now("automatico")

    def _on_profile_changed(self, profile: str) -> None:
        eco = profile == PROFILE_ECO
        self.set_status(
            "Modo eco: muestreo lento para consumir menos recursos."
            if eco
            else "Muestreo completo activado."
        )

    def _on_memory_settings(self, options) -> None:
        self.memory_guard.options = options
        self.set_status(
            "Limpieza automática de memoria activada "
            f"(umbral {options.threshold_percent:.0f}%, cada {options.cooldown_minutes} min)."
            if options.auto_enabled
            else "Limpieza automática de memoria desactivada."
        )

    # --- Memoria y mantenimiento -----------------------------------------
    def release_memory_now(self, trigger: str = "manual") -> None:
        """Libera memoria RAM en segundo plano (a mano, desde la bandeja o por umbral)."""
        if self._memory_busy:
            return
        self._memory_busy = True
        self.optimize_page.set_busy(True)
        options = self.settings.memory_options
        run_in_background(
            self,
            lambda: memory_core.release_memory(options, trigger=trigger),
            self._on_memory_released,
        )
        self.set_status("Liberando memoria RAM…")

    def _on_memory_released(self, result) -> None:
        self._memory_busy = False
        if isinstance(result, Exception):
            self.optimize_page.set_busy(False)
            self.set_status(f"No se pudo liberar memoria: {result}")
            return
        self.memory_guard.note_clean(result)
        self.optimize_page.show_result(result)
        if result.trigger == "automatico":
            self.tray.notify("Memoria liberada", result.summary(), warning=False)
        self.set_status(result.summary())

    def run_maintenance_now(self) -> None:
        """Ejecuta un ciclo de mantenimiento completo sin esperar a la tarea."""
        if self._maintenance_busy:
            return
        self._maintenance_busy = True
        schedule = self.settings.schedule_config
        options = self.settings.memory_options
        self.set_status("Ejecutando el mantenimiento programado…")
        run_in_background(
            self,
            lambda: maintenance_core.run_maintenance(
                schedule,
                metrics=self.metrics,
                tasks=self.task_store,
                memory_options=options,
                trigger="manual",
            ),
            self._on_maintenance_done,
        )

    def _on_maintenance_done(self, report) -> None:
        self._maintenance_busy = False
        if isinstance(report, Exception):
            self.set_status(f"El mantenimiento falló: {report}")
            return
        self.schedule_page.reload_history()
        self.tasks_page.refresh()
        self.tray.notify("Mantenimiento completado", report.summary(), warning=False)
        self.set_status(report.summary())

    def _show_alert_badge(self, event) -> None:
        self.alert_indicator.setText(f"⚠ {event.label} {event.value:.0f}% ({event.time_display})")
        self.alert_indicator.setStyleSheet(f"color: {COLORS['warn']}; font-size: 12px;")
        self.alert_indicator.setVisible(True)

    @property
    def alert_history(self) -> list:
        return list(self._alert_history)

    # ------------------------------------------------------------------
    def navigate(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        for nav_key, button in self._nav_buttons.items():
            button.setChecked(nav_key == key)
        # La página nueva se pinta enseguida con la última muestra: sin esto, al
        # entrar en «Optimizar memoria» la tarjeta de RAM (y el reparto de memoria
        # entre los procesos) aparecían vacíos hasta el muestreo siguiente.
        if self.isVisible():
            self._update_current_page(self.metrics.snapshot)
        if key == "disk":
            self.disk_page.refresh_drives()
        if key == "reports":
            self.reports_page.update_preview()
        if key == "memory":
            self.optimize_page.refresh_processes()
            self.optimize_page.refresh_privileges()
        if key == "startup":
            # La lista del registro es inmediata; las tareas de inicio de sesión
            # las busca en segundo plano porque PowerShell tarda unos segundos.
            self.startup_page.refresh(with_tasks=True)
        if key == "schedule":
            # El estado real de la tarea se consulta la primera vez que se abre
            # la página (así el arranque no lanza PowerShell sin necesidad).
            self.schedule_page.ensure_status()
        self._apply_power_profile(refresh=True)

    # --- Consumo ----------------------------------------------------------
    def _apply_power_profile(self, refresh: bool = False) -> None:
        """Ajusta el muestreo a lo que se está viendo (perfil completo o eco)."""
        visible = self.isVisible()
        eco = self.settings.eco_mode or (self.settings.eco_when_hidden and not visible)
        self.metrics.set_profile(PROFILE_ECO if eco else PROFILE_FULL)
        # La lista de procesos (lo más caro) solo se recorre si de verdad se ve.
        self.metrics.set_process_detail(
            visible and self.stack.currentWidget() in (self.dashboard, self.reports_page)
        )
        if refresh and self.metrics.profile == PROFILE_FULL:
            self.metrics.poll()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().showEvent(event)
        self._apply_power_profile(refresh=True)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().hideEvent(event)
        self._apply_power_profile()

    def set_status(self, message: str) -> None:
        self.status_message.setText(message)
        # Un lector de pantalla no ve la barra de estado: se le anuncia el cambio
        # y queda también en el nombre accesible del texto.
        self.status_message.setAccessibleName(message)
        accessibility.announce(self.status_message, message)

    # ------------------------------------------------------------------
    def _launch_tool(self, identifier) -> tuple[bool, str]:
        """Abre una herramienta del sistema a partir de su identificador."""
        tool = tool_core.find_tool(identifier) if isinstance(identifier, str) else identifier
        if tool is None:
            return False, f"Herramienta desconocida: {identifier}"
        ok, message = tool_core.launch(tool)
        if ok:
            self._log_tool(tool.id)
        return ok, message

    def _log_tool(self, identifier, extra: str = "") -> None:
        """Registra la acción en el historial de mantenimiento."""
        if not self.auto_log_check.isChecked():
            return
        tool = tool_core.find_tool(identifier) if isinstance(identifier, str) else identifier
        title = tool.name if tool else str(identifier)
        category = AUTO_TASK_CATEGORY.get(getattr(tool, "id", str(identifier)), "Otros")
        task = self.task_store.add(
            title=f"{title}",
            category=category,
            notes=extra or "Acción registrada automáticamente al abrirse desde PC Toolkit.",
        )
        self.task_store.toggle_done(task.id)

    def _on_shutdown_wait(self, pendientes: int) -> None:
        """Explica por qué salir puede tardar un par de segundos."""
        self.set_status(
            f"Cerrando: esperando a que acaben {pendientes} tarea(s) en curso…"
        )

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt)
        # Con la bandeja activa, cerrar la ventana mantiene la app en segundo plano.
        if not self._force_quit and self.tray.active and self.settings.close_to_tray:
            event.ignore()
            self.hide()
            self.tray.notify(
                f"{APP_NAME} sigue activo",
                "La aplicación continúa en la bandeja controlando el equipo. "
                "Usa «Salir» en su menú para cerrarla por completo.",
                warning=False,
            )
            return
        self.metrics.stop()
        # Se deja terminar la limpieza de memoria, la consulta de tareas o lo que
        # haya en marcha. `shutdown_background` las encuentra todas (no depende de
        # una lista de páginas) y espera cediendo el bucle de eventos, así que
        # ninguna señal llega a una ventana ya destruida.
        scanner = self.disk_page._scanner if self.disk_page is not None else None  # noqa: SLF001
        if scanner is not None and scanner.isRunning():  # noqa: SLF001
            scanner.cancel()  # noqa: SLF001
            wait_thread(scanner, 2000)  # noqa: SLF001
        interrumpidos = shutdown_background(on_wait=self._on_shutdown_wait)
        if interrumpidos:
            self.set_status(
                f"Se cerró con {interrumpidos} tarea(s) a medias; se repetirán la próxima vez."
            )
        if self.tray.tray is not None:
            self.tray.tray.hide()
        self.settings.sync()
        self.history.prune()
        super().closeEvent(event)


def _describe_alert_config(config: AlertConfig) -> str:
    """Resumen legible de la configuración de alertas."""
    if not config.enabled:
        return "Alertas desactivadas."
    parts = [
        f"{label} {config.threshold_for(metric):.0f}%"
        for metric, label in (
            ("cpu", "CPU"),
            ("ram", "RAM"),
            ("disk", "Disco"),
        )
    ]
    return (
        "Alertas activas: "
        + " · ".join(parts)
        + f" · sostenido {config.sustained_samples} muestras · aviso cada {config.cooldown_minutes} min"
    )
