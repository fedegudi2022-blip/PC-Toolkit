"""Configuración central: rutas de datos, nombres y constantes de la app."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "PC Toolkit"
APP_TAGLINE = "Herramientas para controlar y mantener tu PC"
APP_VERSION = "1.1.0"
ORG_NAME = "PCToolkit"

IS_WINDOWS = os.name == "nt"


def data_dir() -> Path:
    """Carpeta de datos del usuario (%APPDATA%/PCToolkit)."""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    path = root / ORG_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    """Carpeta por defecto para los informes exportados."""
    path = Path.home() / "Documents" / ORG_NAME / "Informes"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = data_dir() / "Informes"
        path.mkdir(parents=True, exist_ok=True)
    return path


def tasks_file() -> Path:
    return data_dir() / "tareas_mantenimiento.json"


def settings_file() -> Path:
    return data_dir() / "ajustes.json"


def config_file() -> Path:
    return data_dir() / "analisis_disco.json"


# --- Colores de la interfaz -----------------------------------------------
# Hay **dos paletas completas** con las mismas claves: la oscura «grafito vivo»
# (grises neutros cálidos, texto casi blanco y acento esmeralda saturado) y la
# clara «papel cálido» (blancos sucios, texto casi negro y el mismo esmeralda
# oscurecido para que el texto siga leyéndose).
#
# `COLORS` y `SURFACES` son siempre la paleta **activa** y se usan por referencia
# en toda la interfaz, así que cambiar de tema es reemplazar su contenido con
# `use_palette()` (nunca reasignarlas: las referencias ya tomadas se quedarían
# apuntando a la paleta vieja).
#
# Claves con significado propio, más allá del color:
#   on_accent    texto e iconos que se pintan encima del acento
#   on_selected  texto sobre una superficie seleccionada (barra lateral, listas)
DARK_COLORS = {
    "bg": "#0F0E0D",
    "bg_alt": "#171615",
    "panel": "#1E1C1A",
    "panel_alt": "#272422",
    "border": "#3E3934",
    "border_soft": "#2C2926",
    "text": "#FFFFFF",
    "text_muted": "#C8C2B9",
    "text_dim": "#918A80",
    "accent": "#1FE3A8",
    "accent_soft": "#14B98A",
    "accent_glow": "#6CFFD2",
    "good": "#3EE693",
    "warn": "#FFB32B",
    "bad": "#FF5A73",
    "magenta": "#FF7BC4",
    "amber": "#FFC46B",
    "on_accent": "#04241A",
    "on_selected": "#6CFFD2",
    "primary_hover": "#A8FFE2",
    "primary_disabled_bg": "#26332D",
    "primary_disabled_border": "#33564A",
    "primary_disabled_text": "#7FA498",
    "danger_border": "#4A2A28",
    "danger_hover": "#2A1815",
    "warn_alt": "#F97F52",
    "good_alt": "#8FE07A",
}

LIGHT_COLORS = {
    "bg": "#F1EFEB",
    "bg_alt": "#E8E4DD",
    "panel": "#FFFFFF",
    "panel_alt": "#F7F4F0",
    "border": "#CBC4BB",
    "border_soft": "#E3DDD5",
    "text": "#17150F",
    "text_muted": "#4D463F",
    "text_dim": "#6C6459",
    "accent": "#0A6B52",
    "accent_soft": "#085C46",
    "accent_glow": "#0B7A5D",
    "good": "#12734B",
    "warn": "#8F5400",
    "bad": "#B02640",
    "magenta": "#9C3F76",
    "amber": "#8A6118",
    "on_accent": "#FFFFFF",
    "on_selected": "#085441",
    "primary_hover": "#0C8A68",
    "primary_disabled_bg": "#E4DFD8",
    "primary_disabled_border": "#CFC8BF",
    # El texto de un control apagado queda por debajo de 4,5:1 a propósito (la
    # WCAG exime a los componentes inactivos): tiene que verse apagado, no legible
    # como si estuviera disponible. 3,6:1 se lee sin confundirse con el activo.
    "primary_disabled_text": "#7A7266",
    "danger_border": "#E4BFC4",
    "danger_hover": "#FBEDEF",
    "warn_alt": "#D2731F",
    "good_alt": "#3FAE7B",
}

# Matices de superficie usados en hover/selecciones/filas alternas.
DARK_SURFACES = {
    "hover": "#2E2B28",
    "pressed": "#252220",
    "selected": "#33473F",
    "track": "#3E3934",
    "track_hover": "#544E48",
    "row": "#211F1D",
}

LIGHT_SURFACES = {
    "hover": "#ECE7E0",
    "pressed": "#E2DCD3",
    "selected": "#D6EDE4",
    "track": "#CBC4BB",
    "track_hover": "#B4ABA0",
    "row": "#FAF8F5",
}

THEME_DARK = "dark"
THEME_LIGHT = "light"

COLORS: dict[str, str] = dict(DARK_COLORS)
SURFACES: dict[str, str] = dict(DARK_SURFACES)
_ACTIVE_PALETTE = THEME_DARK


def use_palette(name: str) -> str:
    """Activa la paleta `name` («dark» o «light») y devuelve la que quedó.

    Se modifica el contenido de los diccionarios en vez de reasignarlos: los
    módulos ya importados siguen viendo la paleta nueva.
    """
    global _ACTIVE_PALETTE
    _ACTIVE_PALETTE = THEME_LIGHT if name == THEME_LIGHT else THEME_DARK
    claro = _ACTIVE_PALETTE == THEME_LIGHT
    COLORS.clear()
    COLORS.update(LIGHT_COLORS if claro else DARK_COLORS)
    SURFACES.clear()
    SURFACES.update(LIGHT_SURFACES if claro else DARK_SURFACES)
    return _ACTIVE_PALETTE


def active_palette() -> str:
    """Paleta que se está usando ahora mismo («dark» o «light»)."""
    return _ACTIVE_PALETTE


def palette_is_light() -> bool:
    """Si la paleta activa es la clara."""
    return _ACTIVE_PALETTE == THEME_LIGHT


def as_flag(value, default: bool) -> bool:
    """Convierte un valor dudoso (JSON, cadena, número) en booleano."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "si", "sí", "yes", "on")
    return default


def as_number(value, default: float, low: float, high: float) -> float:
    """Número válido y dentro de rango; si no se puede leer, el valor por defecto."""
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def as_integer(value, default: int, low: int, high: int) -> int:
    """Entero válido y dentro de rango; si no se puede leer, el valor por defecto."""
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def format_bytes(size: float, decimals: int = 1) -> str:
    """Formatea bytes en unidades legibles (KB, MB, GB, TB)."""
    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{value:.0f} {unit}"
            return f"{value:.{decimals}f} {unit}"
        value /= 1024.0
    return f"{value:.{decimals}f} PB"


def health_label(score: float) -> str:
    if score >= 85:
        return "Excelente"
    if score >= 70:
        return "Buena"
    if score >= 50:
        return "Aceptable"
    if score >= 30:
        return "Mejorable"
    return "Crítica"


def health_color(score: float) -> str:
    if score >= 70:
        return COLORS["good"]
    if score >= 45:
        return COLORS["warn"]
    return COLORS["bad"]
