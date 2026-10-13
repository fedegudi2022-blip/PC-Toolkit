"""Genera todas las capturas de la documentación y las anota con la versión actual.

    .venv\\Scripts\\python.exe tools\\capturar_documentacion.py

Guarda en `capturas\\`:

* las ocho páginas de la aplicación (lo mismo que `app.py --screenshot`), en tema oscuro;
* la portada en tema claro (`inicio_claro.png`);
* el diálogo de ajustes (`dialogo_ajustes.png`);
* el asistente de instalación y el desinstalador (lo hace `capturar_instalador.py`).

Al terminar apunta en `capturas\\estado.json` con qué versión se ha hecho cada imagen. Las
capturas llevan la versión dentro (en la barra lateral y en el asistente), así que al subir
de versión hay que ejecutar esto de nuevo; la suite falla si alguna se quedó atrás.

Sin pantalla disponible se puede ejecutar con `set PCTOOLKIT_OFFSCREEN=1` antes del comando,
teniendo en cuenta que las medidas de texto de esa plataforma no son las de un escritorio.
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

from app import _capture_screens  # noqa: E402
from pctoolkit.core.settings import AppSettings  # noqa: E402
from pctoolkit.ui.alert_settings import AlertSettingsDialog  # noqa: E402
from pctoolkit.ui.shell import MainWindow  # noqa: E402
from pctoolkit.ui.theme import apply_theme  # noqa: E402
from pctoolkit.ui.tray import tray_available  # noqa: E402
from tools import capturas, capturar_instalador  # noqa: E402

SALIDA = ROOT / "capturas"
PAGINAS = ("home", "memory", "startup", "disk", "tools", "tasks", "reports", "schedule")


def _wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _nueva_ventana(settings: AppSettings) -> MainWindow:
    """Una ventana como la de la aplicación, ya con la paleta que toque."""
    ventana = MainWindow(settings)
    ventana.resize(1360, 860)
    ventana.show()
    # La primera muestra de psutil siempre da 0 % de CPU: con un par de muestras las
    # capturas salen con datos reales en vez de a cero.
    _wait(2600)
    return ventana


def main() -> int:
    SALIDA.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("PC Toolkit")
    settings = AppSettings()

    # --- Tema oscuro: las ocho páginas y el diálogo de ajustes --------------
    apply_theme(app, "dark")
    # El analizador de disco necesita una carpeta escaneada; la del usuario da una
    # imagen representativa y termina enseguida.
    oscura = _nueva_ventana(settings)
    codigo = _capture_screens(oscura, SALIDA, str(Path.home()))

    # El diálogo es de la familia de las páginas en oscuro, así que se captura con esa
    # paleta todavía puesta. Se crea sin padre para poder abrirlo con la ventana ya
    # cerrada sin arrastrar su estado.
    dialogo = AlertSettingsDialog(settings, oscura.history, tray_available())
    dialogo.show()
    _wait(600)
    archivo = SALIDA / "dialogo_ajustes.png"
    if not dialogo.grab().save(str(archivo)):
        raise RuntimeError(f"No se pudo guardar {archivo}")
    print(f"Captura guardada: {archivo}  (Ajustes y alertas)")
    dialogo.close()

    # --- Tema claro: la portada -------------------------------------------
    apply_theme(app, "light")
    clara = _nueva_ventana(settings)
    archivo = SALIDA / "inicio_claro.png"
    if not clara.grab().save(str(archivo)):
        raise RuntimeError(f"No se pudo guardar {archivo}")
    print(f"Captura guardada: {archivo}  (Inicio en tema claro)")
    clara.close()

    # --- Instalador y desinstalador ---------------------------------------
    # El asistente se dibuja en tema claro: es lo que se ve al ejecutar el instalador.
    apply_theme(app, "light")
    nombres = capturar_instalador.capturar(SALIDA, app)

    capturas.marcar([f"{pagina}.png" for pagina in PAGINAS] + ["inicio_claro.png", "dialogo_ajustes.png", *nombres])
    print(f"Versión anotada en {capturas.ESTADO.name}: {capturas.APP_VERSION}")
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
