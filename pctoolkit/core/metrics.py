"""Monitor de recursos del sistema basado en psutil.

Expone `MetricsService`, un QObject que muestrea CPU, memoria, almacenamiento,
red y temperatura en un QTimer y emite cada instantánea a la interfaz.
"""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from typing import Any

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from . import logs

# Cadencia normal (ventana a la vista) y cadencia de ahorro (segundo plano).
SAMPLE_INTERVAL_MS = 1200
ECO_INTERVAL_MS = 3500

PROFILE_FULL = "completo"
PROFILE_ECO = "eco"

IS_WINDOWS = os.name == "nt"

DEFAULT_DISK_LABEL = "Disco del sistema"


@dataclass
class MetricsSnapshot:
    """Instantánea inmutable del estado del equipo."""

    timestamp: float = 0.0
    cpu_percent: float = 0.0
    cpu_per_core: list[float] = field(default_factory=list)
    cpu_freq_mhz: float | None = None
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_name: str = ""
    cpu_temp: float | None = None
    ram_percent: float = 0.0
    ram_used: int = 0
    ram_total: int = 0
    swap_percent: float = 0.0
    swap_used: int = 0
    swap_total: int = 0
    disk_percent: float = 0.0
    disk_used: int = 0
    disk_total: int = 0
    disk_free: int = 0
    disk_mount: str = "C:\\"
    disk_label: str = "Disco del sistema"
    read_rate: float = 0.0
    write_rate: float = 0.0
    net_up_rate: float = 0.0
    net_down_rate: float = 0.0
    net_sent: int = 0
    net_recv: int = 0
    # La tarjeta gráfica no la lee psutil: la trae el vigía de la GPU (`core/gpu.py`) y
    # el servicio la guarda aquí. `None` significa «todavía no se sabe» (la primera
    # lectura tarda unos segundos); un 0 es un dato: la tarjeta está parada.
    gpu_percent: float | None = None
    gpu_name: str = ""
    gpu_driver: str = ""
    gpu_engine: str = ""
    boot_time: float = 0.0
    process_count: int = 0
    battery_percent: float | None = None
    battery_plugged: bool | None = None
    load_avg: tuple[float, float, float] | None = None
    top_processes: list[tuple[str, float, float]] = field(default_factory=list)

    # --- Indicadores derivados -------------------------------------------
    @property
    def uptime_seconds(self) -> float:
        return max(0.0, time.time() - self.boot_time) if self.boot_time else 0.0

    @property
    def health_score(self) -> float:
        """Puntuación 0-100 obtenida de la carga de CPU, RAM y disco."""
        cpu = min(self.cpu_percent, 100.0)
        ram = min(self.ram_percent, 100.0)
        disk = min(self.disk_percent, 100.0)
        score = 100.0 - (cpu * 0.34 + ram * 0.36 + max(0.0, disk - 70.0) * 0.30)
        if self.cpu_temp is not None and self.cpu_temp > 45:
            score -= min(10.0, (self.cpu_temp - 45.0) * 0.4)
        return max(0.0, min(100.0, score))

    @property
    def health_label(self) -> str:
        score = self.health_score
        if score >= 85:
            return "Excelente"
        if score >= 70:
            return "Buena"
        if score >= 50:
            return "Aceptable"
        if score >= 30:
            return "Mejorable"
        return "Crítica"

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["health_score"] = round(self.health_score, 1)
        data["health_label"] = self.health_label
        data["uptime_seconds"] = round(self.uptime_seconds, 1)
        return data


def system_drive() -> str:
    """Unidad donde está instalado Windows."""
    drive = os.environ.get("SystemDrive")
    if not drive:
        return "C:\\"
    return drive.rstrip("\\") + "\\"


