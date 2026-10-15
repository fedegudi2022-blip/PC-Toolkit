"""Contadores de rendimiento de Windows por PDH, **sin arrancar ningún proceso**.

Windows publica los mismos datos que da CIM (y que enseña el Administrador de tareas) en su
API de contadores de rendimiento, PDH. Leerlos desde aquí cuesta milisegundos y ninguna
memoria, mientras que pedir lo mismo por PowerShell son ~2,2 s (arrancar el intérprete) y,
si se deja vivo, ~50 MB de RAM.

Tres detalles hacen falta para que esto funcione tal cual:

* **el nombre va en inglés**: en un Windows en castellano los contadores están traducidos
  («\\Procesador»), así que se pide con `PdhAddEnglishCounterW`, que traduce el nombre
  inglés al del sistema;
* **el array se pide dos veces**: la primera llamada con el búfer vacío dice cuánto ocupa y
  cuántas instancias hay; la segunda trae los datos;
* **un porcentaje necesita dos recogidas**: la primera es la referencia y la segunda, el
  dato. La ventana entre las dos es lo que se está midiendo, así que `sample` espera
  `WINDOW_S` (un segundo, como el Administrador de tareas) y no menos: medido en un equipo
  real, con 5 ms el mismo contador daba 21,7 % donde con un segundo daba 3,6 %.

Todo lo que puede fallar aquí (un contador que no existe, un Windows sin la clase de
rendimiento, un permiso) se traduce en una lista vacía: quien lee decide entonces si usa su
camino de reserva. Este módulo no imprime ni avisa.
"""

from __future__ import annotations

import ctypes
import os
import time
from functools import lru_cache

IS_WINDOWS = os.name == "nt"

# --- Constantes de la API -----------------------------------------------------
PDH_FMT_DOUBLE = 0x00000200
# Sin `NOCAP100`, PDH recorta cada instancia a 100 antes de sumarla: el mismo motor puede
# pasar del 100 % al sumar sus procesos, y la suma la hace quien lee, no PDH.
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x00000000

# Ventana de medida por defecto: un segundo, lo mismo que muestra el Administrador de
# tareas. Es un `sleep` del hilo que lee (no gasta CPU) y la lectura va en segundo plano.
WINDOW_S = 1.0


class _FmtCounterValue(ctypes.Structure):
    """`PDH_FMT_COUNTERVALUE`: estado del dato y su valor (unión de tipos)."""

    class _Value(ctypes.Union):
        _fields_ = [
            ("longValue", ctypes.c_long),
            ("doubleValue", ctypes.c_double),
            ("largeValue", ctypes.c_longlong),
        ]

    _fields_ = [("CStatus", ctypes.c_ulong), ("value", _Value)]


class _FmtCounterValueItem(ctypes.Structure):
    """`PDH_FMT_COUNTERVALUE_ITEM_W`: una instancia del contador y su valor."""

    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtCounterValue)]


@lru_cache(maxsize=1)
def _pdh():
    """La biblioteca de PDH, con sus firmas fijadas (o `None` si no hay Windows)."""
    if not IS_WINDOWS:
        return None
    try:
        biblioteca = ctypes.WinDLL("pdh.dll")
    except OSError:
        return None
    # Sin `restype` ctypes devuelve un entero con signo y `PDH_MORE_DATA` (0x8000…) llega
    # como negativo: la comparación falla y parece que el contador no existe.
    biblioteca.PdhOpenQueryW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    biblioteca.PdhOpenQueryW.restype = ctypes.c_ulong
    biblioteca.PdhAddEnglishCounterW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    biblioteca.PdhAddEnglishCounterW.restype = ctypes.c_ulong
    biblioteca.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
    biblioteca.PdhCollectQueryData.restype = ctypes.c_ulong
    biblioteca.PdhGetFormattedCounterArrayW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    ]
    biblioteca.PdhGetFormattedCounterArrayW.restype = ctypes.c_ulong
    biblioteca.PdhCloseQuery.argtypes = [ctypes.c_void_p]
    biblioteca.PdhCloseQuery.restype = ctypes.c_ulong
    return biblioteca


