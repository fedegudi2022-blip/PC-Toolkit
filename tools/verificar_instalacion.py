"""Comprueba el instalador por la vía real del usuario (rutas de verdad).

    .venv\\Scripts\\python.exe tools\\verificar_instalacion.py

Instala con las rutas reales (%LOCALAPPDATA%, escritorio y menú Inicio del
usuario), mide lo que tarda, comprueba accesos directos, registro, arranque
automático y ejecución de la copia instalada, y **desinstala al terminar**, así
que el equipo queda como estaba. Es la comprobación que conviene hacer tras
generar el instalador; la de `build_installer.py` usa carpetas temporales para no
tocar nada del usuario.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import winreg  # noqa: E402

from installer import installer_core as core  # noqa: E402

SETUP = RAIZ / "dist" / "PC Toolkit Setup.exe"
issues: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"  [{'OK  ' if ok else 'FALLO'}] {nombre}" + (f"  ->  {detalle}" if detalle else ""))
    if not ok:
        issues.append(nombre)


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=600, **kwargs)


def shortcut_target(link: Path) -> str:
    result = run(
        [
            "powershell", "-NoProfile", "-Command",
            f"(New-Object -ComObject WScript.Shell).CreateShortcut('{link}').TargetPath",
        ],
        # Igual que el instalador: Windows PowerShell 5.1 necesita sus carpetas de módulos
        # y no las que herede de PowerShell 7 (en el servidor de compilación, el padre es
        # `pwsh`).
        env=core._powershell_environment(),  # noqa: SLF001
    )
    return (result.stdout or "").strip()


def reg_value(root, subkey: str, name: str) -> str | None:
    try:
        with winreg.OpenKey(root, subkey) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
            return str(value)
    except OSError:
        return None


def main() -> int:
    destino = core.default_install_dir()
    escritorio = core.desktop_dir() / "PC Toolkit.lnk"
    menu = core.start_menu_dir() / "PC Toolkit.lnk"
    clave = core.uninstall_key()
    run_key = core.RUN_SUBKEY

    print("Antes de instalar:")
    check("Sin instalación previa", not destino.exists(), str(destino))
    check("Sin acceso directo en el escritorio", not escritorio.exists(), str(escritorio))
    check("Sin arranque automático", reg_value(winreg.HKEY_CURRENT_USER, run_key, core.RUN_VALUE) is None)

    print("\nInstalando (doble clic equivalente: instalación silenciosa con --autostart):")
    inicio = time.perf_counter()
    proceso = run([str(SETUP), "--silent", "--autostart", "--sin-abrir"], cwd=str(SETUP.parent))
    segundos = time.perf_counter() - inicio
    check("El instalador termina bien", proceso.returncode == 0, f"código {proceso.returncode}")
    print((proceso.stdout or "").strip())
    print(f"  Tiempo total del instalador: {segundos:.1f} s")

    exe = destino / "PC Toolkit.exe"
    check("Programa instalado", exe.exists(), str(exe))
    archivos = core.directory_size(destino)
    check("Copia completa", (destino / "_internal").is_dir() and archivos[1] > 100, f"{archivos[1]} archivos, {archivos[0] / 1048576:.1f} MB")
    check("Desinstalador dentro", core.uninstaller_executable(destino).exists())
    check("Acceso directo en el escritorio", escritorio.exists(), str(escritorio))
    check("Acceso directo en el menú Inicio", menu.exists(), str(menu))
    for link in (escritorio, menu):
        objetivo = shortcut_target(link)
        check(f"{link.name} apunta al programa instalado", objetivo.lower() == str(exe).lower(), objetivo)
    entrada = core.read_uninstall_entry() or {}
    check("Registrado en «Aplicaciones instaladas»", entrada.get("DisplayName") == "PC Toolkit", str(entrada.get("DisplayName")))
    check("Con desinstalador registrado", entrada.get("UninstallString") == f'"{core.uninstaller_executable(destino)}"', str(entrada.get("UninstallString")))
    check("Con versión y ubicación", bool(entrada.get("DisplayVersion")) and entrada.get("InstallLocation") == str(destino))
    arranque = reg_value(winreg.HKEY_CURRENT_USER, run_key, core.RUN_VALUE)
    check("Arranque automático en la bandeja", bool(arranque) and arranque.endswith("--bandeja"), str(arranque))

    print("\nEjecutando la copia instalada (mantenimiento simulado):")
    simulacion = run([str(exe), "--mantenimiento", "--simular"], cwd=str(destino))
    check("La aplicación instalada funciona", simulacion.returncode == 0 and "Simulaci" in (simulacion.stdout or ""), (simulacion.stdout or "").strip()[-200:])

    print("\nDesinstalando (lo que hace el desinstalador real):")
    borrado = run([str(core.uninstaller_executable(destino)), "--silencioso"], cwd=str(Path.home()))
    check("El desinstalador termina bien", borrado.returncode == 0, f"código {borrado.returncode}")
    print((borrado.stdout or "").strip())
    check("Acceso directo del escritorio eliminado", not escritorio.exists())
    check("Acceso directo del menú Inicio eliminado", not menu.exists())
    check("Registro limpio", core.read_uninstall_entry() is None)
    check("Arranque automático quitado", reg_value(winreg.HKEY_CURRENT_USER, run_key, core.RUN_VALUE) is None)
    limite = time.time() + 45
    while time.time() < limite and destino.exists():
        time.sleep(1)
    check("Carpeta de programa eliminada", not destino.exists(), str(destino))
    datos = core.user_data_dir()
    check("Datos del usuario conservados", True, str(datos))

    print()
    if issues:
        print("Fallos: " + ", ".join(issues))
        return 1
    print("Todo correcto: instalación real, accesos directos, registro, arranque automático, ejecución y desinstalación limpia.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
