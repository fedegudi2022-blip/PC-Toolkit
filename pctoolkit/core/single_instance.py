"""Una sola copia de PC Toolkit por sesión de usuario.

Sin esta guarda, el arranque automático más el acceso directo (y, sobre todo, el
relanzado con permisos de administrador) dejan dos copias abiertas a la vez: dos
iconos en la bandeja, dos guardias de memoria automática que se pisan y dos procesos
escribiendo el historial.

La guarda tiene dos partes:

* Un `QLockFile` en la carpeta de datos: la primera copia lo bloquea y las demás no
  pueden, así que salen sin llegar a abrir nada.
* Un mensaje propio de Windows, registrado con `RegisterWindowMessageW`: la copia
  nueva lo lanza al aire y la que ya está abierta lo escucha para traer su ventana a
  delante. Se usa un mensaje del sistema en lugar de un servidor local de Qt porque
  `QtNetwork` se elimina del ejecutable al empaquetar (véase `build_exe.py`), y así no
  hay que engordar la descarga solo por esto.

En sistemas que no son Windows la guarda funciona igual (el bloqueo es de Qt); lo que
no hay es el aviso para sacar la ventana a delante.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QLockFile, QObject, Signal

from .config import data_dir

IS_WINDOWS = os.name == "nt"

LOCK_FILE_NAME = "pctoolkit.lock"

# Espera máxima cuando la copia nueva viene de un relanzado con UAC: la anterior tiene
# que cerrarse y soltar el bloqueo, y eso no es instantáneo.
WAIT_AFTER_RESTART_MS = 20_000

# Un bloqueo más viejo que esto se considera abandonado (la copia anterior se cayó sin
# soltarlo) y `QLockFile` lo rompe en vez de dejar la aplicación sin poder abrirse.
STALE_LOCK_MS = 30_000


def lock_path() -> str:
    """Archivo de bloqueo: vive con los datos de la aplicación."""
    return str(data_dir() / LOCK_FILE_NAME)


def _message_name() -> str:
    """Nombre del mensaje de Windows, distinto para cada usuario del equipo."""
    usuario = os.environ.get("USERNAME") or os.environ.get("USER") or "usuario"
    return f"PCToolkit.MostrarVentana.{usuario}"


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    HWND_BROADCAST = 0xFFFF

    class _MSG(ctypes.Structure):
        """Estructura MSG de Windows (solo se mira el campo `message`)."""

        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("w_param", wintypes.WPARAM),
            ("l_param", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("punto_x", wintypes.LONG),
            ("punto_y", wintypes.LONG),
        ]

    _SHOW_MESSAGE: int | None = None

    def show_message_id() -> int:
        """Identificador del mensaje «muéstrate». Se registra una sola vez."""
        global _SHOW_MESSAGE
        if _SHOW_MESSAGE is None:
            _SHOW_MESSAGE = int(ctypes.windll.user32.RegisterWindowMessageW(_message_name()))
        return _SHOW_MESSAGE

    def _broadcast_show() -> bool:
        return bool(
            ctypes.windll.user32.PostMessageW(
                wintypes.HWND(HWND_BROADCAST), wintypes.UINT(show_message_id()), 0, 0
            )
        )

    class _ShowListener(QAbstractNativeEventFilter):
        """Escucha el mensaje con el que otra copia pide que se muestre la ventana.

        Un mensaje difundido llega a cada ventana de nivel superior del proceso, así que
        el aviso se repite; se agrupa para no sacar la ventana varias veces por un solo
        clic.
        """

        window_ms = 500

        def __init__(self, callback: Callable[[], None]) -> None:
            super().__init__()
            self._callback = callback
            self._last = 0.0

        def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt)
            try:
                if event_type == b"windows_generic_MSG" and int(message):
                    aviso = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
                    if aviso.message == show_message_id():
                        ahora = time.monotonic()
                        if ahora - self._last >= self.window_ms / 1000:
                            self._last = ahora
                            self._callback()
            except (ValueError, TypeError, OSError):
                pass
            return False

else:  # pragma: no cover - fuera de Windows no hay ventana que mostrar

    def show_message_id() -> int:
        return 0

    def _broadcast_show() -> bool:
        return False

    _ShowListener = None  # type: ignore[assignment]


class SingleInstance(QObject):
    """Bloqueo de instancia única y aviso para mostrar la ventana que ya está abierta.

    Uso desde el punto de entrada:

        guarda = SingleInstance(app)
        if not guarda.acquire(...):
            guarda.notify_existing()
            return 0
        guarda.listen(window.show_from_another_instance)   # al terminar: release()
    """

    show_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock: QLockFile | None = None
        self._listener: QAbstractNativeEventFilter | None = None

    # --- Bloqueo -------------------------------------------------------------

    def acquire(self, timeout_ms: int = 0) -> bool:
        """Intenta quedarse con el bloqueo. Devuelve False si ya hay otra copia."""
        lock = QLockFile(lock_path())
        lock.setStaleLockTime(STALE_LOCK_MS)
        if not lock.tryLock(max(0, int(timeout_ms))):
            return False
        self._lock = lock
        return True

    @property
    def held(self) -> bool:
        return self._lock is not None

    def release(self) -> None:
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    # --- Aviso a la copia que ya está abierta --------------------------------

    def listen(self, callback: Callable[[], None] | None = None) -> bool:
        """Se pone a escuchar peticiones de «muéstrate» de futuras copias.

        Necesita una `QApplication` creada: el filtro se instala en la aplicación.
        """
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None or _ShowListener is None:
            return False
        escucha = callback or self.show_requested.emit
        self._listener = _ShowListener(escucha)
        app.installNativeEventFilter(self._listener)
        return True

    def stop_listening(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if self._listener is not None and app is not None:
            app.removeNativeEventFilter(self._listener)
        self._listener = None

    def notify_existing(self) -> bool:
        """Pide a la copia que ya está abierta que saque su ventana a delante."""
        return _broadcast_show()
