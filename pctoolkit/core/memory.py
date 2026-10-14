"""Liberador de memoria RAM para Windows (enfoque tipo MemReduct).

Tres técnicas, de menos a más agresiva:

1. **Recorte de los conjuntos de trabajo** (`EmptyWorkingSet`, con
   `SetProcessWorkingSetSizeEx` como respaldo): devuelve a las listas libres
   las páginas que cada proceso no está usando. Es la técnica segura y la que
   produce el efecto visible en el uso de RAM.
2. **Recorte de los conjuntos de trabajo del sistema**
   (`NtSetSystemInformation` con `MemoryEmptyWorkingSets`).
3. **Purga de la lista de espera** (`MemoryPurgeStandbyList`): vacía la caché
   en espera, la mayor reserva reclamable de Windows. Requiere el privilegio
   `SeProfileSingleProcessPrivilege`; si no se puede obtener, se informa en el
   resultado en lugar de fallar en silencio.

Nada se hace «a lo bruto»: los procesos críticos del sistema se saltan siempre,
la limpieza se puede pedir en modo simulación y cada acción se mide antes y
después para poder decir con datos cuánto se ha liberado realmente.

Importante (y honesto): recortar conjuntos de trabajo devuelve memoria a la
lista libre, pero no crea memoria nueva. Las aplicaciones recargarán de disco
lo que necesiten, así que se recomienda un umbral alto y no abusar.
"""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import psutil

from .config import as_flag, as_integer, as_number

IS_WINDOWS = os.name == "nt"

# Procesos cuyo conjunto de trabajo no se toca nunca: hacerlo no libera nada
# útil y puede provocar microcortes del sistema.
CRITICAL_NAMES = frozenset(
    {
        "system",
        "system idle process",
        "registry",
        "memory compression",
        "memcompression",
        "secure system",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "lsaiso.exe",
        "fontdrvhost.exe",
        "dwm.exe",
        "audiodg.exe",
        "msmpeng.exe",
        # El escritorio y sus compañeros: recortarles el conjunto de trabajo provoca
        # microcortes (parpadeo del escritorio, menús que tardan en aparecer) y no
        # devuelve nada aprovechable, porque vuelven a cargar lo mismo enseguida.
        "explorer.exe",
        "sihost.exe",
        "ctfmon.exe",
        "searchhost.exe",
    }
)
CRITICAL_PIDS = frozenset({0, 4})

# --- API de Windows -------------------------------------------------------
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100
TRIM_ACCESS = PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA

SYSTEM_MEMORY_LIST_INFORMATION = 80
MEMORY_EMPTY_WORKING_SETS = 2
MEMORY_PURGE_STANDBY_LIST = 4
MEMORY_PURGE_LOW_PRIORITY_STANDBY_LIST = 5

SE_PROFILE_SINGLE_PROCESS = "SeProfileSingleProcessPrivilege"
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002


@dataclass(frozen=True)
class ProcessTarget:
    """Proceso candidato a que se le recorte el conjunto de trabajo."""

    pid: int
    name: str
    rss: int

    @property
    def megabytes(self) -> float:
        return self.rss / (1024 * 1024)


def is_critical(pid: int, name: str) -> bool:
    """¿Es un proceso que nunca debemos tocar?"""
    if pid in CRITICAL_PIDS:
        return True
    return (name or "").strip().lower() in CRITICAL_NAMES


def select_targets(
    processes: Iterable[tuple[int, str, int]],
    min_mb: float = 20.0,
    *,
    include_own: bool = False,
    own_pid: int | None = None,
    foreground_pid: int | None = None,
) -> list[ProcessTarget]:
    """Filtra la lista de procesos y devuelve los que merecen la pena.

    Separado de psutil a propósito: recibe tuplas `(pid, nombre, rss)` para
    poder probarse con datos sintéticos y sin depender del equipo real.

    Con `foreground_pid` se deja fuera el proceso de la ventana en primer plano: es la que
    el usuario está usando y la que notaría el recorte. Quien limpia de verdad lo averigua
    con `current_foreground_pid()`.
    """
    own = os.getpid() if own_pid is None else own_pid
    minimum = max(0.0, float(min_mb)) * 1024 * 1024
    targets: list[ProcessTarget] = []
    for pid, name, rss in processes:
        if not include_own and pid == own:
            continue
        if foreground_pid and pid == foreground_pid:
            continue
        if is_critical(pid, name):
            continue
        if rss < minimum:
            continue
        targets.append(ProcessTarget(pid=pid, name=name or f"PID {pid}", rss=int(rss)))
    targets.sort(key=lambda item: item.rss, reverse=True)
    return targets


