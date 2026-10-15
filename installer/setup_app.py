"""Asistente de instalación de PC Toolkit (instalar y desinstalar).

Ventana única con tres pantallas: opciones, progreso y resultado. Usa el mismo
tema oscuro que la aplicación y no necesita permisos de administrador, porque
todo se instala en la carpeta del usuario.

También funciona sin ventana, que es lo que usan el panel de control de Windows y
las comprobaciones automáticas:

    PC Toolkit Setup.exe --silent --dir "C:\\ruta" --autostart
    PC Toolkit Setup.exe --silent --desinstalar
    PC Toolkit Setup.exe --desinstalar
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, QThread, Signal  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from installer import installer_core as core  # noqa: E402
from pctoolkit.core.config import APP_TAGLINE, APP_VERSION, COLORS  # noqa: E402
from pctoolkit.ui import icons  # noqa: E402
from pctoolkit.ui.theme import apply_theme  # noqa: E402
from pctoolkit.ui.widgets import BrandLogo, Card, IconBadge, Muted, SectionTitle, hline  # noqa: E402

WINDOW_TITLE = f"Instalador de {core.APP_DISPLAY_NAME}"


class Worker(QThread):
    """Ejecuta la copia (o el borrado) sin congelar la ventana."""

    message = Signal(str)
    done = Signal(object)

    def __init__(self, function, parent=None) -> None:
        super().__init__(parent)
        self._function = function

    def run(self) -> None:  # noqa: D102 (QThread)
        try:
            result = self._function(self.message.emit)
        except Exception as exc:  # noqa: BLE001 - se muestra en la ventana
            result = exc
        self.done.emit(result)


class InstallerWindow(QWidget):
    """Asistente de instalación y desinstalación."""

    def __init__(self, uninstall_mode: bool = False, options: core.InstallOptions | None = None) -> None:
        super().__init__()
        self.uninstall_mode = uninstall_mode
        self.options = options or core.InstallOptions()
        self.result: core.InstallResult | None = None
        self.worker: Worker | None = None
        self.setWindowTitle(WINDOW_TITLE)
        logotipo = icons.logo_pixmap(64)
        self.setWindowIcon(
            QIcon(logotipo) if logotipo is not None else QIcon(icons.pixmap_for("cpu", 64, COLORS["accent"]))
        )
        self.setFixedSize(660, 520)
        self._build()
        self._refresh_state()

    # --- Construcción ----------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        # La marca del asistente es el mismo logotipo de la aplicación (con su nombre para el
        # lector de pantalla); sin el archivo, `BrandLogo` dibuja el glifo de siempre.
        self.logo = BrandLogo(40, f"Logotipo de {core.APP_DISPLAY_NAME}")
        head.addWidget(self.logo)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = QLabel(
            f"Desinstalar {core.APP_DISPLAY_NAME}" if self.uninstall_mode else f"Instalar {core.APP_DISPLAY_NAME}"
        )
        title.setObjectName("SectionTitle")
        subtitle = QLabel(f"{APP_TAGLINE} · versión {APP_VERSION}")
        subtitle.setObjectName("Muted")
        texts.addWidget(title)
        texts.addWidget(subtitle)
        head.addLayout(texts, 1)
        outer.addLayout(head)

        self.card = Card(soft=True)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)
        if self.uninstall_mode:
            self._build_uninstall(card_layout)
        else:
            self._build_install(card_layout)
        outer.addWidget(self.card, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.log_label = QLabel("")
        self.log_label.setObjectName("Dim")
        self.log_label.setWordWrap(True)
        buttons.addWidget(self.log_label, 1)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setObjectName("Ghost")
        self.cancel_button.clicked.connect(self.close)
        self.main_button = QPushButton("Desinstalar" if self.uninstall_mode else "Instalar")
        self.main_button.setObjectName("Primary")
        self.main_button.clicked.connect(self._run)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.main_button)
        outer.addLayout(buttons)

    def _build_install(self, layout: QVBoxLayout) -> None:
        layout.addWidget(SectionTitle("Carpeta de instalación"))
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.folder_edit = QLineEdit(str(self.options.target_dir))
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Cambiar…")
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)
        layout.addWidget(
            Muted(
                "Se instala solo para tu usuario en «Programas locales»: no hace falta ser "
                "administrador ni aceptar ningún aviso de Windows."
            )
        )

        layout.addWidget(hline())
        layout.addWidget(SectionTitle("Opciones"))
        self.desktop_check = QCheckBox("Crear un acceso directo en el escritorio")
        self.desktop_check.setChecked(self.options.desktop_shortcut)
        self.menu_check = QCheckBox("Añadirlo al menú Inicio")
        self.menu_check.setChecked(self.options.start_menu_shortcut)
        self.autostart_check = QCheckBox("Arrancar automáticamente al iniciar sesión (en la bandeja)")
        self.autostart_check.setChecked(self.options.autostart)
        self.launch_check = QCheckBox("Abrir PC Toolkit al terminar la instalación")
        self.launch_check.setChecked(self.options.launch_after)
        for widget in (self.desktop_check, self.menu_check, self.autostart_check, self.launch_check):
            layout.addWidget(widget)
        layout.addStretch(1)

    def _build_uninstall(self, layout: QVBoxLayout) -> None:
        layout.addWidget(SectionTitle("Se va a desinstalar PC Toolkit"))
        for text in (
            "Se eliminarán los archivos del programa, los accesos directos del escritorio y del menú Inicio.",
            "Se quitará el arranque automático si lo tenías activado.",
            "Se eliminará la tarea de mantenimiento programado, si la habías creado.",
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(IconBadge("check", 20, 12, COLORS["accent"]))
            label = Muted(text)
            label.setWordWrap(True)
            row.addWidget(label, 1)
            layout.addLayout(row)

        layout.addWidget(hline())
        self.data_check = QCheckBox("Borrar también mis datos (historial, informes y preferencias)")
        self.data_check.setToolTip(
            "Si no lo marcas, se conservan en %APPDATA%\\PCToolkit y se reutilizarán si vuelves a instalar."
        )
        layout.addWidget(self.data_check)
        self.data_path_label = Muted(f"Datos actuales: {core.user_data_dir()}")
        layout.addWidget(self.data_path_label)
        layout.addStretch(1)

    # --- Estado ----------------------------------------------------------
    def _refresh_state(self) -> None:
        if self.uninstall_mode:
            entry = core.read_uninstall_entry(self.options.key_name)
            if entry is None:
                self.status_label.setText(
                    "No se encontró ninguna instalación registrada en este usuario. "
                    "Puedes borrar el programa a mano si quedara algo."
                )
            else:
                self.status_label.setText(
                    f"Instalada la versión {entry.get('DisplayVersion', '?')} en "
                    f"{entry.get('InstallLocation', '?')}."
                )
            return
        entry = core.read_uninstall_entry(self.options.key_name)
        if entry:
            self.status_label.setText(
                f"Ya había una instalación (versión {entry.get('DisplayVersion', '?')}). "
                "Se actualizará conservando tus datos y preferencias."
            )
        elif core.is_running():
            self.status_label.setText("Hay una instancia de PC Toolkit abierta; se cerrará para actualizar.")
        else:
            self.status_label.setText("Listo para instalar.")

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Elige la carpeta de instalación", str(self.options.target_dir))
        if chosen:
            self.folder_edit.setText(chosen)

    # --- Acciones ---------------------------------------------------------
    def _collect(self) -> core.InstallOptions:
        self.options.target_dir = Path(self.folder_edit.text().strip() or core.default_install_dir())
        self.options.desktop_shortcut = self.desktop_check.isChecked()
        self.options.start_menu_shortcut = self.menu_check.isChecked()
        self.options.autostart = self.autostart_check.isChecked()
        self.options.launch_after = self.launch_check.isChecked()
        return self.options

    def _run(self) -> None:
        if self.uninstall_mode:
            self._start_uninstall()
        else:
            self._start_install()

    def _start_install(self) -> None:
        options = self._collect()
        running = core.is_running()
        if running:
            answer = QMessageBox.question(
                self,
                "PC Toolkit está abierto",
                "Para actualizar hay que cerrar las instancias abiertas de PC Toolkit.\n¿Cerrarlas ahora?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
        def run_install(note) -> core.InstallResult:
            core.close_running_apps()
            return core.install(options, note)

        self._busy(True, "Instalando…")
        self.worker = Worker(run_install, self)
        self.worker.message.connect(self.log_label.setText)
        self.worker.done.connect(self._install_finished)
        self.worker.start()

    def _start_uninstall(self) -> None:
        remove_data = self.data_check.isChecked()
        answer = QMessageBox.question(
            self,
            "Confirmar desinstalación",
            "Se desinstalará PC Toolkit de este usuario.\n¿Continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._busy(True, "Desinstalando…")
        self.worker = Worker(
            lambda note: core.uninstall(
                target_dir=self.options.target_dir,
                key_name=self.options.key_name,
                run_value=self.options.run_value,
                desktop=self.options.desktop,
                start_menu=self.options.start_menu,
                remove_user_data=remove_data,
                progress=note,
            ),
            self,
        )
        self.worker.message.connect(self.log_label.setText)
        self.worker.done.connect(self._uninstall_finished)
        self.worker.start()

    def _busy(self, busy: bool, message: str = "") -> None:
        self.main_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if message:
            self.status_label.setText(message)

    def _install_finished(self, result) -> None:
        self._busy(False)
        if isinstance(result, Exception):
            self.status_label.setText(f"Error inesperado: {result}")
            return
        self.result = result
        if not result.ok:
            self.status_label.setText(result.message)
            return

        detalles = [result.summary()]
        if result.warnings:
            detalles.append("Avisos: " + " ".join(result.warnings))
        self.status_label.setText(" ".join(detalles))
        self.main_button.setText("Terminar")
        try:
            self.main_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.main_button.clicked.connect(self.close)
        if result.shortcuts:
            self.log_label.setText("Accesos directos: " + ", ".join(Path(s).name for s in result.shortcuts))
        if self.options.launch_after and core.app_executable(self.options.target_dir).exists():
            subprocess.Popen([str(core.app_executable(self.options.target_dir))], close_fds=True)

    def _uninstall_finished(self, result) -> None:
        self._busy(False)
        if isinstance(result, Exception):
            self.status_label.setText(f"Error inesperado: {result}")
            return
        self.result = result
        self.status_label.setText(
            result.message + " " + (" ".join(result.warnings) if result.warnings else "")
        )
        self.main_button.setText("Cerrar")
        try:
            self.main_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.main_button.clicked.connect(self.close)
        self.log_label.setText(f"{len(result.removed)} elementos eliminados.")


def silent_install(options: core.InstallOptions) -> int:
    """Instalación sin ventana (para paneles de control y pruebas)."""
    closed, message = core.close_running_apps()
    print(f"  {message}")
    result = core.install(options, progress=lambda text: print(f"  {text}"))
    print(f"  {result.summary()}")
    for warning in result.warnings:
        print(f"  [aviso] {warning}")
    if result.ok and options.launch_after and core.app_executable(options.target_dir).exists():
        subprocess.Popen([str(core.app_executable(options.target_dir))], close_fds=True)
    return 0 if result.ok else 1


def silent_uninstall(options: core.InstallOptions, remove_data: bool) -> int:
    result = core.uninstall(
        target_dir=options.target_dir,
        key_name=options.key_name,
        run_value=options.run_value,
        desktop=options.desktop,
        start_menu=options.start_menu,
        remove_user_data=remove_data,
        progress=lambda text: print(f"  {text}"),
    )
    print(f"  {result.message}")
    for warning in result.warnings:
        print(f"  [aviso] {warning}")
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Instalador de {core.APP_DISPLAY_NAME} {APP_VERSION}")
    parser.add_argument("--desinstalar", action="store_true", help="Desinstala en lugar de instalar")
    parser.add_argument("--silent", "--silencioso", dest="silent", action="store_true", help="Sin ventana")
    parser.add_argument("--dir", dest="target", help="Carpeta de instalación")
    parser.add_argument("--origen", dest="source", help="Carpeta con la aplicación a instalar")
    parser.add_argument("--sin-escritorio", action="store_true", help="No crear acceso directo en el escritorio")
    parser.add_argument("--sin-menu", action="store_true", help="No añadir al menú Inicio")
    parser.add_argument("--autostart", action="store_true", help="Activar el arranque automático")
    parser.add_argument("--sin-abrir", action="store_true", help="No abrir la aplicación al terminar")
    parser.add_argument("--borrar-datos", action="store_true", help="Al desinstalar, borrar también los datos")
    parser.add_argument("--clave", dest="key", default=core.APP_KEY, help="Nombre de la clave de registro")
    parser.add_argument(
        "--valor-arranque", dest="run_value", default=core.RUN_VALUE, help="Nombre del valor de arranque"
    )
    parser.add_argument("--escritorio", dest="desktop", help="Carpeta de escritorio (pruebas)")
    parser.add_argument("--menu", dest="menu", help="Carpeta del menú Inicio (pruebas)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = core.InstallOptions(
        target_dir=Path(args.target) if args.target else core.default_install_dir(),
        desktop_shortcut=not args.sin_escritorio,
        start_menu_shortcut=not args.sin_menu,
        autostart=args.autostart,
        launch_after=not args.sin_abrir,
        source=Path(args.source) if args.source else None,
        key_name=args.key,
        run_value=args.run_value,
        desktop=Path(args.desktop) if args.desktop else None,
        start_menu=Path(args.menu) if args.menu else None,
    )

    if args.silent:
        if args.desinstalar:
            return silent_uninstall(options, args.borrar_datos)
        return silent_install(options)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(f"{core.APP_DISPLAY_NAME} Setup")
    apply_theme(app)
    window = InstallerWindow(uninstall_mode=args.desinstalar, options=options)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
