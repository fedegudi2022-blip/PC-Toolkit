"""Genera las capturas del asistente de instalación y del desinstalador.

    .venv\\Scripts\\python.exe tools\\capturar_instalador.py

Guarda `capturas\\instalador.png` y `capturas\\desinstalador.png`, que son las
imágenes que usa la documentación, y las anota en `capturas\\estado.json` con la versión
con la que se han hecho (eso lo comprueba la suite). Para rehacer **todas** las capturas,
mejor `tools\\capturar_documentacion.py`. Sin pantalla disponible se puede ejecutar con
`set PCTOOLKIT_OFFSCREEN=1` antes del comando.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if os.environ.get("PCTOOLKIT_OFFSCREEN") == "1":
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from installer import installer_core  # noqa: E402
from installer.setup_app import InstallerWindow  # noqa: E402
from pctoolkit.ui.theme import apply_theme  # noqa: E402
from tools import capturas  # noqa: E402

SALIDA = ROOT / "capturas"


def _wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def capturar(salida: Path | None = None, app: QApplication | None = None) -> list[str]:
    """Guarda las dos capturas y devuelve sus nombres.

    Se puede llamar desde fuera (`tools\\capturar_documentacion.py` guarda además las de la
    aplicación) pasando la aplicación de Qt ya creada y el tema ya aplicado.
    """
    destino = salida or SALIDA
    destino.mkdir(parents=True, exist_ok=True)
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv[:1])
        # El asistente de la documentación se enseña en claro, que es como sale en la
        # guía, y no según el tema del equipo donde se generen las capturas.
        apply_theme(app, "light")

    # La carpeta de destino se muestra en la captura: se usa la de verdad para
    # que la imagen explique la instalación real (no se instala nada aquí).
    opciones = installer_core.InstallOptions()

    vistas = (
        (InstallerWindow(options=opciones), "instalador.png", "Asistente de instalación"),
        (InstallerWindow(uninstall_mode=True, options=opciones), "desinstalador.png", "Desinstalador"),
    )
    nombres = []
    for ventana, nombre, titulo in vistas:
        ventana.show()
        _wait(500)
        archivo = destino / nombre
        if not ventana.grab().save(str(archivo)):
            raise RuntimeError(f"No se pudo guardar {archivo}")
        print(f"Captura guardada: {archivo}  ({titulo})")
        ventana.close()
        nombres.append(nombre)
    return nombres


def main() -> int:
    SALIDA.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    apply_theme(app, "light")
    capturar(SALIDA, app)
    capturas.marcar(("instalador.png", "desinstalador.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