def current_foreground_pid() -> int:
    """PID del proceso de la ventana que el usuario tiene delante (0 si no se sabe).

    Recortar el conjunto de trabajo de la ventana en uso se nota de inmediato: es la que
    el usuario está mirando y a la que va a volver en el siguiente clic, así que se deja
    en paz. `GetForegroundWindow` da la ventana y `GetWindowThreadProcessId`, su proceso.
    """
    if not IS_WINDOWS:
        return 0
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        ventana = user32.GetForegroundWindow()
        if not ventana:
            return 0
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(ventana, ctypes.byref(pid))
        return int(pid.value)
    except (AttributeError, OSError, ValueError):
        return 0


def collect_processes() -> list[tuple[int, str, int]]:
    """Instantánea de procesos: `(pid, nombre, RSS)`."""
    result: list[tuple[int, str, int]] = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            memory = info.get("memory_info")
            result.append((int(info["pid"]), str(info.get("name") or ""), int(memory.rss) if memory else 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue
    return result


# --- Llamadas nativas -----------------------------------------------------


_DLLS: dict[str, Any] = {}


def _dll(name: str):
    """Carga (y memoriza) una DLL de Windows."""
    if name not in _DLLS:
        _DLLS[name] = ctypes.WinDLL(name)
    return _DLLS[name]


def _psapi():
    return _dll("psapi.dll")


def _kernel32():
    """kernel32 con las firmas correctas (los HANDLE son de 64 bits)."""
    lib = _dll("kernel32.dll")
    if not getattr(lib, "_pctoolkit_typed", False):
        lib.GetCurrentProcess.restype = ctypes.c_void_p
        lib.OpenProcess.restype = ctypes.c_void_p
        lib.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        lib.CloseHandle.argtypes = [ctypes.c_void_p]
        lib.SetProcessWorkingSetSizeEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_ulong,
        ]
        lib._pctoolkit_typed = True
    return lib


def _ntdll():
    return _dll("ntdll.dll")


def enable_privilege(name: str) -> bool:
    """Activa un privilegio del token actual (necesario para purgar la caché)."""
    if not IS_WINDOWS:
        return False
    try:
        advapi32 = _dll("advapi32.dll")
        kernel32 = _kernel32()
        if not getattr(advapi32, "_pctoolkit_typed", False):
            advapi32.OpenProcessToken.restype = ctypes.c_int
            advapi32.OpenProcessToken.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            advapi32.LookupPrivilegeValueW.restype = ctypes.c_int
            advapi32.LookupPrivilegeValueW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_void_p,
            ]
            advapi32.AdjustTokenPrivileges.restype = ctypes.c_int
            advapi32.AdjustTokenPrivileges.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            advapi32._pctoolkit_typed = True
        token = ctypes.c_void_p()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)
        ):
            return False

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_ulong)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", ctypes.c_ulong), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            kernel32.CloseHandle(token)
            return False
        privileges = TOKEN_PRIVILEGES()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        ok = bool(
            advapi32.AdjustTokenPrivileges(
                token, False, ctypes.byref(privileges), ctypes.sizeof(privileges), None, None
            )
        )
        # AdjustTokenPrivileges devuelve éxito aunque el privilegio no se conceda:
        # el error real se consulta con GetLastError (ERROR_NOT_ALL_ASSIGNED = 1300).
        if ok:
            try:
                ok = ctypes.GetLastError() == 0
            except AttributeError:  # pragma: no cover - fuera de Windows
                pass
        kernel32.CloseHandle(token)
        return ok
    except (OSError, AttributeError):
        return False


