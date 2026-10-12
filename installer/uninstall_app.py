"""Desinstalador de PC Toolkit: mínimo, sin interfaz gráfica y solo con la biblioteca estándar.

Deliberadamente no usa PySide6: así el desinstalador pesa poco, arranca al
instante y sigue funcionando aunque la aplicación esté dañada. La confirmación y
el aviso final se muestran con un cuadro de diálogo nativo de Windows
(`MessageBoxW`), accesible desde `ctypes`.

    Desinstalar PC Toolkit.exe                  Confirma y desinstala (conserva tus datos)
    Desinstalar PC Toolkit.exe --borrar-datos   Además borra historial, informes y preferencias
    Desinstalar PC Toolkit.exe --silencioso     Sin ventanas (lo usa el panel de control)
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer import installer_core as core  # noqa: E402

MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_ICONINFORMATION = 0x00000040
MB_ICONWARNING = 0x00000030
MB_SETFOREGROUND = 0x00010000
IDYES = 6


def _message(text: str, title: str, flags: int) -> int:
    """Cuadro de diálogo nativo de Windows."""
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_SETFOREGROUND))
    except (AttributeError, OSError):  # pragma: no cover - fuera de Windows
        print(f"{title}: {text}")
        return IDYES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Desinstalador de {core.APP_DISPLAY_NAME}")
    parser.add_argument("--silencioso", "--silent", dest="silent", action="store_true", help="Sin ventanas")
    parser.add_argument("--borrar-datos", action="store_true", help="Borrar también historial e informes")
    parser.add_argument("--dir", dest="target", help="Carpeta donde está instalada la aplicación")
    parser.add_argument("--clave", dest="key", default=core.APP_KEY, help="Nombre de la clave de registro")
    parser.add_argument(
        "--valor-arranque", dest="run_value", default=core.RUN_VALUE, help="Nombre del valor de arranque"
    )
    parser.add_argument("--escritorio", dest="desktop", help="Carpeta de escritorio")
    parser.add_argument("--menu", dest="menu", help="Carpeta del menú Inicio")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    folder = Path(args.target) if args.target else core.default_install_dir()
    entry = core.read_uninstall_entry(args.key)
    installed = str(entry.get("InstallLocation")) if entry else str(folder)

    if not args.silent:
        respuesta = _message(
            f"Se desinstalará {core.APP_DISPLAY_NAME} de:\n{installed}\n\n"
            "Se quitarán los accesos directos, el arranque automático y la tarea de\n"
            "mantenimiento, si la tenías creada.\n\n"
            + (
                "También se borrarán tu historial, tus informes y tus preferencias."
                if args.borrar_datos
                else "Tus datos (historial, informes y preferencias) se conservarán."
            )
            + "\n\n¿Continuar?",
            f"Desinstalar {core.APP_DISPLAY_NAME}",
            MB_YESNO | MB_ICONQUESTION,
        )
        if respuesta != IDYES:
            return 0

    result = core.uninstall(
        target_dir=folder,
        key_name=args.key,
        run_value=args.run_value,
        desktop=Path(args.desktop) if args.desktop else None,
        start_menu=Path(args.menu) if args.menu else None,
        remove_user_data=args.borrar_datos,
        progress=(lambda text: print(f"  {text}")) if args.silent else None,
    )

    if args.silent:
        print(f"  {result.message}")
        for warning in result.warnings:
            print(f"  [aviso] {warning}")
    else:
        detalles = [result.message]
        if result.warnings:
            detalles.append(" ".join(result.warnings))
        detalles.append("Puedes volver a instalarlo cuando quieras.")
        _message(
            "\n\n".join(detalles),
            f"{core.APP_DISPLAY_NAME} desinstalado",
            MB_ICONINFORMATION if result.ok else MB_ICONWARNING,
        )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
