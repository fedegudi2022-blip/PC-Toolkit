"""Configuración central: rutas de datos, nombres y constantes de la app."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "PC Toolkit"
APP_TAGLINE = "Herramientas para controlar y mantener tu PC"
APP_VERSION = "1.1.1"
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
# Hay **dos paletas completas** con las mismas claves: la oscura «grafito frío»
# (grises neutros con un matiz verde muy leve, texto casi blanco y verde de
# acento) y la clara «niebla verde» (blancos fríos, texto casi negro y el mismo
# verde oscurecido para que el texto siga leyéndose).
#
# El color se usa por **significado**, no por decoración:
#   verde   → acción principal, selección activa y estado saludable
#   amarillo→ advertencia o atención necesaria
#   rojo    → error, peligro o estado crítico
#   series_*→ datos (CPU, memoria y disco): tres tonos que se distinguen entre sí
#             sin parecer una alerta cuando el valor es normal. Van solo en la
#             gráfica, la leyenda y las barras del icono de la bandeja, nunca como
#             fondo de una tarjeta ni como color de estado.
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
    "bg": "#0B0F0E",
    "bg_alt": "#101614",
    "panel": "#151C19",
    "panel_alt": "#1B2420",
    "border": "#29352F",
    "border_soft": "#1E2823",
    "text": "#F3F7F4",
    "text_muted": "#A7B4AC",
    "text_dim": "#8C9A92",
    "accent": "#22C55E",
    "accent_soft": "#1B9E4B",
    "accent_glow": "#4ADE80",
    "good": "#34D399",
    "warn": "#FBBF24",
    "bad": "#F87171",
    "series_cpu": "#22C55E",
    "series_ram": "#F472B6",
    "series_disk": "#2DD4BF",
    "on_accent": "#04220F",
    "on_selected": "#A7F3C2",
    "primary_hover": "#86EFAC",
    "primary_disabled_bg": "#1B2320",
    "primary_disabled_border": "#2A3A32",
    "primary_disabled_text": "#7E8C85",
    "danger_border": "#4A2A2C",
    "danger_hover": "#2A1618",
}

LIGHT_COLORS = {
    "bg": "#F2F5F3",
    "bg_alt": "#E8EDEA",
    "panel": "#FFFFFF",
    "panel_alt": "#F5F8F6",
    "border": "#C6D0CA",
    "border_soft": "#DCE4DF",
    "text": "#0F1614",
    "text_muted": "#414E48",
    "text_dim": "#5F6D66",
    # En claro el acento es un verde oscuro porque hace dos trabajos a la vez:
    # relleno de los botones principales (con texto blanco encima) y color de texto
    # y de borde sobre blanco. El tono claro no sirve para lo segundo.
    "accent": "#166534",
    "accent_soft": "#14532D",
    "accent_glow": "#15803D",
    "good": "#047857",
    "warn": "#96620B",
    "bad": "#B32424",
    "series_cpu": "#15803D",
    "series_ram": "#BE185D",
    "series_disk": "#0F766E",
    "on_accent": "#FFFFFF",
    "on_selected": "#0B3B22",
    # El hover del botón principal oscurece (el gradiente va de `primary_hover` a
    # `accent_glow`): aclarar el verde obligaría a quitar el texto blanco.
    "primary_hover": "#14532D",
    "primary_disabled_bg": "#E4EAE7",
    "primary_disabled_border": "#CBD5D0",
    # El texto de un control apagado queda por debajo de 4,5:1 a propósito (la
    # WCAG exime a los componentes inactivos): tiene que verse apagado, no legible
    # como si estuviera disponible. 3,6:1 se lee sin confundirse con el activo.
    "primary_disabled_text": "#6E7B75",
    "danger_border": "#E3BDBD",
    "danger_hover": "#FBEDED",
}

# Matices de superficie usados en hover/selecciones/filas alternas.
DARK_SURFACES = {
    "hover": "#1C2621",
    "pressed": "#161E1A",
    "selected": "#1F3D2C",
    "track": "#29352F",
    "track_hover": "#3C4A43",
    "row": "#121A17",
}

LIGHT_SURFACES = {
    "hover": "#E7EEE9",
    "pressed": "#DBE5DF",
    "selected": "#CFEBD9",
    "track": "#C6D0CA",
    "track_hover": "#A9B7AF",
    "row": "#F8FAF9",
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