def trim_process(pid: int) -> bool:
    """Recorta el conjunto de trabajo de un proceso."""
    if not IS_WINDOWS:
        return False
    handle = None
    try:
        psapi = _psapi()
        psapi.EmptyWorkingSet.restype = ctypes.c_int
        psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
        handle = _kernel32().OpenProcess(TRIM_ACCESS, False, int(pid))
        if not handle:
            return False
        if psapi.EmptyWorkingSet(handle):
            return True
        # Respaldo: dejar el conjunto de trabajo en su tamaño mínimo.
        return bool(
            _kernel32().SetProcessWorkingSetSizeEx(
                handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1), ctypes.c_ulong(0)
            )
        )
    except (OSError, AttributeError):
        return False
    finally:
        if handle:
            try:
                _kernel32().CloseHandle(handle)
            except (OSError, AttributeError):
                pass


def _set_memory_list(command: int) -> bool:
    """Envía una orden a la lista de memoria del sistema."""
    if not IS_WINDOWS:
        return False
    try:
        value = ctypes.c_ulong(command)
        status = _ntdll().NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(value), ctypes.sizeof(value)
        )
        return status == 0
    except (OSError, AttributeError):
        return False


def empty_system_working_sets() -> bool:
    return _set_memory_list(MEMORY_EMPTY_WORKING_SETS)


def purge_standby_list(low_priority: bool = False) -> bool:
    """Purga la lista de espera (requiere ser administrador)."""
    command = MEMORY_PURGE_LOW_PRIORITY_STANDBY_LIST if low_priority else MEMORY_PURGE_STANDBY_LIST
    if not enable_privilege(SE_PROFILE_SINGLE_PROCESS):
        return False
    return _set_memory_list(command)


def has_standby_privilege() -> bool:
    """¿Podemos purgar la lista de espera en este equipo?"""
    return enable_privilege(SE_PROFILE_SINGLE_PROCESS)


# --- Opciones y resultados ------------------------------------------------


@dataclass
class MemoryOptions:
    """Configuración de la liberación de memoria.

    Lo que viene activado de fábrica es lo que no puede hacer daño: recortar procesos.
    Las dos operaciones sobre la memoria del sistema —vaciar la caché y purgar por
    completo la lista de espera— van apagadas y se ofrecen como opción, porque son las
    que obligan a ejecutar como administrador y las que más se nota que se han hecho
    (Windows vuelve a llenar esa caché leyendo del disco). La purga suave solo vacía las
    páginas de baja prioridad, que es la versión intermedia.
    """

    auto_enabled: bool = False
    threshold_percent: float = 85.0
    cooldown_minutes: int = 10
    sustained_samples: int = 3
    min_process_mb: float = 30.0
    trim_processes: bool = True
    empty_system_cache: bool = False
    purge_standby: bool = False
    purge_standby_low_priority: bool = False
    include_own_process: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto": bool(self.auto_enabled),
            "umbral": float(self.threshold_percent),
            "espera_min": int(self.cooldown_minutes),
            "muestras": int(self.sustained_samples),
            "min_mb": float(self.min_process_mb),
            "recortar_procesos": bool(self.trim_processes),
            "vaciado_sistema": bool(self.empty_system_cache),
            "lista_espera": bool(self.purge_standby),
            "lista_espera_suave": bool(self.purge_standby_low_priority),
            "incluir_propio": bool(self.include_own_process),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MemoryOptions":
        """Reconstruye las opciones saneando cada campo por separado.

        Un valor corrupto no descarta los demás: se sustituye por su valor por
        defecto y el resto de la configuración del usuario se conserva.
        """
        data = data if isinstance(data, dict) else {}
        defaults = cls()
        return cls(
            auto_enabled=as_flag(data.get("auto"), defaults.auto_enabled),
            threshold_percent=as_number(data.get("umbral"), defaults.threshold_percent, 30.0, 99.0),
            cooldown_minutes=as_integer(data.get("espera_min"), defaults.cooldown_minutes, 1, 720),
            sustained_samples=as_integer(data.get("muestras"), defaults.sustained_samples, 1, 60),
            min_process_mb=as_number(data.get("min_mb"), defaults.min_process_mb, 0.0, 2048.0),
            trim_processes=as_flag(data.get("recortar_procesos"), defaults.trim_processes),
            empty_system_cache=as_flag(data.get("vaciado_sistema"), defaults.empty_system_cache),
            purge_standby=as_flag(data.get("lista_espera"), defaults.purge_standby),
            purge_standby_low_priority=as_flag(
                data.get("lista_espera_suave"), defaults.purge_standby_low_priority
            ),
            include_own_process=as_flag(data.get("incluir_propio"), defaults.include_own_process),
        )


