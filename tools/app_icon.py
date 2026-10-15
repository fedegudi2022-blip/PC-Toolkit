"""Genera el icono de PC Toolkit (el .ico del ejecutable y su PNG) desde el logotipo.

El logotipo original vive en `assets\\pc_toolkit_logo.png` (1024×1024, con
transparencia) y es la única fuente: aquí no se dibuja nada. Se reescala a cada tamaño
del .ico y el .ico se arma a mano con imágenes PNG, formato que Windows acepta desde
Vista.

El .ico y el PNG de `assets\\` **están en el repositorio**, así que si el logotipo
cambia hay que volver a ejecutar esto (`python tools\\app_icon.py`); la suite comprueba
que siguen coincidiendo con el original, de modo que un olvido se nota.

El archivo no se pinta con la paleta de la interfaz a propósito: es la marca de la
aplicación, igual en tema claro y oscuro, y no sigue a los colores que el usuario elija.

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

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

LOGO = ROOT / "assets" / "pc_toolkit_logo.png"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
DEFAULT_TARGET = ROOT / "assets" / "pc_toolkit.ico"

_LOGO: QImage | None = None


def logo_image() -> QImage:
    """El logotipo original, leído una sola vez por ejecución."""
    global _LOGO
    if _LOGO is None:
        if not LOGO.is_file():
            raise FileNotFoundError(f"Falta el logotipo: {LOGO}")
        imagen = QImage(str(LOGO))
        if imagen.isNull():
            raise ValueError(f"No se pudo leer el logotipo: {LOGO}")
        _LOGO = imagen
    return _LOGO


def icon_image(size: int = 256) -> QImage:
    """Icono en el tamaño pedido, reescalado desde el logotipo.

    El original tiene 1024 px y los iconos pequeños son de 16: bajar de golpe a 16
    dejaría el trazo sucio (Qt promedia una ventana mucho mayor que el resultado), así
    que se parte por mitades hasta quedar cerca del tamaño final y se remata con un
    ajuste suave. Es más lento, pero esto se ejecuta al empaquetar, no al abrir.
    """
    size = max(1, int(size))
    actual = logo_image()
    while actual.width() // 2 >= size:
        actual = actual.scaled(
            actual.width() // 2,
            actual.height() // 2,
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
    if actual.width() != size:
        actual = actual.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return actual


def png_bytes(size: int) -> bytes:
    """Devuelve el PNG del icono en el tamaño indicado."""
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
    """Guarda el icono como PNG (para la documentación y las revisiones)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    icon_image(size).save(str(target), "PNG")
    return target


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    target = Path(args[0]) if args else DEFAULT_TARGET
    written = write_ico(target)
    print(f"Icono generado desde {LOGO.name}: {written} ({written.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    # Re escalar imágenes no necesita ventana, pero Qt necesita una aplicación.
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    raise SystemExit(main())
