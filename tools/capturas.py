"""Anota con qué versión se generó cada captura de la documentación.

Las imágenes de `capturas\\` llevan dentro la versión de la aplicación (en la barra lateral de
cada página y en el asistente de instalación), así que al subir de versión hay que volver a
generarlas: si no, la documentación enseña un número que ya no es el del programa.

Aquí se lleva la cuenta de eso. Cada herramienta de capturas apunta en `capturas\\estado.json`
la versión con la que acaba de guardar sus archivos, y la suite
(`tests\\smoke_test.py`) comprueba que esa versión es la actual y que el archivo no ha
cambiado desde entonces. Cuando no coincide, falla diciendo qué capturas hay que rehacer:

    .venv\\Scripts\\python.exe tools\\capturar_documentacion.py     Todas las capturas
    .venv\\Scripts\\python.exe tools\\capturar_instalador.py        Solo el instalador
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pctoolkit.core.config import APP_VERSION  # noqa: E402

CAPTURAS = ROOT / "capturas"
ESTADO = CAPTURAS / "estado.json"


def sha256(ruta: Path) -> str:
    """Resumen del archivo: con esto se nota si la imagen cambió por su cuenta."""
    resumen = hashlib.sha256()
    with ruta.open("rb") as manejador:
        for bloque in iter(lambda: manejador.read(1024 * 1024), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def leer() -> dict[str, dict[str, str]]:
    """Lo anotado hasta ahora; un archivo ilegible se trata como si no hubiera nada."""
    try:
        datos = json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def marcar(nombres: Iterable[str]) -> Path:
    """Apunta la versión actual y el resumen de las capturas recién generadas."""
    datos = leer()
    for nombre in nombres:
        ruta = CAPTURAS / nombre
        if not ruta.exists():
            raise FileNotFoundError(f"No existe la captura que se quiere anotar: {ruta}")
        datos[nombre] = {"version": APP_VERSION, "sha256": sha256(ruta)}
    ESTADO.write_text(
        json.dumps(datos, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return ESTADO