@dataclass
class MemoryCleanResult:
    """Lo que realmente ha pasado en una limpieza."""

    started_at: float = field(default_factory=time.time)
    duration: float = 0.0
    trigger: str = "manual"
    before_percent: float = 0.0
    after_percent: float = 0.0
    before_available: int = 0
    after_available: int = 0
    total_bytes: int = 0
    processes_trimmed: int = 0
    processes_skipped: int = 0
    trimmed_bytes: int = 0
    system_cache_empty: bool = False
    standby_purged: bool = False
    standby_message: str = ""
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def freed_bytes(self) -> int:
        """Memoria disponible ganada (puede ser 0 si otra cosa la consumió)."""
        return max(0, self.after_available - self.before_available)

    @property
    def freed_mb(self) -> float:
        return self.freed_bytes / (1024 * 1024)

    @property
    def percent_gain(self) -> float:
        return max(0.0, self.before_percent - self.after_percent)

    @property
    def ok(self) -> bool:
        return self.processes_trimmed > 0 or self.system_cache_empty or self.standby_purged

    def summary(self) -> str:
        """Texto corto para la barra de estado."""
        if self.dry_run:
            return (
                f"Simulación: se recortarían {self.processes_trimmed} procesos "
                f"({self.trimmed_bytes / (1024 * 1024):.0f} MB)."
            )
        if not self.ok:
            return "No se pudo liberar memoria: ningún proceso accesible."
        parts = [f"Liberados {self.freed_mb:.0f} MB"]
        parts.append(f"{self.processes_trimmed} procesos")
        if self.standby_purged:
            parts.append("caché purgada")
        if self.errors:
            parts.append(f"{len(self.errors)} avisos")
        return " · ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "momento": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at)),
            "origen": self.trigger,
            "duracion_segundos": round(self.duration, 2),
            "antes_porcentaje": round(self.before_percent, 1),
            "despues_porcentaje": round(self.after_percent, 1),
            "liberado_mb": round(self.freed_mb, 1),
            "trabajados_mb": round(self.trimmed_bytes / (1024 * 1024), 1),
            "procesos": self.processes_trimmed,
            "omitidos": self.processes_skipped,
            "capa_sistema": self.system_cache_empty,
            "lista_espera": self.standby_purged,
            "avisos": list(self.errors),
        }


def _available_bytes() -> int:
    """Memoria disponible según el sistema (la que verá el usuario)."""
    try:
        return int(psutil.virtual_memory().available)
    except (OSError, AttributeError):  # pragma: no cover - defensivo
        return 0


