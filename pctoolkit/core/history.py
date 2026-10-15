"""Historial de métricas de las últimas 24 horas.

Los muestreos se guardan como líneas JSON en `%APPDATA%\\PCToolkit\\historial.jsonl`
(un registro por línea, resistente a cierres inesperados) y se recortan para
mantener solo la ventana de retención.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from . import config, logs

RETENTION_HOURS = 24
DEFAULT_INTERVAL_SECONDS = 30


def history_file() -> Path:
    return config.data_dir() / "historial.jsonl"


@dataclass
class Sample:
    """Un punto del historial.

    La GPU es la única métrica que no mide el muestreo normal: la trae su vigía
    (`ui/gpu_monitor.py`) y solo existe mientras se está consultando, así que aquí un
    `None` no es un cero, es «no se sabe». La gráfica lo dibuja como un hueco, que es la
    verdad, en lugar de unir los extremos con una línea que nadie midió.
    """

    timestamp: float
    cpu: float
    ram: float
    disk: float
    net_down: float = 0.0
    net_up: float = 0.0
    gpu: float | None = None

    def to_json(self) -> str:
        datos = {
            "t": round(float(self.timestamp), 1),
            "c": round(float(self.cpu), 1),
            "r": round(float(self.ram), 1),
            "d": round(float(self.disk), 1),
            "nd": round(float(self.net_down), 1),
            "nu": round(float(self.net_up), 1),
        }
        # La clave de la GPU solo se escribe cuando hay dato: así el archivo no engorda con
        # `null`s y las muestras guardadas antes de que la tarjeta entrara en el historial
        # se leen tal cual, con la clave ausente.
        if self.gpu is not None:
            datos["g"] = round(float(self.gpu), 1)
        return json.dumps(datos, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sample | None":
        try:
            return cls(
                timestamp=float(data["t"]),
                cpu=float(data.get("c", 0.0)),
                ram=float(data.get("r", 0.0)),
                disk=float(data.get("d", 0.0)),
                net_down=float(data.get("nd", 0.0)),
                net_up=float(data.get("nu", 0.0)),
                gpu=float(data["g"]) if data.get("g") is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_snapshot(
        cls, snapshot, timestamp: float | None = None, gpu: float | None = None
    ) -> "Sample":
        """Copia una muestra del servicio de métricas.

        El uso de la GPU se pasa aparte a propósito: no sale del muestreo sino de su vigía,
        y quien sabe si la lectura que trae la muestra está viva —y por tanto si se puede
        guardar— es quien graba, no esto.
        """
        return cls(
            timestamp=float(timestamp if timestamp is not None else time.time()),
            cpu=float(snapshot.cpu_percent),
            ram=float(snapshot.ram_percent),
            disk=float(snapshot.disk_percent),
            net_down=float(snapshot.net_down_rate),
            net_up=float(snapshot.net_up_rate),
            gpu=None if gpu is None else float(gpu),
        )


class HistoryStore:
    """Almacén en disco del historial (una línea JSON por muestra)."""

    def __init__(self, path: Path | None = None, retention_hours: int = RETENTION_HOURS) -> None:
        self.path = Path(path) if path else history_file()
        self.retention_hours = retention_hours
        self._writes_since_prune = 0

    # ------------------------------------------------------------------
    def append(self, sample: Sample) -> None:
        """Añade una muestra y recorta el archivo de vez en cuando."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(sample.to_json() + "\n")
        except OSError:
            return
        self._writes_since_prune += 1
        if self._writes_since_prune >= 200:
            self.prune()

    def load(self, hours: int | None = None, now: float | None = None) -> list[Sample]:
        """Devuelve las muestras dentro de la ventana temporal, en orden."""
        if not self.path.exists():
            return []
        cutoff = (now if now is not None else time.time()) - (hours or self.retention_hours) * 3600
        samples: list[Sample] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sample = Sample.from_dict(data) if isinstance(data, dict) else None
                    if sample and sample.timestamp >= cutoff:
                        samples.append(sample)
        except OSError:
            return []
        samples.sort(key=lambda item: item.timestamp)
        return samples

    def prune(self, now: float | None = None) -> int:
        """Reescribe el archivo conservando solo la ventana de retención."""
        cutoff = (now if now is not None else time.time()) - self.retention_hours * 3600
        samples = self.load(hours=self.retention_hours, now=now)
        kept = [sample for sample in samples if sample.timestamp >= cutoff]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                for sample in kept:
                    handle.write(sample.to_json() + "\n")
            tmp.replace(self.path)
        except OSError:
            return 0
        self._writes_since_prune = 0
        return len(kept)

    def clear(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError as exc:
            logs.warning(f"No se pudo vaciar el historial ({self.path})", exc)
        self._writes_since_prune = 0

    def count(self) -> int:
        if not self.path.exists():
            return 0
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            return 0

    # ------------------------------------------------------------------
    def stats(self, hours: int = RETENTION_HOURS, now: float | None = None) -> dict[str, Any]:
        """Promedios y máximos por métrica en la ventana indicada.

        De cada métrica se cuentan también sus `datos`: la serie de la GPU tiene huecos —solo
        se guarda mientras se consulta la tarjeta— y un 0 % diría que no trabajó justo
        cuando lo que pasó es que nadie miró.
        """
        samples = self.load(hours=hours, now=now)
        result: dict[str, Any] = {
            "muestras": len(samples),
            "desde": samples[0].timestamp if samples else None,
            "hasta": samples[-1].timestamp if samples else None,
            "horas": hours,
        }
        for key in ("cpu", "ram", "disk", "gpu"):
            values = [valor for valor in (getattr(sample, key) for sample in samples) if valor is not None]
            result[key] = {
                "media": round(sum(values) / len(values), 1) if values else 0.0,
                "maximo": round(max(values), 1) if values else 0.0,
                "minimo": round(min(values), 1) if values else 0.0,
                "actual": round(values[-1], 1) if values else 0.0,
                "datos": len(values),
            }
        return result


class HistoryRecorder(QObject):
    """Escucha las métricas y guarda una muestra cada `interval_seconds`."""

    recorded = Signal(object)

    def __init__(
        self,
        metrics,
        store: HistoryStore | None = None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.metrics = metrics
        self.store = store or HistoryStore()
        self.interval_seconds = max(1, int(interval_seconds))
        self._last_recorded = 0.0

    def attach(self) -> None:
        self.metrics.sampled.connect(self.on_sample)

    def on_sample(self, snapshot) -> None:
        now = time.time()
        if now - self._last_recorded < self.interval_seconds:
            return
        self.record(snapshot, now)

    def record(self, snapshot, now: float | None = None) -> Sample:
        timestamp = float(now if now is not None else time.time())
        sample = Sample.from_snapshot(snapshot, timestamp=timestamp, gpu=self.current_gpu(snapshot))
        self.store.append(sample)
        self._last_recorded = timestamp
        self.recorded.emit(sample)
        return sample

    def current_gpu(self, snapshot) -> float | None:
        """Uso de la GPU que corresponde a esta muestra, o `None` si no hay lectura viva.

        Se guarda el valor de la tarjeta **solo si se está consultando** (el servicio de
        métricas lo dice): el último dato conocido no se repite en la muestra siguiente,
        porque repetirlo muestra tras muestra dibujaría una línea plana que la tarjeta no hizo
        justo cuando el vigía dejó de mirarla. Sin dato, la gráfica deja un hueco.

        El indicador se lee con `getattr` para que un servicio de métricas de mentira (las
        pruebas) no tenga que traerlo para poder grabar.
        """
        if snapshot.gpu_percent is None or not getattr(self.metrics, "gpu_watched", False):
            return None
        return float(snapshot.gpu_percent)
