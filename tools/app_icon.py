"""Genera el icono de PC Toolkit y el archivo .ico para el ejecutable.

El dibujo se hace con QPainter (sin ventanas ni recursos externos) y el .ico
se arma a mano con imágenes PNG, formato que Windows acepta desde Vista.

Los colores salen de `DARK_COLORS`: el icono es una imagen fija, así que se pinta
siempre con la paleta oscura (la identidad de la aplicación) y no con la que el
usuario tenga elegida en ese momento en la interfaz.

Uso:
    python tools/app_icon.py [destino.ico]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen  # noqa: E402

from pctoolkit.core.config import DARK_COLORS  # noqa: E402

BASE = 256
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
DEFAULT_TARGET = ROOT / "assets" / "pc_toolkit.ico"


def icon_image(size: int = BASE) -> QImage:
    """Icono de la app: chip verde sobre un cuadrado redondeado de grafito."""
    image = QImage(BASE, BASE, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # Fondo con degradado: el mismo grafito de las tarjetas de la aplicación.
    gradient = QLinearGradient(0, 0, BASE, BASE)
    # Se queda en los grises de tarjeta y no baja al de fondo: así el cuadrado se
    # distingue de una barra de tareas oscura en vez de fundirse con ella.
    gradient.setColorAt(0.0, QColor(DARK_COLORS["panel_alt"]))
    gradient.setColorAt(0.6, QColor(DARK_COLORS["panel"]))
    gradient.setColorAt(1.0, QColor(DARK_COLORS["border_soft"]))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    radius = BASE * 0.23
    painter.drawRoundedRect(QRectF(0, 0, BASE, BASE), radius, radius)

    # Brillo superior
    glow = QLinearGradient(0, 0, 0, BASE * 0.6)
    glow.setColorAt(0.0, QColor(255, 255, 255, 46))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(glow)
    painter.drawRoundedRect(QRectF(BASE * 0.04, BASE * 0.04, BASE * 0.92, BASE * 0.55), radius, radius)

    # Chip, en el verde de acento de la interfaz.
    chip = QRectF(BASE * 0.28, BASE * 0.28, BASE * 0.44, BASE * 0.44)
    pen = QPen(QColor(DARK_COLORS["accent"]))
    pen.setWidthF(BASE * 0.055)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(chip, BASE * 0.05, BASE * 0.05)

    # Núcleo interior, un poco más claro que las patillas.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(DARK_COLORS["accent_glow"]))
    painter.drawRoundedRect(
        QRectF(BASE * 0.42, BASE * 0.42, BASE * 0.16, BASE * 0.16), BASE * 0.03, BASE * 0.03
    )

    # Patillas
    pen.setWidthF(BASE * 0.05)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    for offset in (0.38, 0.5, 0.62):
        painter.drawLine(
            QPointF(BASE * offset, BASE * 0.19), QPointF(BASE * offset, BASE * 0.28)
        )
        painter.drawLine(
            QPointF(BASE * offset, BASE * 0.72), QPointF(BASE * offset, BASE * 0.81)
        )
        painter.drawLine(
            QPointF(BASE * 0.19, BASE * offset), QPointF(BASE * 0.28, BASE * offset)
        )
        painter.drawLine(
            QPointF(BASE * 0.72, BASE * offset), QPointF(BASE * 0.81, BASE * offset)
        )
    painter.end()

    if size == BASE:
        return image
    return image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def png_bytes(size: int) -> bytes:
    """Devuelve el PNG del icono en el tamaño indicado."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    icon_image(size).save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def write_ico(target: Path | str = DEFAULT_TARGET, sizes: tuple[int, ...] = ICO_SIZES) -> Path:
    """Escribe un .ico multi-resolución con entradas PNG."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    images = [(size, png_bytes(size)) for size in sizes]

    header = struct.pack("<HHH", 0, 1, len(images))
    directory = b""
    offset = len(header) + 16 * len(images)
    payload = b""
    for size, data in images:
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)

    target.write_bytes(header + directory + payload)
    return target


def write_png(target: Path | str, size: int = 256) -> Path:
    """Guarda el icono como PNG (para la documentación)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    icon_image(size).save(str(target), "PNG")
    return target


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    target = Path(args[0]) if args else DEFAULT_TARGET
    written = write_ico(target)
    print(f"Icono generado: {written} ({written.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    # QPainter sobre QImage funciona sin ventana, pero Qt necesita una app.
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    raise SystemExit(main())
