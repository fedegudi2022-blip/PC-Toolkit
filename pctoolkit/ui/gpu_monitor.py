"""Vigilancia de la tarjeta gráfica en segundo plano.

Leer la GPU cuesta un proceso de PowerShell (~2,5 s), así que no se puede llamar desde el
muestreo de las métricas (1,2 s) ni desde el hilo de la ventana. Aquí vive el «cuándo»:
un temporizador lento que lanza la lectura en un hilo (`pctoolkit.ui.workers`, el mismo
mecanismo que usan las tareas de inicio y la memoria) y avisa con el resultado.

Tres reglas de convivencia:

* **un proceso mientras se mira**: al empezar a vigilar se abre el proceso de lectura
  (`core/gpu.py`) y al parar se cierra. Arrancar PowerShell cuesta ~2,2 s de los ~2,4 s de
  una lectura, así que reutilizarlo es lo que hace barata la vuelta siguiente —y cerrarlo al
  salir de la portada es lo que evita dejar un PowerShell de fondo—;
* **una lectura a la vez**: si la anterior todavía está en marcha, el turno se salta (una
  consulta de 2,4 s cada 15 s no se solapa en el caso normal, pero en un equipo lento sí);
* **solo donde se enseña, si cuesta**: con los contadores de Windows el uso se lee sin
  abrir ningún proceso, así que la tarjeta se sigue mirando aunque la ventana esté oculta
  —es lo que hace que su serie del historial no tenga agujeros—; donde Windows no publica
  esos contadores el camino es un PowerShell, y entonces vale la regla de siempre: solo con
  la tarjeta a la vista, que es cuando el proceso tiene sentido;
* **la cadencia del perfil**: cada 15 s en el perfil completo y cada minuto en eco
  (`set_eco`), que es el modo de fábrica.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import gpu
from .workers import run_in_background

# Reintentos rápidos mientras la tarjeta no haya dado ningún dato: la primera lectura puede
# coincidir con un PowerShell lento y con la cadencia de eco (un minuto) la tarjeta se
# quedaría en «Leyendo…» demasiado rato. Se agotan a los pocos intentos, para que un equipo
# sin contadores de GPU no se pase la vida preguntando.
RETRIES = 3
RETRY_MS = 5_000


class GpuMonitor(QObject):
    """Consulta la tarjeta gráfica cada `interval_ms` y emite `sampled`."""

    sampled = Signal(object)

    def __init__(self, interval_ms: int = gpu.USAGE_INTERVAL_MS, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.info: gpu.GpuInfo | None = None
        self._busy = False
        self._active = False
        self._retries = 0
        # El camino barato se pregunta una vez por sesión (véase `cheap_route`).
        self._cheap: bool | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(max(3000, int(interval_ms)))
        self._timer.timeout.connect(self.poll)

    @property
    def interval_ms(self) -> int:
        return self._timer.interval()

    @property
    def cheap_route(self) -> bool:
        """¿Windows da el uso de la GPU por contadores, sin abrir ningún proceso?

        Se pregunta una sola vez por sesión —abrir y cerrar la consulta son milisegundos— y
        se recuerda: es lo que decide si la tarjeta se puede seguir mirando con la ventana
        oculta, y la respuesta no cambia mientras la aplicación vive.
        """
        if self._cheap is None:
            self._cheap = gpu.engine_counters_available()
        return self._cheap

    def set_eco(self, eco: bool) -> None:
        """Ajusta la cadencia al perfil de muestreo.

        El modo eco es el de fábrica y su idea es consumir poco, así que la tarjeta se sigue
        leyendo sola (es lo que la hace útil) pero cada minuto en lugar de cada 15 s.
        """
        intervalo = gpu.ECO_INTERVAL_MS if eco else gpu.USAGE_INTERVAL_MS
        if intervalo != self._timer.interval():
            self._timer.setInterval(max(3000, int(intervalo)))

    @property
    def active(self) -> bool:
        return self._active

    @property
    def busy(self) -> bool:
        """¿Hay una lectura en marcha? (para las pruebas y para no encadenarlas)."""
        return self._busy

    @property
    def retries(self) -> int:
        """Reintentos rápidos gastados desde la última lectura con datos."""
        return self._retries

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Empieza a vigilar: primera lectura ya y luego cada intervalo."""
        self._active = True
        # El proceso de lectura se abre aquí y se cierra en `stop`: vive lo que dura el
        # vistazo a la tarjeta, no la aplicación entera.
        gpu.open_reader()
        self.poll()
        self._timer.start()

    def stop(self) -> None:
        self._active = False
        self._timer.stop()
        gpu.close_reader()

    def set_active(self, active: bool) -> None:
        """Activa o para la vigilancia (el modo eco la para)."""
        if bool(active) == self._active:
            return
        self.start() if active else self.stop()

    def poll(self) -> None:
        """Lanza una lectura si se está vigilando y no hay otra en marcha.

        Sin vigilancia no se lee, aunque llegue un turno: un reintento que se dispare después
        de `stop` arrancaría un hilo que nadie va a esperar —y si la ventana se está
        cerrando, ese hilo vivo mientras Qt la destruye aborta el proceso—.
        """
        if self._busy or not self._active:
            return
        self._busy = True
        run_in_background(self, gpu.read_gpu_info, self._on_read)

    def _on_read(self, result) -> None:
        self._busy = False
        info = result if isinstance(result, gpu.GpuInfo) else None
        if info is not None:
            self.info = info
            self._retries = 0
        elif self.info is None and self._retries < RETRIES:
            # Sin ningún dato todavía y una lectura que no trajo nada: se vuelve a preguntar
            # pronto en vez de esperar al siguiente turno (hasta un minuto en eco).
            self._retries += 1
            QTimer.singleShot(RETRY_MS, self.poll)
        # Un fallo no rompe nada, pero se avisa igual (`None`): así la tarjeta del panel
        # puede decir que no hay datos en vez de quedarse en «leyendo» para siempre. El
        # motivo queda en el registro, y una sola vez por sesión.
        self.sampled.emit(info)
