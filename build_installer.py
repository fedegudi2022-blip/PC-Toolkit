"""Genera el instalador de PC Toolkit y comprueba que instala y desinstala de verdad.

    python build_installer.py              Empaqueta y verifica
    python build_installer.py --rebuild    Reconstruye antes la aplicación
    python build_installer.py --no-verify  Solo empaqueta
    python build_installer.py --firmar …   Firma al terminar (véase firmar.py)

El resultado queda listo para repartir en `dist\\`:

    dist\\PC Toolkit Setup.exe     Instalador (doble clic)
    dist\\PC Toolkit\\              Aplicación que copia el instalador

La comprobación instala en una carpeta temporal con escritorio, menú Inicio y
clave de registro propios, ejecuta la aplicación instalada, la desinstala y
verifica que no queda rastro. Así se prueba el ciclo completo sin ensuciar el
equipo del usuario.
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

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

APP_FOLDER = RAIZ / "dist" / "PC Toolkit"
SETUP_EXE = RAIZ / "dist" / "PC Toolkit Setup.exe"
UNINSTALLER = APP_FOLDER / "Desinstalar PC Toolkit.exe"
BUILD_DIR = RAIZ / "build" / "installer"

TEST_KEY = "PCToolkitBuildTest"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("  " + " ".join(str(part) for part in command[:6]) + (" …" if len(command) > 6 else ""))
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def app_build_is_stale() -> bool:
    """¿La aplicación empaquetada es más antigua que el código fuente?"""
    exe = APP_FOLDER / "PC Toolkit.exe"
    if not exe.exists():
        return True
    empaquetado = exe.stat().st_mtime
    fuentes = [RAIZ / "app.py", *sorted((RAIZ / "pctoolkit").rglob("*.py"))]
    return any(fuente.stat().st_mtime > empaquetado for fuente in fuentes if fuente.exists())


def build_app(rebuilt: bool) -> bool:
    """Genera (o comprueba) la carpeta de la aplicación y el archivo de versión."""
    from build_exe import write_version_file

    write_version_file()
    if rebuilt or app_build_is_stale():
        print("Generando la aplicación con PyInstaller…")
        result = run([sys.executable, str(RAIZ / "build_exe.py")], cwd=str(RAIZ))
        if result.returncode != 0:
            print((result.stdout or "")[-1500:])
            print((result.stderr or "")[-800:])
            return False
    else:
        print("La aplicación empaquetada está al día; se reutiliza.")
    return True


def build_uninstaller() -> bool:
    """Compila el desinstalador y lo deja dentro de la carpeta de la aplicación."""
    print("Compilando el desinstalador…")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "Desinstalar PC Toolkit",
        "--distpath",
        str(APP_FOLDER),
        "--workpath",
        str(BUILD_DIR / "uninstaller"),
        "--specpath",
        str(BUILD_DIR),
        "--paths",
        str(RAIZ),
        "--exclude-module",
        "PySide6",
        "--exclude-module",
        "PySide6.QtWidgets",
        str(RAIZ / "installer" / "uninstall_app.py"),
    ]
    result = run(command, cwd=str(RAIZ))
    if result.returncode != 0 or not UNINSTALLER.exists():
        print((result.stdout or "")[-1500:])
        print((result.stderr or "")[-800:])
        return False
    return True


def build_setup() -> bool:
    """Compila el instalador en `dist\\` junto a la carpeta de la aplicación."""
    print("Compilando el instalador…")
    icon = RAIZ / "assets" / "pc_toolkit.ico"
    version_file = RAIZ / "assets" / "version_info.txt"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
    ]
    if version_file.exists():
        command += ["--version-file", str(version_file)]
    command += [
        "--name",
        "PC Toolkit Setup",
        "--icon",
        str(icon),
        "--distpath",
        str(RAIZ / "dist"),
        "--workpath",
        str(BUILD_DIR / "setup"),
        "--specpath",
        str(BUILD_DIR),
        "--paths",
        str(RAIZ),
        str(RAIZ / "installer" / "setup_app.py"),
    ]
    result = run(command, cwd=str(RAIZ))
    if result.returncode != 0 or not SETUP_EXE.exists():
        print((result.stdout or "")[-1500:])
        print((result.stderr or "")[-800:])
        return False
    size = SETUP_EXE.stat().st_size / (1024 * 1024)
    print(f"  Instalador generado: {SETUP_EXE.name} ({size:.1f} MB)")
    return True


def remove_tree(path: Path, attempts: int = 10, wait_seconds: float = 0.5) -> bool:
    """Borra una carpeta insistiendo un poco (Windows puede tenerla ocupada)."""
    for intento in range(max(1, attempts)):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        if intento < attempts - 1:
            time.sleep(wait_seconds)
    return not path.exists()


def verify() -> bool:
    """Ciclo completo con rutas y clave de registro propias de la prueba."""
    print("Comprobando el instalador (instalar, ejecutar y desinstalar)…")
    base = Path(tempfile.mkdtemp(prefix="pctoolkit_installer_"))
    destino = base / "programa"
    escritorio = base / "escritorio"
    menu = base / "menu"
    datos = base / "appdata"
    entorno = dict(os.environ, APPDATA=str(datos), LOCALAPPDATA=str(base / "local"))
    entorno.pop("PCTOOLKIT_OFFSCREEN", None)
    for carpeta in (escritorio, menu, datos):
        carpeta.mkdir(parents=True, exist_ok=True)

    ok = True

    # --- Instalación silenciosa -------------------------------------------------
    install = run(
        [
            str(SETUP_EXE),
            "--silent",
            "--dir",
            str(destino),
            "--clave",
            TEST_KEY,
            "--escritorio",
            str(escritorio),
            "--menu",
            str(menu),
            "--autostart",
            "--sin-abrir",
        ],
        cwd=str(SETUP_EXE.parent),
        env=entorno,
        timeout=600,
    )
    if install.returncode != 0:
        print(f"  [!] La instalación falló (código {install.returncode})")
        print((install.stdout or "")[-1200:])
        print((install.stderr or "")[-600:])
        return False

    copiada = destino / "PC Toolkit.exe"
    desinstalador = destino / "Desinstalar PC Toolkit.exe"
    if not copiada.exists() or not (destino / "_internal").is_dir():
        print("  [!] La aplicación no se copió completa")
        return False
    if not desinstalador.exists():
        print("  [!] Falta el desinstalador dentro de la instalación")
        ok = False
    for enlace in (escritorio / "PC Toolkit.lnk", menu / "PC Toolkit.lnk"):
        if not enlace.exists():
            print(f"  [!] No se creó el acceso directo {enlace}")
            ok = False

    # El acceso directo debe apuntar al ejecutable instalado.
    consulta = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(New-Object -ComObject WScript.Shell).CreateShortcut('{escritorio / 'PC Toolkit.lnk'}').TargetPath",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    objetivo = (consulta.stdout or "").strip()
    if objetivo.lower() != str(copiada).lower():
        print(f"  [!] El acceso directo apunta a {objetivo!r} en vez de {str(copiada)!r}")
        ok = False

    # --- Registro ----------------------------------------------------------------
    import winreg

    clave = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{TEST_KEY}"
    entrada = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as handle:
            indice = 0
            while True:
                try:
                    nombre, valor, _tipo = winreg.EnumValue(handle, indice)
                except OSError:
                    break
                entrada[nombre] = valor
                indice += 1
    except OSError:
        print("  [!] No se registró la aplicación en «Aplicaciones instaladas»")
        ok = False

    if entrada:
        esperado = {
            "DisplayName": "PC Toolkit",
            "UninstallString": f'"{desinstalador}"',
            "InstallLocation": str(destino),
        }
        for nombre, valor in esperado.items():
            if entrada.get(nombre) != valor:
                print(f"  [!] Registro: {nombre} = {entrada.get(nombre)!r} (esperado {valor!r})")
                ok = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as handle:
            arranque, _tipo = winreg.QueryValueEx(handle, "PCToolkit")
        if "--bandeja" not in str(arranque):
            print(f"  [!] Arranque automático sin --bandeja: {arranque!r}")
            ok = False
    except OSError:
        print("  [!] No se activó el arranque automático")
        ok = False

    # --- La copia instalada funciona --------------------------------------------
    simulacion = run(
        [str(copiada), "--mantenimiento", "--simular"],
        cwd=str(destino),
        env=entorno,
        timeout=300,
    )
    if simulacion.returncode != 0 or "Simulaci" not in (simulacion.stdout or ""):
        print(f"  [!] La aplicación instalada no funciona (código {simulacion.returncode})")
        print((simulacion.stdout or "")[-600:])
        ok = False

    # --- Desinstalación ----------------------------------------------------------
    borrado = run(
        [
            str(desinstalador),
            "--silencioso",
            "--dir",
            str(destino),
            "--clave",
            TEST_KEY,
            "--escritorio",
            str(escritorio),
            "--menu",
            str(menu),
        ],
        cwd=str(base),
        env=entorno,
        timeout=300,
    )
    if borrado.returncode != 0:
        print(f"  [!] La desinstalación falló (código {borrado.returncode})")
        print((borrado.stdout or "")[-800:])
        ok = False

    for enlace in (escritorio / "PC Toolkit.lnk", menu / "PC Toolkit.lnk"):
        if enlace.exists():
            print(f"  [!] Quedó el acceso directo {enlace}")
            ok = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave):
            print("  [!] Quedó la clave de registro de la aplicación")
            ok = False
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as handle:
            winreg.QueryValueEx(handle, "PCToolkit")
        print("  [!] Quedó activado el arranque automático")
        ok = False
    except OSError:
        pass

    # La carpeta se borra desde un proceso aparte: se le da tiempo.
    limite = time.time() + 40
    while time.time() < limite and destino.exists():
        time.sleep(1)
    if destino.exists():
        print(f"  [!] La carpeta de instalación sigue ahí: {destino}")
        ok = False

    # La carpeta temporal de la prueba debe desaparecer: se insiste unas cuantas
    # veces porque Windows puede tenerla ocupada un instante tras cerrar la
    # aplicación instalada, y si no se consigue se informa (no se oculta).
    if not remove_tree(base):
        print(f"  [!] No se pudo borrar del todo la carpeta temporal de la prueba: {base}")
        ok = False

    if ok:
        print("  Instalación, accesos directos, registro, arranque automático, ejecución y")
        print("  desinstalación comprobados correctamente (sin dejar rastro).")
    return ok


def sign_executables(extra: list[str]) -> bool:
    """Firma todo lo compilado en `dist\\` (opción --firmar).

    Se firma al final, cuando ya está todo creado: el programa que copia el
    instalador, el desinstalador, el propio instalador y, si existe, la variante
    de un solo archivo. Los argumentos de firma son los de `firmar.py` tal cual
    (`--pfx`, `--huella`, `--dlib`…), o los de `PCTOOLKIT_SIGN_ARGS`.
    """
    from firmar import artifacts, environment_arguments, sign_paths

    argumentos = [str(parte) for parte in extra] or environment_arguments()
    archivos = artifacts()
    print("\nFirmando los ejecutables de dist/…")
    if not archivos:
        print("  [!] No hay ningún ejecutable que firmar en dist/.")
        return False
    if argumentos:
        print("  Argumentos de firma: " + " ".join(argumentos))
    else:
        print("  Sin argumentos propios: se usa el certificado del almacén de Windows o los de")
        print("  PCTOOLKIT_SIGN_ARGS. Para firmar con un PFX:  --firmar --pfx llave.pfx")
    return sign_paths(archivos, argumentos)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Genera el instalador de PC Toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python build_installer.py\n"
            "  python build_installer.py --rebuild --firmar --pfx llave.pfx\n"
            "  python build_installer.py --firmar --dlib Azure.CodeSigning.Dlib.dll --metadatos metadata.json\n"
        ),
    )
    parser.add_argument("--rebuild", action="store_true", help="Reconstruye la aplicación antes")
    parser.add_argument("--no-verify", action="store_true", help="No comprobar el resultado")
    parser.add_argument(
        "--firmar",
        action="store_true",
        help="Firma el instalador, el desinstalador y el programa al terminar",
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

    if not build_app(args.rebuild):
        print("No se pudo preparar la aplicación.")
        return 1
    if not build_uninstaller():
        print("No se pudo compilar el desinstalador.")
        return 1
    if not build_setup():
        print("No se pudo compilar el instalador.")
        return 1

    if not args.no_verify and not verify():
        print("\nEl instalador no superó la comprobación.")
        return 1

    # Se firma al final, cuando el instalador y sus piezas ya están creadas.
    if args.firmar and not sign_executables(extra):
        print("\n[!] La firma ha fallado; los ejecutables se quedan sin firmar.")
        return 1

    print("\nListo para repartir:")
    print(f"  {SETUP_EXE}  ({SETUP_EXE.stat().st_size / (1024 * 1024):.1f} MB)")
    print(f"  {APP_FOLDER}  (carpeta completa, con el desinstalador dentro)")
    print("Copia ambos al mismo sitio: el instalador busca la aplicación en su propia carpeta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
