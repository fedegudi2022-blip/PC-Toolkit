"""Preferencias persistentes (QSettings) de PC Toolkit."""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings

from .alerts import AlertConfig
from .appearance import MODE_DARK, normalize_mode
from .history import DEFAULT_INTERVAL_SECONDS
from .memory import MemoryOptions
from .scheduler import MaintenanceSchedule

ORG = "PCToolkit"
APP = "PC Toolkit"


class AppSettings:
    """Acceso tipado a las preferencias del usuario."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings(ORG, APP)

    # --- Utilidades ------------------------------------------------------
    def _bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, "true" if default else "false", type=str)
        return str(value).lower() in ("true", "1", "yes", "si", "sí")

    def _int(self, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self._settings.value(key, default))
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    def _float(self, key: str, default: float) -> float:
        try:
            return float(self._settings.value(key, default))
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value) -> None:
        self._settings.setValue(key, value)

    def sync(self) -> None:
        self._settings.sync()

    # --- Alertas y bandeja ----------------------------------------------
    @property
    def alert_config(self) -> AlertConfig:
        raw = self._settings.value("alertas", "", type=str)
        if raw:
            try:
                return AlertConfig.from_dict(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
        return AlertConfig()

    @alert_config.setter
    def alert_config(self, config: AlertConfig) -> None:
        self._settings.setValue("alertas", json.dumps(config.to_dict(), ensure_ascii=False))

    @property
    def tray_enabled(self) -> bool:
        return self._bool("bandeja_activa", True)

    @tray_enabled.setter
    def tray_enabled(self, value: bool) -> None:
        self.set("bandeja_activa", "true" if value else "false")

    @property
    def close_to_tray(self) -> bool:
        return self._bool("cerrar_a_bandeja", True)

    @close_to_tray.setter
    def close_to_tray(self, value: bool) -> None:
        self.set("cerrar_a_bandeja", "true" if value else "false")

    @property
    def notify_on_alert(self) -> bool:
        return self._bool("avisar_alertas", True)

    @notify_on_alert.setter
    def notify_on_alert(self, value: bool) -> None:
        self.set("avisar_alertas", "true" if value else "false")

    @property
    def auto_log_tools(self) -> bool:
        return self._bool("registrar_acciones", True)

    @auto_log_tools.setter
    def auto_log_tools(self, value: bool) -> None:
        self.set("registrar_acciones", "true" if value else "false")

    # --- Historial -------------------------------------------------------
    @property
    def history_interval_seconds(self) -> int:
        return self._int("historial_intervalo", DEFAULT_INTERVAL_SECONDS, 5, 3600)

    @history_interval_seconds.setter
    def history_interval_seconds(self, value: int) -> None:
        self.set("historial_intervalo", max(5, min(3600, int(value))))

    @property
    def history_enabled(self) -> bool:
        return self._bool("historial_activo", True)

    @history_enabled.setter
    def history_enabled(self, value: bool) -> None:
        self.set("historial_activo", "true" if value else "false")

    # --- Apariencia ------------------------------------------------------
    @property
    def theme_mode(self) -> str:
        """Tema de la interfaz: `auto` (el que tenga Windows), `dark` o `light`.

        Por defecto, oscuro: la identidad de la aplicación es el negro y solo cambia si
        se elige a mano en «Ajustes y alertas → Apariencia» (`auto` para seguir a
        Windows o `light` para el claro).
        """
        return normalize_mode(self._settings.value("tema", MODE_DARK, type=str))

    @theme_mode.setter
    def theme_mode(self, value: str) -> None:
        self.set("tema", normalize_mode(value))

    # --- Rendimiento -----------------------------------------------------
    @property
    def eco_mode(self) -> bool:
        """Modo eco: muestreo más lento para gastar menos CPU/RAM."""
        return self._bool("modo_eco", True)

    @eco_mode.setter
    def eco_mode(self, value: bool) -> None:
        self.set("modo_eco", "true" if value else "false")

    @property
    def eco_when_hidden(self) -> bool:
        """Aplica el modo eco mientras la ventana está oculta o en la bandeja."""
        return self._bool("eco_oculto", True)

    @eco_when_hidden.setter
    def eco_when_hidden(self, value: bool) -> None:
        self.set("eco_oculto", "true" if value else "false")

    # --- Memoria RAM -----------------------------------------------------
    @property
    def memory_options(self) -> MemoryOptions:
        raw = self._settings.value("memoria", "", type=str)
        if raw:
            try:
                return MemoryOptions.from_dict(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
        return MemoryOptions()

    @memory_options.setter
    def memory_options(self, options: MemoryOptions) -> None:
        self.set("memoria", json.dumps(options.to_dict(), ensure_ascii=False))

    # --- Mantenimiento programado ----------------------------------------
    @property
    def schedule_config(self) -> MaintenanceSchedule:
        raw = self._settings.value("mantenimiento", "", type=str)
        if raw:
            try:
                return MaintenanceSchedule.from_dict(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
        return MaintenanceSchedule()

    @schedule_config.setter
    def schedule_config(self, schedule: MaintenanceSchedule) -> None:
        self.set("mantenimiento", json.dumps(schedule.to_dict(), ensure_ascii=False))

    # --- Actualizaciones --------------------------------------------------
    # Tres claves y todas caben en las preferencias de siempre
    # (HKCU\Software\PCToolkit): si se buscan actualizaciones, cuándo se miró por última
    # vez y qué versión se pidió no volver a ver. La marca de tiempo es lo que permite
    # comprobar «como mucho una vez al día» sin volver a consultar nada.
    @property
    def check_updates(self) -> bool:
        """Si se busca una versión nueva al arrancar (se puede desactivar)."""
        return self._bool("buscar_actualizaciones", True)

    @check_updates.setter
    def check_updates(self, value: bool) -> None:
        self.set("buscar_actualizaciones", "true" if value else "false")

    @property
    def last_update_check(self) -> float:
        """Momento (segundos desde 1970) de la última comprobación; 0 si nunca."""
        return self._float("ultima_comprobacion", 0.0)

    @last_update_check.setter
    def last_update_check(self, value: float) -> None:
        self.set("ultima_comprobacion", float(value))

    @property
    def skipped_version(self) -> str:
        """Versión que el usuario pidió no volver a ver (vacío si ninguna)."""
        return str(self._settings.value("version_omitida", "", type=str) or "")

    @skipped_version.setter
    def skipped_version(self, value: str) -> None:
        self.set("version_omitida", str(value or ""))
