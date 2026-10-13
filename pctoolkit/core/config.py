"""Configuración central: rutas de datos, nombres y constantes de la app."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "PC Toolkit"
APP_TAGLINE = "Herramientas para controlar y mantener tu PC"
APP_VERSION = "1.2.0"
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
# Hay **dos paletas completas** con las mismas claves: la oscura «negro mate»
# (fondo negro de verdad, superficies en grises neutros sin matiz y texto casi
# blanco) y la clara «papel» (blancos fríos, texto casi negro y el mismo violeta
# oscurecido para que el texto siga leyéndose sobre blanco).
#
# El color se usa por **significado**, no por decoración:
#   violeta → acción principal, foco y selección
#   cian    → estado saludable
#   ámbar   → advertencia o atención necesaria
#   rojo    → error, peligro o estado crítico
#   series_*→ datos (CPU cian, memoria violeta y disco naranja): tres tonos que se
#             distinguen entre sí sin parecer una alerta cuando el valor es normal.
#             Van solo en la gráfica, la leyenda y las barras del icono de la
#             bandeja, nunca como fondo de una tarjeta ni como color de estado.
#
# El verde no aparece en la paleta: ni como acento, ni como estado, ni como dato. La
# suite lo comprueba de verdad (ningún color de la paleta tiene matiz verde).
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
    # Negro de verdad en el fondo y tres grises neutros (sin matiz de ningún color)
    # para las superficies: cada nivel se separa del anterior lo justo para que se
    # vean los bordes de las tarjetas.
    "bg": "#000000",
    "bg_alt": "#0A0A0A",
    "panel": "#121212",
    "panel_alt": "#1A1A1A",
    "border": "#2E2E33",
    "border_soft": "#1F1F23",
    "text": "#F4F4F6",
    "text_muted": "#A9A9B2",
    "text_dim": "#8E8E97",
    # Violeta/índigo de acento: botones principales, foco del teclado y selección.
    "accent": "#8B5CF6",
    "accent_soft": "#7C3AED",
    "accent_glow": "#A78BFA",
    # El estado saludable es cian: el verde ya no se usa para nada.
    "good": "#22D3EE",
    "warn": "#FBBF24",
    "bad": "#F87171",
    # Datos: CPU cian, memoria violeta y disco naranja.
    "series_cpu": "#22D3EE",
    "series_ram": "#A78BFA",
    "series_disk": "#FB923C",
    # Sobre el violeta el texto va casi negro: es lo que mantiene el 4,5:1
    # (con blanco encima, #8B5CF6 se queda en 3,5:1).
    "on_accent": "#0B0714",
    "on_selected": "#E9E4FF",
    "primary_hover": "#B9A2FF",
    "primary_disabled_bg": "#16161A",
    "primary_disabled_border": "#2A2A31",
    "primary_disabled_text": "#6C6C76",
    "danger_border": "#4A2A2C",
    "danger_hover": "#2A1618",
}

LIGHT_COLORS = {
    # Blancos y grises fríos, sin el matiz verdoso de la paleta anterior.
    "bg": "#F5F5F7",
    "bg_alt": "#EBEBEF",
    "panel": "#FFFFFF",
    "panel_alt": "#F7F7F9",
    "border": "#C9C9D2",
    "border_soft": "#E0E0E6",
    "text": "#111114",
    "text_muted": "#4B4B54",
    "text_dim": "#63636D",
    # En claro el acento es el mismo violeta, oscurecido porque hace dos trabajos a
    # la vez: relleno de los botones principales (con texto blanco encima) y color
    # de texto y de borde sobre blanco. El tono brillante no sirve para lo segundo.
    "accent": "#6D28D9",
    "accent_soft": "#5B21B6",
    "accent_glow": "#7C3AED",
    "good": "#0E7490",
    "warn": "#96620B",
    "bad": "#B32424",
    "series_cpu": "#0E7490",
    "series_ram": "#7C3AED",
    "series_disk": "#C2410C",
    "on_accent": "#FFFFFF",
    "on_selected": "#2E1065",
    # El hover del botón principal oscurece (el gradiente va de `primary_hover` a
    # `accent_glow`): aclarar el violeta obligaría a quitar el texto blanco.
    "primary_hover": "#5B21B6",
    "primary_disabled_bg": "#E6E6EB",
    "primary_disabled_border": "#D0D0D8",
    # El texto de un control apagado queda por debajo de 4,5:1 a propósito (la
    # WCAG exime a los componentes inactivos): tiene que verse apagado, no legible
    # como si estuviera disponible. 3,6:1 se lee sin confundirse con el activo.
    "primary_disabled_text": "#7B7B85",
    "danger_border": "#E3BDBD",
    "danger_hover": "#FBEDED",
}

# Matices de superficie usados en hover/selecciones/filas alternas. Son grises
# neutros salvo la selección, que lleva el violeta del acento para que se vea de un
# vistazo qué fila o qué sección está activa.
DARK_SURFACES = {
    "hover": "#1C1C21",
    "pressed": "#151519",
    "selected": "#2A2140",
    "track": "#2E2E33",
    "track_hover": "#3F3F46",
    "row": "#0E0E11",
}

LIGHT_SURFACES = {
    "hover": "#EDEDF2",
    "pressed": "#DFDFE6",
    "selected": "#E7E0FB",
    "track": "#C9C9D2",
    "track_hover": "#B2B2BC",
    "row": "#FAFAFC",
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
