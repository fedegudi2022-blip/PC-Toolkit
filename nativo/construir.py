"""Compila el módulo nativo de PC Toolkit (`pctk_nativo.dll`).

    .venv\\Scripts\\python.exe nativo\\construir.py
    .venv\\Scripts\\python.exe nativo\\construir.py --salida C:\\ruta\\pctk_nativo.dll

De momento lleva dos trabajos, cada uno en su archivo:

* `escaner_disco.cpp`: el recorrido del analizador de disco (`disk.py`), que **no** se usa
  por defecto porque en las medidas sale igual que el de Python (ver DOCUMENTACION.md);
* `paquetes_appx.cpp`: el listado de aplicaciones instaladas (`apps.py`), que es lo que
  evita los ~6 s de `Get-AppxPackage`.

El módulo es **opcional**: si no está compilado, la aplicación tira por sus caminos de
siempre. Por eso este script avisa y sale con el código 2 cuando no hay ningún compilador, en
vez de fallar: en un equipo sin C++ la aplicación funciona igual.

Compiladores que busca, en este orden:

1. `g++` de MinGW-w64 (el que instala `winget install BrechtSanders.WinLibs.POSIX.UCRT`).
   Se enlaza el runtime **dentro** de la DLL (`-static-libgcc -static-libstdc++`) para que el
   archivo valga solo y no haya que repartir más piezas con la aplicación.
2. `cl` de MSVC, si está en el PATH (en el servidor de integración lo pone el paso que
   prepara el entorno de Visual Studio).
3. `cl` a través de `vcvars64.bat`, buscando la instalación con `vswhere`.

Los códigos de salida son 0 (compilado), 1 (falló la compilación) y 2 (sin compilador).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FUENTES = (AQUI / "escaner_disco.cpp", AQUI / "paquetes_appx.cpp")
DESTINO = AQUI / "pctk_nativo.dll"

# Rutas donde suele quedar WinLibs sin estar en el PATH (winget lo instala por usuario).
RUTAS_GPP = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages",
    Path("C:/mingw64"),
    Path("C:/msys64/mingw64"),
)


def buscar_gpp() -> str | None:
    """Ruta de `g++`, si hay uno instalado (en el PATH o donde lo deja winget)."""
    encontrado = shutil.which("g++")
    if encontrado:
        return encontrado
    for base in RUTAS_GPP:
        if not base or not str(base) or not base.exists():
            continue
        for candidato in sorted(base.glob("*/mingw64/bin/g++.exe")):
            return str(candidato)
        for candidato in (base / "bin/g++.exe",):
            if candidato.exists():
                return str(candidato)
    return None


def buscar_cl() -> tuple[str, str | None] | None:
    """Ruta de `cl` y, si hace falta, el `vcvars64.bat` que prepara el entorno."""
    directo = shutil.which("cl")
    if directo:
        return directo, None
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / (
        "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    if not vswhere.exists():
        return None
    try:
        salida = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    instalacion = salida.stdout.strip()
    if not instalacion:
        return None
    vcvars = Path(instalacion) / "VC/Auxiliary/Build/vcvars64.bat"
    if not vcvars.exists():
        return None
    return "", str(vcvars)


def compilar_gpp(gpp: str, fuentes: tuple[Path, ...], destino: Path) -> int:
    orden = [
        gpp,
        "-std=c++17",
        "-O2",
        "-shared",
        # `-static` y no solo `-static-libgcc -static-libstdc++`: con los hilos POSIX,
        # MinGW deja `libwinpthread-1.dll` como dependencia y entonces `ctypes` no puede
        # cargar la DLL en un equipo donde no esté ese archivo.
        "-static",
        "-DNDEBUG",
        *[str(fuente) for fuente in fuentes],
        "-o",
        str(destino),
        "-ladvapi32",
        "-lshlwapi",
    ]
    print("[nativo] " + " ".join(orden))
    try:
        resultado = subprocess.run(orden, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[nativo] No se pudo ejecutar g++: {exc}")
        return 1
    if resultado.stdout.strip():
        print(resultado.stdout.strip())
    if resultado.stderr.strip():
        print(resultado.stderr.strip())
    return 0 if resultado.returncode == 0 else 1


def compilar_cl(cl: str, vcvars: str | None, fuentes: tuple[Path, ...], destino: Path) -> int:
    opciones = [
        "/nologo",
        "/std:c++17",
        "/O2",
        "/EHsc",
        "/LD",
        f"/Fe:{destino}",
        *[str(fuente) for fuente in fuentes],
    ]
    if vcvars:
        orden = subprocess.list2cmdline(
            ["cmd", "/c", f'call "{vcvars}" >nul && cl ' + subprocess.list2cmdline(opciones)]
        )
        print(f"[nativo] {orden}")
        resultado = subprocess.run(orden, capture_output=True, text=True, shell=False, timeout=900)
    else:
        orden = [cl] + opciones
        print("[nativo] " + " ".join(orden))
        resultado = subprocess.run(orden, capture_output=True, text=True, timeout=900)
    if resultado.stdout.strip():
        print(resultado.stdout.strip())
    if resultado.stderr.strip():
        print(resultado.stderr.strip())
    return 0 if resultado.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    analizador = argparse.ArgumentParser(description="Compila el módulo nativo del escáner de disco")
    analizador.add_argument(
        "--salida",
        default=str(DESTINO),
        help="Dónde dejar la DLL (por defecto, junto a este script)",
    )
    analizador.add_argument(
        "--exigir",
        action="store_true",
        help="Sale con error si no hay compilador (lo usa el servidor de integración)",
    )
    args = analizador.parse_args(argv)

    if os.name != "nt":
        print("[nativo] El módulo es para Windows: aquí no hay nada que compilar.")
        return 0 if not args.exigir else 1

    destino = Path(args.salida).resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)

    gpp = buscar_gpp()
    if gpp:
        print(f"[nativo] Compilador: {gpp}")
        codigo = compilar_gpp(gpp, FUENTES, destino)
    else:
        cl = buscar_cl()
        if not cl:
            aviso = (
                "[nativo] No hay compilador de C++: la aplicación usará su recorrido de Python. "
                "Para compilarlo, instala MinGW-w64 con "
                "'winget install BrechtSanders.WinLibs.POSIX.UCRT'."
            )
            print(aviso)
            return 1 if args.exigir else 2
        ruta_cl, vcvars = cl
        print(f"[nativo] Compilador: {'cl (con vcvars64)' if vcvars else ruta_cl}")
        codigo = compilar_cl(ruta_cl, vcvars, FUENTES, destino)

    if codigo != 0:
        print("[nativo] La compilación falló.")
        return 1
    if not destino.exists():
        print(f"[nativo] La compilación terminó sin dejar {destino}.")
        return 1
    print(f"[nativo] Listo: {destino} ({destino.stat().st_size / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
