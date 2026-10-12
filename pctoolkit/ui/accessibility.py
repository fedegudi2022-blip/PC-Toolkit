"""Accesibilidad: anuncios para lectores de pantalla y rol de los widgets propios.

Dos cosas distintas que Qt no resuelve solo:

* **Anuncios.** Qt no lee la barra de estado por su cuenta: los mensajes que van
  apareciendo («Liberando memoria…», «Mantenimiento completado», «Cerrando…»)
  hay que anunciarlos de forma explícita. Se usa el evento de anuncio de Qt 6.8
  (`QAccessibleAnnouncementEvent`); con versiones anteriores —las dependencias
  permiten 6.6— el mensaje queda al menos en el nombre accesible del texto, que
  es lo que el lector lee al llegar a la barra con el teclado.
* **Rol.** Un widget pintado a mano (medidores, barras, tarjetas) llega al
  lector con el rol genérico que Qt da a su clase base: `QWidget` es «cliente»,
  `QFrame` es «borde». Un medidor se anunciaría como un borde y una tarjeta
  pulsable como un marco cualquiera. El rol solo se puede cambiar sustituyendo
  la interfaz accesible del widget, así que aquí se instala una fábrica que
  devuelve la interfaz propia de los widgets que la piden (y `None` para el
  resto, que siguen con la de Qt).
"""

from __future__ import annotations

import weakref
from typing import Callable

from PySide6.QtCore import QObject, QPoint, QRect, Qt
from PySide6.QtGui import QAccessible, QAccessibleObject

try:  # Qt 6.8+
    from PySide6.QtGui import QAccessibleAnnouncementEvent
except ImportError:  # pragma: no cover - depende de la versión de Qt instalada
    QAccessibleAnnouncementEvent = None  # type: ignore[assignment]

Announcer = Callable[[str], None]

_announcer: Announcer | None = None


def set_announcer(announcer: Announcer | None) -> None:
    """Sustituye el envío real por lo que se le pase (lo usan las pruebas).

    Sirve para comprobar *qué* se anuncia sin depender de que haya un lector de
    pantalla instalado, que es la única forma de observarlo de verdad.
    """
    global _announcer
    _announcer = announcer


def announce(source: QObject, message: str, *, assertive: bool = False) -> bool:
    """Anuncia `message` como cambio en `source`; devuelve si se pudo anunciar.

    `assertive` interrumpe al lector (para no perder un aviso importante); por
    defecto se espera al hueco libre del discurso.
    """
    texto = (message or "").strip()
    if not texto:
        return False
    if _announcer is not None:
        _announcer(texto)
        return True
    if QAccessibleAnnouncementEvent is None:
        return False
    evento = QAccessibleAnnouncementEvent(source, texto)
    if hasattr(QAccessible, "AnnouncementPoliteness"):
        evento.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
    QAccessible.updateAccessibility(evento)
    return True


# --- Nombre, descripción y rol accesibles -------------------------------
# El rol de un widget lo decide su interfaz accesible, así que los widgets que
# quieren otro distinto se apuntan aquí y la fábrica de abajo los reconoce.
_roles: "weakref.WeakKeyDictionary[QObject, QAccessible.Role]" = weakref.WeakKeyDictionary()
_factory_installed = False


def mark(widget, role: QAccessible.Role, name: str = "", description: str = "") -> None:
    """Rol, nombre y descripción de un widget propio, en una sola llamada.

    En un widget pintado a mano (medidores, barras, tarjetas) no hay texto que
    leer: su nombre y su descripción son lo único que llega al lector, así que van
    junto al rol para que no se olvide ninguno de los tres.
    """
    if name:
        widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)
    set_role(widget, role)


def set_role(widget, role: QAccessible.Role) -> None:
    """Fija el rol accesible de un widget (el que anuncia el lector)."""
    _roles[widget] = role
    _install_factory()


def role_of(widget) -> QAccessible.Role | None:
    """Rol activo del widget: el fijado aquí o, si no, el que trae Qt."""
    fijado = _roles.get(widget)
    if fijado is not None:
        return fijado
    interfaz = QAccessible.queryAccessibleInterface(widget)
    return interfaz.role() if interfaz is not None else None


def _install_factory() -> None:
    """Instala (una sola vez) la fábrica que da rol propio a los widgets marcados."""
    global _factory_installed
    if _factory_installed:
        return
    QAccessible.installFactory(_interface_factory)
    _factory_installed = True


def _interface_factory(class_name: str, obj: QObject):  # noqa: ARG001 (Qt da el nombre de la clase)
    """Devuelve la interfaz del widget si tiene rol propio; si no, deja pasar a Qt.

    Devolver `None` es lo que hace que el resto de la interfaz siga funcionando
    como siempre: Qt prueba las fábricas en orden y sigue con la suya.
    """
    role = _roles.get(obj)
    return None if role is None else _RoleInterface(obj, role)


def _own_text(widget: QObject) -> str:
    """Texto propio del widget (etiquetas, botones) cuando no tiene nombre puesto.

    Qt lee el texto de un `QLabel` como nombre accesible; al darle un rol propio a
    una etiqueta (el título de una sección, por ejemplo) hay que seguir leyéndolo.
    """
    texto = getattr(widget, "text", None)
    return texto() if callable(texto) else ""


class _RoleInterface(QAccessibleObject):
    """Interfaz accesible mínima: el rol que se pidió y los datos del widget.

    Qt deja sin implementar el rol, el texto, el estado y el rectángulo, así que
    se rellenan desde el widget real: sin el estado, el lector no sabría si el
    widget está activo, visible o tiene el foco.
    """

    def __init__(self, widget: QObject, role: QAccessible.Role) -> None:
        super().__init__(widget)
        self._role = role
        # Referencia débil: la interfaz no debe mantener vivo al widget.
        self._widget = weakref.ref(widget)

    def _alive(self) -> QObject | None:
        """El widget, o `None` si ya se destruyó (Qt puede preguntar tarde)."""
        return self._widget()

    def role(self) -> QAccessible.Role:  # noqa: D102 (Qt)
        return self._role

    def text(self, kind: QAccessible.Text) -> str:  # noqa: D102 (Qt)
        widget = self._alive()
        if widget is None:
            return ""
        if kind == QAccessible.Text.Name:
            return widget.accessibleName() or _own_text(widget)
        if kind == QAccessible.Text.Description:
            return widget.accessibleDescription()
        if kind == QAccessible.Text.Help:
            return widget.toolTip()
        return ""

    def state(self) -> QAccessible.State:  # noqa: D102 (Qt)
        state = QAccessible.State()
        widget = self._alive()
        if widget is None:
            return state
        visible = widget.isVisible()
        enabled = widget.isEnabled()
        state.visible = visible
        state.invisible = not visible
        state.enabled = enabled
        state.unavailable = not enabled
        state.focusable = widget.focusPolicy() != Qt.NoFocus
        state.focused = widget.hasFocus()
        return state

    def rect(self) -> QRect:  # noqa: D102 (Qt)
        widget = self._alive()
        if widget is None:
            return QRect()
        return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())

    def childCount(self) -> int:  # noqa: D102 (Qt)
        # El contenido de estas piezas ya se lee por los widgets de dentro.
        return 0
