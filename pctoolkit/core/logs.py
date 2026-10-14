"""Registro de la aplicación en un archivo de texto.

Los fallos que no llegan a la interfaz se perdían en silencio: un `except OSError: pass`
dejaba al usuario sin saber que sus tareas no se habían guardado o que un informe no se
había escrito. Aquí queda todo anotado en `%LOCALAPPDATA%\\PCToolkit\\logs\\pctoolkit.log`,
con rotación (para que el archivo no crezca sin fin) y sin depender de que la interfaz
esté en marcha: el modo desatendido también escribe.

Se usa a propósito el registrador `pctoolkit` y no el raíz: así no se cambia el
comportamiento de las bibliotecas de terceros, que no tienen por qué escribir aquí.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

LOGGER_NAME = "pctoolkit"
FILE_NAME = "pctoolkit.log"

# Un archivo pequeño y unas pocas copias: lo justo para poder mirar qué pasó ayer sin
# llenar el disco del usuario.
MAX_BYTES = 512 * 1024
BACKUP_COUNT = 3

_configured = False
_path: Path | None = None


def logs_dir() -> Path:
    """Carpeta de registros del usuario."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "PCToolkit" / "logs"


def log_file() -> Path:
    """Archivo de registro que se usa (exista o no todavía)."""
    return logs_dir() / FILE_NAME


def logger(name: str | None = None) -> logging.Logger:
    """Registrador de la aplicación, o de uno de sus módulos (`pctoolkit.tasks`…)."""
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def warning(message: str, exc: BaseException | None = None, name: str = "") -> None:
    """Deja un aviso en el registro sin molestar al usuario.

    Es lo que sustituye a los `except OSError: pass`: el programa sigue funcionando
    igual, pero el motivo queda escrito para poder averiguarlo después.
    """
    logger(name).warning(f"{message}: {exc}" if exc is not None else message)


def setup(level: int = logging.INFO) -> Path | None:
    """Prepara el registro (una sola vez) y el aviso de excepciones sin capturar.

    Devuelve la ruta del archivo, o `None` si no se pudo crear (por permisos o porque la
    carpeta no existe y no se puede crear): en ese caso la aplicación sigue igual, solo
    que sin dejar rastro en disco.
    """
    global _configured, _path
    registrador = logger()
    if _configured:
        return _path
    _configured = True
    registrador.setLevel(level)
    try:
        carpeta = logs_dir()
        carpeta.mkdir(parents=True, exist_ok=True)
        manejador = logging.handlers.RotatingFileHandler(
            carpeta / FILE_NAME,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        manejador.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        registrador.addHandler(manejador)
        _path = carpeta / FILE_NAME
    except OSError as exc:
        print(f"No se pudo preparar el registro en archivo: {exc}", file=sys.stderr)

    # Una excepción que llegue hasta aquí se pierde (no hay nadie que la enseñe): se anota
    # y se deja que el gancho anterior haga lo suyo con el mensaje de siempre.
    anterior = sys.excepthook

    def _excepthook(tipo, valor, rastro) -> None:
        registrador.error("Excepción sin capturar", exc_info=(tipo, valor, rastro))
        anterior(tipo, valor, rastro)

    sys.excepthook = _excepthook

    anterior_hilo = getattr(threading, "excepthook", None)
    if anterior_hilo is not None:

        def _excepthook_hilo(args) -> None:
            registrador.error(
                "Excepción sin capturar en un hilo",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            anterior_hilo(args)

        threading.excepthook = _excepthook_hilo
    return _path