def system_volume_label(drive: str | None = None) -> str:
    """Etiqueta del volumen de la unidad del sistema («Windows», «Disco local»…).

    Se pregunta a Windows por el volumen de **esa** unidad (`GetVolumeInformationW`) y,
    si no contesta, se usa el sistema de archivos de esa misma partición.

    Antes esto se resolvía en cada muestra con `psutil.disk_partitions()`, que recorre
    todas las unidades (y puede quedarse esperando por una de red caída) y devolvía la
    primera de la lista, que no tiene por qué ser la del sistema. Ahora se calcula una
    sola vez, al crear el servicio.
    """
    raiz = drive or system_drive()
    if not raiz.endswith("\\"):
        raiz += "\\"
    if IS_WINDOWS:
        try:
            import ctypes

            nombre = ctypes.create_unicode_buffer(261)
            sistema_archivos = ctypes.create_unicode_buffer(261)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
                raiz, nombre, len(nombre), None, None, None, sistema_archivos, len(sistema_archivos)
            )
            if ok and nombre.value.strip():
                return nombre.value.strip()
            if ok and sistema_archivos.value.strip():
                return sistema_archivos.value.strip()
        except (AttributeError, OSError):
            pass
    try:
        for particion in psutil.disk_partitions(all=False):
            if particion.mountpoint.rstrip("\\").upper() == raiz.rstrip("\\").upper():
                if particion.fstype:
                    return particion.fstype
    except (OSError, AttributeError):
        pass
    return DEFAULT_DISK_LABEL


def cpu_display_name() -> str:
    name = platform.processor() or ""
    name = name.strip()
    if not name:
        name = platform.machine()
    return name


