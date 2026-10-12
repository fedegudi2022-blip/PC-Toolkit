"""Modo de apariencia: oscuro, claro o el que tenga Windows.

La aplicación puede seguir el ajuste de Windows (lo normal) o llevarle la
contraria a propósito. La lectura del sistema es una consulta al registro del
usuario (`AppsUseLightTheme`), la misma que usa Windows para saber si el modo
claro está activo; si no se puede leer (otro sistema, clave ausente), se asume
oscuro, que es el aspecto con el que se diseñó la aplicación.
"""

from __future__ import annotations

from .config import THEME_DARK, THEME_LIGHT

MODE_AUTO = "auto"
MODE_DARK = THEME_DARK
MODE_LIGHT = THEME_LIGHT

MODES = (MODE_AUTO, MODE_DARK, MODE_LIGHT)

MODE_LABELS = {
    MODE_AUTO: "Seguir a Windows",
    MODE_DARK: "Oscuro",
    MODE_LIGHT: "Claro",
}

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
LIGHT_VALUE = "AppsUseLightTheme"


def normalize_mode(value) -> str:
    """Modo válido a partir de lo guardado en las preferencias (o `auto`)."""
    texto = str(value or "").strip().lower()
    if texto in (MODE_LIGHT, "claro", "light"):
        return MODE_LIGHT
    if texto in (MODE_DARK, "oscuro", "dark"):
        return MODE_DARK
    return MODE_AUTO


def system_mode() -> str:
    """Modo que tiene Windows configurado para las aplicaciones.

    `AppsUseLightTheme = 1` significa modo claro. Cualquier problema al leerlo
    (no es Windows, no hay clave, valor raro) se resuelve como oscuro.
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - solo en sistemas sin registro de Windows
        return THEME_DARK
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as clave:
            valor, _tipo = winreg.QueryValueEx(clave, LIGHT_VALUE)
    except OSError:
        return THEME_DARK
    return THEME_LIGHT if int(valor) == 1 else THEME_DARK


def resolve_mode(mode: str) -> str:
    """Paleta que corresponde a un modo: `auto` mira el sistema, el resto se respeta."""
    elegido = normalize_mode(mode)
    return system_mode() if elegido == MODE_AUTO else elegido


def describe_mode(mode: str) -> str:
    """Frase corta que explica qué va a pasar con este modo (para el diálogo)."""
    elegido = normalize_mode(mode)
    actual = system_mode()
    if elegido == MODE_AUTO:
        return (
            "Se usa el mismo modo que Windows, que ahora mismo está en "
            + ("claro." if actual == THEME_LIGHT else "oscuro.")
        )
    return "Siempre en modo " + ("claro, aunque Windows esté en oscuro." if elegido == THEME_LIGHT else "oscuro, aunque Windows esté en claro.")
