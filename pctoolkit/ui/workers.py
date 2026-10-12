"""Ejecución en segundo plano para operaciones lentas (memoria y tareas).

Liberar memoria tarda segundos y consultar el Programador de tareas arranca
PowerShell; ninguno de los dos debe bloquear la interfaz. `BackgroundCall`
envuelve una función cualquiera en un hilo y devuelve su resultado (o la
excepción) por señal.

Cada hilo queda *hijo* de quien lo lanza y, además, apuntado en un registro
compartido: al cerrar la aplicación no hace falta acordarse de qué páginas
tenían trabajo en marcha (`shutdown_background` los encuentra todos). Es
importante, porque Qt **aborta el proceso** si se destruye un `QThread` que
sigue trabajando; esperar aquí es lo que convierte el cierre en algo silencioso
en vez de un error inesperado.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QElapsedTimer, QEventLoop, QThread, QTimer, Signal

# Espera máxima del cierre ordenado: de sobra para las consultas de PowerShell
# (miden ~4 s) y lo bastante corta para que salir nunca se sienta bloqueado.
SHUTDOWN_TIMEOUT_MS = 20_000

_ACTIVE: set[BackgroundCall] = set()
_LOCK = threading.Lock()


class BackgroundCall(QThread):
    """Ejecuta `function` en un hilo aparte y emite `done` con el resultado."""

    done = Signal(object)

    def __init__(self, function: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._function = function
        self._cancelled = False

    def run(self) -> None:  # noqa: D102 (QThread)
        try:
            try:
                result: Any = self._function()
            except Exception as exc:  # noqa: BLE001 - la interfaz muestra el error
                result = exc
            # Al cerrar se cancela la llamada: el resultado ya no interesa y
            # avisar serviría solo para tocar una ventana que se está yendo.
            if not self._cancelled:
                self.done.emit(result)
        finally:
            with _LOCK:
                _ACTIVE.discard(self)

    @property
    def cancelled(self) -> bool:
        """Si la llamada ya no va a avisar de su resultado (se está cerrando)."""
        return self._cancelled

    def cancel(self) -> None:
        """Deja de avisar cuando termine y suelta su pila de resultados."""
        self._cancelled = True

    def describe(self) -> str:
        """Nombre legible de la tarea, para mensajes y pruebas."""
        return getattr(self._function, "__name__", "tarea en segundo plano")


def run_in_background(owner, function: Callable[[], Any], callback: Callable[[Any], None]) -> BackgroundCall:
    """Lanza `function` y llama a `callback` con el resultado al terminar."""
    call = BackgroundCall(function, owner)
    call.done.connect(callback)
    call.done.connect(call.deleteLater)
    with _LOCK:
        _ACTIVE.add(call)
    call.start()
    return call


def pending_calls() -> list[BackgroundCall]:
    """Llamadas en segundo plano que siguen trabajando, de toda la aplicación.

    Se miran todas juntas a propósito: en el cierre no vale con acordarse de
    cada página que lanza trabajo.
    """
    with _LOCK:
        candidatas = list(_ACTIVE)
    return [call for call in candidatas if call.isRunning()]


def pump_events(milliseconds: int = 60) -> None:
    """Deja correr el bucle de eventos `milliseconds` sin quedarse dormido a secas.

    Hace falta para que, mientras se espera a los hilos, Qt entregue las señales
    y los borrados diferidos pendientes (`.deleteLater()`) en lugar de dejarlos
    para después de destruir la ventana.
    """
    app = QCoreApplication.instance()
    if app is None:
        time.sleep(max(1, milliseconds) / 1000)
        return
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec(QEventLoop.AllEvents)


def wait_thread(thread: QThread | None, timeout_ms: int = 4000) -> bool:
    """Espera a un hilo cualquiera (por ejemplo el analizador de disco) cediendo el bucle."""
    if thread is None or not thread.isRunning():
        return True
    clock = QElapsedTimer()
    clock.start()
    while thread.isRunning():
        if clock.hasExpired(max(200, timeout_ms)):
            return False
        pump_events(50)
    return True


def shutdown_background(
    timeout_ms: int = SHUTDOWN_TIMEOUT_MS,
    on_wait: Callable[[int], None] | None = None,
) -> int:
    """Espera a que termine todo el trabajo en segundo plano antes de cerrar.

    Devuelve cuántos hilos hubo que interrumpir (0 en el caso normal). Se para
    la espera en `timeout_ms` y, si alguno sigue vivo, se interrumpe: un hilo
    vivo mientras Qt destruye su objeto aborta el proceso entero, así que el
    último recurso es preferible a que Windows muestre un error del programa.
    """
    calls = pending_calls()
    if not calls:
        return 0
    # Primero se cancelan: si alguna acaba en mitad del cierre, su resultado no
    # se aplica a una ventana que ya se está yendo.
    for call in calls:
        call.cancel()
    if on_wait is not None:
        on_wait(len(calls))

    clock = QElapsedTimer()
    clock.start()
    while True:
        vivos = [call for call in calls if call.isRunning()]
        if not vivos:
            return 0
        if clock.hasExpired(max(200, timeout_ms)):
            for hilo in vivos:
                hilo.terminate()
            for hilo in vivos:
                hilo.wait(1000)
            return sum(1 for hilo in vivos if hilo.isRunning())
        pump_events(60)
