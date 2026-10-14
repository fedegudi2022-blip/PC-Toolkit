"""Registro de cambios reversibles de PC Toolkit («deshacer»).

Cada acción que modifica algo del equipo se anota aquí con lo necesario para
volver atrás: qué se tocó, cómo estaba antes y cómo quedó. Es deliberadamente
independiente de la interfaz y del registro de Windows —recibe diccionarios y
devuelve resúmenes— para que se pueda probar sin tocar el equipo.

Las acciones se guardan como líneas JSON en `%APPDATA%\\PCToolkit\\deshacer.jsonl`:
un registro por línea, resistente a cierres inesperados, igual que el historial
de métricas.

El archivo es texto que cualquier proceso del usuario puede editar, así que sus datos
**no se dan por buenos**: quien actúa sobre ellos (por ejemplo `startup.py` antes de
tocar el registro) comprueba que la clave de destino, la raíz del registro y el nombre
del valor estén en las listas cerradas que él mismo usa. Aquí solo se lee y se escribe.

Quien sabe deshacer una acción concreta es su propio módulo (por ejemplo
`startup.py` para las entradas de arranque): aquí solo se guarda a quién
preguntar. Así el mismo panel de deshacer sirve para todo lo que se añada más
adelante sin tocar este archivo.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import config, logs

DEFAULT_LIMIT = 200

# Tope de longitud de una línea del archivo. Lo que guarda la aplicación ocupa unos
# cientos de bytes; una línea de megabytes solo puede venir de un archivo editado a mano
# (o dañado) y no merece que se cargue en memoria para descubrir que no sirve.
MAX_LINE_BYTES = 64 * 1024

# Tipos de cambio conocidos (el identificador que usa cada módulo).
KIND_ARRANQUE = "arranque"

ORIGIN_MANUAL = "manual"
ORIGIN_AUTOMATICO = "automatico"

ORIGIN_LABELS = {
    ORIGIN_MANUAL: "a mano",
    ORIGIN_AUTOMATICO: "automático",
}


def undo_file() -> Path:
    return config.data_dir() / "deshacer.jsonl"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class UndoAction:
    """Un cambio que se puede deshacer."""

    id: str
    kind: str
    title: str
    before: str
    after: str
    at: float
    origin: str = ORIGIN_MANUAL
    data: dict[str, Any] = field(default_factory=dict)
    reverted: bool = False
    reverted_at: float | None = None

    # ------------------------------------------------------------------
    @property
    def origin_label(self) -> str:
        return ORIGIN_LABELS.get(self.origin, self.origin)

    @property
    def time_display(self) -> str:
        return datetime.fromtimestamp(self.at).strftime("%H:%M")

    @property
    def date_display(self) -> str:
        """Fecha corta; «hoy» y «ayer» se leen mejor que un número."""
        moment = datetime.fromtimestamp(self.at)
        today = datetime.now().date()
        delta = (today - moment.date()).days
        if delta == 0:
            return f"hoy {self.time_display}"
        if delta == 1:
            return f"ayer {self.time_display}"
        return moment.strftime("%d/%m %H:%M")

    @property
    def reverted_display(self) -> str:
        if not self.reverted or self.reverted_at is None:
            return ""
        return datetime.fromtimestamp(self.reverted_at).strftime("%d/%m %H:%M")

    @property
    def summary(self) -> str:
        """Frase corta y legible: «Discord: de activada a desactivada»."""
        if self.before == self.after:
            return f"{self.title}: {self.after.lower()}"
        return f"{self.title}: de {self.before.lower()} a {self.after.lower()}"

    @property
    def revertible(self) -> bool:
        return not self.reverted and bool(self.data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tipo": self.kind,
            "titulo": self.title,
            "antes": self.before,
            "despues": self.after,
            "fecha": round(float(self.at), 3),
            "origen": self.origin,
            "datos": self.data,
            "deshecho": self.reverted,
            "fecha_deshecho": round(float(self.reverted_at), 3) if self.reverted_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UndoAction | None":
        if not isinstance(data, dict):
            return None
        try:
            action = cls(
                id=str(data["id"]),
                kind=str(data["tipo"]),
                title=str(data["titulo"]),
                before=str(data.get("antes", "")),
                after=str(data.get("despues", "")),
                at=float(data["fecha"]),
                origin=str(data.get("origen", ORIGIN_MANUAL)),
                data=dict(data.get("datos") or {}),
                reverted=bool(data.get("deshecho", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None
        try:
            action.reverted_at = float(data["fecha_deshecho"]) if data.get("fecha_deshecho") else None
        except (TypeError, ValueError):
            action.reverted_at = None
        return action


# --- Quién sabe deshacer cada tipo ---------------------------------------
Reverter = Callable[[UndoAction, "UndoStore"], tuple[bool, str]]

_REVERTERS: dict[str, Reverter] = {}


def register(kind: str, reverter: Reverter) -> None:
    """Registra cómo se deshace un tipo de cambio (lo hace el módulo dueño)."""
    _REVERTERS[kind] = reverter


def can_revert(kind: str) -> bool:
    return kind in _REVERTERS


def revert_action(store: "UndoStore", action: UndoAction) -> tuple[bool, str]:
    """Deshace una acción y la marca. Devuelve (éxito, mensaje).

    El estado se relee del almacén: la copia que tiene la pantalla puede ser
    vieja (por ejemplo, si ya se deshizo desde otro sitio) y no se debe repetir
    el cambio por eso.
    """
    current = store.get(action.id) or action
    if current.reverted:
        return False, "Ese cambio ya se había deshecho."
    reverter = _REVERTERS.get(current.kind)
    if reverter is None:
        return False, "Este tipo de cambio no se puede deshacer desde aquí."
    ok, message = reverter(current, store)
    if ok:
        store.mark_reverted(current.id)
    return ok, message


class UndoStore:
    """Lista en disco de los cambios hechos, del más reciente al más antiguo."""

    def __init__(self, path: Path | None = None, limit: int = DEFAULT_LIMIT) -> None:
        self.path = Path(path) if path else undo_file()
        self.limit = max(1, int(limit))

    # ------------------------------------------------------------------
    def _read(self) -> list[UndoAction]:
        if not self.path.exists():
            return []
        actions: list[UndoAction] = []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    if len(line.encode("utf-8", errors="replace")) > MAX_LINE_BYTES:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    action = UndoAction.from_dict(data)
                    if action is not None:
                        actions.append(action)
        except OSError:
            return []
        actions.sort(key=lambda item: item.at, reverse=True)
        return actions

    def _write(self, actions: list[UndoAction]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                for action in reversed(actions):  # en disco, del más antiguo al más nuevo
                    handle.write(json.dumps(action.to_dict(), ensure_ascii=False) + "\n")
            tmp.replace(self.path)
        except OSError as exc:
            logs.warning(f"No se pudo anotar el cambio en {self.path}", exc)
            return

    # ------------------------------------------------------------------
    def add(
        self,
        kind: str,
        title: str,
        before: str,
        after: str,
        data: dict[str, Any] | None = None,
        origin: str = ORIGIN_MANUAL,
        now: float | None = None,
    ) -> UndoAction:
        """Anota un cambio ya hecho."""
        action = UndoAction(
            id=_new_id(),
            kind=kind,
            title=title,
            before=before,
            after=after,
            at=float(now if now is not None else time.time()),
            origin=origin,
            data=dict(data or {}),
        )
        actions = self._read()
        actions.insert(0, action)
        self._write(actions[: self.limit])
        return action

    def all(self) -> list[UndoAction]:
        """Todos los cambios, del más reciente al más antiguo."""
        return self._read()

    def recent(self, limit: int = 8) -> list[UndoAction]:
        return self._read()[: max(1, int(limit))]

    def get(self, action_id: str) -> UndoAction | None:
        for action in self._read():
            if action.id == action_id:
                return action
        return None

    def pending(self) -> list[UndoAction]:
        """Cambios que todavía se pueden deshacer."""
        return [action for action in self._read() if action.revertible]

    def mark_reverted(self, action_id: str, now: float | None = None) -> UndoAction | None:
        actions = self._read()
        found: UndoAction | None = None
        for action in actions:
            if action.id == action_id:
                action.reverted = True
                action.reverted_at = float(now if now is not None else time.time())
                found = action
                break
        if found is not None:
            self._write(actions)
        return found

    def clear(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError as exc:
            logs.warning(f"No se pudo vaciar el registro de cambios ({self.path})", exc)

    def count(self) -> int:
        return len(self._read())

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            return 0
