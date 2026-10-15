"""Configuración central: rutas de datos, nombres y constantes de la app."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QLocale

APP_NAME = "PC Toolkit"
APP_TAGLINE = "Herramientas para controlar y mantener tu PC"
# La versión se escribe **solo aquí**: de esta constante salen el ejecutable
# (`build_exe.write_version_file`), el instalador, los nombres de los archivos de cada
# publicación y lo que consulta el actualizador para saber si hay algo nuevo. Para
# publicar una versión: se sube este número, se regeneran las capturas de la
# documentación (`tools\\capturar_documentacion.py`) y se empuja una etiqueta `v…`
# igual a la versión (lo detalla DOCUMENTACION.md).
APP_VERSION = "1.3.0"
ORG_NAME = "PCToolkit"
# Repositorio del que salen las actualizaciones (`pctoolkit.core.actualizador`).
GITHUB_REPO = "fedegudi2022-blip/PC-Toolkit"

IS_WINDOWS = os.name == "nt"


def data_dir() -> Path:
    """Carpeta de datos del usuario (%APPDATA%/PCToolkit)."""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    path = root / ORG_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


# Identificadores (FOLDERID_*) de las carpetas personales que Windows conoce. Con
# OneDrive o una política de la empresa, las carpetas personales se redirigen y
# `%USERPROFILE%\Documents` deja de ser la carpeta que ve el usuario.
KNOWN_FOLDERS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


def known_folder(name: str, fallback: Path | str | None = None) -> Path:
    r"""Carpeta personal de Windows (escritorio, documentos, descargas…).

    Se pregunta a Windows por su ruta real (`SHGetKnownFolderPath`). Importa porque estas
    carpetas pueden estar redirigidas —a OneDrive, por ejemplo— y entonces
    `%USERPROFILE%\Documents` apunta a un sitio que el usuario no ve. Si la consulta
    falla (o no estamos en Windows), se devuelve `fallback` o la ruta previsible.
    """
    previsible = Path(fallback) if fallback is not None else Path.home() / name
    identificador = KNOWN_FOLDERS.get(name)
    if not IS_WINDOWS or not identificador:
        return previsible
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_byte * 8),
            ]

        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CLSIDFromString.restype = ctypes.c_long
        guid = GUID()
        if ole32.CLSIDFromString(ctypes.c_wchar_p(identificador), ctypes.byref(guid)) != 0:
            return previsible
        puntero = ctypes.POINTER(ctypes.c_wchar)()
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(puntero)) != 0:
            return previsible
        try:
            ruta = Path(ctypes.wstring_at(puntero)) if puntero else previsible
        finally:
            ole32.CoTaskMemFree(puntero)
        return ruta if str(ruta) and ruta.is_absolute() else previsible
    except (AttributeError, OSError, ValueError):
        return previsible


@lru_cache(maxsize=8)
def user_folder(name: str) -> Path:
    """Carpeta personal de Windows, memorizada (se consulta más de una vez)."""
    return known_folder(name)


def reports_dir() -> Path:
    """Carpeta por defecto para los informes exportados."""
    path = user_folder("Documents") / ORG_NAME / "Informes"
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
# Hay **dos paletas completas** con las mismas claves: la oscura «D2» (fondos
# grafito, superficies en grises fríos y acento casi blanco) y la clara (blancos y
# grises fríos, con el acento tinta #1F2328 y texto blanco encima).
#
# El color se usa por **significado**, no por decoración:
#   acento  → acción principal, foco y selección
#   verde   → estado saludable
#   ámbar   → advertencia o atención necesaria
#   rojo    → error, peligro o estado crítico
#   series_*→ datos (CPU azul, memoria cian, disco naranja y red gris azulada):
#             cuatro tonos que se distinguen entre sí sin parecer una alerta cuando
#             el valor es normal. Van solo en la gráfica, la leyenda, los medidores y
#             las barras del icono de la bandeja, nunca como fondo de una tarjeta.
#
# Estos valores están medidos, no elegidos a ojo: la suite comprueba los contrastes
# WCAG y que cada serie se distinga de las demás y de los colores de estado también
# con daltonismo (simulación de Machado y CIEDE2000).
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
    # Fondos grafito y superficies frías: cada nivel se separa del anterior lo justo
    # para que se vean los bordes de las tarjetas.
    "bg": "#0E0F11",
    "bg_alt": "#121316",
    "panel": "#17191C",
    "panel_alt": "#1C1F23",
    "border": "#2A2D32",
    "border_soft": "#212429",
    "text": "#EEF0F2",
    "text_muted": "#9AA0A8",
    "text_dim": "#8B9199",
    # Acento casi blanco: botones principales, foco del teclado y selección. El texto
    # que se pinta encima va casi negro.
    "accent": "#E8EAED",
    "accent_soft": "#B6BBC2",
    "accent_glow": "#F6F7F8",
    # Estados: verde para bien, ámbar para el aviso y rojo para el mal.
    "good": "#6BD5A1",
    "warn": "#FFE251",
    "bad": "#D25969",
    # Datos: CPU azul, memoria cian, disco naranja y red gris azulada.
    "series_cpu": "#4F9DDE",
    "series_ram": "#5EEEFE",
    "series_disk": "#D79A4C",
    "series_net": "#A0A7B2",
    # Sobre el acento casi blanco el texto va casi negro: eso mantiene el 15,7:1.
    "on_accent": "#101113",
    "on_selected": "#EEF0F2",
    "primary_hover": "#D3D7DB",
    "primary_disabled_bg": "#1B1D20",
    "primary_disabled_border": "#2A2D32",
    "primary_disabled_text": "#6E747B",
    "danger_border": "#4E3134",
    "danger_hover": "#271719",
}

LIGHT_COLORS = {
    # Blancos y grises fríos, sin matiz de ningún color.
    "bg": "#F4F5F7",
    "bg_alt": "#ECEFF2",
    "panel": "#FFFFFF",
    "panel_alt": "#F7F8FA",
    "border": "#C6CBD1",
    "border_soft": "#E2E5E9",
    "text": "#16181B",
    "text_muted": "#4C5259",
    "text_dim": "#5F656D",
    # El acento es tinta (#1F2328): relleno de los botones principales con texto
    # blanco encima y, a la vez, color de texto y de borde sobre blanco.
    "accent": "#1F2328",
    "accent_soft": "#5A6169",
    "accent_glow": "#3A4046",
    "good": "#02623A",
    "warn": "#794800",
    "bad": "#A42A2C",
    "series_cpu": "#1274CE",
    "series_ram": "#439A9D",
    "series_disk": "#C17A1B",
    "series_net": "#485263",
    "on_accent": "#FFFFFF",
    "on_selected": "#14171A",
    # El hover del botón principal aclara el borde manteniendo el texto blanco.
    "primary_hover": "#4A5158",
    "primary_disabled_bg": "#E7E9EC",
    "primary_disabled_border": "#D3D7DB",
    # El texto de un control apagado queda por debajo de 4,5:1 a propósito (la
    # WCAG exime a los componentes inactivos): tiene que verse apagado, no legible
    # como si estuviera disponible. 3,5:1 se lee sin confundirse con el activo.
    "primary_disabled_text": "#767C84",
    "danger_border": "#E3C2C4",
    "danger_hover": "#FBEDEE",
}

# Matices de superficie usados en hover/selecciones/filas alternas. Son grises
# fríos: la selección se distingue por su propio gris y por el borde de 1 px en
# `text_muted` que le pinta la hoja de estilos, no por un color de acento.
DARK_SURFACES = {
    "hover": "#1F2226",
    "pressed": "#1A1C1F",
    "selected": "#23262B",
    "track": "#2A2D32",
    "track_hover": "#3B3F45",
    "row": "#131417",
}

LIGHT_SURFACES = {
    "hover": "#E9ECEF",
    "pressed": "#DDE1E5",
    "selected": "#E2E6EB",
    "track": "#C6CBD1",
    "track_hover": "#AEB5BC",
    "row": "#FAFBFC",
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


@lru_cache(maxsize=4)
def number_locale() -> QLocale:
    """Configuración regional con la que se escriben los números: la del sistema.

    Windows en español escribe «1.234,5» o «1,234.5» según la región, y hasta ahora la
    aplicación usaba siempre el formato inglés (punto decimal y sin separador de miles, o
    el separador de Python). Con `QLocale` los números se leen como en el resto del
    sistema, igual que los de la documentación.
    """
    return QLocale.system()


def format_number(value: float, decimals: int = 0) -> str:
    """Número con separador de miles y decimales según la configuración regional."""
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return "-"
    return number_locale().toString(numero, "f", max(0, int(decimals)))


def format_integer(value: float) -> str:
    """Recuento con separador de miles («140.493 archivos»)."""
    return format_number(value, 0)


def format_bytes(size: float, decimals: int = 1) -> str:
    """Formatea bytes en unidades legibles (KB, MB, GB, TB)."""
    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{format_number(value, 0)} {unit}"
            return f"{format_number(value, decimals)} {unit}"
        value /= 1024.0
    return f"{format_number(value, decimals)} PB"


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
