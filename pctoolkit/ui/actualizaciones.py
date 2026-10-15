"""Actualizaciones dentro de la aplicación: comprobar, avisar, descargar e instalar.

Aquí vive la parte visible de `pctoolkit.core.actualizador`. Tres piezas:

* `UpdateDialog` — «Hay una versión nueva», con lo que cambia y tres salidas claras:
  actualizar ahora, más tarde u omitir esa versión.
* `DownloadDialog` — barra de progreso con botón de cancelar; la descarga va en un hilo
  aparte (`run_in_background`), así que la ventana sigue respondiendo.
* `UpdateController` — decide *cuándo* se pregunta a GitHub (una vez al arrancar, como
  mucho una vez al día, y solo si está activado en los ajustes) y lleva el orden de las
  cosas al actualizar: descargar, comprobar el SHA-256, cerrar la aplicación y dejar que
  el instalador haga el resto.

Nada sale de aquí sin el visto bueno de quien usa el programa: la comprobación automática
solo avisa, y descargar e instalar siempre pasa por un diálogo. Cuando la copia es la
portátil no se instala nada —no hay instalación que reemplazar—: se ofrece abrir la
página de la versión para descargarla a mano.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import actualizador, logs
from ..core.actualizador import Cancelled, Release, UpdateError
from ..core.config import APP_VERSION, COLORS, format_bytes
from ..core.settings import AppSettings
from . import accessibility
from .widgets import Card, IconBadge, Muted, SectionTitle, hline
from .workers import run_in_background

# Como mucho una comprobación al día: la API de GitHub sin credenciales solo admite 60
# consultas por hora, y a quien usa el programa no le hace falta saber esto cada rato.
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# Salidas posibles del diálogo de versión nueva.
CHOICE_UPDATE = "update"
CHOICE_LATER = "later"
CHOICE_SKIP = "skip"


class _CancelFlag:
    """Permite que el botón «Cancelar» hable con el hilo que está descargando."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def __call__(self) -> bool:
        return self.cancelled


class UpdateDialog(QDialog):
    """«Hay una versión nueva»: qué cambia y qué hacer con ella."""

    def __init__(
        self,
        release: Release,
        *,
        current: str = APP_VERSION,
        installable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.release = release
        self.installable = installable
        self.choice = CHOICE_LATER
        self.setWindowTitle("Hay una versión nueva")
        self.setMinimumSize(560, 460)
        self.setSizeGripEnabled(True)
        self._build(current)

    def _build(self, current: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("download", 36, 19, COLORS["accent_glow"]))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel("Hay una versión nueva")
        title.setObjectName("SectionTitle")
        self.subtitle = QLabel(
            f"Tienes la {current} y está disponible la {self.release.version}."
        )
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        texts.addWidget(title)
        texts.addWidget(self.subtitle)
        head.addLayout(texts, 1)
        layout.addLayout(head)

        card = Card(soft=True, name=f"Novedades de la versión {self.release.version}")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)
        card_layout.addWidget(SectionTitle(f"Novedades de la versión {self.release.version}"))
        card_layout.addWidget(hline())
        notes = QTextEdit()
        notes.setPlainText(self.release.notes_excerpt())
        # Solo para leer: se pueden copiar las notas, pero no cambiar.
        notes.setReadOnly(True)
        notes.setAccessibleName(f"Notas de la versión {self.release.version}")
        notes.setFixedHeight(190)
        card_layout.addWidget(notes)
        layout.addWidget(card, 1)

        if self.installable:
            detail = (
                f"Se descargará el instalador ({format_bytes(self.release.asset.size)}), se "
                "comprobará su SHA-256 y se instalará encima de esta versión, conservando tus "
                "datos y preferencias. La aplicación se cerrará y volverá a abrirse sola."
            )
        else:
            detail = (
                "Esta copia es la versión portátil, así que PC Toolkit no puede instalarla sola: "
                f"se abrirá la página de la versión {self.release.version} para que descargues "
                "el archivo a mano."
            )
        aviso = Muted(detail)
        aviso.setWordWrap(True)
        layout.addWidget(aviso)

        buttons = QDialogButtonBox()
        etiqueta = "Actualizar ahora" if self.installable else "Abrir la página de la versión"
        self.update_button = buttons.addButton(etiqueta, QDialogButtonBox.AcceptRole)
        self.update_button.setObjectName("Primary")
        self.update_button.setAccessibleName(etiqueta)
        self.later_button = buttons.addButton("Más tarde", QDialogButtonBox.RejectRole)
        self.skip_button = buttons.addButton("Omitir esta versión", QDialogButtonBox.DestructiveRole)
        self.skip_button.setToolTip(
            f"No volver a avisar de la {self.release.version}. Si publicas otra versión más "
            "adelante, sí se volverá a avisar."
        )
        buttons.accepted.connect(self._accept_update)
        buttons.rejected.connect(self._decide_later)
        self.skip_button.clicked.connect(self._skip)
        layout.addWidget(buttons)

        # El foco arranca en la acción principal, y con el tabulador se llega a las otras dos.
        self.update_button.setDefault(True)
        self.update_button.setFocus(Qt.OtherFocusReason)

    # ------------------------------------------------------------------
    def _accept_update(self) -> None:
        self.choice = CHOICE_UPDATE
        self.accept()

    def _decide_later(self) -> None:
        self.choice = CHOICE_LATER
        self.reject()

    def _skip(self) -> None:
        self.choice = CHOICE_SKIP
        self.reject()