def release_memory(
    options: MemoryOptions | None = None,
    *,
    trigger: str = "manual",
    targets: list[ProcessTarget] | None = None,
    dry_run: bool = False,
) -> MemoryCleanResult:
    """Libera memoria RAM aplicando las técnicas activadas en `options`.

    Si se pasa `targets`, se usa esa lista (útil para limpiar un único proceso
    o para probar sin depender de los procesos reales).
    """
    options = options or MemoryOptions()
    before = psutil.virtual_memory()
    result = MemoryCleanResult(
        trigger=trigger,
        before_percent=float(before.percent),
        before_available=int(before.available),
        total_bytes=int(before.total),
        dry_run=dry_run,
    )
    started = time.perf_counter()

    if targets is None and not options.trim_processes:
        targets = []
    if targets is None:
        targets = select_targets(
            collect_processes(),
            options.min_process_mb,
            include_own=options.include_own_process,
            # La ventana que el usuario tiene delante se queda como está.
            foreground_pid=current_foreground_pid(),
        )

    trimmed = 0
    skipped = 0
    for target in targets:
        if dry_run:
            trimmed += 1
            result.trimmed_bytes += target.rss
            continue
        before_rss = target.rss
        if target.pid > 0:
            try:
                before_rss = psutil.Process(target.pid).memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if trim_process(target.pid):
            trimmed += 1
            after_rss = 0
            try:
                after_rss = psutil.Process(target.pid).memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                after_rss = before_rss
            result.trimmed_bytes += max(0, before_rss - after_rss)
        else:
            skipped += 1

    # Las dos operaciones sobre la memoria del sistema comparten privilegio:
    # o se conceden las dos o se informa una sola vez de que hacen falta permisos.
    purga_suave = options.purge_standby_low_priority and not options.purge_standby
    if (options.empty_system_cache or options.purge_standby or purga_suave) and not dry_run:
        if not enable_privilege(SE_PROFILE_SINGLE_PROCESS):
            result.standby_message = (
                "La caché del sistema y la lista de espera requieren ejecutar PC Toolkit "
                "como administrador."
            )
            result.errors.append(result.standby_message)
        else:
            if options.empty_system_cache:
                result.system_cache_empty = empty_system_working_sets()
                if not result.system_cache_empty:
                    result.errors.append("El sistema rechazó el vaciado de la caché.")
            if options.purge_standby:
                result.standby_purged = purge_standby_list()
                result.standby_message = (
                    "Caché en espera purgada."
                    if result.standby_purged
                    else "El sistema rechazó la purga de la caché en espera."
                )
                if not result.standby_purged:
                    result.errors.append(result.standby_message)
            elif purga_suave:
                # Solo las páginas de baja prioridad: libera menos, pero sin vaciar de golpe
                # todo lo que Windows tenía cacheado para volver a leer del disco.
                result.standby_purged = purge_standby_list(low_priority=True)
                result.standby_message = (
                    "Caché en espera purgada (solo páginas de baja prioridad)."
                    if result.standby_purged
                    else "El sistema rechazó la purga suave de la caché en espera."
                )
                if not result.standby_purged:
                    result.errors.append(result.standby_message)
    elif dry_run:
        result.system_cache_empty = bool(options.empty_system_cache)
        result.standby_purged = bool(options.purge_standby or purga_suave)

    result.processes_trimmed = trimmed
    result.processes_skipped = skipped
    if not dry_run:
        time.sleep(0.25)  # deja que Windows consolide la liberación
    after = psutil.virtual_memory()
    result.after_percent = float(after.percent)
    result.after_available = int(after.available)
    result.duration = time.perf_counter() - started
    return result


class MemoryGuard:
    """Decide cuándo toca una limpieza automática.

    Solo dispara si está activada, la RAM lleva varias muestras por encima del
    umbral y ha pasado el tiempo de espera desde la última limpieza: así se
    evita el bucle «libero, la RAM sube otra vez, libero…».
    """

    def __init__(self, options: MemoryOptions | None = None, now: float | None = None) -> None:
        self.options = options or MemoryOptions()
        self._streak = 0
        self._last_clean = now
        self._last_result: MemoryCleanResult | None = None

    @property
    def streak(self) -> int:
        return self._streak

    @property
    def last_result(self) -> MemoryCleanResult | None:
        return self._last_result

    @property
    def cooldown_remaining(self) -> float:
        if self._last_clean is None:
            return 0.0
        elapsed = time.time() - self._last_clean
        return max(0.0, self.options.cooldown_minutes * 60 - elapsed)

    def observe(self, percent: float, now: float | None = None) -> bool:
        """Registra una muestra y responde si hay que limpiar ahora."""
        moment = time.time() if now is None else now
        if not self.options.auto_enabled:
            self._streak = 0
            return False
        if float(percent) >= self.options.threshold_percent:
            self._streak += 1
        else:
            self._streak = 0
        if self._streak < max(1, self.options.sustained_samples):
            return False
        if self._last_clean is not None and moment - self._last_clean < self.options.cooldown_minutes * 60:
            return False
        return True

    def note_clean(self, result: MemoryCleanResult | None = None, now: float | None = None) -> None:
        self._last_clean = time.time() if now is None else now
        self._streak = 0
        if result is not None:
            self._last_result = result

    def reset(self) -> None:
        self._streak = 0
        self._last_clean = None
        self._last_result = None