class CounterQuery:
    """Un contador de PDH y sus instancias (una fila por instancia).

    Se abre al pedir el primer dato y se cierra al terminar: abrir son milisegundos, así que
    no compensa guardarlo entre lecturas —y guardarlo es justo lo que este módulo evita.
    """

    def __init__(self, path: str, window_s: float = WINDOW_S) -> None:
        self.path = path
        self.window_s = max(0.0, float(window_s))
        self._consulta: ctypes.c_void_p | None = None
        self._contador: ctypes.c_void_p | None = None

    @property
    def started(self) -> bool:
        """¿Está abierta la consulta?"""
        return self._consulta is not None

    def start(self) -> bool:
        """Abre la consulta y añade el contador. `False` si Windows no lo tiene."""
        if self.started:
            return True
        biblioteca = _pdh()
        if biblioteca is None:
            return False
        consulta = ctypes.c_void_p()
        if biblioteca.PdhOpenQueryW(None, 0, ctypes.byref(consulta)) != 0:
            return False
        contador = ctypes.c_void_p()
        estado = biblioteca.PdhAddEnglishCounterW(
            consulta, self.path, 0, ctypes.byref(contador)
        )
        if estado != 0:
            biblioteca.PdhCloseQuery(consulta)
            return False
        self._consulta, self._contador = consulta, contador
        return True

    def close(self) -> None:
        """Suelta la consulta (no hace nada si no estaba abierta)."""
        biblioteca = _pdh()
        if self._consulta is not None and biblioteca is not None:
            biblioteca.PdhCloseQuery(self._consulta)
        self._consulta, self._contador = None, None

    def collect(self) -> bool:
        """Toma una muestra de referencia; el dato sale de la segunda."""
        biblioteca = _pdh()
        if not self.start() or biblioteca is None or self._consulta is None:
            return False
        return biblioteca.PdhCollectQueryData(self._consulta) == 0

    def sample(self) -> list[tuple[str, float]]:
        """Lee el contador: referencia, espera la ventana, segunda muestra y lectura.

        Devuelve `[(instancia, valor)]` con las instancias que tienen dato válido, y una
        lista vacía si algo falla (contador inexistente, sin instancias, sin datos).
        """
        if not self.start():
            return []
        try:
            if not self.collect():
                return []
            if self.window_s > 0:
                time.sleep(self.window_s)
            if not self.collect():
                return []
            return self.read()
        finally:
            self.close()

    def read(self) -> list[tuple[str, float]]:
        """Lee lo último recogido (el búfer se pide dos veces: tamaño y contenido)."""
        biblioteca = _pdh()
        if biblioteca is None or self._contador is None:
            return []
        tamano = ctypes.c_ulong(0)
        cuenta = ctypes.c_ulong(0)
        formato = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        estado = biblioteca.PdhGetFormattedCounterArrayW(
            self._contador, formato, ctypes.byref(tamano), ctypes.byref(cuenta), None
        )
        if estado != PDH_MORE_DATA or cuenta.value == 0 or tamano.value == 0:
            return []
        bufer = ctypes.create_string_buffer(tamano.value)
        estado = biblioteca.PdhGetFormattedCounterArrayW(
            self._contador, formato, ctypes.byref(tamano), ctypes.byref(cuenta), bufer
        )
        if estado != 0:
            return []
        elementos = ctypes.cast(bufer, ctypes.POINTER(_FmtCounterValueItem))
        filas: list[tuple[str, float]] = []
        for indice in range(cuenta.value):
            elemento = elementos[indice]
            if elemento.FmtValue.CStatus != PDH_CSTATUS_VALID_DATA:
                continue
            filas.append((elemento.szName or "", float(elemento.FmtValue.value.doubleValue)))
        return filas


def sample(path: str, window_s: float = WINDOW_S) -> list[tuple[str, float]]:
    """Atajo para una lectura suelta de un contador con instancias."""
    return CounterQuery(path, window_s).sample()
