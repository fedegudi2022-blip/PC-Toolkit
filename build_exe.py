"""Empaqueta PC Toolkit como ejecutable de Windows con PyInstaller.

Uso:
    python build_exe.py                Carpeta autocontenida (arranque rápido)
    python build_exe.py --onefile      Un único .exe (arranque más lento)
    python build_exe.py --no-verify    Omite la comprobación del ejecutable
    python build_exe.py --debug        Conserva la consola para ver errores
    python build_exe.py --firmar …     Firma el ejecutable al terminar (véase firmar.py)

El resultado queda en `dist/` y no necesita Python instalado en el equipo de
destino: PyInstaller incluye el intérprete, Qt y psutil dentro.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pctoolkit.core.config import APP_NAME, APP_VERSION, format_bytes  # noqa: E402

ASSETS = ROOT / "assets"
ICON = ASSETS / "pc_toolkit.ico"
VERSION_FILE = ASSETS / "version_info.txt"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_EXE = "PC Toolkit.exe"

# Módulos de PySide6 que la aplicación no usa: excluirlos reduce mucho el tamaño.
EXCLUDES = (
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    # Bibliotecas estándar que no se usan
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "pdb",
    "lib2to3",
    "test",
)


# Archivos y carpetas que la aplicación (solo QtWidgets + psutil) no usa.
# Se eliminan tras el empaquetado y la verificación final confirma que todo sigue bien.
PRUNE_FILES = (
    "opengl32sw.dll",
    "Qt6Quick.dll",
    "Qt6Qml.dll",
    "Qt6QuickControls2.dll",
    "Qt6QmlModels.dll",
    "Qt6QmlWorkerScript.dll",
    "Qt6Pdf.dll",
    "Qt6OpenGL.dll",
    "Qt6VirtualKeyboard.dll",
    "QtNetwork.pyd",
    "Qt6Network.dll",
    "libcrypto-3.dll",
    "libcrypto-3-x64.dll",
    "libssl-3.dll",
    "libssl-3-x64.dll",
    "QtPdf.pyd",
    "QtQml.pyd",
    "QtQuick.pyd",
    "QtOpenGL.pyd",
    "QtSvg.pyd",
)
PRUNE_DIRS = (
    "PySide6/plugins/qmltooling",
    "PySide6/plugins/designer",
    "PySide6/plugins/sqldrivers",
    "PySide6/plugins/multimedia",
    "PySide6/plugins/networkinformation",
    "PySide6/plugins/tls",
    "PySide6/plugins/renderers",
    "PySide6/plugins/generic",
    "PySide6/qml",
    "PySide6/translations",
)


def prune_bundle(folder: Path) -> tuple[int, list[str]]:
    """Elimina los archivos que no hacen falta; devuelve bytes liberados."""
    freed = 0
    removed: list[str] = []
    for name in PRUNE_FILES:
        for path in folder.rglob(name):
            try:
                freed += path.stat().st_size
                path.unlink()
                removed.append(str(path.relative_to(folder)))
            except OSError:
                continue
    for relative in PRUNE_DIRS:
        directory = folder / relative
        if directory.is_dir():
            for item in directory.rglob("*"):
                if item.is_file():
                    freed += item.stat().st_size
            shutil.rmtree(directory, ignore_errors=True)
            removed.append(relative)
    return freed, removed


def version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in version.split(".") if part.isdigit()][:4]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


def write_version_file(target: Path = VERSION_FILE) -> Path:
    """Archivo de propiedades del .exe (versión, producto, descripción)."""
    version = version_tuple(APP_VERSION)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version},
    prodvers={version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('0C0A04B0', [
        StringStruct('CompanyName', 'PCToolkit'),
        StringStruct('FileDescription', '{APP_NAME} - caja de herramientas para Windows'),
        StringStruct('FileVersion', '{APP_VERSION}'),
        StringStruct('InternalName', 'pctoolkit'),
        StringStruct('LegalCopyright', 'Uso libre'),
        StringStruct('OriginalFilename', '{APP_EXE}'),
        StringStruct('ProductName', '{APP_NAME}'),
        StringStruct('ProductVersion', '{APP_VERSION}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [3082, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return target


def ensure_icon() -> Path:
    """Genera el icono si no existe (o lo refresca siempre)."""
    from tools.app_icon import write_ico, write_png

    write_ico(ICON)
    write_png(ASSETS / "pc_toolkit_icono.png", 256)
    return ICON


def build_command(onefile: bool, debug: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "PC Toolkit",
        "--icon",
        str(ICON),
        "--version-file",
        str(VERSION_FILE),
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        "--onefile" if onefile else "--onedir",
        "--console" if debug else "--windowed",
    ]
    for module in EXCLUDES:
        command += ["--exclude-module", module]
    command.append(str(ROOT / "app.py"))
    return command


def run_pyinstaller(onefile: bool, debug: bool) -> Path:
    command = build_command(onefile, debug)
    print("Ejecutando PyInstaller…")
    print("  " + " ".join(command[:8]) + " …")
    started = time.time()
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller falló con código {result.returncode}")
    elapsed = time.time() - started
    exe = DIST / APP_EXE if onefile else DIST / APP_NAME / APP_EXE
    print(f"Empaquetado en {elapsed:.1f} s")
    if not exe.exists():
        raise SystemExit(f"No se encontró el ejecutable esperado: {exe}")
    return exe


def measure_startup(exe: Path) -> float:
    """Tiempo de arranque hasta que la app está lista (sin abrir ventana)."""
    started = time.time()
    try:
        subprocess.run(
            [str(exe), "--version"], cwd=str(exe.parent), timeout=120, capture_output=True
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0.0
    return time.time() - started


def folder_size(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
            count += 1
    return total, count


def verify(exe: Path) -> bool:
    """Ejecuta el ejecutable y comprueba que genera las capturas."""
    print("Comprobando el ejecutable (análisis de disco, historial y capturas)…")
    output = Path(tempfile.mkdtemp(prefix="pctoolkit_exe_"))
    sample = output / "carpeta_de_prueba"
    sample.mkdir(parents=True, exist_ok=True)
    (sample / "video.mp4").write_bytes(b"0" * 400_000)
    (sample / "documento.txt").write_bytes(b"0" * 5_000)
    (sample / "subcarpeta").mkdir()
    (sample / "subcarpeta" / "dato.bin").write_bytes(b"0" * 50_000)
    environment = dict(os.environ)
    environment["APPDATA"] = str(output)
    environment["LOCALAPPDATA"] = str(output)
    started = time.time()
    try:
        result = subprocess.run(
            [str(exe), "--screenshot", str(output), "--demo", "--analizar", str(sample)],
            cwd=str(exe.parent),
            env=environment,
            timeout=300,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print("  [!] El ejecutable no terminó en 5 minutos.")
        return False
    elapsed = time.time() - started
    if result.returncode != 0:
        print(f"  [!] Código de salida {result.returncode}")
        print((result.stderr or result.stdout or "").strip()[:800])
        return False
    pages = ("home", "memory", "startup", "disk", "tools", "tasks", "reports", "schedule")
    missing = [name for name in pages if not (output / f"{name}.png").exists()]
    if missing:
        print(f"  [!] Faltan capturas: {', '.join(missing)}")
        print((result.stdout or "").strip()[:400])
        return False
    sizes = [(output / f"{name}.png").stat().st_size for name in pages]
    # El historial demuestra que psutil y el almacén en disco funcionan dentro del .exe.
    history = output / "PCToolkit" / "historial.jsonl"
    lines = history.read_text(encoding="utf-8").strip().splitlines() if history.exists() else []
    if not lines:
        print("  [!] El ejecutable no escribió ninguna muestra del historial")
        return False
    # El modo desatendido (el que lanza la tarea programada) también debe funcionar
    # dentro del .exe; en simulación, para no tocar nada del equipo.
    if not _verify_maintenance(exe, environment):
        return False
    print(f"  Ejecutable verificado en {elapsed:.1f} s: {len(pages)} capturas, "
          f"entre {min(sizes) // 1024} y {max(sizes) // 1024} KB cada una, "
          f"{len(lines)} muestra(s) de historial y mantenimiento desatendido en simulación")
    shutil.rmtree(output, ignore_errors=True)
    return True


def _verify_maintenance(exe: Path, environment: dict) -> bool:
    """Comprueba que el ejecutable puede ejecutar el mantenimiento sin ventana."""
    try:
        result = subprocess.run(
            [str(exe), "--mantenimiento", "--simular"],
            cwd=str(exe.parent),
            env=environment,
            timeout=180,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print("  [!] El mantenimiento desatendido no terminó a tiempo.")
        return False
    salida = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "Simulaci" not in salida:
        print(f"  [!] El mantenimiento desatendido falló (código {result.returncode})")
        print(salida.strip()[:600])
        return False
    pasos = [line.strip() for line in salida.splitlines() if line.strip().startswith("[OK")]
    print(f"  Mantenimiento desatendido: {len(pasos)} paso(s) simulados correctamente")
    return bool(pasos)


def forwarded_signing_arguments(extra: list[str]) -> list[str]:
    """Argumentos de firma: los de la orden o los de PCTOOLKIT_SIGN_ARGS.

    Se pasan a `firmar.py` tal cual, así que valen todas sus opciones
    (`--pfx`, `--huella`, `--sujeto`, `--csp`, `--dlib`, `--sello`…). Lo que no
    venga en la orden se busca en la variable de entorno, que es la forma cómoda
    de firmar en un servidor de compilación (véase .github/workflows/firmar.yml).
    """
    from firmar import environment_arguments

    return [str(parte) for parte in extra] or environment_arguments()


def sign_executables(files: list[Path], extra: list[str]) -> bool:
    """Firma los ejecutables recién creados (opción --firmar)."""
    from firmar import sign_paths

    argumentos = forwarded_signing_arguments(extra)
    print("\nFirmando los ejecutables…")
    if argumentos:
        print("  Argumentos de firma: " + " ".join(argumentos))
    else:
        print("  Sin argumentos propios: se usa el certificado del almacén de Windows o los de")
        print("  PCTOOLKIT_SIGN_ARGS. Para firmar con un PFX:  --firmar --pfx llave.pfx")
    return sign_paths(files, argumentos)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Empaqueta {APP_NAME} {APP_VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python build_exe.py\n"
            "  python build_exe.py --onefile --firmar --pfx llave.pfx\n"
            "  python build_exe.py --firmar --huella 1A2B3C…   (certificado del almacén de Windows)\n"
            "  python build_exe.py --firmar --dlib Azure.CodeSigning.Dlib.dll --metadatos metadata.json\n"
        ),
    )
    parser.add_argument("--onefile", action="store_true", help="Genera un único .exe (más lento al abrir)")
    parser.add_argument("--no-verify", action="store_true", help="No ejecutar la comprobación final")
    parser.add_argument("--debug", action="store_true", help="Mantener la consola visible")
    parser.add_argument(
        "--firmar",
        action="store_true",
        help="Firma el ejecutable al terminar (los argumentos de firma se pasan tal cual a firmar.py)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Los argumentos que no son de este script van al firmador: --pfx, --huella…
    args, extra = parser.parse_known_args(argv)
    if extra and not args.firmar:
        parser.error(
            "argumentos no reconocidos: " + " ".join(extra) + "  (los argumentos de firma necesitan --firmar)"
        )

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller no está instalado. Ejecuta:")
        print("  .venv\\Scripts\\python.exe -m pip install pyinstaller")
        return 1

    icon = ensure_icon()
    version_file = write_version_file()
    print(f"Icono: {icon.relative_to(ROOT)}  ·  Versión: {version_file.relative_to(ROOT)}")

    exe = run_pyinstaller(args.onefile, args.debug)

    if args.onefile:
        print(f"Ejecutable: {exe}  ({format_bytes(exe.stat().st_size)})")
    else:
        freed, removed = prune_bundle(exe.parent)
        print(f"Recorte de archivos no usados: {format_bytes(freed)} ({len(removed)} elementos)")
        total, count = folder_size(exe.parent)
        print(f"Carpeta: {exe.parent}")
        print(f"Tamaño total: {format_bytes(total)} en {count:,} archivos")
        print(f"Ejecutable principal: {exe.name}")

    if args.no_verify:
        print("Comprobación omitida (--no-verify).")
    elif verify(exe):
        startup = measure_startup(exe)
        if startup:
            print(f"Arranque medido (carga de módulos, sin ventana): {startup:.2f} s")
        print("El ejecutable funciona sin Python instalado: listo para copiar a otro equipo.")
    else:
        print("[!] La comprobación del ejecutable ha fallado.")
        return 1

    # Se firma al final, sobre lo que ya está comprobado.
    if args.firmar and not sign_executables([exe], extra):
        print("[!] La firma ha fallado; el ejecutable se queda sin firmar.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