class DownloadDialog(QDialog):
    """Progreso de la descarga, con la opción de cancelarla."""

    def __init__(self, release: Release, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.release = release
        self.cancel_flag = _CancelFlag()
        self.path: Path | None = None
        self.error: str = ""
        self.setWindowTitle("Descargando la actualización")
        self.setMinimumWidth(480)
        # Sin la «?» del título y sin cierre por Escape: mientras se escribe en disco, la
        # única salida ordenada es el botón (que avisa al hilo y borra lo a medias).
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel(f"Descargando la versión {self.release.version}")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        detalle = (
            f"{self.release.asset.name} · {format_bytes(self.release.asset.size)}. "
            "Al terminar se comprueba su SHA-256 antes de instalarlo."
        )
        self.detail = Muted(detalle)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAccessibleName("Progreso de la descarga")
        layout.addWidget(self.progress)

        self.status_label = Muted("Empezando la descarga…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setObjectName("Ghost")
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)

    # ------------------------------------------------------------------
    def _cancel(self) -> None:
        self.cancel_flag.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Cancelando la descarga…")
        accessibility.announce(self.status_label, "Cancelando la descarga")

    def set_progress(self, done: int, total: int) -> None:
        """Refresca la barra con lo descargado (la llama el hilo, ya en la interfaz)."""
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done * 100 / total)))
            self.status_label.setText(f"Descargados {format_bytes(done)} de {format_bytes(total)}.")
        else:
            # Sin `Content-Length` no se puede saber cuánto queda: barra en movimiento.
            self.progress.setRange(0, 0)
            self.status_label.setText(f"Descargados {format_bytes(done)}.")

    def set_done(self, path: Path) -> None:
        """Descarga terminada y verificada."""
        self.path = path
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status_label.setText("Descarga comprobada. Instalando…")
        accessibility.announce(self.status_label, "Descarga comprobada. Instalando.")
        self.accept()

    def set_error(self, message: str) -> None:
        """La descarga (o la comprobación del hash) falló."""
        self.error = message
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText(message)
        self.cancel_button.setText("Cerrar")
        self.cancel_button.setEnabled(True)
        accessibility.announce(self.status_label, message, assertive=True)


