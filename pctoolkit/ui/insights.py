"""Controlador de los avisos de la portada: reúne los datos y decide qué se enseña.

El motor (`pctoolkit.core.insights`) es lógica pura y no sabe de dónde salen los números.
Aquí se juntan las tres fuentes que ya existen en la aplicación:

* **las métricas** de siempre (memoria, disco, salud y el proceso que más CPU gasta), que
  llegan en cada muestreo;
* **el historial de 24 h**, que es lo que permite hablar de «sostenido» en vez de un pico;
* **los programas de inicio y los temporales**, que cuestan segundos (consultar las tareas
  de inicio de sesión arranca PowerShell), así que se piden **una vez** en segundo plano y
  se refrescan muy de tarde en tarde.

Nada de esto corre en el hilo de la ventana ni cada segundo: recalcular con cada muestra
sería leer el historial entero sesenta veces por minuto para decir lo mismo.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import cleaner
from ..core import insights as insights_core
from ..core import startup as startup_core
from .workers import run_in_background

# Cada cuánto se recalculan los avisos baratos (métricas + historial).
REFRESH_MS = 30_000
# Y cada cuánto se vuelven a mirar los caros (programas de inicio): un cambio ahí no es
# urgente y la consulta arranca PowerShell.
SLOW_REFRESH_MS = 30 * 60_000
# Antigüedad de los temporales que se cuentan en el aviso (el suelo de la limpieza).
TEMP_AGE_HOURS = 24.0


class InsightController(QObject):
    """Junta los datos, evalúa los avisos y avisa a quien los pinta."""

    updated = Signal(object)

    def __init__(self, settings, history=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.history = history
        self.store = insights_core.InsightStore()
        self._snapshot = None
        self._slow: dict[str, object] = {}
        self._active: list[insights_core.Insight] = []
        self._slow_running = False
        self._slow_started = 0.0
        self._last_refresh = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._slow_timer = QTimer(self)
        self._slow_timer.setInterval(SLOW_REFRESH_MS)
        self._slow_timer.timeout.connect(self.refresh_slow)
        if self.enabled:
            self.start()

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.settings.insights_enabled)

    @property
    def active(self) -> list[insights_core.Insight]:
        """Los avisos que se están enseñando (sin los descartados)."""
        return list(self._active)

    @property
    def slow_pending(self) -> bool:
        """¿Se están consultando los programas de inicio? (para pruebas y mensajes)."""
        return self._slow_running

    def start(self) -> None:
        """Enciende los avisos: se evalúan ya, pero los datos caros esperan.

        Los programas de inicio cuestan segundos de PowerShell y al arrancar no hacen falta
        para nada: la aplicación tiene la norma de no consultar nada en segundo plano al
        abrirse. Se piden cuando la portada está a la vista y ha pasado el tiempo de
        refresco, o cuando el temporizador de media hora los pida.
        """
        self._timer.start()
        self._slow_timer.start()
        self.refresh()

    def stop(self) -> None:
        self._timer.stop()
        self._slow_timer.stop()

    def set_enabled(self, value: bool) -> None:
        """Activa o desactiva los avisos (y lo guarda en las preferencias)."""
        self.settings.insights_enabled = bool(value)
        self.settings.sync()
        if value:
            self.start()
        else:
            self.stop()
            self._slow = {}
            self._active = []
            self.updated.emit([])

    # ------------------------------------------------------------------
    def on_snapshot(self, snapshot) -> None:
        """Guarda la última muestra y, si toca, recalcula.

        El muestreo llega cada segundo y medio: recalcular con cada uno sería leer el
        historial entero muchísimas veces para decir lo mismo. Se recalcula como mucho cada
        `REFRESH_MS` (y la primera vez, con la primera muestra, para que la tarjeta no nazca
        vacía).
        """
        self._snapshot = snapshot
        if time.monotonic() - self._last_refresh >= REFRESH_MS / 1000:
            self.refresh()

    def refresh(self) -> None:
        """Recalcula los avisos con lo que ya se sabe y avisa a la tarjeta."""
        self._last_refresh = time.monotonic()
        if not self.enabled:
            self._active = []
            self.updated.emit([])
            return
        evidence = self.evidence()
        self._active = insights_core.evaluate(
            evidence, dismissed=self.store.dismissed()
        )
        self.updated.emit(self._active)

    def dismiss(self, insight) -> None:
        """Descarta un aviso (no vuelve hasta que caduque o empeore)."""
        self.store.dismiss(insight)
        self.refresh()

    def restore_dismissed(self) -> None:
        """Olvida los descartes y vuelve a evaluar: los avisos vuelven a salir."""
        self.store.clear()
        self.refresh()

    def dismissed_count(self) -> int:
        return len(self.store.dismissed())

    # --- Datos ----------------------------------------------------------
    def evidence(self) -> insights_core.Evidence:
        """Todo lo que se sabe ahora mismo, medido o consultado, sin inventar nada."""
        evidence = insights_core.Evidence(thresholds=self.settings.alert_config.thresholds)
        snapshot = self._snapshot
        if snapshot is not None:
            evidence.ram_percent = float(getattr(snapshot, "ram_percent", 0.0) or 0.0)
            evidence.swap_percent = float(getattr(snapshot, "swap_percent", 0.0) or 0.0)
            evidence.disk_percent = float(getattr(snapshot, "disk_percent", 0.0) or 0.0)
            evidence.disk_free = int(getattr(snapshot, "disk_free", 0) or 0)
            evidence.disk_mount = str(getattr(snapshot, "disk_mount", "") or "")
            evidence.health_score = float(getattr(snapshot, "health_score", 100.0) or 100.0)
            procesos = getattr(snapshot, "top_processes", None) or []
            if procesos:
                nombre, cpu, _memoria = procesos[0]
                evidence.cpu_top_process = str(nombre)
                evidence.cpu_top_percent = float(cpu or 0.0)
        media, minutos = self.cpu_sustained()
        evidence.cpu_avg = media
        evidence.cpu_minutes = minutos
        for clave, valor in self._slow.items():
            setattr(evidence, clave, valor)
        return evidence

    def cpu_sustained(self) -> tuple[float | None, float]:
        """Media de CPU y minutos que abarca, a partir del historial guardado.

        Se mira solo la ventana de los últimos minutos (no las 24 h): lo que se quiere contar
        es «lleva así un rato», y devuelve `None` si todavía no hay muestras suficientes para
        afirmarlo. La ventana que se devuelve es la **real** (de la primera a la última
        muestra), no la que se pediría en el mejor de los casos.
        """
        if self.history is None:
            return None, 0.0
        limite = time.time() - insights_core.SUSTAINED_MINUTES * 60
        try:
            muestras = [s for s in self.history.load(hours=1) if s.timestamp >= limite]
        except (OSError, ValueError):
            return None, 0.0
        if len(muestras) < 2:
            return None, 0.0
        media = sum(float(s.cpu) for s in muestras) / len(muestras)
        minutos = (muestras[-1].timestamp - muestras[0].timestamp) / 60
        return media, minutos

    # --- Datos caros ------------------------------------------------------
    def refresh_slow(self, force: bool = False) -> None:
        """Pide en segundo plano los programas de inicio y el tamaño de los temporales.

        Con `force` se pide aunque no haya pasado el tiempo de refresco (lo usan las
        pruebas); sin él, mirar la portada cien veces no lanza cien consultas.
        """
        if not self.enabled or self._slow_running:
            return
        if not force and time.monotonic() - self._slow_started < SLOW_REFRESH_MS / 1000:
            return
        self._slow_running = True
        self._slow_started = time.monotonic()
        run_in_background(self, self._collect_slow, self._slow_ready)

    def _collect_slow(self) -> dict[str, object]:
        """Lo que cuesta segundos: se ejecuta en un hilo aparte."""
        datos: dict[str, object] = {}
        entradas = list(startup_core.read_entries()) + list(startup_core.read_slow_entries())
        activas = [entrada for entrada in entradas if entrada.enabled]
        datos["startup_total"] = len(entradas)
        datos["startup_heavy"] = sum(
            1 for entrada in activas if entrada.cost[0] == startup_core.COST_MEDIO
        )
        datos["startup_dead"] = sum(1 for entrada in activas if entrada.target_exists is False)
        datos["startup_admin"] = sum(1 for entrada in entradas if entrada.needs_admin)
        # Se miden las categorías que la portada puede señalar sin miedo (temporales, cachés de
        # navegador, miniaturas y volcados): las que no piden administrador ni borran nada
        # irreversible. El aviso de limpieza lleva dentro las que **tienen algo**, así que al
        # abrir la ventana desde él no se marca una categoría vacía.
        plan = cleaner.scan(cleaner.RECOMMENDED_TARGETS, min_age_hours=TEMP_AGE_HOURS)
        con_algo = tuple(item.key for item in plan.available_items if item.size > 0)
        de_temp = plan.item("temp")
        datos["temp_bytes"] = de_temp.size if de_temp is not None else 0
        datos["temp_files"] = de_temp.files if de_temp is not None else 0
        datos["clean_bytes"] = plan.total_size
        datos["clean_categories"] = len(con_algo)
        datos["clean_targets"] = con_algo
        return datos

    def _slow_ready(self, resultado) -> None:
        self._slow_running = False
        if isinstance(resultado, Exception) or not isinstance(resultado, dict):
            return  # sin esos datos, los avisos que dependen de ellos no salen
        self._slow = resultado
        self.refresh()
