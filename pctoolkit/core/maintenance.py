"""Mantenimiento automático sin interfaz (modo desatendido).

Es lo que ejecuta la tarea programada: limpiar temporales, liberar memoria
(opcional), generar un informe y dejar constancia en el registro. También se
puede lanzar a mano desde la aplicación, y siempre se puede pedir una
simulación (`dry_run`) que no modifica nada.

El mismo módulo atiende la **limpieza programada** (`--limpieza`, la tarea que crea la ventana
de «Liberar espacio»): un solo paso, con las categorías del catálogo que se elijan, sin
memoria ni informe. Comparte la limpieza (`_step_clean`) y el registro para que el histórico
tenga una sola forma, venga de donde venga.

Cada ejecución se anota como una línea JSON en
`%APPDATA%\\PCToolkit\\mantenimiento.jsonl` (igual que el historial de métricas)
para que sobreviva a cierres inesperados y se pueda leer el histórico.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import cleaner as cleaner_core
from . import logs
from . import memory as memory_core
from . import reports as reports_core
from .config import data_dir, format_bytes, reports_dir
from .disk import list_drives
from .metrics import MetricsService
from .scheduler import MaintenanceSchedule

MAX_LOG_ENTRIES = 200


@dataclass
class MaintenanceItem:
    """Resultado de un paso del mantenimiento."""

    key: str
    title: str
    ok: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paso": self.key,
            "titulo": self.title,
            "correcto": self.ok,
            "detalle": self.detail,
            # Las categorías limpiadas (clave → bytes liberados). Sin esto el histórico
            # diría que hubo una limpieza pero no de qué, y una limpieza programada de unas
            # categorías concretas no se podría distinguir de un paso cualquiera.
            "categorias": dict(self.data.get("categorias") or {}),
        }


@dataclass
class MaintenanceReport:
    """Resumen completo de una ejecución."""

    started_at: float = field(default_factory=time.time)
    duration: float = 0.0
    dry_run: bool = False
    trigger: str = "manual"
    # Cómo se llama lo que se ha ejecutado. La limpieza programada comparte histórico con el
    # mantenimiento, así que su entrada tiene que decir «Limpieza»: si las dos dijeran
    # «Mantenimiento», en la lista no se sabría cuál de las dos tareas se ejecutó.
    label: str = "Mantenimiento"
    items: list[MaintenanceItem] = field(default_factory=list)
    freed_bytes: int = 0
    removed_files: int = 0
    report_path: str = ""
    memory_freed_bytes: int = 0
    log_path: str = ""

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.items) if self.items else False

    def item(self, key: str) -> MaintenanceItem | None:
        for entry in self.items:
            if entry.key == key:
                return entry
        return None

    def summary(self) -> str:
        if not self.items:
            return "No se ejecutó ningún paso."
        parts: list[str] = []
        if self.removed_files:
            parts.append(f"{self.removed_files} archivos · {format_bytes(self.freed_bytes)}")
        if self.memory_freed_bytes:
            parts.append(f"memoria {format_bytes(self.memory_freed_bytes)}")
        if self.report_path:
            parts.append("informe generado")
        head = "Simulación" if self.dry_run else self.label
        return f"{head}: " + (" · ".join(parts) if parts else "sin cambios que aplicar") + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "momento": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at)),
            "origen": self.trigger,
            "simulacion": self.dry_run,
            "duracion_segundos": round(self.duration, 2),
            "correcto": self.ok,
            "archivos_eliminados": self.removed_files,
            "liberado_disco": self.freed_bytes,
            "liberado_memoria": self.memory_freed_bytes,
            "informe": self.report_path,
            "resumen": self.summary(),
            "pasos": [item.to_dict() for item in self.items],
        }


def maintenance_log_path() -> Path:
    return data_dir() / "mantenimiento.jsonl"


def _append_log(entry: dict[str, Any]) -> Path:
    """Añade una línea al histórico y recorta el archivo si crece demasiado."""
    path = maintenance_log_path()
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim_log(path)
    except OSError as exc:
        logs.warning(f"No se pudo anotar la ejecución en {path}", exc)
    return path


def _trim_log(path: Path, keep: int = MAX_LOG_ENTRIES) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= keep:
        return
    try:
        path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except OSError as exc:
        logs.warning(f"No se pudo recortar el histórico de mantenimiento ({path})", exc)


def recent_runs(limit: int = 8) -> list[dict[str, Any]]:
    """Últimas ejecuciones registradas, de la más reciente a la más antigua."""
    path = maintenance_log_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except (ValueError, TypeError):
            continue  # línea corrupta: se ignora, como en el historial
        if len(entries) >= limit:
            break
    return entries


def _step_clean(
    targets: Sequence[str] | None,
    min_age_hours: float,
    dry_run: bool,
    folder: str | None = None,
) -> MaintenanceItem:
    """Limpia las categorías pedidas con el catálogo de `pctoolkit.core.cleaner`.

    El paso usa el mismo catálogo que la ventana de limpieza (con su vista previa), así que
    lo que se limpia solo y lo que se limpia a mano son **las mismas cosas**: la
    programación guarda qué categorías quiere cada uno (`MaintenanceSchedule.clean_targets`).
    """
    claves = cleaner_core.normalize_keys(targets)
    if not claves:
        return MaintenanceItem(
            "limpieza",
            "Limpiar archivos",
            True,
            "No hay ninguna categoría de limpieza seleccionada.",
            {"archivos": 0, "bytes": 0, "omitidos": 0, "categorias": {}},
        )
    if dry_run:
        plan = cleaner_core.scan(claves, min_age_hours=min_age_hours, temp_folder=folder)
        detalle = plan.summary()
        if plan.total_files:
            detalle = (
                f"Se borrarían {plan.total_files} archivos ({format_bytes(plan.total_size)}) "
                f"en {len(plan.available_items)} categoría(s)."
            )
        return MaintenanceItem(
            "limpieza",
            "Limpiar archivos",
            True,
            detalle,
            # Mismas claves que en la limpieza real para que el resumen cuadre.
            {
                "archivos": plan.total_files,
                "bytes": plan.total_size,
                "omitidos": sum(item.skipped + item.locked for item in plan.items),
                "categorias": {item.key: item.size for item in plan.available_items},
            },
        )
    result = cleaner_core.clean(claves, min_age_hours=min_age_hours, temp_folder=folder)
    disponibles = [item for item in result.items if not item.blocked]
    if not disponibles:
        motivo = next((item.blocked for item in result.items if item.blocked), "")
        return MaintenanceItem(
            "limpieza",
            "Limpiar archivos",
            True,
            motivo or "No había nada que limpiar en las categorías elegidas.",
            {"archivos": 0, "bytes": 0, "omitidos": 0, "categorias": {}},
        )
    detalle = (
        f"{result.deleted} elemento(s) eliminado(s) ({format_bytes(result.freed)}); "
        f"{result.skipped} sin permisos o en uso."
    )
    if len(disponibles) > 1:
        detalle += f" Categorías: {cleaner_core.keys_label([item.key for item in disponibles])}."
    return MaintenanceItem(
        "limpieza",
        "Limpiar archivos",
        True,
        detalle,
        {
            "archivos": result.deleted,
            "bytes": result.freed,
            "omitidos": result.skipped,
            "categorias": {item.key: item.freed for item in disponibles},
        },
    )


def _step_memory(options: memory_core.MemoryOptions | None, dry_run: bool) -> MaintenanceItem:
    options = options or memory_core.MemoryOptions()
    result = memory_core.release_memory(options, trigger="mantenimiento", dry_run=dry_run)
    return MaintenanceItem(
        "memoria",
        "Liberar memoria RAM",
        result.ok,
        result.summary(),
        result.to_dict(),
    )


def _step_report(metrics: MetricsService, tasks, dry_run: bool) -> MaintenanceItem:
    try:
        # El informe necesita la lista de procesos, aunque el muestreo normal la omita.
        snapshot = metrics.poll(with_processes=True)
        drives = list_drives()
        system_info = metrics.system_info()
        store_stats = tasks.stats() if tasks is not None else {}
        task_list = tasks.to_dicts() if tasks is not None else []
        report = reports_core.build_report(snapshot, system_info, drives, task_list, store_stats)
        if dry_run:
            return MaintenanceItem(
                "informe",
                "Generar informe",
                True,
                f"Se generaría un informe con {len(drives)} unidades y {len(task_list)} tareas.",
                {"unidades": len(drives), "tareas": len(task_list)},
            )
        target = reports_dir() / f"mantenimiento_{time.strftime('%Y-%m-%d_%H%M')}.html"
        reports_core.export_html(report, target)
        return MaintenanceItem(
            "informe",
            "Generar informe",
            True,
            f"Informe guardado en {target.name}.",
            {"ruta": str(target), "salud": report["resumen"]["salud"]},
        )
    except (OSError, ValueError, KeyError) as exc:
        return MaintenanceItem("informe", "Generar informe", False, f"No se pudo generar el informe: {exc}")


def run_cleanup(
    targets: Sequence[str] | None = None,
    *,
    min_age_hours: float = 24.0,
    dry_run: bool = False,
    trigger: str = "programado",
    register: bool = True,
    temp_folder: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> MaintenanceReport:
    """Limpia solo las categorías pedidas, sin los demás pasos del mantenimiento.

    Es lo que ejecuta la tarea de limpieza programada (`--limpieza`): usa la **misma** limpieza
    del catálogo y el **mismo** registro que el mantenimiento, pero sin liberar memoria ni
    generar informe. Así se puede tener una limpieza diaria de temporales y un mantenimiento
    semanal con informe sin que una ejecución arrastre lo de la otra.

    Devuelve un informe con la misma forma que el del mantenimiento (un solo paso), para que
    el histórico y la interfaz lo lean igual y se pueda comparar con lo que se limpia a mano.
    """
    claves = cleaner_core.normalize_keys(targets)
    report = MaintenanceReport(
        dry_run=dry_run, trigger=trigger, label="Limpieza", log_path=str(maintenance_log_path())
    )
    started = time.perf_counter()
    if progress:
        progress(f"Limpiando {cleaner_core.keys_label(claves)}…")
    item = _step_clean(claves, min_age_hours, dry_run, temp_folder)
    report.items.append(item)
    report.removed_files = int(item.data.get("archivos", 0))
    report.freed_bytes = int(item.data.get("bytes", 0))
    report.duration = time.perf_counter() - started

    if register and not dry_run:
        entry = report.to_dict()
        entry["nombre_equipo"] = __import__("platform").node()
        _append_log(entry)
    report.log_path = str(maintenance_log_path())
    return report


def run_maintenance(
    schedule: MaintenanceSchedule | None = None,
    *,
    metrics: MetricsService | None = None,
    tasks=None,
    memory_options: memory_core.MemoryOptions | None = None,
    dry_run: bool = False,
    trigger: str = "programado",
    clean: bool | None = None,
    export: bool | None = None,
    release_memory: bool | None = None,
    min_age_hours: float | None = None,
    register: bool = True,
    temp_folder: str | None = None,
    clean_targets: Sequence[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> MaintenanceReport:
    """Ejecuta un ciclo completo de mantenimiento y devuelve el informe.

    Los parámetros explícitos (`clean`, `export`, `release_memory`) tienen
    prioridad sobre la configuración de `schedule`, que es lo que usa la tarea
    programada.
    """
    schedule = schedule or MaintenanceSchedule()
    do_clean = schedule.clean_temp if clean is None else clean
    do_report = schedule.export_report if export is None else export
    do_memory = schedule.release_memory if release_memory is None else release_memory
    age = schedule.temp_min_age_hours if min_age_hours is None else min_age_hours
    # Sin categorías explícitas manda la programación; y si tampoco dice ninguna (una
    # programación construida a mano, o una guardada antes de que existiera el catálogo), el
    # catálogo se queda con la de siempre: los temporales del usuario. Es lo que hacía el
    # mantenimiento antes de que hubiera categorías, y no limpiar nada sería un cambio
    # silencioso de comportamiento.
    categorias = schedule.clean_targets if clean_targets is None else clean_targets
    if not cleaner_core.normalize_keys(categorias):
        categorias = cleaner_core.DEFAULT_TARGETS

    report = MaintenanceReport(dry_run=dry_run, trigger=trigger, log_path=str(maintenance_log_path()))
    started = time.perf_counter()
    own_metrics = metrics is None
    service = metrics or MetricsService()
    own_tasks = tasks is None
    if own_tasks:
        from .tasks import TaskStore

        tasks = TaskStore()

    def note(message: str) -> None:
        if progress:
            progress(message)

    try:
        if do_clean:
            note(f"Limpiando archivos ({cleaner_core.keys_label(categorias)})…")
            item = _step_clean(categorias, age, dry_run, temp_folder)
            report.items.append(item)
            report.removed_files += int(item.data.get("archivos", 0))
            report.freed_bytes += int(item.data.get("bytes", 0))
        if do_memory:
            note("Liberando memoria RAM…")
            item = _step_memory(memory_options, dry_run)
            report.items.append(item)
            report.memory_freed_bytes = int(item.data.get("liberado_mb", 0) * 1024 * 1024)
        if do_report:
            note("Generando el informe…")
            item = _step_report(service, tasks, dry_run)
            report.items.append(item)
            report.report_path = str(item.data.get("ruta", ""))
        if not report.items:
            report.items.append(
                MaintenanceItem("nada", "Comprobar configuración", True, "No había ningún paso activado.")
            )
    finally:
        if own_metrics:
            service.stop()

    report.duration = time.perf_counter() - started

    if register and not dry_run:
        entry = report.to_dict()
        entry["nombre_equipo"] = __import__("platform").node()
        _append_log(entry)
        if schedule.auto_log_task and tasks is not None and (do_clean or do_report or do_memory):
            try:
                task = tasks.add(
                    title="Mantenimiento automático",
                    category="Automatización",
                    notes=report.summary(),
                )
                tasks.toggle_done(task.id)
            except (OSError, AttributeError):
                pass
    report.log_path = str(maintenance_log_path())
    return report
