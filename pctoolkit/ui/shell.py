"""Ventana principal: barra lateral, páginas y estado."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import maintenance as maintenance_core
from ..core import memory as memory_core
from ..core import startup as startup_core
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
from ..core.undo import ORIGIN_AVISO, UndoStore
from . import accessibility, icons, theme
from .actualizaciones import UpdateController
from .alert_settings import AlertSettingsDialog
from .dashboard import DashboardPage
from .disk_page import DiskAnalyzerPage
from .gpu_monitor import GpuMonitor
from .insights import InsightController
from .optimize_page import OptimizeMemoryPage
from .reports_page import ReportsPage
from .schedule_page import MaintenanceSchedulePage
from .startup_page import StartupPage
from .tasks_page import TasksPage
from .tools_page import ToolsPage
from .windows_settings_page import WindowsSettingsPage
from .theme import apply_theme
from .tray import TrayController, tray_available
from .widgets import BrandLogo
from .workers import run_in_background, shutdown_background, wait_thread

# Tamaño con el que se abre la ventana la primera vez (el resto de las veces manda el que
# se dejó apuntado en las preferencias). Es el mismo con el que se documentan las capturas;
# en una pantalla más pequeña se recorta para que no nazca más grande que el escritorio.
DEFAULT_WINDOW_SIZE = QSize(1360, 860)
# Por debajo de esto la interfaz deja de caber con holgura (barra lateral, tarjetas y tabla).
MIN_WINDOW_SIZE = QSize(1180, 740)

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
        # El icono de la ventana (y el de la barra de tareas) es el logotipo de la
        # aplicación. Si no estuviera el archivo, se usa el glifo dibujado: la ventana
        # nunca se queda sin icono.
        logotipo = icons.logo_pixmap(64)
        if logotipo is not None:
            self.setWindowIcon(QIcon(logotipo))
        else:
            # Sin el archivo del logotipo se enseña el glifo de siempre, y sigue al tema: la
            # barra de tareas no vuelve a pedir el icono cuando se cambia de claro a oscuro.
            theme.window_icon(self, "cpu", 64, COLORS["accent_glow"])
        # El tamaño de apertura no se fija aquí: lo pone `_restore_window_geometry` con el que
        # se dejó apuntado la última vez (o con el de siempre la primera vez).
        self.setMinimumSize(MIN_WINDOW_SIZE)

        self.settings = settings or AppSettings()
        self.task_store = TaskStore(self)
        self.undo_store = UndoStore()
        self.metrics = MetricsService(parent=self)
        # La tarjeta gráfica se lee aparte del muestreo: cada vuelta cuesta un proceso de
        # PowerShell (~2,5 s), así que va en su propio vigía, en segundo plano y lento.
        self.gpu_monitor = GpuMonitor(parent=self)
        self.history = history or HistoryStore()
        self.recorder = HistoryRecorder(
            self.metrics,
            store=self.history,
            interval_seconds=self.settings.history_interval_seconds,
            parent=self,
        )
        self.alerts = AlertEngine(self.settings.alert_config)
        # Los avisos propios de la portada: juntan las métricas, el historial, los programas
        # de inicio y los temporales. El controlador se encarga de cuándo recalcular (los
        # programas de inicio cuestan segundos y van aparte, en segundo plano).
        self.insights = InsightController(self.settings, self.history, parent=self)
        self.memory_guard = MemoryGuard(self.settings.memory_options)
        self.tray = TrayController(self.settings, parent=self)
        # Actualizaciones: el controlador comprueba si hay versión nueva (lo pide `app.py`
        # al arrancar, y el botón de los ajustes cuando se pulsa) y aquí se le engancha la
        # ventana: la barra de estado, el aviso de la bandeja y el cierre ordenado al
        # instalar.
        self.updates = UpdateController(self.settings, parent=self)
        self.updates.attach_window(self)
        self._alert_history: list = []
        self._force_quit = False
        self._system_info: dict = {}
        self._memory_busy = False
        self._maintenance_busy = False
        self._startup_busy = False
        self._pages: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, SidebarButton] = {}
        self._shortcuts: list[QShortcut] = []
        # Última muestra del equipo (la que enseña el panel). Se guarda porque los ajustes la
        # usan para poner las lecturas al lado de los umbrales sin volver a preguntar nada.
        self.last_snapshot = None

        self._build()
        self._wire()
        self._restore_window_geometry()
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
        theme.styled(separator, "background: {border_soft};")
        layout.addWidget(separator)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.dashboard = DashboardPage(
            self.metrics, self._launch_tool, self.history, self.settings.alert_config
        )
        self.optimize_page = OptimizeMemoryPage(self.settings)
        self.startup_page = StartupPage(self.undo_store, self._launch_tool)
        self.schedule_page = MaintenanceSchedulePage(self.settings)
        self.disk_page = DiskAnalyzerPage()
        self.tools_page = ToolsPage(self._launch_tool, self._log_tool, self.settings)
        self.tasks_page = TasksPage(self.task_store)
        self.reports_page = ReportsPage(self.metrics, self.task_store, self.disk_page)
        # Los ajustes recomendados de Windows: catálogo propio, con análisis previo y
        # vuelta atrás anotada en el historial de deshacer (el mismo que usa el resto
        # de la aplicación).
        self.windows_page = WindowsSettingsPage(self.undo_store)

        for key, page in (
            ("home", self.dashboard),
            ("memory", self.optimize_page),
            ("startup", self.startup_page),
            ("windows", self.windows_page),
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
        theme.styled(sidebar, "background: {bg_alt};")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 18)
        layout.setSpacing(8)

        # La marca: el logotipo de la aplicación y, a su lado, el nombre. El logotipo es una
        # imagen, así que lleva nombre accesible (lo pone `BrandLogo`): sin él, un lector de
        # pantalla pasaría de largo por la marca.
        marca = QHBoxLayout()
        marca.setSpacing(10)
        self.brand_logo = BrandLogo(36, f"Logotipo de {APP_NAME}")
        marca.addWidget(self.brand_logo, 0, Qt.AlignTop)
        textos_marca = QVBoxLayout()
        textos_marca.setSpacing(0)
        brand = QLabel("PC TOOLKIT")
        brand.setObjectName("BrandLabel")
        subtitle = QLabel("MANTENIMIENTO")
        subtitle.setObjectName("BrandSub")
        textos_marca.addWidget(brand)
        textos_marca.addWidget(subtitle)
        marca.addLayout(textos_marca)
        marca.addStretch(1)
        layout.addLayout(marca)
        layout.addSpacing(14)

        items = [
            ("home", "Inicio", "home"),
            ("memory", "Optimizar memoria", "memory"),
            ("startup", "Programas de inicio", "power"),
            ("windows", "Ajustes de Windows", "gear"),
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
            "Umbrales de CPU, memoria y disco, bandeja del sistema, modo eco, historial y"
            " actualizaciones."
            "  ·  Ctrl+0"
        )
        self.alert_button.clicked.connect(self.open_alert_settings)
        layout.addWidget(self.alert_button)
        # Los ajustes van en Ctrl+0 (no en Ctrl+9) porque con «Ajustes de Windows» las
        # secciones son ya nueve: el cero queda al lado y no le quita el número a ninguna.
        settings_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        settings_shortcut.activated.connect(self.open_alert_settings)
        self._shortcuts.append(settings_shortcut)

        # El texto va en dos líneas: con la barra lateral a 226 px, el rótulo entero no
        # cabía en una (se cortaba por el final) y acortarlo dejaba el nombre a medias.
        self.auto_log_check = QCheckBox("Registrar acciones\nautomáticamente")
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
        self.gpu_monitor.sampled.connect(self._on_gpu)
        self.recorder.recorded.connect(self._on_history_sample)
        self.tray.open_requested.connect(self.open_from_tray)
        self.tray.quit_requested.connect(self.quit_application)
        self.tray.settings_requested.connect(self.open_alert_settings)
        self.tray.memory_requested.connect(lambda: self.release_memory_now("bandeja"))
        self.tray.maintenance_requested.connect(self.run_maintenance_now)
        self.dashboard.navigate.connect(self.navigate)
        self.dashboard.export_requested.connect(lambda: self.navigate("reports"))
        self.dashboard.status.connect(self.set_status)
        # Avisos de la portada: se pintan cuando el controlador termina de evaluarlos, y sus
        # botones vuelven aquí (descartar, ver el detalle o recuperar los descartados).
        self.insights.updated.connect(self._on_insights)
        self.dashboard.insight_action.connect(self._on_insight_action)
        self.dashboard.insight_dismiss.connect(self.insights.dismiss)
        self.dashboard.insights_restore.connect(self.insights.restore_dismissed)
        self.optimize_page.status.connect(self.set_status)
        self.startup_page.status.connect(self.set_status)
        self.windows_page.status.connect(self.set_status)
        # Reiniciar con permisos deja la copia elevada esperando: esta tiene que cerrarse
        # para soltar el bloqueo de instancia única y que solo quede una ventana abierta.
        self.optimize_page.restart_requested.connect(self._restart_as_admin)
        self.startup_page.restart_requested.connect(self._restart_as_admin)
        self.optimize_page.release_requested.connect(lambda: self.release_memory_now("manual"))
        self.optimize_page.settings_changed.connect(self._on_memory_settings)
        self.schedule_page.status.connect(self.set_status)
        self.disk_page.status.connect(self.set_status)
        self.tools_page.status.connect(self.set_status)
        self.tasks_page.status.connect(self.set_status)
        self.reports_page.status.connect(self.set_status)

    @property
    def nav_shortcuts(self) -> list[QShortcut]:
        """Atajos de teclado de las páginas (Ctrl+1…Ctrl+9) y de los ajustes (Ctrl+0)."""
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

    def _restart_as_admin(self) -> None:
        """Cierra esta copia tras lanzar la elevada, para no dejar dos instancias."""
        self.set_status("Reiniciando con permisos de administrador…")
        self.quit_application()

    def show_from_another_instance(self) -> None:
        """Otra copia ha intentado abrirse: se trae esta ventana a delante.

        La segunda instancia no llega a arrancar (el bloqueo la para), así que lo único
        que se hace aquí es devolver el foco a la que ya estaba.
        """
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def open_alert_settings(self) -> None:
        paleta_antes = active_palette()
        dialog = AlertSettingsDialog(
            self.settings,
            self.history,
            tray_available(),
            self,
            updates=self.updates,
            # La última muestra va al diálogo: los umbrales se enseñan al lado de lo que marca
            # el equipo ahora mismo, y esa lectura ya la tiene la ventana (no hay que volver a
            # preguntarle al sistema, ni esperar a un segundo muestreo para tener un dato).
            snapshot=self.last_snapshot,
        )
        if dialog.exec() == AlertSettingsDialog.Accepted:
            config = dialog.config
            self.alerts.config = config
            self.alerts.reset()
            self.tray.refresh_config(config)
            # Los medidores de la portada marcan el mismo umbral y avisan con él: si el
            # usuario lo cambia, tienen que enterarse sin reiniciar nada.
            self.dashboard.set_alert_config(config)
            # El interruptor de los avisos propios vive en ese mismo diálogo: se aplica aquí,
            # que es donde ya se sabe que se guardó de verdad.
            self.insights.set_enabled(self.settings.insights_enabled)
            self.recorder.interval_seconds = max(5, self.settings.history_interval_seconds)
            self._apply_power_profile(refresh=True)
            resumen = _describe_alert_config(config)
            paleta_nueva = apply_theme(QApplication.instance(), self.settings.theme_mode)
            if paleta_nueva != paleta_antes:
                # El tema se aplica sobre la ventana que ya está abierta: la paleta de la
                # aplicación y la hoja QSS se rehacen en `apply_theme`, los iconos se dibujan
                # al pedirlos y los estilos en línea se vuelven a resolver aquí. Antes se
                # rehacía la ventana entera —con su bandeja, sus hilos y su sitio en la
                # pantalla— y eso se notaba: parpadeo, foco perdido y un segundo icono de
                # bandeja mientras la vieja se cerraba.
                theme.repaint()
                self.set_status(
                    resumen
                    + "  ·  Tema "
                    + ("claro." if paleta_nueva == THEME_LIGHT else "oscuro.")
                )
                return
            self.set_status(resumen)
        else:
            # Si se cancela, los controles de la bandeja vuelven a su estado real.
            self.tray.refresh_config(self.settings.alert_config)

    def save_window_geometry(self) -> None:
        """Deja apuntado el tamaño, el sitio y si la ventana estaba maximizada.

        Se llama al cerrar (también cuando cerrar solo la manda a la bandeja) y sirve para
        que la próxima vez se abra donde se dejó, en la misma pantalla y con el mismo estado.
        """
        try:
            self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        except RuntimeError:  # pragma: no cover - la ventana ya no existe
            return

    def _restore_window_geometry(self) -> None:
        """Devuelve la ventana a donde estaba; sin nada apuntado, a su tamaño de siempre.

        Lo que se guarda lleva dentro la pantalla y si estaba maximizada, así que una ventana
        que se dejó a pantalla completa vuelve a pantalla completa.
        """
        guardada = self.settings.window_geometry
        if guardada:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(guardada.encode("ascii"))):
                    return
            except (ValueError, TypeError):
                pass
        # Primera vez (o preferencias rotas): el tamaño con el que se documenta la aplicación,
        # recortado si la pantalla es más pequeña que eso.
        pantalla = self.screen() or QApplication.primaryScreen()
        disponible = pantalla.availableGeometry() if pantalla is not None else None
        if disponible is None:  # pragma: no cover - sin pantalla no hay nada que calcular
            self.resize(DEFAULT_WINDOW_SIZE)
            return
        self.resize(
            min(DEFAULT_WINDOW_SIZE.width(), max(MIN_WINDOW_SIZE.width(), disponible.width() - 80)),
            min(DEFAULT_WINDOW_SIZE.height(), max(MIN_WINDOW_SIZE.height(), disponible.height() - 120)),
        )

    def _on_history_sample(self, sample) -> None:
        self.dashboard.append_history_sample(sample)

    def _on_insights(self, activos) -> None:
        """Pinta los avisos vigentes (con cuántos hay descartados por quien usa el programa)."""
        self.dashboard.update_insights(activos, self.insights.dismissed_count())

    def _on_insight_action(self, clave: str, targets=None) -> None:
        """Hace lo que pide un aviso, o lleva a donde se puede hacer.

        Para la limpieza, el aviso trae las categorías que señala (`targets`) y la ventana se
        abre con ellas **marcadas**: se ve lo que se liberaría y hay que confirmar el borrado,
        igual que si se hubieran marcado a mano. Para la memoria y los programas de inicio el
        aviso **cambia cosas**, así que pregunta antes (y la segunda salida de cada uno —«ver el
        reparto», «revisar el inicio»— sigue llevando a su página para mirarlo con calma).
        """
        if clave == "disco":
            self.navigate("disk")
        elif clave == "inicio":
            self.navigate("startup")
        elif clave == "memoria":
            self.navigate("memory")
        elif clave == "limpieza":
            self.navigate("tools")
            self.tools_page.open_clean_dialog(tuple(targets or ()))
        elif clave == "liberar-memoria":
            self._offer_release_memory()
        elif clave == "inicio-pesado":
            self._offer_disable_heavy()
        elif clave == "monitor":
            self._launch_tool("resmon")

    def _offer_release_memory(self) -> None:
        """Libera memoria desde el aviso, preguntando antes (no se cierra ni se pierde nada)."""
        options = self.settings.memory_options
        extra = (
            "\n\nTambién se vaciará la caché del sistema, tal como lo tienes configurado."
            if options.empty_system_cache
            else ""
        )
        if not self._confirmar(
            "Liberar memoria",
            "Se recortará la memoria reservada de los procesos; la vuelven a pedir cuando la "
            f"necesiten, así que no se cierra ninguna aplicación.{extra}\n\n¿Liberarla ahora?",
            "Liberar",
            por_defecto=QMessageBox.Yes,
        ):
            self.set_status("No se liberó nada.")
            return
        self.release_memory_now("aviso")

    def _confirmar(
        self,
        titulo: str,
        texto: str,
        etiqueta: str,
        *,
        por_defecto=QMessageBox.No,
    ) -> bool:
        """Pregunta con el texto delante y el botón diciendo lo que va a pasar.

        Se construye el cuadro a mano (en vez de `QMessageBox.question`) por lo mismo que el de
        la limpieza: así el botón de aceptar lleva el verbo de la acción —«Liberar»,
        «Desactivar»— y no un «Sí» que no dice nada, y la respuesta se puede leer sin depender
        de qué botón venga marcado por defecto.
        """
        pregunta = QMessageBox(self)
        pregunta.setWindowTitle(titulo)
        pregunta.setIcon(QMessageBox.Question)
        pregunta.setText(texto)
        pregunta.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        pregunta.button(QMessageBox.Yes).setText(etiqueta)
        pregunta.button(QMessageBox.No).setText("Cancelar")
        pregunta.setDefaultButton(por_defecto)
        return pregunta.exec() == QMessageBox.Yes

    def _offer_disable_heavy(self) -> None:
        """Desactiva los programas de inicio pesados que señala el aviso, con la lista delante.

        No se borra nada: se cambia el mismo estado que cambia la página de programas de inicio,
        con el mismo apunte de deshacer (marcado como «desde un aviso»), así que la vuelta atrás
        está donde siempre.
        """
        if self._startup_busy:
            return
        entradas = startup_core.read_entries()
        # Las tareas de inicio de sesión las busca la página en segundo plano: si ya están
        # cargadas cuentan también, y si no, la lista de la confirmación dice exactamente lo que
        # se va a cambiar (que es lo que importa).
        conocidas = {entrada.id for entrada in entradas}
        entradas += [
            entrada for entrada in self.startup_page.entries if entrada.id not in conocidas
        ]
        pesadas = startup_core.heavy_entries(entradas)
        if not pesadas:
            self.set_status(
                "No hay ninguna entrada de inicio pesada que se pueda desactivar desde aquí."
            )
            self.navigate("startup")
            return
        lista = "\n".join(f"• {entrada.name}" for entrada in pesadas)
        if not self._confirmar(
            "Desactivar programas de inicio",
            f"Se desactivarán {len(pesadas)} entrada(s) de inicio:\n\n{lista}\n\n"
            "No se borra nada: cada entrada se queda donde está y se puede volver a activar desde "
            "la página de programas de inicio o con «Deshacer».\n\n¿Desactivarlas ahora?",
            "Desactivar",
        ):
            self.set_status("No se cambió nada.")
            return
        self._startup_busy = True
        self.set_status(f"Desactivando {len(pesadas)} entrada(s) de inicio…")
        run_in_background(
            self,
            lambda: [
                startup_core.move(entrada, False, self.undo_store, origin=ORIGIN_AVISO)
                for entrada in pesadas
            ],
            self._after_disable_heavy,
        )

    def _after_disable_heavy(self, resultado) -> None:
        """Cuenta lo que se cambió y lo que no, y deja la página de inicio al día."""
        self._startup_busy = False
        if isinstance(resultado, Exception):
            self.set_status(f"No se pudieron cambiar los programas de inicio: {resultado}")
            return
        hechas = [mensaje for ok, mensaje in resultado if ok]
        fallos = [mensaje for ok, mensaje in resultado if not ok]
        if hechas and not fallos:
            self.set_status(f"{len(hechas)} programa(s) de inicio desactivado(s). Se puede deshacer.")
        elif hechas:
            self.set_status(
                f"Desactivados {len(hechas)} de {len(hechas) + len(fallos)}: {fallos[0]}"
            )
        else:
            self.set_status(f"No se cambió ninguno: {fallos[0] if fallos else 'sin respuesta'}")
        self.startup_page.refresh(with_tasks=False)
        self.insights.refresh()

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
        # Se guarda la última muestra: es la que se enseña en los ajustes (al lado de cada
        # umbral) y la que se puede releer sin volver a tocar el sistema.
        self.last_snapshot = snap
        # Con la ventana oculta no se repinta nada: es lo que mantiene bajo el
        # consumo cuando la aplicación solo vive en la bandeja.
        if self.isVisible():
            self._update_current_page(snap)
        # Los avisos se alimentan de la muestra actual; el controlador decide cada cuánto
        # recalcular (leer el historial en cada muestreo sería gastar sin motivo).
        self.insights.on_snapshot(snap)
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

    def _on_gpu(self, info) -> None:
        """Ficha de la tarjeta gráfica: la lee su vigía, en segundo plano.

        Con `None` (la vuelta no trajo datos) se avisa igualmente al servicio, que así
        puede decir que no hay datos en vez de dejarlo en «leyendo» para siempre.
        """
        self.metrics.set_gpu(info)

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
        theme.styled(self.alert_indicator, "color: {warn}; font-size: 12px;")
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
        if key == "home":
            # Al volver a la portada se revisan los avisos: es barato (métricas e historial) y
            # así lo que se enseña es de este momento, no de hace medio minuto. Los datos
            # caros (programas de inicio) solo se piden con la ventana a la vista: al
            # arrancar no se consulta nada en segundo plano.
            self.insights.refresh()
            if self.isVisible():
                self.insights.refresh_slow()
        if key == "windows" and not self.windows_page.analysed:
            # El primer vistazo a los ajustes se analiza solo: leer el registro es
            # inmediato y así la página nunca aparece «sin analizar».
            self.windows_page.analyse()
        self._apply_power_profile(refresh=True)

    # --- Consumo ----------------------------------------------------------
    def _apply_power_profile(self, refresh: bool = False) -> None:
        """Ajusta el muestreo a lo que se está viendo (perfil completo o eco).

        Con la ventana cerrándose no se toca nada: al aceptar el cierre, Qt la oculta y manda
        un `hideEvent` **después** de haber esperado a lo que estaba en marcha, y este ajuste
        volvería a poner en marcha el vigía de la tarjeta (con los contadores se consulta
        aunque la ventana no se vea, que es la regla normal). Esa lectura dejaría un hilo
        vivo mientras Qt destruye la ventana, y el proceso acabaría en un error de verdad al
        salir: se vio midiendo el recorrido de `--screenshot` (código 0xC0000409).
        """
        if self._force_quit:
            return
        visible = self.isVisible()
        eco = self.settings.eco_mode or (self.settings.eco_when_hidden and not visible)
        self.metrics.set_profile(PROFILE_ECO if eco else PROFILE_FULL)
        # La tarjeta gráfica se consulta más allá de donde se enseña cuando el dato sale de
        # los contadores de Windows: ese camino no abre ningún proceso, así que seguir
        # mirando con la ventana oculta es lo que hace que la serie de la GPU en el historial
        # no tenga agujeros. Donde Windows no publica el contador, cada vuelta es un
        # PowerShell de ~2,5 s y vale la regla de siempre: solo con la tarjeta a la vista
        # (la portada o los informes). La cadencia sigue al perfil (15 s en completo, un
        # minuto en eco), y el modo eco **no** la apaga porque es el de fábrica: si la
        # apagara, la tarjeta no se llenaría nunca.
        a_la_vista = visible and self.stack.currentWidget() in (self.dashboard, self.reports_page)
        self.gpu_monitor.set_eco(eco)
        # El aviso de la tarjeta dice la cadencia y el alcance de verdad: en eco es un minuto
        # y, con contadores, se consulta aunque la ventana no se vea.
        self.dashboard.set_gpu_cadence(self.gpu_monitor.interval_ms, continua=self.gpu_monitor.cheap_route)
        self.gpu_monitor.set_active(a_la_vista or self.gpu_monitor.cheap_route)
        # El historial necesita saber si la lectura está viva: sin consulta no se guarda el
        # último uso conocido (sería una línea plana que la tarjeta no hizo).
        self.metrics.set_gpu_watched(self.gpu_monitor.active)
        # La lista de procesos (lo más caro) solo se recorre si de verdad se ve.
        self.metrics.set_process_detail(a_la_vista)
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
        # El tamaño y el sitio se apuntan siempre, también cuando cerrar solo la esconde.
        self.save_window_geometry()
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
        self.gpu_monitor.stop()
        self.insights.stop()
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
