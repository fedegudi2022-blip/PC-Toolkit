"""Modo de apariencia: oscuro, claro o el que tenga Windows.

La aplicación puede seguir el ajuste de Windows (lo normal) o llevarle la
contraria a propósito. La lectura del sistema es una consulta al registro del
usuario (`AppsUseLightTheme`), la misma que usa Windows para saber si el modo
claro está activo; si no se puede leer (otro sistema, clave ausente), se asume
oscuro, que es el aspecto con el que se diseñó la aplicación.

Seguir a Windows es seguirle **de verdad**: `SystemThemeWatcher` escucha el aviso que
Windows manda a las ventanas cuando cambia el tema, así que pasar a claro u oscuro se
ve en la aplicación en el momento, sin cerrarla y volver a abrirla.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, Signal

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

# El aviso que Windows manda a las ventanas cuando cambia el aspecto del sistema. El del
# modo claro/oscuro es `WM_SETTINGCHANGE` con `lParam` apuntando a «ImmersiveColorSet»; los
# otros dos los manda en los mismos casos (cambio de tema de la ventana y de los colores del
# sistema), y leer el registro una vez de más cuesta microsegundos, así que se escuchan los
# tres y decide la lectura.
WM_SETTINGCHANGE = 0x001A
WM_THEMECHANGED = 0x031A
WM_SYSCOLORCHANGE = 0x0015
THEME_MESSAGES = (WM_SETTINGCHANGE, WM_THEMECHANGED, WM_SYSCOLORCHANGE)

# Red de seguridad, en milisegundos: los avisos de Windows no están garantizados (si el tema
# cambia mientras la aplicación arranca todavía no hay ventana que lo reciba, y nada obliga a
# que una ventana vea el mensaje), así que de vez en cuando se vuelve a leer el registro. Una
# lectura son 9 µs: cada 30 s es 0,00003 % de un núcleo. Lo que ya no hay es el sondeo cada
# dos segundos que había antes: manda el aviso, y esto solo tapa el caso de uno perdido.
SAFETY_INTERVAL_MS = 30000


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


class SystemThemeWatcher(QObject, QAbstractNativeEventFilter):
    """Avisa cuando Windows cambia entre el modo claro y el oscuro.

    Windows se lo cuenta a las ventanas en el momento (`WM_SETTINGCHANGE` con
    «ImmersiveColorSet», y también `WM_THEMECHANGED` y `WM_SYSCOLORCHANGE`), y ese aviso se
    recibe en el **filtro de mensajes nativos** de la aplicación: no se sondea el registro,
    el cambio llega cuando pasa y no se paga nada mientras no cambie nada.

    Como los avisos no están garantizados —si el tema cambia mientras la aplicación arranca
    no hay ventana que los reciba—, queda una **red de seguridad** lenta
    (`SAFETY_INTERVAL_MS`) que vuelve a leer el registro de vez en cuando.

    `poll()` lee el registro y avisa si cambió: es lo que hacen el aviso y la red de
    seguridad, y también se puede llamar a mano (lo hacen las comprobaciones).
    """

    changed = Signal(str)  # el modo del sistema que acaba de entrar (`dark` o `light`)

    def __init__(self, parent: QObject | None = None, safety_ms: int = SAFETY_INTERVAL_MS) -> None:
        # Las dos bases se inicializan a mano: `super()` se quedaría en `QObject` y la parte de
        # filtro de mensajes no llegaría a montarse, con lo que Qt no llamaría nunca a
        # `nativeEventFilter` (sin ningún error: simplemente no llega nada).
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._last = system_mode()
        self._listening = False
        self._safety = QTimer(self)
        self._safety.setInterval(max(5000, int(safety_ms)))
        self._safety.timeout.connect(self.poll)

    # --- Lo que se puede preguntar desde fuera ----------------------------
    @property
    def safety_ms(self) -> int:
        """Cada cuánto se relee el registro por si un aviso se perdiera."""
        return self._safety.interval()

    @property
    def last(self) -> str:
        """Último modo del sistema que se ha visto."""
        return self._last

    def is_listening(self) -> bool:
        """¿Está puesta la escucha de los avisos de Windows?"""
        return self._listening

    def is_running(self) -> bool:
        """¿Está el vigía en marcha (escucha puesta y red de seguridad andando)?"""
        return self._listening and self._safety.isActive()

    # --- Ponerlo en marcha y pararlo --------------------------------------
    def start(self) -> None:
        """Empieza a escuchar. La lectura de ahora es la referencia, no un cambio."""
        self._last = system_mode()
        app = QCoreApplication.instance()
        if app is not None and not self._listening:
            app.installNativeEventFilter(self)
            self._listening = True
        self._safety.start()

    def stop(self) -> None:
        """Deja de escuchar (con un modo elegido a mano no hay nada que seguir)."""
        app = QCoreApplication.instance()
        if app is not None and self._listening:
            app.removeNativeEventFilter(self)
            self._listening = False
        self._safety.stop()

    # --- El aviso de Windows ----------------------------------------------
    def nativeEventFilter(self, eventType, message):
        """Mira el tema cuando Windows avisa de un cambio de aspecto.

        Qt llama aquí por cada mensaje nativo; los avisos que interesan son tres y el resto
        siguen su camino. Se devuelve siempre `(False, 0)`: el mensaje no es nuestro.
        """
        if not self._listening or bytes(eventType) != b"windows_generic_MSG":
            return False, 0
        if _codigo_del_mensaje(message) in THEME_MESSAGES:
            self.poll()
        return False, 0

    def poll(self) -> str | None:
        """Mira el sistema y avisa si cambió; devuelve el modo nuevo o nada si sigue igual."""
        actual = system_mode()
        if actual == self._last:
            return None
        self._last = actual
        try:
            self.changed.emit(actual)
        except RuntimeError:  # pragma: no cover - la ventana que escuchaba ya no existe
            # En vez de insistir con un aviso que ya no tiene a quién llegar, se apaga: es lo
            # que dejaría un filtro instalado apuntando a una ventana cerrada.
            self.stop()
            return None
        return actual


def _codigo_del_mensaje(message) -> int | None:
    """Código del mensaje de Windows a partir del puntero que entrega Qt.

    Qt pasa aquí un puntero a la estructura `MSG` de la API de Windows, que se lee con
    `ctypes`. Si llegara con otra forma —otra versión de Qt, otro sistema— se devuelve nada y
    el vigía sigue con su red de seguridad, en vez de caerse dentro del bucle de eventos.
    """
    try:
        from ctypes import wintypes

        return int(wintypes.MSG.from_address(int(message)).message)
    except (ImportError, TypeError, ValueError, OSError):  # pragma: no cover - forma inesperada
        return None


def describe_mode(mode: str) -> str:
    """Frase corta que explica qué va a pasar con este modo (para el diálogo)."""
    elegido = normalize_mode(mode)
    actual = system_mode()
    if elegido == MODE_AUTO:
        return (
            "Se usa el mismo modo que Windows, que ahora mismo está en "
            + ("claro." if actual == THEME_LIGHT else "oscuro.")
            + " Si lo cambias ahí, la aplicación cambia con él al momento."
        )
    return "Siempre en modo " + ("claro, aunque Windows esté en oscuro." if elegido == THEME_LIGHT else "oscuro, aunque Windows esté en claro.")
