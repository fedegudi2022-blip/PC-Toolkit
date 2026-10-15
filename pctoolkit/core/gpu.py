"""Datos de la tarjeta gráfica por las vías propias de Windows.

`psutil` no publica nada de la GPU, pero Windows sí lo tiene: la clase de rendimiento
`Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine` da el uso de cada motor
gráfico por proceso y `Win32_VideoController` da las tarjetas y su controlador. Las dos
consultas viajan en el **mismo** proceso de PowerShell, y eso es lo que abarata la
lectura: medida en un equipo real, una vuelta completa tarda ~2,4 s y de ellos ~2,2 s son
el arranque de PowerShell, así que separarlas costaría casi lo mismo cada una.

El uso se calcula como lo hace el Administrador de tareas: se suman los procesos que
trabajan en **el mismo motor** (mismo adaptador, mismo motor físico y mismo tipo: 3D,
copia, vídeo…) y se enseña el motor más cargado. Sumarlo todo daría un número que no
existe en ningún sitio, y quedarse con el primer motor enseñaría el que no trabaja.

Hay tres maneras de leer, y las tres salen de aquí (`read_gpu_info`), de la más barata a la
más cara:

* **PDH y el registro** (`read_gpu_info_pdh`, el camino normal): Windows da el uso por su API
  de contadores de rendimiento (`pdh.py`) y la tarjeta por el registro. **Sin ningún
  proceso**: ~1 s de ventana de medida (espera, no CPU) y nada de memoria;
* **proceso vivo** (`GpuReader`), cuando Windows no da el contador: un solo PowerShell que
  contesta una lectura por línea, abierto por el vigía mientras la tarjeta está a la vista;
  cada vuelta son unas décimas en vez de ~2,4 s (el intérprete ya está arrancado), a cambio
  de ~50 MB residentes;
* **un proceso por lectura**: la vuelta suelta (un informe, una prueba) cuando no hay
  proceso vivo ni contadores; ~2,2 s, casi todo arrancar el intérprete.

Con lo que cuesta, esto no va en el muestreo normal de 1,2 s: se consulta cada
`USAGE_INTERVAL_MS` y solo mientras la ventana está a la vista (lo decide
`pctoolkit.ui.gpu_monitor`, que además lo hace en segundo plano).
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import logs, pdh
from .tools import powershell_environment

IS_WINDOWS = os.name == "nt"

# --- Camino directo: PDH y el registro ---------------------------------------
# El uso se lee con los contadores de rendimiento de Windows (`pdh.py`) y la tarjeta y su
# controlador, del registro de clases de dispositivos: ni un proceso por el camino. Es lo
# que se usa siempre que Windows lo permita; PowerShell queda de reserva (por ejemplo, en
# un equipo donde la clase de rendimiento de la GPU no exista).
GPU_ENGINE_COUNTER = r"\GPU Engine(*)\Utilization Percentage"

# Los adaptadores de pantalla viven aquí: {4d36e968-…} es la clase «Tarjetas gráficas» y
# cada subcarpeta numerada es un adaptador, con su nombre en `DriverDesc` y la versión del
# controlador en `DriverVersion` (lo mismo que enseña el Administrador de dispositivos).
DISPLAY_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
# Windows deja este adaptador cuando no hay controlador de vídeo instalado. CIM no lo cuenta
# como tarjeta y aquí tampoco: no es una GPU, es el sustituto que pone el sistema. Si es el
# único que hay, sí se enseña: es la verdad del equipo.
BASIC_DISPLAY = "microsoft basic display"

# --- Cadencia -----------------------------------------------------------------
# 15 s es la medida razonable: la consulta tarda ~2,5 s, así que preguntar cada 5 s
# tendría un PowerShell vivo casi todo el rato, y el uso de la GPU no cambia tanto como
# para necesitar más resolución que la del propio vistazo.
#
# En modo eco —que es el de fábrica— se pregunta cada minuto: la tarjeta se sigue leyendo
# sola (que es lo que la hace útil), pero cuatro veces menos, que es lo que pide ese modo.
USAGE_INTERVAL_MS = 15_000
ECO_INTERVAL_MS = 60_000

# --- Guion de PowerShell ------------------------------------------------------
# `-Compress` deja el JSON en una línea; el motor se filtra en PowerShell para no traer
# 371 filas de ceros (los motores existen aunque nadie los use).
#
# La consulta va en dos trozos porque se usa de dos maneras: entera (un proceso por
# lectura) y con la conversión a JSON asignada a una variable (dentro del bucle del proceso
# vivo). Ojo con el `;`: `$salida = $tarjetas = @(...); ...` solo asigna la **primera**
# sentencia —la lista de tarjetas— y el JSON se iría por la salida por su cuenta.
_QUERY = (
    "$tarjetas = @(Get-CimInstance Win32_VideoController | ForEach-Object {"
    "[pscustomobject]@{nombre=[string]$_.Name;controlador=[string]$_.DriverVersion}});"
    "$motores = @(Get-CimInstance -ClassName "
    "Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | "
    "Where-Object { $_.UtilizationPercentage -gt 0 } | ForEach-Object {"
    "[pscustomobject]@{motor=[string]$_.Name;"
    "uso=[double]$_.UtilizationPercentage}});"
)

_JSON = (
    "ConvertTo-Json -InputObject ([pscustomobject]@{"
    "tarjetas=$tarjetas;motores=$motores}) -Compress -Depth 4"
)

# Cabecera común: la salida se lee como UTF-8 (sin esto PowerShell escribe en la página del
# sistema y los nombres con acentos llegan corruptos) y los errores no cortan el guion.
_HEADER = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='SilentlyContinue';"
)

GPU_SCRIPT = _HEADER + _QUERY + _JSON

# Marca que llevan delante las respuestas del proceso vivo. El guion podría soltar algo
# suelto por la salida (un aviso de un cmdlet, por ejemplo) y eso descolocaría la
# conversación: quien lee solo hace caso a las líneas marcadas y salta el resto.
RESPUESTA = "##"

# Proceso vivo: el mismo PowerShell contesta una lectura por línea hasta que se cierra su
# entrada estándar (ahí sale del bucle y termina solo, sin dejar nada de fondo). Cada
# petición se responde con una línea marcada y con el JSON ya comprimido.
READER_SCRIPT = (
    _HEADER
    + "while ($true) { $peticion = [Console]::In.ReadLine();"
    " if ($null -eq $peticion) { break };"
    " " + _QUERY + 
    " $salida = " + _JSON + ";"
    " [Console]::Out.WriteLine('" + RESPUESTA + "' + $salida); [Console]::Out.Flush() }"
)

# Cuánto se espera una respuesta antes de dar el proceso por perdido (y arrancar otro).
# Es holgado a propósito: una lectura normal tarda décimas de segundo y una lenta, segundos.
READ_TIMEOUT_S = 8.0
PEDIDO = "leer\n"

# Nombre de un motor: `pid_1240_luid_0x00000000_0x0000897A_phys_0_eng_0_engtype_3D`.
# Del identificador solo interesa el adaptador (`luid`), el motor físico (`phys`) y el
# tipo de motor: el `pid` y el `eng_N` cambian de una vuelta a otra para el mismo trabajo.
# Ojo con el `luid`: Windows lo escribe en **dos** palabras hexadecimales (alta y baja),
# y en Windows 11 la segunda cambia por adaptador, así que las dos hacen falta.
ENGINE_PATTERN = re.compile(
    r"luid_(?P<luid>0x[0-9a-f]+_0x[0-9a-f]+)_phys_(?P<phys>\d+)_eng_(?P<eng>\d+)_engtype_(?P<tipo>\w+)",
    re.IGNORECASE,
)

# Nombre legible del tipo de motor, que es lo que se enseña junto al porcentaje.
ENGINE_LABELS = {
    "3d": "3D",
    "compute": "cálculo",
    "copy": "copia",
    "video": "vídeo",
    "videodecode": "vídeo",
    "videoencode": "codificación",
    "videoprocessing": "vídeo",
}


@dataclass
class GpuInfo:
    """Lo que se sabe de la tarjeta gráfica en una vuelta."""

    name: str = ""
    driver: str = ""
    # `None` solo cuando la lectura falló; un 0 es un dato: la GPU no está trabajando.
    usage: float | None = None
    engine: str = ""
    cards: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """Nombre para enseñar: la tarjeta, y cuántas más hay si hay más de una."""
        if not self.cards:
            return self.name
        if len(self.cards) > 1:
            return f"{self.cards[0]} (+{len(self.cards) - 1})"
        return self.cards[0]

    def to_dict(self) -> dict:
        return {
            "nombre": self.name,
            "controlador": self.driver,
            "uso": self.usage,
            "motor": self.engine,
            "tarjetas": list(self.cards),
        }


def engine_totals(rows) -> dict[tuple[str, int, str], float]:
    """Suma el uso de cada motor: lo de todos los procesos que usan **ese** motor.

    La clave es el adaptador (luid), el motor físico y el tipo de motor. El tipo se
    normaliza a minúsculas para que `engtype_3d` y `engtype_3D` no cuenten como dos.
    """
    totales: dict[tuple[str, int, str], float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        nombre = str(row.get("motor") or "")
        coincidencia = ENGINE_PATTERN.search(nombre)
        if coincidencia is None:
            # Un nombre con otra forma (Windows lo cambia de versión en versión) no se
            # descarta: se cuenta por separado. Tirarlo sería devolver un 0 cuando la
            # tarjeta está trabajando, que es el peor error posible aquí.
            clave = (nombre.lower(), 0, "")
        else:
            clave = (
                coincidencia.group("luid").lower(),
                int(coincidencia.group("phys")),
                coincidencia.group("tipo").lower(),
            )
        try:
            uso = float(row.get("uso") or 0.0)
        except (TypeError, ValueError):
            continue
        totales[clave] = totales.get(clave, 0.0) + uso
    return totales


def usage_summary(rows) -> tuple[float, str]:
    """(porcentaje del motor más cargado, nombre del tipo de motor).

    Un 0 es un dato válido —no hay ningún motor trabajando— y se devuelve tal cual: la
    diferencia entre «0 %» y «no se pudo leer» la marca `GpuInfo.usage`.
    """
    totales = engine_totals(rows)
    if not totales:
        return 0.0, ""
    clave, uso = max(totales.items(), key=lambda item: item[1])
    tipo = clave[2]
    return min(100.0, max(0.0, round(uso))), ENGINE_LABELS.get(tipo, tipo)


def gpu_info_from_output(text: str) -> GpuInfo | None:
    """Convierte la salida del guion en una ficha; `None` si no hay nada que leer.

    Es una función pura (lo que llega es texto, no un proceso) para poder probarla con la
    salida de un equipo de verdad sin abrir PowerShell.
    """
    payload = (text or "").strip()
    if not payload.startswith("{") and not payload.startswith("["):
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {}
    if not isinstance(data, dict):
        return None
    tarjetas = data.get("tarjetas") or []
    if isinstance(tarjetas, dict):
        tarjetas = [tarjetas]
    nombres: list[str] = []
    controlador = ""
    for tarjeta in tarjetas:
        if not isinstance(tarjeta, dict):
            continue
        nombre = str(tarjeta.get("nombre") or "").strip()
        if nombre:
            nombres.append(nombre)
        if not controlador:
            controlador = str(tarjeta.get("controlador") or "").strip()
    uso, motor = usage_summary(data.get("motores") or [])
    return GpuInfo(
        name=nombres[0] if nombres else "",
        driver=controlador,
        usage=uso,
        engine=motor,
        cards=tuple(nombres),
    )


_warned = False


def _warn_once(detail) -> None:
    """Avisa una sola vez por sesión: esto corre cada pocos segundos mientras se mira."""
    global _warned
    if _warned:
        return
    _warned = True
    logs.warning("No se pudieron leer los datos de la tarjeta gráfica", detail)


class GpuReader:
    """Proceso de PowerShell que se queda vivo y contesta una lectura por línea.

    Arrancar PowerShell son ~2,2 s de los ~2,4 s que cuesta la primera lectura: casi todo
    el tiempo se va en el intérprete, no en preguntar. Con el proceso vivo, cada vuelta
    siguiente es una línea de ida y vuelta.

    El proceso **no vive por su cuenta**: lo abre quien va a leer seguido y lo cierra al
    terminar (`close`), porque un PowerShell parado ocupando memoria en segundo plano es
    exactamente lo que esta aplicación evita. Si el proceso se muere, `close` no hace
    falta para arrancar otro: la siguiente lectura lo hace sola.

    Las respuestas se leen en un hilo aparte y se dejan en una cola, para poder esperar
    con un tope de tiempo: esperar a un proceso colgado sin tope dejaría la tarjeta en
    «Leyendo…» para siempre.
    """

    def __init__(self, script: str = READER_SCRIPT, timeout: float = READ_TIMEOUT_S) -> None:
        self._script = script
        self._timeout = timeout
        self._proceso: subprocess.Popen | None = None
        self._cola: queue.Queue[str | None] = queue.Queue()
        # Reentrante a propósito: `read` cierra el proceso desde dentro (al agotarse el
        # tiempo o al recibir una respuesta rara) y `close` vuelve a tomar el mismo candado.
        # Con un candado normal eso sería un bloqueo consigo mismo.
        self._lock = threading.RLock()
        # Cuántas veces se ha arrancado el proceso y cuántas lecturas ha contestado: sirven
        # para las pruebas (una sola puesta en marcha para muchas lecturas).
        self.starts = 0
        self.reads = 0

    @property
    def alive(self) -> bool:
        """¿Hay un proceso vivo detrás?"""
        return self._proceso is not None and self._proceso.poll() is None

    @property
    def pid(self) -> int | None:
        """Identificador del proceso, para medir lo que ocupa (y para las pruebas)."""
        return self._proceso.pid if self._proceso is not None else None

    def read(self) -> GpuInfo | None:
        """Pide una lectura; `None` si el proceso no está (o no contesta)."""
        with self._lock:
            if not IS_WINDOWS:
                return None
            if not self.alive and not self._start():
                return None
            try:
                assert self._proceso is not None and self._proceso.stdin is not None
                self._proceso.stdin.write(PEDIDO)
                self._proceso.stdin.flush()
            except (OSError, ValueError) as exc:
                _warn_once(exc)
                self.close()
                return None
            info = self._leer_respuesta()
            if info is None:
                # O no contestó, o la respuesta no tenía la forma esperada. En los dos casos
                # se cierra: un proceso del que no te puedes fiar es peor que ninguno, y la
                # lectura siguiente arranca otro.
                self.close()
                return None
            self.reads += 1
            return info

    def _leer_respuesta(self) -> GpuInfo | None:
        """Espera la respuesta marcada; `None` si se agota el tiempo o el proceso se va."""
        limite = time.monotonic() + self._timeout
        while True:
            restante = limite - time.monotonic()
            if restante <= 0:
                # Se espera con tope a propósito: esperar sin él a un proceso colgado
                # dejaría la tarjeta en «Leyendo…» para siempre.
                _warn_once("el proceso de lectura de la tarjeta dejó de contestar")
                return None
            try:
                linea = self._cola.get(timeout=restante)
            except queue.Empty:
                _warn_once("el proceso de lectura de la tarjeta dejó de contestar")
                return None
            if linea is None:
                return None
            texto = linea.strip()
            if not texto.startswith(RESPUESTA):
                continue  # salida suelta del guion: no es una respuesta, se salta
            return gpu_info_from_output(texto[len(RESPUESTA) :])

    def close(self) -> None:
        """Cierra el proceso (el bucle sale solo al cerrarse su entrada estándar)."""
        with self._lock:
            self._kill()

    # ------------------------------------------------------------------
    def _start(self) -> bool:
        try:
            # El guion va en la línea de órdenes, **no** por la entrada estándar: con
            # `-Command -` PowerShell lee toda la entrada como el guion y no ejecuta nada
            # hasta que se cierra, así que no habría ida y vuelta posible. La entrada queda
            # libre para pedir lecturas.
            self._proceso = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    self._script,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=powershell_environment(),
            )
        except OSError as exc:
            _warn_once(exc)
            return False
        self.starts += 1
        self._cola = queue.Queue()
        hilo = threading.Thread(target=self._bombear, args=(self._proceso, self._cola), daemon=True)
        hilo.start()
        return True

    @staticmethod
    def _bombear(proceso: subprocess.Popen, cola: "queue.Queue[str | None]") -> None:
        """Pasa las líneas de salida a la cola; al cerrarse el flujo, avisa con `None`."""
        if proceso.stdout is not None:
            for linea in proceso.stdout:
                cola.put(linea)
        cola.put(None)

    def _kill(self) -> None:
        proceso, self._proceso = self._proceso, None
        if proceso is None:
            return
        try:
            if proceso.stdin is not None:
                proceso.stdin.close()
        except OSError:
            pass
        try:
            proceso.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proceso.kill()
            try:
                proceso.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


# --- Proceso compartido -------------------------------------------------------
# Quien sabe cuánto va a durar la lectura es el vigía (`pctoolkit.ui.gpu_monitor`): abre el
# lector al empezar a mirar la tarjeta y lo cierra al dejar de mirarla. Guardarlo aquí, y no
# en el vigía, deja que `read_gpu_info()` siga siendo la única puerta de entrada —lo que ya
# espían las pruebas y las comprobaciones de trabajo en segundo plano— sin cambiar su firma.
_reader: GpuReader | None = None
_reader_lock = threading.Lock()


def open_reader() -> GpuReader:
    """Abre (si no lo estaba) el lector compartido y lo devuelve."""
    global _reader
    with _reader_lock:
        if _reader is None:
            _reader = GpuReader()
        return _reader


def close_reader() -> None:
    """Cierra el lector compartido, si estaba abierto."""
    global _reader
    with _reader_lock:
        lector, _reader = _reader, None
    if lector is not None:
        lector.close()


def reader_open() -> bool:
    """¿Hay un lector abierto? (para las pruebas y para no abrir dos)."""
    with _reader_lock:
        return _reader is not None


def cards_from_registry() -> list[tuple[str, str]]:
    """Tarjetas y controlador del registro: `[(nombre, versión del controlador)]`."""
    if not IS_WINDOWS:
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - solo pasa fuera de Windows
        return []
    tarjetas: list[tuple[str, str]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS_KEY) as clase:
            indice = 0
            while True:
                try:
                    subclave = winreg.EnumKey(clase, indice)
                except OSError:
                    break
                indice += 1
                # Las subcarpetas de adaptador son números de cuatro cifras (0000, 0001…);
                # al lado hay otras como `Configuration` o `Properties` que no lo son.
                if len(subclave) != 4 or not subclave.isdigit():
                    continue
                try:
                    with winreg.OpenKey(clase, subclave) as adaptador:
                        nombre = str(winreg.QueryValueEx(adaptador, "DriverDesc")[0]).strip()
                        version = str(winreg.QueryValueEx(adaptador, "DriverVersion")[0]).strip()
                except OSError:
                    continue
                if nombre:
                    tarjetas.append((nombre, version))
    except OSError:
        return []
    reales = [tarjeta for tarjeta in tarjetas if BASIC_DISPLAY not in tarjeta[0].lower()]
    return reales or tarjetas


def engine_counters_available() -> bool:
    """¿Windows publica el contador de la GPU de este equipo?

    Abre y cierra la consulta sin esperar la ventana de medida (milisegundos): sirve para
    decidir el camino antes de leer, y para que las pruebas sepan si pueden comprobarlo.
    """
    consulta = pdh.CounterQuery(GPU_ENGINE_COUNTER, window_s=0)
    try:
        return consulta.start()
    finally:
        consulta.close()


def read_gpu_info_pdh() -> GpuInfo | None:
    """Lee tarjeta y uso sin arrancar ningún proceso: registro y contadores de Windows.

    Devuelve `None` cuando Windows no da el contador (sin clase de rendimiento de GPU, sin
    permisos…): entonces quien llama usa el camino de PowerShell.
    """
    filas = pdh.sample(GPU_ENGINE_COUNTER, pdh.WINDOW_S)
    if not filas:
        return None
    # El nombre de cada instancia trae el adaptador, el motor físico y el tipo
    # (`pid_…_luid_…_phys_0_eng_0_engtype_3D`), así que la suma por motor es la misma que
    # con CIM: se reutiliza tal cual.
    uso, motor = usage_summary([{"motor": nombre, "uso": valor} for nombre, valor in filas])
    tarjetas = cards_from_registry()
    nombres = tuple(nombre for nombre, _ in tarjetas)
    return GpuInfo(
        name=nombres[0] if nombres else "",
        driver=tarjetas[0][1] if tarjetas else "",
        usage=uso,
        engine=motor,
        cards=nombres,
    )


def read_gpu_info(timeout: int = 30) -> GpuInfo | None:
    """Lee la tarjeta y su uso (bloquea: se llama desde un hilo, no desde la ventana).

    Tres caminos, del más barato al más caro, y se usa el primero que funcione:

    1. **PDH y el registro** (`read_gpu_info_pdh`): sin ningún proceso. Cuesta la ventana de
       medida (~1 s de espera, sin CPU) y nada de memoria;
    2. **proceso vivo** (`open_reader`), si lo hay abierto: una vuelta de ~0,36 s, con el
       proceso (≈50 MB) ya en marcha. Es la reserva cuando Windows no da el contador;
    3. **un proceso de PowerShell por lectura**: ~2,2 s, casi todo arranque del intérprete.

    Devuelve `None` —y lo deja dicho en el registro, una sola vez— cuando Windows no
    contesta: sin contadores de rendimiento (máquinas virtuales sin GPU, por ejemplo) es
    normal que no haya nada que leer, y eso no es un error de la aplicación.
    """
    directo = read_gpu_info_pdh()
    if directo is not None:
        return directo
    with _reader_lock:
        lector = _reader
    if lector is not None:
        return lector.read()
    if not IS_WINDOWS:
        return None
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", GPU_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=powershell_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn_once(exc)
        return None
    info = gpu_info_from_output(result.stdout or "")
    if info is None:
        _warn_once((result.stderr or "").strip()[:200] or "la salida no traía datos")
    return info
