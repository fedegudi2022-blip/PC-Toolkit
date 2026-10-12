"""Motor de alertas por umbrales de CPU, memoria y disco.

La lógica es deliberadamente independiente de la interfaz: recibe instantáneas
y devuelve eventos de alerta, de modo que pueda probarse sin abrir ventanas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

METRIC_CPU = "cpu"
METRIC_RAM = "ram"
METRIC_DISK = "disk"
METRICS = (METRIC_CPU, METRIC_RAM, METRIC_DISK)

METRIC_LABELS = {
    METRIC_CPU: "CPU",
    METRIC_RAM: "Memoria RAM",
    METRIC_DISK: "Disco del sistema",
}

DEFAULT_THRESHOLDS = {METRIC_CPU: 90.0, METRIC_RAM: 90.0, METRIC_DISK: 90.0}


@dataclass
class AlertConfig:
    """Umbrales y comportamiento de las alertas."""

    enabled: bool = True
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    sustained_samples: int = 3
    cooldown_minutes: int = 15

    def threshold_for(self, metric: str) -> float:
        return float(self.thresholds.get(metric, DEFAULT_THRESHOLDS.get(metric, 90.0)))

    def set_threshold(self, metric: str, value: float) -> None:
        self.thresholds[metric] = max(1.0, min(100.0, float(value)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "thresholds": {key: float(value) for key, value in self.thresholds.items()},
            "sustained_samples": int(self.sustained_samples),
            "cooldown_minutes": int(self.cooldown_minutes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AlertConfig":
        config = cls()
        if not isinstance(data, dict):
            return config
        config.enabled = bool(data.get("enabled", True))
        thresholds = data.get("thresholds")
        if isinstance(thresholds, dict):
            for metric in METRICS:
                if metric in thresholds:
                    try:
                        config.set_threshold(metric, float(thresholds[metric]))
                    except (TypeError, ValueError):
                        continue
        try:
            config.sustained_samples = max(1, min(30, int(data.get("sustained_samples", 3))))
        except (TypeError, ValueError):
            pass
        try:
            config.cooldown_minutes = max(1, min(240, int(data.get("cooldown_minutes", 15))))
        except (TypeError, ValueError):
            pass
        return config


@dataclass
class AlertEvent:
    """Aviso generado al superarse un umbral de forma sostenida."""

    metric: str
    value: float
    threshold: float
    at: datetime

    @property
    def label(self) -> str:
        return METRIC_LABELS.get(self.metric, self.metric)

    @property
    def title(self) -> str:
        return f"{self.label} al {self.value:.0f}%"

    @property
    def message(self) -> str:
        return (
            f"El uso de {self.label.lower()} llegó al {self.value:.0f}%, "
            f"por encima del umbral configurado ({self.threshold:.0f}%).\n"
            "Revisa los procesos activos o libera espacio."
        )

    @property
    def time_display(self) -> str:
        return self.at.strftime("%H:%M:%S")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": round(self.value, 1),
            "threshold": self.threshold,
            "at": self.at.isoformat(timespec="seconds"),
        }


class AlertEngine:
    """Evalúa cada instantánea y decide cuándo avisar."""

    def __init__(self, config: AlertConfig | None = None, now: datetime | None = None) -> None:
        self.config = config or AlertConfig()
        self._streaks: dict[str, int] = {metric: 0 for metric in METRICS}
        self._cooldown_until: dict[str, datetime | None] = {metric: None for metric in METRICS}
        self._now = now

    # ------------------------------------------------------------------
    def _clock(self) -> datetime:
        return self._now or datetime.now()

    def reset(self) -> None:
        for metric in METRICS:
            self._streaks[metric] = 0
            self._cooldown_until[metric] = None

    @staticmethod
    def value_for(snapshot, metric: str) -> float:
        return {
            METRIC_CPU: lambda s: s.cpu_percent,
            METRIC_RAM: lambda s: s.ram_percent,
            METRIC_DISK: lambda s: s.disk_percent,
        }[metric](snapshot)

    def status(self) -> dict[str, dict[str, Any]]:
        """Estado interno por métrica (para diagnóstico y pruebas)."""
        return {
            metric: {
                "streak": self._streaks[metric],
                "cooldown_until": self._cooldown_until[metric].isoformat(timespec="seconds")
                if self._cooldown_until[metric]
                else None,
            }
            for metric in METRICS
        }

    def evaluate(self, snapshot) -> list[AlertEvent]:
        """Devuelve las alertas nuevas que correspondan a esta instantánea."""
        events: list[AlertEvent] = []
        if not self.config.enabled:
            return events

        now = self._clock()
        for metric in METRICS:
            value = float(self.value_for(snapshot, metric) or 0.0)
            threshold = self.config.threshold_for(metric)

            if value < threshold:
                # Al bajar del umbral se reinicia la racha y el enfriamiento.
                self._streaks[metric] = 0
                self._cooldown_until[metric] = None
                continue

            self._streaks[metric] += 1
            if self._streaks[metric] < max(1, self.config.sustained_samples):
                continue

            until = self._cooldown_until[metric]
            if until and now < until:
                continue

            self._cooldown_until[metric] = now + timedelta(minutes=max(1, self.config.cooldown_minutes))
            events.append(AlertEvent(metric=metric, value=value, threshold=threshold, at=now))
        return events
