"""Guardas de documentación: capturas, informe de muestra y recuentos de las guías.

No prueban el código: comprueban que lo que cuentan las guías sigue siendo verdad (que las
capturas son de la versión y la paleta de hoy y que los recuentos que citan son los de la
pasada). Van en su propio archivo a propósito, para que una captura vieja no bloquee la
comprobación de un cambio de código: se pueden dejar fuera con

    .venv\\Scripts\\python.exe tests\\smoke_test.py --sin-documentacion

o ejecutar solas (sin el recuento, que solo tiene sentido al final de la pasada completa):

    .venv\\Scripts\\python.exe tests\\documentacion.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ROOT = AQUI.parent
for ruta in (str(ROOT), str(AQUI)):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

from pctoolkit.core.config import APP_VERSION  # noqa: E402

from _checks import CHECKS, POR_FUNCION, check, report  # noqa: E402


def verificar_documentacion(*, comprobar_recuentos: bool = True) -> None:
    """La documentación no se queda atrás: versión, capturas y recuentos.

    Las capturas llevan dentro la versión del programa (en la barra lateral de cada página y
    en el asistente de instalación) y las guías citan el total de comprobaciones, así que al
    subir de versión o al añadir pruebas hay cosas que rehacer. Aquí se comprueba lo que la
    suite puede saber de verdad: que las guías hablan de la versión actual, que cada captura
    citada existe, que su fondo es el de la paleta de hoy y que `capturas\\estado.json` dice
    que se generó con esta versión. El recuento va el último, cuando ya se sabe el total.
    """
    from collections import Counter

    from PySide6.QtGui import QImage

    import firmar
    from pctoolkit.core import config
    from pctoolkit.ui.tray import tray_available
    from tools import capturas

    guias = {
        nombre: (ROOT / nombre).read_text(encoding="utf-8") for nombre in ("README.md", "DOCUMENTACION.md")
    }

    # --- Las capturas de la documentación ---------------------------------
    # Todas las imágenes de `capturas\` son documentación (unas salen en la portada y otras
    # en el detalle), así que se miran todas; y cualquier ruta que citen las guías tiene que
    # existir de verdad.
    imagenes = sorted((ROOT / "capturas").glob("*.png"))
    citadas = sorted(
        {
            ruta
            for texto in guias.values()
            for ruta in re.findall(r"capturas[/\\][\w./\\-]+\.(?:png|html)", texto)
        }
    )
    faltan = [ruta for ruta in citadas if not (ROOT / ruta.replace("\\", "/")).exists()]
    invalidas = [archivo.name for archivo in imagenes if archivo.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"]
    check(
        "Las capturas de la documentación existen y son PNG",
        bool(imagenes) and not faltan and not invalidas,
        f"{len(imagenes)} capturas y {len(citadas)} enlaces de las guías"
        if not (faltan or invalidas)
        else "faltan o no son PNG: " + ", ".join(faltan + invalidas),
    )

    # --- El fondo de las capturas es el de la paleta actual ---------------
    fondos = {
        config.DARK_COLORS["bg"].lower(),
        config.DARK_COLORS["bg_alt"].lower(),
        config.LIGHT_COLORS["bg"].lower(),
        config.LIGHT_COLORS["bg_alt"].lower(),
    }
    dominantes: dict[str, str] = {}
    for archivo in imagenes:
        imagen = QImage(str(archivo))
        cuenta: Counter = Counter()
        for y in range(0, imagen.height(), 4):
            for x in range(0, imagen.width(), 4):
                cuenta[imagen.pixelColor(x, y).name()] += 1
        dominantes[archivo.name] = cuenta.most_common(1)[0][0] if cuenta else "sin píxeles"
    ajenas = {nombre: color for nombre, color in dominantes.items() if color not in fondos}
    check(
        "Las capturas se ven con la paleta actual",
        bool(dominantes) and not ajenas,
        ", ".join(f"{nombre} ({color})" for nombre, color in ajenas.items())
        or f"{len(dominantes)} capturas con el fondo de la paleta activa",
    )

    # --- Cada captura está anotada con la versión con la que se generó ----
    # Lo anotan las propias herramientas de capturas (`tools\\capturas.py`), así que si una
    # imagen se hizo con otra versión —o se cambió a mano— se sabe por la anotación.
    anotado = capturas.leer()
    problemas = []
    for archivo in imagenes:
        ficha = anotado.get(archivo.name) or {}
        if not ficha:
            problemas.append(f"{archivo.name}: sin anotar")
        elif ficha.get("version") != APP_VERSION:
            problemas.append(f"{archivo.name}: se hizo con la {ficha.get('version')}")
        elif ficha.get("sha256") != capturas.sha256(archivo):
            problemas.append(f"{archivo.name}: cambió desde que se anotó")
    check(
        "Las capturas se generaron con la versión actual",
        not problemas,
        "; ".join(problemas)
        or f"{len(imagenes)} capturas de la {APP_VERSION} (tools\\capturar_documentacion.py)",
    )

    # --- El informe de muestra --------------------------------------------
    # Es un documento generado (no una captura), así que se mira lo que lleva dentro: la
    # versión con la que se hizo y el acento de la paleta clara, que es con la que se pinta.
    informe = ROOT / "capturas" / "informe_muestra.html"
    texto_informe = informe.read_text(encoding="utf-8") if informe.exists() else ""
    ficha_informe = anotado.get(informe.name) or {}
    problemas_informe = []
    if not texto_informe:
        problemas_informe.append(f"no existe {informe.name}")
    else:
        if f"PC Toolkit {APP_VERSION}" not in texto_informe:
            problemas_informe.append("no dice la versión actual")
        if config.LIGHT_COLORS["accent"] not in texto_informe:
            problemas_informe.append(f"no lleva el acento de hoy ({config.LIGHT_COLORS['accent']})")
        if ficha_informe.get("version") != APP_VERSION:
            problemas_informe.append(f"anotado como {ficha_informe.get('version') or 'sin anotar'}")
        elif ficha_informe.get("sha256") != capturas.sha256(informe):
            problemas_informe.append("cambió desde que se anotó")
    check(
        "El informe de muestra es de la versión y la paleta actuales",
        not problemas_informe,
        "; ".join(problemas_informe) or f"informe_muestra.html con la {APP_VERSION} y el acento claro",
    )

    # --- Las guías hablan de la versión actual ----------------------------
    esperados = [
        f"PC-Toolkit-{APP_VERSION}-Setup.exe",
        f"PC-Toolkit-{APP_VERSION}-portable.exe",
    ]
    descargas = [objetivo for objetivo in esperados if objetivo in guias["README.md"]]
    otras = sorted(set(re.findall(r"PC-Toolkit-(\d+\.\d+\.\d+)-", guias["README.md"])) - {APP_VERSION})
    portada = re.search(r"^# PC Toolkit (\d+\.\d+\.\d+)", guias["DOCUMENTACION.md"], re.M)
    check(
        "Las guías hablan de la versión actual",
        len(descargas) == len(esperados) and not otras and bool(portada) and portada.group(1) == APP_VERSION,
        f"README: {len(descargas)}/2 descargas"
        + (f", versiones sueltas: {', '.join(otras)}" if otras else "")
        + f"; DOCUMENTACION: {portada.group(1) if portada else 'sin versión en el título'}",
    )

    # --- El recuento de comprobaciones ------------------------------------
    # Esta es la última comprobación de la suite a propósito: necesita el total definitivo,
    # que es el número de comprobaciones ya hechas más esta misma.
    if not comprobar_recuentos:
        print(
            "Recuentos de las guías: se comprueban en la pasada completa "
            "(tests\\smoke_test.py)."
        )
        return
    total = len(CHECKS) + 1
    documentados = [
        int(numero)
        for texto in guias.values()
        for numero in re.findall(r"(\d+)\s+comprobaciones", texto) + re.findall(r"salen (\d+)", texto)
    ]
    # Sin bandeja, sin signtool, sin `dist\` compilado o sin los contadores de la GPU se
    # omiten unas cuantas, así que en un equipo incompleto la guía puede pedir un número
    # mayor. En uno completo tiene que ser el mismo: si no, alguien añadió pruebas (o quitó)
    # sin actualizar la documentación.
    from pctoolkit.core import gpu as gpu_core

    completo = (
        tray_available()
        and firmar.find_signtool() is not None
        and bool(firmar.artifacts())
        and gpu_core.engine_counters_available()
    )
    coinciden = bool(documentados) and len(set(documentados)) == 1
    documentado = documentados[0] if coinciden else 0
    detalle = (
        f"guías: {sorted(set(documentados))}; esta pasada: {total}; "
        f"equipo completo: {'sí' if completo else 'no'}"
    )
    if coinciden and documentado != total:
        # Con el número no basta para arreglarlo: en un servidor faltaba una comprobación y,
        # sin saber de qué función salía, no había forma de encontrarla (el registro del
        # servidor no se puede abrir desde otro equipo). El reparto va por función.
        reparto = ", ".join(f"{nombre}={cuenta}" for nombre, cuenta in sorted(POR_FUNCION.items()))
        detalle += f"; por función: {reparto}"
    check(
        "Los recuentos de las guías coinciden con esta pasada",
        coinciden and (documentado == total if completo else documentado >= total),
        detalle,
    )


def main() -> int:
    """Ejecuta solo las guardas de documentación."""
    verificar_documentacion(comprobar_recuentos=False)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