class MetricsService(QObject):
    """Muestrea el sistema periódicamente y publica `sampled`.

    El trabajo se adapta a lo que la interfaz necesita de verdad:

    * **perfil completo**: muestreo rápido y, si alguien lo pide, desglose por
      núcleo y lista de procesos (lo más caro de todo).
    * **perfil eco**: muestreo lento, sin desglose por núcleo y sin recorrer la
      lista de procesos; se usa cuando la ventana está oculta o en la bandeja,
      que es cuando la aplicación debe consumir lo mínimo.
    """

    sampled = Signal(MetricsSnapshot)
    profile_changed = Signal(str)

    def __init__(self, interval_ms: int = SAMPLE_INTERVAL_MS, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._interval = interval_ms
        self._profile = PROFILE_FULL
        self._per_core = True
        self._with_processes = True
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.poll)
        self._last_disk_io: tuple[float, int, int] | None = None
        self._last_net_io: tuple[float, int, int] | None = None
        self._last_time = time.time()
        self._snapshot = MetricsSnapshot()
        self._gpu: Any | None = None
        self._gpu_failed = False
        # ¿Está el vigía consultando la tarjeta ahora mismo? El historial lo necesita para
        # saber si el uso que trae la muestra es una lectura viva o el último valor conocido.
        self._gpu_watched = False
        self._temp_available = self._probe_temperatures()
        self._drive = system_drive()
        self._disk_error_logged = False
        # La etiqueta del disco se resuelve una sola vez: es un dato que no cambia y que
        # antes se consultaba en cada muestra.
        self._disk_label = system_volume_label(self._drive)
        self._cpu_name = cpu_display_name()
        # Primera lectura de cpu_percent para que la siguiente sea válida.
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

    # ------------------------------------------------------------------
    def _probe_temperatures(self) -> bool:
        try:
            return bool(psutil.sensors_temperatures())
        except (AttributeError, NotImplementedError, OSError):
            return False

    @property
    def supports_temperature(self) -> bool:
        return self._temp_available

    def set_gpu(self, info: Any | None) -> None:
        """Guarda lo que leyó el vigía de la GPU.

        No se toca la muestra ya publicada: el dato entra en la siguiente (≤1,2 s después),
        que es cuando la ventana repinta de todos modos. Así la lectura lenta de la GPU no
        obliga a repintar por su cuenta ni interfiere con el muestreo normal.

        Con `None` —una lectura que se quedó sin datos, o un equipo cuyos contadores no
        existen— se marca `gpu_failed`, para que la interfaz pueda decir que no hay datos
        en lugar de quedarse en «leyendo» para siempre.
        """
        self._gpu = info
        self._gpu_failed = info is None

    @property
    def gpu_failed(self) -> bool:
        """¿La última lectura de la tarjeta gráfica se quedó sin datos?"""
        return self._gpu_failed

    def set_gpu_watched(self, watched: bool) -> None:
        """Avisa de si el vigía está consultando la tarjeta ahora mismo.

        Lo pone la ventana al ajustar el consumo (`_apply_power_profile`). Con la consulta
        parada, el último uso leído se queda en la tarjeta del panel pero **no** entra en el
        historial: guardarlo como si fuera de este momento dibujaría una línea plana que la
        tarjeta no hizo.
        """
        self._gpu_watched = bool(watched)

    @property
    def gpu_watched(self) -> bool:
        """¿Hay una consulta de la tarjeta en marcha? (el historial deja huecos sin ella)"""
        return self._gpu_watched

    @property
    def snapshot(self) -> MetricsSnapshot:
        return self._snapshot

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def interval_ms(self) -> int:
        return self._interval

    def set_profile(self, profile: str) -> None:
        """Cambia entre «completo» y «eco» (más lento y más liviano)."""
        profile = PROFILE_ECO if profile == PROFILE_ECO else PROFILE_FULL
        if profile == self._profile:
            return
        self._profile = profile
        full = profile == PROFILE_FULL
        self._per_core = full
        self.set_interval(SAMPLE_INTERVAL_MS if full else ECO_INTERVAL_MS)
        self.profile_changed.emit(profile)

    def set_interval(self, interval_ms: int) -> None:
        self._interval = max(400, int(interval_ms))
        self._timer.setInterval(self._interval)

    def set_process_detail(self, enabled: bool) -> None:
        """Activa o desactiva el recorrido de la lista de procesos."""
        self._with_processes = bool(enabled)

    def start(self) -> None:
        self.poll(with_processes=self._with_processes)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ------------------------------------------------------------------
    def poll(self, with_processes: bool | None = None) -> MetricsSnapshot:
        """Toma una muestra. `with_processes` fuerza (o evita) la lista de procesos."""
        want_processes = self._with_processes if with_processes is None else bool(with_processes)
        snap = MetricsSnapshot(timestamp=time.time())
        now = time.time()
        elapsed = max(0.001, now - self._last_time)
        self._last_time = now

        # --- CPU -----------------------------------------------------
        snap.cpu_percent = psutil.cpu_percent(interval=None)
        snap.cpu_per_core = psutil.cpu_percent(interval=None, percpu=True) if self._per_core else []
        snap.cpu_cores = psutil.cpu_count(logical=False) or 0
        snap.cpu_threads = psutil.cpu_count(logical=True) or 0
        snap.cpu_name = self._cpu_name
        try:
            freq = psutil.cpu_freq()
            snap.cpu_freq_mhz = freq.current if freq else None
        except (AttributeError, OSError):
            snap.cpu_freq_mhz = None
        if self._temp_available:
            snap.cpu_temp = self._read_temperature()
        # La GPU viene ya leída (y de una vuelta reciente): aquí solo se copia.
        if self._gpu is not None:
            snap.gpu_percent = self._gpu.usage
            snap.gpu_name = self._gpu.name
            snap.gpu_driver = self._gpu.driver
            snap.gpu_engine = self._gpu.engine

        # --- Memoria -------------------------------------------------
        vm = psutil.virtual_memory()
        snap.ram_percent = vm.percent
        snap.ram_used = vm.used
        snap.ram_total = vm.total
        sm = psutil.swap_memory()
        snap.swap_percent = sm.percent
        snap.swap_used = sm.used
        snap.swap_total = sm.total

        # --- Almacenamiento -----------------------------------------
        try:
            usage = psutil.disk_usage(self._drive)
            snap.disk_percent = usage.percent
            snap.disk_used = usage.used
            snap.disk_total = usage.total
            snap.disk_free = usage.free
        except OSError as exc:
            # Se avisa una sola vez: esto corre en cada muestra y una unidad que no
            # responde llenaría el registro de la misma línea cada segundo.
            if not self._disk_error_logged:
                self._disk_error_logged = True
                logs.warning(f"No se pudo leer el uso de {self._drive}", exc)
        snap.disk_mount = self._drive
        snap.disk_label = self._disk_label
        try:
            io = psutil.disk_io_counters()
            if io:
                if self._last_disk_io:
                    _, r0, w0 = self._last_disk_io
                    snap.read_rate = max(0.0, (io.read_bytes - r0) / elapsed)
                    snap.write_rate = max(0.0, (io.write_bytes - w0) / elapsed)
                self._last_disk_io = (now, io.read_bytes, io.write_bytes)
        except (OSError, AttributeError):
            pass

        # --- Red -----------------------------------------------------
        try:
            net = psutil.net_io_counters()
            snap.net_sent = net.bytes_sent
            snap.net_recv = net.bytes_recv
            if self._last_net_io:
                _, s0, r0 = self._last_net_io
                snap.net_up_rate = max(0.0, (net.bytes_sent - s0) / elapsed)
                snap.net_down_rate = max(0.0, (net.bytes_recv - r0) / elapsed)
            self._last_net_io = (now, net.bytes_sent, net.bytes_recv)
        except (OSError, AttributeError):
            pass

        # --- Sistema -------------------------------------------------
        snap.boot_time = psutil.boot_time()
        try:
            snap.process_count = len(psutil.pids())
        except (OSError, AttributeError):
            snap.process_count = 0
        try:
            battery = psutil.sensors_battery()
            if battery:
                snap.battery_percent = round(battery.percent, 0)
                snap.battery_plugged = battery.power_plugged
        except (AttributeError, NotImplementedError, OSError):
            pass
        try:
            snap.load_avg = tuple(psutil.getloadavg())  # type: ignore[assignment]
        except (AttributeError, OSError):
            snap.load_avg = None
        snap.top_processes = self._top_processes() if want_processes else []

        self._snapshot = snap
        self.sampled.emit(snap)
        return snap

    def _read_temperature(self) -> float | None:
        try:
            temps = psutil.sensors_temperatures()
        except (AttributeError, NotImplementedError, OSError):
            return None
        for entries in temps.values():
            for entry in entries:
                if entry.current:
                    return round(float(entry.current), 1)
        return None

    def _top_processes(self, limit: int = 6) -> list[tuple[str, float, float]]:
        """Procesos que más CPU consumen (porcentaje, memoria MB)."""
        result: list[tuple[str, float, float]] = []
        try:
            for proc in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
                try:
                    info = proc.info
                    mem_mb = (info["memory_info"].rss / (1024 * 1024)) if info.get("memory_info") else 0.0
                    result.append((info.get("name") or "?", float(info.get("cpu_percent") or 0.0), mem_mb))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (OSError, AttributeError):
            return []
        result.sort(key=lambda item: item[1], reverse=True)
        return result[:limit]

    def system_info(self) -> dict[str, Any]:
        """Información estable del equipo para el informe."""
        return {
            "nombre_equipo": platform.node(),
            "sistema": f"{platform.system()} {platform.release()}",
            "version": platform.version(),
            "arquitectura": platform.machine(),
            "procesador": self._cpu_name,
            "grafica": self._snapshot.gpu_name,
            "controlador_grafico": self._snapshot.gpu_driver,
            "nucleos_fisicos": self._snapshot.cpu_cores,
            "procesadores_logicos": self._snapshot.cpu_threads,
            "python": platform.python_version(),
            "psutil": psutil.__version__,
        }
