"""Registro de tareas de mantenimiento con persistencia en JSON."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, Signal

from . import config, logs

STATUS_PENDING = "Pendiente"
STATUS_DONE = "Completada"
PRIORITIES = ("Alta", "Normal", "Baja")

TASK_CATEGORIES = (
    "Limpieza",
    "Actualizaciones",
    "Seguridad",
    "Copias de seguridad",
    "Hardware",
    "Rendimiento",
    "Otros",
)

TEMPLATES: list[tuple[str, str, str]] = [
    ("Eliminar archivos temporales", "Limpieza", "Usa el Liberador de espacio o la acción rápida de temporales."),
    ("Analizar el disco con el analizador de espacio", "Rendimiento", "Revisa las carpetas más pesadas del sistema."),
    ("Ejecutar Windows Update", "Actualizaciones", "Instala las actualizaciones de seguridad pendientes."),
    ("Analizar el disco con Windows Defender", "Seguridad", "Análisis rápido del sistema."),
    ("Comprobar la salud del disco (SMART)", "Hardware", "Revisa el estado del disco con herramientas del fabricante."),
    ("Actualizar controladores", "Hardware", "Verifica actualizaciones en Administrador de dispositivos."),
    ("Crear una copia de seguridad", "Copias de seguridad", "Respalda documentos y fotos importantes."),
    ("Revisar programas de inicio", "Rendimiento", "Desactiva aplicaciones que no necesitas al arrancar."),
    ("Desfragmentar u optimizar unidades", "Rendimiento", "Optimiza el disco del sistema."),
    ("Vaciar la papelera de reciclaje", "Limpieza", "Libera espacio eliminando archivos antiguos."),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class MaintenanceTask:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    category: str = "Otros"
    notes: str = ""
    priority: str = "Normal"
    status: str = STATUS_PENDING
    created_at: str = field(default_factory=_now)
    due_date: str = ""
    completed_at: str = ""

    @property
    def is_done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def is_overdue(self) -> bool:
        if self.is_done or not self.due_date:
            return False
        try:
            return date.fromisoformat(self.due_date) < date.today()
        except ValueError:
            return False

    @property
    def created_display(self) -> str:
        return _pretty_date(self.created_at)

    @property
    def due_display(self) -> str:
        return _pretty_date(self.due_date) if self.due_date else "Sin fecha"

    @property
    def completed_display(self) -> str:
        return _pretty_date(self.completed_at) if self.completed_at else ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_tasks(raw: Any) -> list[MaintenanceTask]:
    """Convierte el contenido del archivo (o de una línea) en tareas, sin fallar por ruido."""
    items = raw.get("tareas", raw) if isinstance(raw, dict) else raw
    tasks: list[MaintenanceTask] = []
    if not isinstance(items, list):
        return tasks
    for item in items:
        if not isinstance(item, dict):
            continue
        data = {key: item[key] for key in item if key in MaintenanceTask.__dataclass_fields__}
        data.setdefault("title", "(sin título)")
        tasks.append(MaintenanceTask(**data))
    return tasks


def _pretty_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d/%m/%Y %H:%M")


class TaskStore(QObject):
    """Lista de tareas guardada en %APPDATA%/PCToolkit/tareas_mantenimiento.json."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tasks: list[MaintenanceTask] = []
        self.load()

    # --- Persistencia ----------------------------------------------------
    @property
    def path(self):
        return config.tasks_file()

    def load(self) -> None:
        path = self.path
        if not path.exists():
            self._tasks = []
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logs.warning(f"El archivo de tareas no se pudo leer ({path}); se empieza de cero", exc)
            try:
                shutil.copy2(path, path.with_suffix(".json.bak"))
            except OSError as copia:
                logs.warning("Tampoco se pudo guardar una copia del archivo de tareas", copia)
            self._tasks = []
            return
        self._tasks = _parse_tasks(raw)

    def save(self) -> None:
        """Guarda sin perder lo que haya escrito otro proceso y sin dejar el archivo a medias.

        El mantenimiento desatendido corre en su propio proceso y escribe este mismo
        archivo, así que volcar la copia en memoria tal cual borraría las tareas que
        hubiera añadido (o completado) desde la otra ventana. Antes de escribir se relee
        el disco y se fusiona por identificador: lo que hay en memoria manda, y lo del
        disco que aquí no está se añade. La escritura es atómica (`.tmp` +
        `os.replace`), de modo que nadie llegue a leer un archivo a medio escribir.
        """
        self._merge_from_disk()
        payload = {
            "version": 1,
            "actualizado": _now(),
            "tareas": [task.to_dict() for task in self._tasks],
        }
        ruta = self.path
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            temporal = ruta.with_name(ruta.name + ".tmp")
            temporal.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporal, ruta)
        except OSError as exc:
            # Que no se pueda guardar no puede pasar en silencio: lo que el usuario acaba
            # de escribir se perdería y no habría forma de saber por qué.
            logs.warning(f"No se pudieron guardar las tareas en {ruta}", exc)

    def _merge_from_disk(self) -> int:
        """Añade a la copia en memoria las tareas que solo estén en el disco.

        Devuelve cuántas se han traído. Las que ya están (mismo id) no se tocan: la
        versión que tiene la ventana abierta es la que el usuario acaba de cambiar.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        conocidos = {task.id for task in self._tasks}
        traidas = 0
        for tarea in _parse_tasks(raw):
            if tarea.id not in conocidos:
                self._tasks.append(tarea)
                conocidos.add(tarea.id)
                traidas += 1
        return traidas

    # --- Consultas -------------------------------------------------------
    def all(self) -> list[MaintenanceTask]:
        return list(self._tasks)

    def pending(self) -> list[MaintenanceTask]:
        return [task for task in self._tasks if not task.is_done]

    def completed(self) -> list[MaintenanceTask]:
        return [task for task in self._tasks if task.is_done]

    def get(self, task_id: str) -> MaintenanceTask | None:
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    def stats(self) -> dict[str, Any]:
        pending = self.pending()
        completed = self.completed()
        overdue = [task for task in pending if task.is_overdue]
        by_category: dict[str, int] = {}
        for task in self._tasks:
            by_category[task.category] = by_category.get(task.category, 0) + 1
        last_completed = ""
        for task in completed:
            if task.completed_at and task.completed_at > last_completed:
                last_completed = task.completed_at
        return {
            "total": len(self._tasks),
            "pendientes": len(pending),
            "completadas": len(completed),
            "vencidas": len(overdue),
            "porcentaje": round(len(completed) / len(self._tasks) * 100, 1) if self._tasks else 0.0,
            "por_categoria": by_category,
            "ultima_completada": _pretty_date(last_completed) if last_completed else "—",
        }

    def to_dicts(self) -> list[dict[str, Any]]:
        return [task.to_dict() for task in self._tasks]

    # --- Mutaciones ------------------------------------------------------
    def add(
        self,
        title: str,
        category: str = "Otros",
        notes: str = "",
        priority: str = "Normal",
        due_date: str = "",
    ) -> MaintenanceTask:
        task = MaintenanceTask(
            title=title.strip() or "(sin título)",
            category=category,
            notes=notes.strip(),
            priority=priority if priority in PRIORITIES else "Normal",
            due_date=due_date,
        )
        self._tasks.insert(0, task)
        self.save()
        self.changed.emit()
        return task

    def update(self, task_id: str, **fields: Any) -> MaintenanceTask | None:
        task = self.get(task_id)
        if not task:
            return None
        for key, value in fields.items():
            if key in MaintenanceTask.__dataclass_fields__ and key != "id":
                setattr(task, key, value)
        self.save()
        self.changed.emit()
        return task

    def toggle_done(self, task_id: str) -> MaintenanceTask | None:
        task = self.get(task_id)
        if not task:
            return None
        if task.is_done:
            task.status = STATUS_PENDING
            task.completed_at = ""
        else:
            task.status = STATUS_DONE
            task.completed_at = _now()
        self.save()
        self.changed.emit()
        return task

    def remove(self, task_id: str) -> bool:
        before = len(self._tasks)
        self._tasks = [task for task in self._tasks if task.id != task_id]
        if len(self._tasks) != before:
            self.save()
            self.changed.emit()
            return True
        return False

    def clear_completed(self) -> int:
        removed = len(self.completed())
        if removed:
            self._tasks = self.pending()
            self.save()
            self.changed.emit()
        return removed

    def add_template(self, title: str, category: str, notes: str) -> MaintenanceTask:
        due = (date.today() + timedelta(days=7)).isoformat()
        return self.add(title=title, category=category, notes=notes, priority="Normal", due_date=due)
