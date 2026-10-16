"""Módulo nativo opcional (C++): el escáner de disco y el listado de aplicaciones.

Las partes que se llevan al módulo nativo (C++, `nativo\\*.cpp`, se compila con
`nativo\\construir.py`) son las que se midieron antes y después:

* **paquetes instalados**: leerlos del registro en vez de preguntarle a PowerShell. Medido en
  un equipo con 97 paquetes: 5,73 s con `Get-AppxPackage` frente a ~0,05 s por aquí. El
  registro se lee en **dos** sitios —lo que la cuenta tiene registrado y lo que la máquina
  tiene puesto para ella—, porque uno solo deja fuera aplicaciones instaladas. Lo que el
  registro no puede dar son las aplicaciones que algunas ediciones (Windows Server) guardan
  solo en su base de datos interna: ahí el listado trae menos que `Get-AppxPackage` (véase
  `pctoolkit/core/apps.py` y, en la documentación, el apartado 3.12).
* **recorrido del disco**: va en el módulo, pero **no se usa por defecto**: en las medidas
  sale igual que el de Python (0,83 s frente a 0,83 s en `C:\\Program Files`, 5,44 frente a
  5,18 en `C:\\Windows\\WinSxS`), porque el recorrido es de disco y `os.scandir` ya es C. Se
  deja detrás de `PCTOOLKIT_NATIVO_DISCO=1` para poder medirlo.

El módulo es **opcional** en todo: si la DLL no está compilada, no se puede cargar o el
sistema no es Windows, cada sitio tira por su camino de siempre y no cambia nada más. Aquí no
se decide qué hacer con los datos: se llama y se devuelve lo que contesta.

Dónde se busca la DLL, por orden:

1. `PCTOOLKIT_NATIVO`, si apunta a un archivo (lo usan las comprobaciones para probar una
   compilación concreta y para forzar el camino de Python);
2. lo que haya junto al ejecutable cuando la aplicación va empaquetada (`sys._MEIPASS`);
3. `nativo\\pctk_escaner.dll` en el proyecto (donde lo deja `construir.py`);
4. dentro del paquete, por si una instalación decide llevarla ahí.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from . import logs

NOMBRE = "pctk_nativo.dll"
VARIABLE_RUTA = "PCTOOLKIT_NATIVO"
# El recorrido de disco del módulo nativo sale a pedirlo así: por defecto se usa el de Python,
# que en las medidas va igual o mejor (ver el docstring de arriba).
VARIABLE_DISCO = "PCTOOLKIT_NATIVO_DISCO"
# Valores con los que se apaga el módulo a propósito: sirven para comparar los dos recorridos
# (el nativo y el de Python) sin renombrar archivos, y para quien prefiera el de Python.
APAGADO = {"0", "no", "off", "ninguno", "nunca"}

# Cada cuánto se comprueba, durante un escaneo nativo, si hay que parar. La DLL lee su bandera
# a cada paso; quien la pone es este vigilante, porque la llamada está ocupada en C++.
INTERVALO_CANCELACION_MS = 100

# La función de aviso que la DLL llama de vez en cuando: (contexto, ruta, archivos vistos).
Aviso = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulonglong)

_estado: dict[str, Any] = {"intentado": False, "dll": None, "motivo": ""}
_candado = threading.Lock()


def _candidatas() -> list[Path]:
    """Rutas donde puede estar la DLL, en orden de preferencia."""
    opciones: list[Path] = []
    elegida = os.environ.get(VARIABLE_RUTA)
    if elegida:
        opciones.append(Path(elegida))
    empaquetado = getattr(sys, "_MEIPASS", None)
    if empaquetado:
        opciones.append(Path(empaquetado) / "nativo" / NOMBRE)
        opciones.append(Path(empaquetado) / NOMBRE)
    opciones.append(Path(getattr(sys, "executable", "")).parent / NOMBRE)
    proyecto = Path(__file__).resolve().parents[2]
    opciones.append(proyecto / "nativo" / NOMBRE)
    opciones.append(Path(__file__).resolve().parent / NOMBRE)
    return [ruta for ruta in opciones if str(ruta)]


def _cargar() -> tuple[Any, str]:
    """Carga la DLL una sola vez. Devuelve (librería, motivo si no se pudo)."""
    with _candado:
        if _estado["intentado"]:
            return _estado["dll"], _estado["motivo"]
        _estado["intentado"] = True
        if os.name != "nt":
            _estado["motivo"] = "solo Windows tiene este módulo"
            return None, _estado["motivo"]
        if (os.environ.get(VARIABLE_RUTA) or "").strip().lower() in APAGADO:
            _estado["motivo"] = f"desactivado con {VARIABLE_RUTA}"
            return None, _estado["motivo"]
        for ruta in _candidatas():
            if not ruta.exists() or not ruta.is_file():
                continue
            try:
                libreria = ctypes.CDLL(str(ruta))
                libreria.pctk_escanear_disco.restype = ctypes.c_void_p
                libreria.pctk_escanear_disco.argtypes = [
                    ctypes.c_wchar_p,
                    ctypes.c_ulonglong,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_wchar_p,
                    ctypes.c_wchar_p,
                    ctypes.c_wchar_p,
                    Aviso,
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_int),
                ]
                libreria.pctk_liberar.restype = None
                libreria.pctk_liberar.argtypes = [ctypes.c_void_p]
            except (OSError, AttributeError) as exc:
                # Una DLL vieja o de otra versión no puede tumbar el escaneo: se sigue con
                # Python, que es lo que se hacía antes de que existiera.
                _estado["motivo"] = f"no se pudo cargar {ruta.name}: {exc}"
                logs.warning(f"El módulo nativo ({ruta}) no se pudo cargar", exc, name="disco")
                continue
            _estado["dll"] = libreria
            _estado["ruta"] = str(ruta)
            _estado["motivo"] = ""
            return libreria, ""
        if not _estado["motivo"]:
            _estado["motivo"] = "no está compilado (nativo\\construir.py)"
        return None, _estado["motivo"]


def disponible() -> tuple[bool, str]:
    """¿Se puede usar el módulo nativo? Con el motivo cuando no."""
    libreria, motivo = _cargar()
    return libreria is not None, motivo


def ruta() -> str:
    """Ruta de la DLL en uso (vacío si no hay ninguna)."""
    _cargar()
    return str(_estado.get("ruta") or "")


def disco_por_defecto() -> bool:
    """¿Se pide el recorrido del disco al módulo nativo sin que nadie lo diga?

    Por defecto no: en las medidas (ver el docstring del módulo) sale igual que el de Python.
    Con `PCTOOLKIT_NATIVO_DISCO=1` se enciende para poder medirlo o probarlo.
    """
    return (os.environ.get(VARIABLE_DISCO) or "").strip().lower() in ("1", "si", "sí", "yes", "on")


def listar_paquetes() -> list[dict[str, Any]] | None:
    """Paquetes instalados leídos del registro. Nada si el módulo no está.

    Devuelve lo mismo que traía `Get-AppxPackage` en lo que la aplicación usa —nombre
    completo, nombre visible, carpeta, marco de trabajo y si viene con Windows—, pero sin
    arrancar un proceso de PowerShell (que era lo que costaba los seis segundos). En Windows
    10 y 11, que es donde vive la aplicación, el registro trae **más** que `Get-AppxPackage`
    (medido: 98 y 89 en un Windows 11); en ediciones de servidor puede traer menos, porque
    Windows guarda ahí las aplicaciones provisionadas solo en su base de datos interna.
    """
    libreria, _motivo = _cargar()
    if libreria is None:
        return None
    try:
        libreria.pctk_listar_paquetes.restype = ctypes.c_void_p
        libreria.pctk_listar_paquetes.argtypes = []
        puntero = libreria.pctk_listar_paquetes()
    except (OSError, AttributeError) as exc:
        logs.warning("El listado nativo de aplicaciones falló", exc, name="aplicaciones")
        return None
    if not puntero:
        return None
    try:
        texto = ctypes.wstring_at(puntero)
    finally:
        libreria.pctk_liberar(ctypes.c_void_p(puntero))
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:  # pragma: no cover - respuesta inesperada
        logs.warning("El listado nativo devolvió algo que no es JSON", exc, name="aplicaciones")
        return None
    return datos if isinstance(datos, list) else None


def escanear(
    root: str,
    *,
    min_large_file: int,
    top_children: int,
    top_files: int,
    saltar: list[str],
    categorias: dict[str, tuple[str, ...]],
    categoria_otros: str,
    progress: Callable[[str, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Pide el recorrido a la DLL. Devuelve el resultado o nada si no se pudo usar.

    Los nombres que se saltan y el catálogo de categorías van como texto («uno por línea») para
    no tener que cruzar estructuras entre Python y C++: el criterio sigue estando en Python.
    """
    libreria, _motivo = _cargar()
    if libreria is None:
        return None

    def _avisar(_contexto, ruta: str, archivos: int) -> None:
        if progress is not None:
            progress(ruta, int(archivos))

    aviso = Aviso(_avisar)
    bandera = ctypes.c_int(0)
    parar = threading.Event()
    vigilante: threading.Thread | None = None
    if should_cancel is not None:

        def _vigilar() -> None:
            while not parar.wait(INTERVALO_CANCELACION_MS / 1000):
                if should_cancel():
                    bandera.value = 1
                    return

        vigilante = threading.Thread(target=_vigilar, name="disco-cancelacion", daemon=True)
        vigilante.start()

    texto_saltar = "\n".join(nombre.lower() for nombre in saltar)
    texto_categorias = "\n".join(
        "|".join((categoria, *extensiones)) for categoria, extensiones in categorias.items()
    )
    try:
        puntero = libreria.pctk_escanear_disco(
            str(root),
            int(min_large_file),
            int(top_children),
            int(top_files),
            texto_saltar,
            texto_categorias,
            categoria_otros,
            aviso,
            None,
            ctypes.byref(bandera),
        )
    except OSError as exc:  # pragma: no cover - la DLL falló dentro de C++
        logs.warning("El escáner nativo falló", exc, name="disco")
        return None
    finally:
        parar.set()
        if vigilante is not None:
            vigilante.join(timeout=1.0)
    if not puntero:
        return None
    try:
        texto = ctypes.wstring_at(puntero)
    finally:
        libreria.pctk_liberar(ctypes.c_void_p(puntero))
    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:  # pragma: no cover - respuesta inesperada
        logs.warning("El escáner nativo devolvió algo que no es JSON", exc, name="disco")
        return None