class UpdateController(QObject):
    """Comprueba si hay versión nueva y, si se acepta, la descarga e instala.

    Habla con la ventana por señales, así que funciona igual con la ventana delante, con
    la aplicación solo en la bandeja o desde el diálogo de ajustes.
    """

    #: Resultado de una comprobación pedida a mano: `Release` o `None` si ya está al día.
    checked = Signal(object)
    #: Una comprobación pedida a mano que no se pudo hacer (el texto, en `message`).
    failed = Signal(str)
    #: Lo que va pasando (para la barra de estado de la ventana).
    status = Signal(str)
    #: Bytes descargados y total (0 si el servidor no lo dice). Se emite **desde el hilo
    #: de descarga**: Qt lo entrega en la interfaz, que es donde vive la barra.
    progress = Signal(int, int)

    def __init__(self, settings: AppSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        # La ventana se engancha después de construirla (necesita el controlador ya
        # creado para poder conectar sus señales); sin ella solo se avisa por señales.
        self._window = None
        self._busy = False
        self._tray_notice_connected = False
        #: Última versión de la que ya se avisó (para no abrir el diálogo dos veces).
        self.pending: Release | None = None

    # --- Enganche con la ventana -----------------------------------------
    def attach_window(self, window) -> None:
        """Engancha la ventana principal: da estado, avisos de bandeja y cierre ordenado."""
        self._window = window
        self.status.connect(window.set_status)

    # --- Comprobaciones ---------------------------------------------------
    def start_automatic(self) -> None:
        """Comprueba si hay versión nueva al arrancar, como mucho una vez al día."""
        if not self.settings.check_updates:
            return
        ultima = self.settings.last_update_check
        if ultima and (time.time() - ultima) < CHECK_INTERVAL_SECONDS:
            return
        self._start(interactive=False)

    def check_now(self) -> None:
        """Comprobación pedida a mano (botón «Buscar actualizaciones»)."""
        self._start(interactive=True)

    @property
    def busy(self) -> bool:
        """Si hay una comprobación o descarga en marcha."""
        return self._busy

    def _start(self, *, interactive: bool) -> None:
        if self._busy:
            if interactive:
                self.status.emit("Ya se está comprobando si hay actualizaciones…")
            return
        self._busy = True
        if interactive:
            self.status.emit("Buscando actualizaciones…")
        run_in_background(self, actualizador.fetch_latest, lambda result: self._checked(result, interactive))

    def _checked(self, result, interactive: bool) -> None:
        self._busy = False
        # La marca se apunta siempre que la consulta termine, salga bien o mal: si no hay
        # conexión, no tiene sentido volver a intentarlo en cada arranque del día.
        self.settings.last_update_check = time.time()
        self.settings.sync()

        if isinstance(result, Exception):
            mensaje = str(result)
            if not isinstance(result, UpdateError):
                logs.warning("La comprobación de actualizaciones falló", result, name="actualizador")
                mensaje = f"No se pudo comprobar si hay una versión nueva: {result}"
            if interactive:
                self.failed.emit(mensaje)
            else:
                logs.warning(f"Comprobación automática de actualizaciones: {mensaje}", name="actualizador")
            return

        if result is None:
            if interactive:
                self.checked.emit(None)
            return

        release: Release = result
        if not interactive and release.version == self.settings.skipped_version:
            # Se pidió no volver a ver esta versión: se queda anotada, sin molestar.
            self.pending = None
            return
        self.pending = release
        if interactive:
            self.checked.emit(release)
        self._announce(release, interactive=interactive)

    def _announce(self, release: Release, *, interactive: bool) -> None:
        """Avisa de la versión nueva: diálogo si hay ventana, aviso si solo hay bandeja."""
        window = self._window
        if window is None:
            return
        if not interactive and not window.isVisible():
            # En segundo plano (arranque automático solo con la bandeja) un diálogo modal
            # aparecería encima de lo que esté haciendo el usuario, sin que lo haya pedido:
            # se avisa y se deja dicho dónde está la actualización cuando quiera ponerla.
            # Se conecta una sola vez: al hacer clic en el aviso se abre el diálogo de
            # verdad, con las mismas opciones que si hubiera aparecido al arrancar.
            self.pending = release
            if not self._tray_notice_connected:
                window.tray.notify_clicked.connect(self.show_pending)
                self._tray_notice_connected = True
            window.tray.notify(
                f"PC Toolkit {release.version} disponible",
                "Haz clic aquí o abre «Ajustes y alertas → Buscar actualizaciones» para "
                "instalarla cuando quieras.",
                warning=False,
            )
            return
        self.show_pending()

    def show_pending(self) -> None:
        """Muestra el diálogo de la versión pendiente, si hay alguna."""
        release = self.pending
        if release is None:
            return
        window = self._window
        if window is None:
            return
        instalable = release.installable and not actualizador.is_portable()
        dialog = UpdateDialog(
            release,
            current=APP_VERSION,
            installable=instalable,
            parent=window,
        )
        elegido = dialog.exec()
        if elegido != QDialog.Accepted:
            if dialog.choice == CHOICE_SKIP:
                self.settings.skipped_version = release.version
                self.settings.sync()
                self.status.emit(f"No se volverá a avisar de la versión {release.version}.")
            self.pending = None
            return
        if dialog.choice != CHOICE_UPDATE:
            return
        if not instalable:
            self._open_page(release)
            self.pending = None
            return
        self._install(release)

    # --- Instalación -------------------------------------------------------
    def _install(self, release: Release) -> None:
        """Descarga, verifica, lanza el instalador y cierra la aplicación."""
        window = self._window
        dialog = DownloadDialog(release, parent=window)
        flag = dialog.cancel_flag

        def descargar() -> Path:
            # El progreso se emite con la señal del controlador (una por trozo de 256 KiB) y
            # Qt lo entrega en el hilo de la interfaz, que es donde está la barra.
            return actualizador.download_installer(
                release, progress=self.progress.emit, cancel=flag
            )

        def terminado(result) -> None:
            if isinstance(result, Exception):
                dialog.set_error(
                    "Se canceló la descarga." if isinstance(result, Cancelled) else str(result)
                )
                return
            dialog.set_done(result)

        # La descarga va en un hilo aparte: la barra se mueve y «Cancelar» responde mientras
        # se escribe el archivo en disco.
        self.progress.connect(dialog.set_progress)
        run_in_background(self, descargar, terminado)
        try:
            dialog.exec()
        finally:
            self.progress.disconnect(dialog.set_progress)

        if dialog.error or dialog.path is None:
            self.pending = release
            if dialog.error:
                self.status.emit(dialog.error)
            return

        try:
            actualizador.launch_installer(
                dialog.path, target_dir=actualizador.installation_folder()
            )
        except UpdateError as exc:
            self.pending = release
            self.status.emit(str(exc))
            return

        self.pending = None
        if window is not None:
            # El cierre es el de siempre (el mismo que «Salir»): para la métrica, espera a
            # lo que esté en marcha, esconde la bandeja y guarda las preferencias. El
            # instalador se encarga de reemplazar los archivos y de volver a abrir la
            # versión nueva, así que aquí no se hace nada más.
            window.set_status(
                f"Instalando la versión {release.version}: la aplicación se cerrará y "
                "volverá a abrirse sola."
            )
            window.quit_application()

    def _open_page(self, release: Release) -> None:
        """Abre la página de la versión en el navegador (versión portátil)."""
        if QDesktopServices.openUrl(QUrl(release.page_url)):
            self.status.emit(
                f"Se abrió la página de la versión {release.version} en el navegador."
            )
        else:
            self.status.emit(
                f"No se pudo abrir el navegador. La versión {release.version} está en "
                f"{release.page_url}"
            )
