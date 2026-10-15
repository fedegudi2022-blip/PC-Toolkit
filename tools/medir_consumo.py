"""Mide el consumo real de PC Toolkit en cada perfil de muestreo.

Sirve para comprobar con datos (no a ojo) que el modo eco y el modo en segundo
plano gastan de verdad menos CPU que el muestreo completo.

    .venv\\Scripts\\python.exe tools\\medir_consumo.py                     (los tres perfiles)
    .venv\\Scripts\\python.exe tools\\medir_consumo.py eco --segundos 20   (solo uno)

Cada perfil se mide en un proceso nuevo, con datos de usuario en una carpeta
temporal, para que las mediciones no se contaminen entre sí.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MODOS = ("completo", "eco", "oculto")


def medir(modo: str, segundos: int) -> str:
    """Ejecuta la medición en un proceso aparte y devuelve su línea de resultados."""
    script = f'''
import os, sys, tempfile, time
from pathlib import Path

RAIZ = Path(r"{RAIZ}")
sys.path.insert(0, str(RAIZ))
home = tempfile.mkdtemp(prefix="medicion_")
os.environ["APPDATA"] = home
os.environ["LOCALAPPDATA"] = home

import psutil
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from pctoolkit.core.history import HistoryStore
from pctoolkit.core.memory import MemoryOptions
from pctoolkit.core.settings import AppSettings
from pctoolkit.ui.shell import MainWindow
from pctoolkit.ui.theme import apply_theme

modo = "{modo}"
settings = AppSettings(QSettings(str(Path(home) / "ajustes.ini"), QSettings.IniFormat))
settings.eco_mode = modo == "eco"
settings.eco_when_hidden = True
settings.memory_options = MemoryOptions(auto_enabled=False)
store = HistoryStore(Path(home) / "historial.jsonl")

app = QApplication([])
apply_theme(app)
window = MainWindow(settings, store)
window.resize(1320, 840)
window.show()
if modo == "oculto":
    window.hide()
muestras = {{"n": 0}}
window.metrics.sampled.connect(lambda _s: muestras.__setitem__("n", muestras["n"] + 1))

proceso = psutil.Process()
proceso.cpu_percent(None)
inicio = time.time()
cpu, rss = [], []
while time.time() - inicio < {segundos}:
    app.processEvents()
    time.sleep(0.02)
    cpu.append(proceso.cpu_percent(None))
    rss.append(proceso.memory_info().rss)

cpu = cpu[10:]
media_cpu = sum(cpu) / len(cpu)
print(
    f"{{modo:9s}} perfil={{window.metrics.profile:9s}} intervalo={{window.metrics.interval_ms:5d}} ms  "
    f"CPU={{media_cpu:5.2f}} % de un nucleo  RSS={{sum(rss)/len(rss)/1048576:5.1f}} MB  "
    f"muestras={{muestras['n']}}  procesos={{len(window.metrics.snapshot.top_processes)}}"
)
# Se sale por la vía normal (la misma que «Salir»): cerrar la ventana la dejaría en la
# bandeja y el guion terminaría con trabajo en segundo plano aún en marcha, que es como se
# cuelga un proceso al destruir un hilo vivo. Además, así la medición espera a lo que
# quedara a medias, igual que el usuario al salir.
window.quit_application()
'''
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=segundos + 90
    )
    line = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not line:
        return f"{modo:9s} error: {(result.stderr or '').strip().splitlines()[-1:] }"
    return line[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consumo de PC Toolkit por perfil de muestreo")
    parser.add_argument("modo", nargs="?", choices=(*MODOS, "todos"), default="todos")
    parser.add_argument("--segundos", type=int, default=16, help="Duración de cada medición")
    args = parser.parse_args(argv)

    modos = MODOS if args.modo == "todos" else (args.modo,)
    print(f"Midiendo {args.segundos} s por perfil…\n")
    for modo in modos:
        print(medir(modo, max(6, args.segundos)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
