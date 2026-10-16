"""Iconos vectoriales monocromos dibujados con QPainter.

Se generan en tiempo de ejecución (sin archivos externos) para que el tema
oscuro conserve el mismo trazo fino que el resto de la interfaz.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QIconEngine,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from ..core.config import COLORS

_CACHE: dict[tuple[str, int, str, float], QIcon] = {}
# Los motores de los iconos que siguen al tema. El `QIcon` se queda con ellos en C++, pero
# la parte de Python tiene que seguir viva: sin esto, Qt se quedaría con un objeto recogido.
_ENGINES: dict[tuple[str, int, str, float], "_PaletteIconEngine"] = {}

# --- Logotipo -------------------------------------------------------------
# El original (`assets\pc_toolkit_logo.png`, 1024×1024) es la marca de la aplicación y se
# usa tal cual para el icono de la ventana: no se tiñe con la paleta porque no es un icono
# de la interfaz, es el logotipo. Si el archivo no está (una copia sin `assets\`), quien lo
# pida recibe `None` y enseña el glifo dibujado: la ventana nunca se queda sin icono por un
# archivo que falta.
LOGO_NAME = "pc_toolkit_logo.png"
_LOGO_CACHE: dict[int, QPixmap] = {}


def logo_path() -> Path | None:
    """Ruta del logotipo: en el proyecto o dentro del ejecutable empaquetado."""
    candidatas: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidatas.append(Path(bundle) / "assets" / LOGO_NAME)
    candidatas.append(Path(__file__).resolve().parents[2] / "assets" / LOGO_NAME)
    for ruta in candidatas:
        if ruta.is_file():
            return ruta
    return None


def logo_pixmap(size: int = 64) -> QPixmap | None:
    """Logotipo escalado al tamaño pedido (o `None` si el archivo no está)."""
    if size in _LOGO_CACHE:
        return _LOGO_CACHE[size]
    ruta = logo_path()
    if ruta is None:
        return None
    imagen = QImage(str(ruta))
    if imagen.isNull():
        return None
    escala = 3  # igual que el resto de iconos: nítido en pantallas con escalado
    pixmap = QPixmap.fromImage(
        imagen.scaled(size * escala, size * escala, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    )
    pixmap.setDevicePixelRatio(escala)
    _LOGO_CACHE[size] = pixmap
    return pixmap


def icon(name: str, size: int = 20, color: str | None = None, width: float = 1.6) -> QIcon:
    """Icono de la interfaz, dibujado con la paleta que esté activa.

    Sin color propio (`color=None`) devuelve un icono que **se dibuja cada vez que se pinta**,
    así que el mismo objeto que ya está puesto en un botón cambia de color con el tema. Con un
    color propio se guarda como siempre: ese color no depende del tema (el de encima del
    acento, por ejemplo, se pide a propósito).
    """
    key = (name, size, color or "default", width)
    if key in _CACHE:
        return _CACHE[key]
    if color is None:
        engine = _PaletteIconEngine(name, size, width)
        result = QIcon(engine)
        _ENGINES[key] = engine
    else:
        result = QIcon(pixmap_for(name, size, color, width))
    _CACHE[key] = result
    return result


class _PaletteIconEngine(QIconEngine):
    """Motor que dibuja el icono al pedirlo, con los colores de la paleta de ahora.

    Un `QIcon` normal guarda los píxeles ya pintados: al cambiar de tema se queda con los
    colores del tema viejo y habría que volver a ponerlo en cada sitio donde se use (menú,
    barra lateral, botones de cada página…). Este motor no guarda nada: cada vez que alguien
    pide el mapa de píxeles, lo dibuja con la paleta activa.
    """

    def __init__(self, name: str, size: int, width: float) -> None:
        super().__init__()
        self._name = name
        self._size = size
        self._width = width

    def _dibujar(self) -> QPixmap:
        return pixmap_for(self._name, self._size, None, self._width)

    def paint(self, painter, rect, mode, state) -> None:  # noqa: N802 (Qt)
        painter.drawPixmap(rect, self._dibujar())

    def pixmap(self, size, mode, state) -> QPixmap:  # noqa: N802 (Qt)
        return self._dibujar()

    def clone(self) -> QIconEngine:  # noqa: N802 (Qt)
        return _PaletteIconEngine(self._name, self._size, self._width)


def icon_from_pixmap(pixmap: QPixmap) -> QIcon:
    """Envuelve un mapa de píxeles ya dibujado en un QIcon."""
    return QIcon(pixmap)


def dot_pixmap(color: str, size: int = 10) -> QPixmap:
    """Punto de color plano (leyendas de gráficas)."""
    scale = 3
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size * scale, size * scale)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def pixmap_for(name: str, size: int = 20, color: str | None = None, width: float = 1.6) -> QPixmap:
    scale = 3  # para pantallas con escalado
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(scale, scale)
    # El color se resuelve al dibujar (y no al importar) para que los cambios de
    # paleta se apliquen también a los iconos que no piden color propio.
    pen = QPen(QColor(color or COLORS["text_muted"]))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    _draw(painter, name, float(size))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def _draw(painter: QPainter, name: str, s: float) -> None:
    drawer = _DRAWERS.get(name, _draw_dot)
    painter.save()
    drawer(painter, s)
    painter.restore()


def _rect(s: float, x: float, y: float, w: float, h: float) -> QRectF:
    return QRectF(x * s, y * s, w * s, h * s)


def _draw_home(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(0.2 * s, 0.48 * s)
    path.lineTo(0.5 * s, 0.22 * s)
    path.lineTo(0.8 * s, 0.48 * s)
    path.moveTo(0.29 * s, 0.45 * s)
    path.lineTo(0.29 * s, 0.78 * s)
    path.lineTo(0.71 * s, 0.78 * s)
    path.lineTo(0.71 * s, 0.45 * s)
    p.drawPath(path)


def _draw_cpu(p: QPainter, s: float) -> None:
    p.drawRoundedRect(_rect(s, 0.28, 0.28, 0.44, 0.44), 2.5, 2.5)
    p.drawRoundedRect(_rect(s, 0.41, 0.41, 0.18, 0.18), 1.4, 1.4)
    for pos in (0.38, 0.5, 0.62):
        p.drawLine(QPointF(pos * s, 0.16 * s), QPointF(pos * s, 0.28 * s))
        p.drawLine(QPointF(pos * s, 0.72 * s), QPointF(pos * s, 0.84 * s))
        p.drawLine(QPointF(0.16 * s, pos * s), QPointF(0.28 * s, pos * s))
        p.drawLine(QPointF(0.72 * s, pos * s), QPointF(0.84 * s, pos * s))


def _draw_chip(p: QPainter, s: float) -> None:
    p.drawRoundedRect(_rect(s, 0.24, 0.3, 0.52, 0.4), 3, 3)
    p.drawRect(_rect(s, 0.34, 0.4, 0.14, 0.1))
    p.drawRect(_rect(s, 0.54, 0.4, 0.12, 0.2))
    p.drawLine(QPointF(0.34 * s, 0.78 * s), QPointF(0.66 * s, 0.78 * s))


def _draw_memory(p: QPainter, s: float) -> None:
    p.drawRoundedRect(_rect(s, 0.16, 0.34, 0.68, 0.3), 3, 3)
    for x in (0.28, 0.42, 0.56, 0.7):
        p.drawLine(QPointF(x * s, 0.4 * s), QPointF(x * s, 0.58 * s))
    p.drawLine(QPointF(0.3 * s, 0.64 * s), QPointF(0.3 * s, 0.72 * s))
    p.drawLine(QPointF(0.7 * s, 0.64 * s), QPointF(0.7 * s, 0.72 * s))


def _draw_disk(p: QPainter, s: float) -> None:
    p.drawRoundedRect(_rect(s, 0.18, 0.26, 0.64, 0.48), 4, 4)
    p.drawEllipse(QPointF(0.5 * s, 0.5 * s), 0.16 * s, 0.16 * s)
    p.drawEllipse(QPointF(0.5 * s, 0.5 * s), 0.045 * s, 0.045 * s)


def _draw_network(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.5 * s, 0.2 * s), QPointF(0.5 * s, 0.6 * s))
    p.drawLine(QPointF(0.28 * s, 0.4 * s), QPointF(0.72 * s, 0.4 * s))
    p.drawLine(QPointF(0.28 * s, 0.4 * s), QPointF(0.5 * s, 0.2 * s))
    p.drawLine(QPointF(0.72 * s, 0.4 * s), QPointF(0.5 * s, 0.2 * s))
    p.drawLine(QPointF(0.28 * s, 0.4 * s), QPointF(0.32 * s, 0.7 * s))
    p.drawLine(QPointF(0.72 * s, 0.4 * s), QPointF(0.68 * s, 0.7 * s))
    p.drawLine(QPointF(0.32 * s, 0.7 * s), QPointF(0.68 * s, 0.7 * s))


def _draw_gauge(p: QPainter, s: float) -> None:
    rect = _rect(s, 0.2, 0.24, 0.6, 0.6)
    p.drawArc(rect, 200 * 16, 160 * 16)
    p.drawLine(QPointF(0.5 * s, 0.54 * s), QPointF(0.66 * s, 0.38 * s))


def _draw_cloud(p: QPainter, s: float) -> None:
    p.drawEllipse(QPointF(0.42 * s, 0.5 * s), 0.16 * s, 0.16 * s)
    p.drawEllipse(QPointF(0.6 * s, 0.52 * s), 0.13 * s, 0.13 * s)
    p.drawLine(QPointF(0.26 * s, 0.64 * s), QPointF(0.72 * s, 0.64 * s))


def _draw_shield(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(0.5 * s, 0.18 * s)
    path.lineTo(0.78 * s, 0.3 * s)
    path.lineTo(0.74 * s, 0.6 * s)
    path.lineTo(0.5 * s, 0.8 * s)
    path.lineTo(0.26 * s, 0.6 * s)
    path.lineTo(0.22 * s, 0.3 * s)
    path.closeSubpath()
    p.drawPath(path)


def _draw_gear(p: QPainter, s: float) -> None:
    p.drawEllipse(QPointF(0.5 * s, 0.5 * s), 0.18 * s, 0.18 * s)
    p.drawEllipse(QPointF(0.5 * s, 0.5 * s), 0.06 * s, 0.06 * s)
    for angle in range(0, 360, 45):
        import math

        rad = math.radians(angle)
        x1 = 0.5 * s + math.cos(rad) * 0.22 * s
        y1 = 0.5 * s + math.sin(rad) * 0.22 * s
        x2 = 0.5 * s + math.cos(rad) * 0.31 * s
        y2 = 0.5 * s + math.sin(rad) * 0.31 * s
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _draw_list(p: QPainter, s: float) -> None:
    for y in (0.32, 0.5, 0.68):
        p.drawPoint(QPointF(0.28 * s, y * s))
        p.drawLine(QPointF(0.4 * s, y * s), QPointF(0.74 * s, y * s))


def _draw_calendar(p: QPainter, s: float) -> None:
    p.drawRoundedRect(_rect(s, 0.22, 0.28, 0.56, 0.46), 3, 3)
    p.drawLine(QPointF(0.22 * s, 0.42 * s), QPointF(0.78 * s, 0.42 * s))
    p.drawLine(QPointF(0.36 * s, 0.2 * s), QPointF(0.36 * s, 0.32 * s))
    p.drawLine(QPointF(0.64 * s, 0.2 * s), QPointF(0.64 * s, 0.32 * s))


def _draw_report(p: QPainter, s: float) -> None:
    p.drawRoundedRect(_rect(s, 0.26, 0.2, 0.48, 0.6), 3, 3)
    for y in (0.38, 0.5, 0.62):
        p.drawLine(QPointF(0.38 * s, y * s), QPointF(0.62 * s, y * s))


def _draw_download(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.5 * s, 0.22 * s), QPointF(0.5 * s, 0.6 * s))
    p.drawLine(QPointF(0.34 * s, 0.45 * s), QPointF(0.5 * s, 0.62 * s))
    p.drawLine(QPointF(0.66 * s, 0.45 * s), QPointF(0.5 * s, 0.62 * s))
    p.drawLine(QPointF(0.26 * s, 0.76 * s), QPointF(0.74 * s, 0.76 * s))


def _draw_restore(p: QPainter, s: float) -> None:
    rect = _rect(s, 0.22, 0.26, 0.56, 0.56)
    p.drawArc(rect, 40 * 16, 280 * 16)
    p.drawLine(QPointF(0.24 * s, 0.42 * s), QPointF(0.36 * s, 0.3 * s))
    p.drawLine(QPointF(0.24 * s, 0.42 * s), QPointF(0.36 * s, 0.48 * s))


def _draw_broom(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.66 * s, 0.24 * s), QPointF(0.42 * s, 0.5 * s))
    path = QPainterPath()
    path.moveTo(0.4 * s, 0.5 * s)
    path.lineTo(0.26 * s, 0.78 * s)
    path.lineTo(0.54 * s, 0.64 * s)
    path.closeSubpath()
    p.drawPath(path)


def _draw_trash(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.24 * s, 0.32 * s), QPointF(0.76 * s, 0.32 * s))
    p.drawLine(QPointF(0.42 * s, 0.22 * s), QPointF(0.58 * s, 0.22 * s))
    path = QPainterPath()
    path.moveTo(0.3 * s, 0.32 * s)
    path.lineTo(0.34 * s, 0.8 * s)
    path.lineTo(0.66 * s, 0.8 * s)
    path.lineTo(0.7 * s, 0.32 * s)
    p.drawPath(path)


def _draw_folder(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(0.2 * s, 0.34 * s)
    path.lineTo(0.44 * s, 0.34 * s)
    path.lineTo(0.5 * s, 0.28 * s)
    path.lineTo(0.8 * s, 0.28 * s)
    path.lineTo(0.8 * s, 0.72 * s)
    path.lineTo(0.2 * s, 0.72 * s)
    path.closeSubpath()
    p.drawPath(path)


def _draw_terminal(p: QPainter, s: float) -> None:
    p.drawRoundedRect(_rect(s, 0.18, 0.28, 0.64, 0.44), 3, 3)
    p.drawLine(QPointF(0.3 * s, 0.42 * s), QPointF(0.4 * s, 0.5 * s))
    p.drawLine(QPointF(0.3 * s, 0.58 * s), QPointF(0.4 * s, 0.5 * s))
    p.drawLine(QPointF(0.48 * s, 0.58 * s), QPointF(0.68 * s, 0.58 * s))


def _draw_info(p: QPainter, s: float) -> None:
    p.drawEllipse(QPointF(0.5 * s, 0.5 * s), 0.3 * s, 0.3 * s)
    p.drawPoint(QPointF(0.5 * s, 0.36 * s))
    p.drawLine(QPointF(0.5 * s, 0.46 * s), QPointF(0.5 * s, 0.64 * s))


def _draw_warn(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(0.5 * s, 0.2 * s)
    path.lineTo(0.82 * s, 0.76 * s)
    path.lineTo(0.18 * s, 0.76 * s)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(0.5 * s, 0.38 * s), QPointF(0.5 * s, 0.56 * s))
    p.drawPoint(QPointF(0.5 * s, 0.66 * s))


def _draw_tools(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.26 * s, 0.72 * s), QPointF(0.62 * s, 0.36 * s))
    p.drawLine(QPointF(0.62 * s, 0.36 * s), QPointF(0.74 * s, 0.24 * s))
    p.drawLine(QPointF(0.68 * s, 0.3 * s), QPointF(0.78 * s, 0.4 * s))
    p.drawEllipse(QPointF(0.36 * s, 0.62 * s), 0.1 * s, 0.1 * s)


def _draw_analyze(p: QPainter, s: float) -> None:
    p.drawEllipse(QPointF(0.46 * s, 0.46 * s), 0.24 * s, 0.24 * s)
    p.drawLine(QPointF(0.64 * s, 0.64 * s), QPointF(0.8 * s, 0.8 * s))


def _draw_temp(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.42 * s, 0.28 * s), QPointF(0.42 * s, 0.6 * s))
    p.drawLine(QPointF(0.58 * s, 0.28 * s), QPointF(0.58 * s, 0.6 * s))
    p.drawEllipse(QPointF(0.5 * s, 0.68 * s), 0.14 * s, 0.14 * s)
    p.drawLine(QPointF(0.5 * s, 0.24 * s), QPointF(0.5 * s, 0.62 * s))


def _draw_heart(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(0.5 * s, 0.74 * s)
    path.cubicTo(0.1 * s, 0.5 * s, 0.24 * s, 0.22 * s, 0.5 * s, 0.4 * s)
    path.cubicTo(0.76 * s, 0.22 * s, 0.9 * s, 0.5 * s, 0.5 * s, 0.74 * s)
    p.drawPath(path)


def _draw_power(p: QPainter, s: float) -> None:
    path = QPainterPath()
    rect = _rect(s, 0.26, 0.28, 0.48, 0.48)
    path.arcMoveTo(rect, 110)
    path.arcTo(rect, 110, 200)
    p.drawPath(path)
    p.drawLine(QPointF(0.5 * s, 0.16 * s), QPointF(0.5 * s, 0.5 * s))


def _draw_theme(p: QPainter, s: float) -> None:
    """Círculo con media luna rellena: el tema claro y el oscuro en el mismo icono."""
    p.drawEllipse(QPointF(0.5 * s, 0.5 * s), 0.26 * s, 0.26 * s)
    path = QPainterPath()
    path.moveTo(0.5 * s, 0.24 * s)
    path.arcTo(_rect(s, 0.24, 0.24, 0.52, 0.52), 90, 180)
    path.closeSubpath()
    p.setBrush(p.pen().color())
    p.drawPath(path)


def _draw_window(p: QPainter, s: float) -> None:
    """Ventana con su barra de título: tamaño, sitio y maximizar."""
    p.drawRoundedRect(_rect(s, 0.2, 0.26, 0.6, 0.48), 3, 3)
    p.drawLine(QPointF(0.2 * s, 0.4 * s), QPointF(0.8 * s, 0.4 * s))
    p.drawPoint(QPointF(0.29 * s, 0.34 * s))
    p.drawPoint(QPointF(0.38 * s, 0.34 * s))


def _draw_check(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.28 * s, 0.52 * s), QPointF(0.44 * s, 0.68 * s))
    p.drawLine(QPointF(0.44 * s, 0.68 * s), QPointF(0.74 * s, 0.32 * s))


def _draw_arrow(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.38 * s, 0.3 * s), QPointF(0.62 * s, 0.5 * s))
    p.drawLine(QPointF(0.62 * s, 0.5 * s), QPointF(0.38 * s, 0.7 * s))


def _draw_up(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.5 * s, 0.74 * s), QPointF(0.5 * s, 0.28 * s))
    p.drawLine(QPointF(0.34 * s, 0.44 * s), QPointF(0.5 * s, 0.28 * s))
    p.drawLine(QPointF(0.66 * s, 0.44 * s), QPointF(0.5 * s, 0.28 * s))


def _draw_down(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(0.5 * s, 0.26 * s), QPointF(0.5 * s, 0.72 * s))
    p.drawLine(QPointF(0.34 * s, 0.56 * s), QPointF(0.5 * s, 0.72 * s))
    p.drawLine(QPointF(0.66 * s, 0.56 * s), QPointF(0.5 * s, 0.72 * s))


def _draw_dot(p: QPainter, s: float) -> None:
    p.drawEllipse(QPointF(0.5 * s, 0.5 * s), 0.16 * s, 0.16 * s)


_DRAWERS = {
    "home": _draw_home,
    "cpu": _draw_cpu,
    "chip": _draw_chip,
    "memory": _draw_memory,
    "disk": _draw_disk,
    "network": _draw_network,
    "gauge": _draw_gauge,
    "cloud": _draw_cloud,
    "shield": _draw_shield,
    "gear": _draw_gear,
    "list": _draw_list,
    "calendar": _draw_calendar,
    "report": _draw_report,
    "download": _draw_download,
    "restore": _draw_restore,
    "broom": _draw_broom,
    "trash": _draw_trash,
    "folder": _draw_folder,
    "terminal": _draw_terminal,
    "info": _draw_info,
    "warn": _draw_warn,
    "tool": _draw_tools,
    "analyze": _draw_analyze,
    "temp": _draw_temp,
    "power": _draw_power,
    "heart": _draw_heart,
    "theme": _draw_theme,
    "window": _draw_window,
    "check": _draw_check,
    "arrow": _draw_arrow,
    "up": _draw_up,
    "down": _draw_down,
}


def available_icons() -> list[str]:
    return sorted(_DRAWERS)
