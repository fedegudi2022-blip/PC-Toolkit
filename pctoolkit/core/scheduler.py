"""Mantenimiento programado usando el Programador de tareas de Windows.

PC Toolkit registra una tarea propia (en la carpeta `\\PCToolkit\\`) que ejecuta
el modo desatendido de la aplicación (`--mantenimiento`). La tarea se crea con
`schtasks` a partir de un XML que se genera aquí, con dos decisiones pensadas:

* se ejecuta **como el usuario actual** (`InteractiveToken`) y **sin
  privilegios elevados** (`LeastPrivilege`), de modo que no hace falta ser
  administrador ni guardar contraseña alguna;
* `StartWhenAvailable` permite recuperar la ejecución perdida si el equipo
  estaba apagado a la hora prevista, y no se detiene por estar con batería.

Toda la generación de XML y de líneas de comandos es pura (sin tocar el
sistema), así que se puede probar sin crear tareas reales.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

from .tools import MIN_TEMP_AGE_HOURS, powershell_environment

IS_WINDOWS = __import__("os").name == "nt"

TASK_FOLDER = "PCToolkit"
TASK_NAME = "Mantenimiento PC Toolkit"
TASK_PATH = f"\\{TASK_FOLDER}\\{TASK_NAME}"
# La limpieza programada desde la ventana de «Liberar espacio» es una tarea **aparte**: hace
# solo la limpieza (sin memoria ni informe) y se puede tener una limpieza diaria junto a un
# mantenimiento semanal, o quitar una sin tocar la otra. Por eso lleva su propio nombre.
CLEANUP_TASK_NAME = "Limpieza PC Toolkit"
CLEANUP_TASK_PATH = f"\\{TASK_FOLDER}\\{CLEANUP_TASK_NAME}"

FREQ_DAILY = "Diario"
FREQ_WEEKLY = "Semanal"
FREQ_LOGON = "Al iniciar sesión"
FREQUENCIES = (FREQ_DAILY, FREQ_WEEKLY, FREQ_LOGON)

WEEKDAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
WEEKDAY_CODES = {
    "Lunes": "MON",
    "Martes": "TUE",
    "Miércoles": "WED",
    "Jueves": "THU",
    "Viernes": "FRI",
    "Sábado": "SAT",
    "Domingo": "SUN",
}
WEEKDAY_XML = {
    "Lunes": "Monday",
    "Martes": "Tuesday",
    "Miércoles": "Wednesday",
    "Jueves": "Thursday",
    "Viernes": "Friday",
    "Sábado": "Saturday",
    "Domingo": "Sunday",
}

TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"

# Códigos de resultado del Programador de tareas que conviene traducir.
RESULT_TEXTS = {
    0: "Correcta",
    1: "Código de salida 1",
    267009: "En ejecución",
    267011: "Todavía no se ha ejecutado",
    267014: "Terminada por el usuario",
    2147943785: "Acceso denegado",
    2147942402: "Archivo no encontrado",
    2147943712: "Cancelada por el usuario",
}


def describe_result(code: int | None) -> str:
    """Traduce el código de la última ejecución a un texto legible."""
    if code is None:
        return "Sin datos"
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "Sin datos"
    text = RESULT_TEXTS.get(value)
    if text:
        return text
    if value >= 0x80070000 and value <= 0x8007FFFF:  # error de Windows
        return f"Error 0x{value:08X}"
    return f"Código {value} (0x{value:08X})" if value > 0xFFFF else f"Código {value}"


def weekday_plural(weekday: str) -> str:
    """Día de la semana en minúscula y plural, para frases («los domingos a las 20:30»).

    Lunes, martes, miércoles y jueves se escriben igual en plural; sábado y domingo no.
    Escribirlo a mano dio «los domingo» durante un tiempo: es el tipo de detalle que solo se
    nota cuando ya está publicado, así que se resuelve en un sitio y lo usan las dos
    programaciones (el mantenimiento y la limpieza).
    """
    dia = str(weekday).lower()
    return f"{dia}s" if dia in ("sábado", "domingo") else dia


def normalize_time(value: str) -> str:
    """Devuelve una hora válida en formato HH:MM."""
    try:
        hours, minutes = str(value).strip().replace(".", ":").split(":")[:2]
        hour = max(0, min(23, int(hours)))
        minute = max(0, min(59, int(minutes)))
    except (ValueError, IndexError):
        return "12:00"
    return f"{hour:02d}:{minute:02d}"


@dataclass
class MaintenanceSchedule:
    """Qué se ejecuta y cuándo."""

    enabled: bool = False
    frequency: str = FREQ_DAILY
    time: str = "12:00"
    weekday: str = "Lunes"
    clean_temp: bool = True
    temp_min_age_hours: float = 24.0
    release_memory: bool = False
    export_report: bool = True
    auto_log_task: bool = True
    # Qué categorías limpia (claves del catálogo de `pctoolkit.core.cleaner`). Por defecto
    # solo los temporales del usuario: es lo que limpiaba la aplicación antes de que
    # existiera el catálogo, y una tarea programada que empiece a vaciar la papelera sola
    # porque la aplicación se actualizó sería justo lo que no se quiere.
    clean_targets: tuple[str, ...] = ()

    def normalized(self) -> "MaintenanceSchedule":
        """Copia con los valores saneados."""
        from .cleaner import DEFAULT_TARGETS, normalize_keys

        clone = replace(self)
        clone.time = normalize_time(self.time)
        clone.frequency = self.frequency if self.frequency in FREQUENCIES else FREQ_DAILY
        clone.weekday = self.weekday if self.weekday in WEEKDAYS else "Lunes"
        # El suelo lo pone `tools`: por debajo de una hora no se borra nada, así que
        # aquí se refleja para que la interfaz no permita pedir algo que no se hará.
        clone.temp_min_age_hours = max(MIN_TEMP_AGE_HOURS, min(720.0, float(self.temp_min_age_hours)))
        # Una lista vacía es «no se ha elegido nada» (programación antigua o recién creada):
        # se queda con lo de siempre, los temporales, en vez de no limpiar nada.
        claves = tuple(str(clave) for clave in (self.clean_targets or ()))
        clone.clean_targets = normalize_keys(claves) or DEFAULT_TARGETS
        return clone

    def to_dict(self) -> dict[str, Any]:
        return {
            "activo": bool(self.enabled),
            "frecuencia": self.frequency,
            "hora": self.time,
            "dia": self.weekday,
            "limpiar": bool(self.clean_temp),
            "antiguedad_horas": float(self.temp_min_age_hours),
            "categorias": list(self.clean_targets),
            "memoria": bool(self.release_memory),
            "informe": bool(self.export_report),
            "registrar": bool(self.auto_log_task),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MaintenanceSchedule":
        """Reconstruye la programación saneando cada campo por separado."""
        from .config import as_flag, as_number

        data = data if isinstance(data, dict) else {}
        defaults = cls()
        categorias = data.get("categorias")
        return cls(
            enabled=as_flag(data.get("activo"), defaults.enabled),
            frequency=str(data.get("frecuencia") or defaults.frequency),
            time=str(data.get("hora") or defaults.time),
            weekday=str(data.get("dia") or defaults.weekday),
            clean_temp=as_flag(data.get("limpiar"), defaults.clean_temp),
            temp_min_age_hours=as_number(
                data.get("antiguedad_horas"), defaults.temp_min_age_hours, MIN_TEMP_AGE_HOURS, 720.0
            ),
            clean_targets=tuple(str(clave) for clave in categorias)
            if isinstance(categorias, list)
            else (),
            release_memory=as_flag(data.get("memoria"), defaults.release_memory),
            export_report=as_flag(data.get("informe"), defaults.export_report),
            auto_log_task=as_flag(data.get("registrar"), defaults.auto_log_task),
        ).normalized()

    def describe(self) -> str:
        """Frase corta con cuándo y qué se ejecuta (esté activa o no)."""
        when = (
            f"todos los días a las {self.time}"
            if self.frequency == FREQ_DAILY
            else (
                f"los {weekday_plural(self.weekday)} a las {self.time}"
                if self.frequency == FREQ_WEEKLY
                else "al iniciar sesión"
            )
        )
        tasks = self.task_list()
        return f"Se ejecuta {when}: " + ", ".join(tasks) + "."


    def task_list(self) -> list[str]:
        items: list[str] = []
        if self.clean_temp:
            from .cleaner import DEFAULT_TARGETS, keys_label

            # Sin categorías (una programación recién construida) se limpia lo de siempre:
            # los temporales del usuario, que es lo que hacía antes del catálogo.
            items.append(f"limpiar ({keys_label(self.clean_targets or DEFAULT_TARGETS)})")
        if self.release_memory:
            items.append("liberar memoria")
        if self.export_report:
            items.append("generar informe")
        return items or ["solo registrar la ejecución"]


@dataclass
class CleanupSchedule:
    """Cuándo se limpia: solo limpieza, en su propia tarea de Windows.

    El mantenimiento automático hace varias cosas a la vez (limpiar, liberar memoria,
    generar un informe) y no siempre es lo que se quiere: quien solo quiera que las
    categorías que marca en la ventana de limpieza se borren todos los días a las 21:00 no
    tiene por qué activar el resto. Esta programación registra una tarea aparte
    (`\\PCToolkit\\Limpieza PC Toolkit`) que ejecuta `--limpieza`, y las dos conviven: el
    mantenimiento no borra por su cuenta más de lo que tenga configurado.

    Sin categorías no se programa nada: una tarea de limpieza sin objetivos no tiene sentido,
    así que la ventana no deja crearla vacía.
    """

    enabled: bool = False
    frequency: str = FREQ_DAILY
    time: str = "21:00"
    weekday: str = "Domingo"
    targets: tuple[str, ...] = ()
    min_age_hours: float = 24.0

    def normalized(self) -> "CleanupSchedule":
        """Copia con los valores saneados (mismas reglas que el mantenimiento)."""
        from .cleaner import normalize_keys

        clone = replace(self)
        clone.time = normalize_time(self.time)
        clone.frequency = self.frequency if self.frequency in FREQUENCIES else FREQ_DAILY
        clone.weekday = self.weekday if self.weekday in WEEKDAYS else "Lunes"
        clone.min_age_hours = max(MIN_TEMP_AGE_HOURS, min(720.0, float(self.min_age_hours)))
        # Aquí una lista vacía es una lista vacía (a diferencia del mantenimiento, que cae en
        # los temporales de siempre): sin categorías no hay nada que programar.
        clone.targets = normalize_keys(tuple(str(clave) for clave in (self.targets or ())))
        return clone

    def to_dict(self) -> dict[str, Any]:
        return {
            "activo": bool(self.enabled),
            "frecuencia": self.frequency,
            "hora": self.time,
            "dia": self.weekday,
            "categorias": list(self.targets),
            "antiguedad_horas": float(self.min_age_hours),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CleanupSchedule":
        from .config import as_flag, as_number

        data = data if isinstance(data, dict) else {}
        defaults = cls()
        categorias = data.get("categorias")
        return cls(
            enabled=as_flag(data.get("activo"), defaults.enabled),
            frequency=str(data.get("frecuencia") or defaults.frequency),
            time=str(data.get("hora") or defaults.time),
            weekday=str(data.get("dia") or defaults.weekday),
            targets=tuple(str(clave) for clave in categorias)
            if isinstance(categorias, list)
            else (),
            min_age_hours=as_number(
                data.get("antiguedad_horas"), defaults.min_age_hours, MIN_TEMP_AGE_HOURS, 720.0
            ),
        ).normalized()

    def describe(self) -> str:
        """Frase corta con cuándo se limpia y qué (esté activa o no)."""
        when = (
            f"todos los días a las {self.time}"
            if self.frequency == FREQ_DAILY
            else (
                f"los {weekday_plural(self.weekday)} a las {self.time}"
                if self.frequency == FREQ_WEEKLY
                else "al iniciar sesión"
            )
        )
        return f"Se ejecuta {when}: " + ", ".join(self.task_list()) + "."

    def task_list(self) -> list[str]:
        from .cleaner import keys_label

        return [f"limpiar ({keys_label(self.targets)})"]


# --- Línea de comandos y XML (funciones puras) ----------------------------


def application_root() -> Path:
    """Carpeta del proyecto (o del ejecutable congelado)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def maintenance_arguments(schedule: MaintenanceSchedule) -> list[str]:
    """Argumentos para el modo desatendido."""
    arguments = ["--mantenimiento"]
    if not schedule.clean_temp:
        arguments.append("--sin-limpieza")
    if not schedule.export_report:
        arguments.append("--sin-informe")
    if schedule.release_memory:
        arguments.append("--liberar-memoria")
    if not schedule.auto_log_task:
        arguments.append("--sin-registro")
    arguments.append("--antiguedad")
    arguments.append(f"{schedule.temp_min_age_hours:g}")
    return arguments


def cleanup_arguments(schedule: CleanupSchedule) -> list[str]:
    """Argumentos del modo «solo limpieza» (sin los demás pasos del mantenimiento)."""
    arguments = ["--limpieza"]
    claves = schedule.normalized().targets
    if claves:
        arguments.append("--categorias")
        arguments.append(",".join(claves))
    arguments.append("--antiguedad")
    arguments.append(f"{schedule.min_age_hours:g}")
    return arguments


def _launcher(arguments: list[str]) -> tuple[str, str]:
    """(programa, argumentos) que ejecutará el Programador de tareas."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), " ".join(arguments)
    script = application_root() / "app.py"
    # El script se entrecomilla siempre: es un argumento del intérprete y la ruta puede
    # llevar espacios (por ejemplo en «C:\Users\José Luis\…»), donde sin comillas el
    # Programador de tareas cortaría la línea y el modo desatendido no arrancaría.
    return str(Path(sys.executable).resolve()), " ".join([f'"{script}"', *arguments])


def launcher_command(schedule: MaintenanceSchedule) -> tuple[str, str]:
    return _launcher(maintenance_arguments(schedule))


def cleanup_launcher_command(schedule: CleanupSchedule) -> tuple[str, str]:
    return _launcher(cleanup_arguments(schedule))


def _command_line(program: str, arguments: str) -> str:
    return f'"{program}" {arguments}'.strip()


def command_line(schedule: MaintenanceSchedule) -> str:
    """Línea completa, informativa, para mostrar en la interfaz."""
    program, arguments = launcher_command(schedule)
    return _command_line(program, arguments)


def cleanup_command_line(schedule: CleanupSchedule) -> str:
    """La misma línea, para la tarea de limpieza programada."""
    program, arguments = cleanup_launcher_command(schedule)
    return _command_line(program, arguments)


def _boundary(time_value: str, moment: float | None = None) -> str:
    """Fecha y hora de la primera ejecución, en formato ISO local."""
    stamp = time.localtime(time.time() if moment is None else moment)
    hour, minute = normalize_time(time_value).split(":")
    return time.strftime("%Y-%m-%dT", stamp) + f"{hour}:{minute}:00"


def _start_boundary(schedule: MaintenanceSchedule, moment: float | None = None) -> str:
    """Primera ejecución de la tarea de mantenimiento."""
    return _boundary(schedule.time, moment)


def _task_xml_body(
    *,
    description: str,
    uri: str,
    program: str,
    arguments: str,
    frequency: str,
    time_value: str,
    weekday: str,
    moment: float | None = None,
) -> str:
    """XML de una tarea para `schtasks /Create /XML` (lo comparten las dos tareas).

    Los ajustes son los mismos en ambas —usuario actual sin privilegios elevados, recuperar la
    ejecución perdida, no parar por batería— porque lo que cambia entre el mantenimiento y la
    limpieza programada es **qué se ejecuta**, no cómo se ejecuta.
    """
    if frequency == FREQ_WEEKLY:
        trigger = (
            "<CalendarTrigger><StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled>"
            "<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><{day}/></DaysOfWeek>"
            "</ScheduleByWeek></CalendarTrigger>"
        ).format(boundary=_boundary(time_value, moment), day=WEEKDAY_XML[weekday])
    elif frequency == FREQ_LOGON:
        trigger = (
            "<LogonTrigger><Enabled>true</Enabled><Delay>PT2M</Delay></LogonTrigger>"
        )
    else:
        trigger = (
            "<CalendarTrigger><StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled>"
            "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
        ).format(boundary=_boundary(time_value, moment))

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="{TASK_NAMESPACE}">
  <RegistrationInfo>
    <Description>{_xml_text(description)}</Description>
    <URI>{_xml_text(uri)}</URI>
  </RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml_text(program)}</Command>
      <Arguments>{_xml_text(arguments)}</Arguments>
      <WorkingDirectory>{_xml_text(str(application_root()))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def task_xml(schedule: MaintenanceSchedule, moment: float | None = None) -> str:
    """XML de la tarea de mantenimiento."""
    schedule = schedule.normalized()
    program, arguments = launcher_command(schedule)
    return _task_xml_body(
        description=f"PC Toolkit · mantenimiento automático ({schedule.describe()})",
        uri=TASK_PATH,
        program=program,
        arguments=arguments,
        frequency=schedule.frequency,
        time_value=schedule.time,
        weekday=schedule.weekday,
        moment=moment,
    )


def cleanup_task_xml(schedule: CleanupSchedule, moment: float | None = None) -> str:
    """XML de la tarea de limpieza programada (sin el resto del mantenimiento)."""
    schedule = schedule.normalized()
    program, arguments = cleanup_launcher_command(schedule)
    return _task_xml_body(
        description=f"PC Toolkit · limpieza programada ({schedule.describe()})",
        uri=CLEANUP_TASK_PATH,
        program=program,
        arguments=arguments,
        frequency=schedule.frequency,
        time_value=schedule.time,
        weekday=schedule.weekday,
        moment=moment,
    )


def _xml_text(value: str) -> str:
    """Escapa texto para el XML de la tarea."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Todas las operaciones llevan la ruta de la tarea como último argumento opcional: por
# defecto la del mantenimiento (que es la que existía antes), y la limpieza programada pasa
# la suya. Así una función sirve para las dos y no hay dos copias del mismo comando.
def schtasks_create_arguments(xml_path: str | Path, task_path: str = TASK_PATH) -> list[str]:
    return ["/Create", "/TN", task_path, "/XML", str(xml_path), "/F"]


def schtasks_delete_arguments(task_path: str = TASK_PATH) -> list[str]:
    return ["/Delete", "/TN", task_path, "/F"]


def schtasks_change_arguments(enabled: bool, task_path: str = TASK_PATH) -> list[str]:
    return ["/Change", "/TN", task_path, "/ENABLE" if enabled else "/DISABLE"]


def schtasks_run_arguments(task_path: str = TASK_PATH) -> list[str]:
    return ["/Run", "/TN", task_path]


def schtasks_query_arguments(task_path: str = TASK_PATH) -> list[str]:
    return ["/Query", "/TN", task_path, "/FO", "LIST", "/V"]


# --- Acceso real al sistema ----------------------------------------------


@dataclass
class TaskOperation:
    """Resultado de una operación sobre el Programador de tareas."""

    ok: bool
    message: str
    command: str = ""
    output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "mensaje": self.message, "comando": self.command, "salida": self.output}


@dataclass
class TaskStatus:
    """Estado de la tarea registrada."""

    exists: bool = False
    enabled: bool = False
    state: str = ""
    next_run: str = ""
    last_run: str = ""
    last_result: int | None = None
    missed_runs: int = 0
    command: str = ""
    message: str = ""

    @property
    def last_result_text(self) -> str:
        return describe_result(self.last_result)

    @property
    def next_run_display(self) -> str:
        return self.next_run or "Sin programar"

    @property
    def last_run_display(self) -> str:
        return self.last_run or "Nunca"


def _hidden_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_schtasks(arguments: Sequence[str], timeout: int = 40) -> subprocess.CompletedProcess:
    """Ejecuta `schtasks` sin abrir ninguna ventana.

    La consola de Windows habla en su página de códigos OEM (la 850 en un Windows en
    español), no en cp1252: leerla con la página equivocada convertía las rutas con
    acentos en signos raros. `encoding="oem"` usa la página real del sistema y
    `errors="replace"` deja pasar cualquier byte suelto sin romper la lectura.
    """
    return subprocess.run(
        ["schtasks", *arguments],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        timeout=timeout,
        creationflags=_hidden_flags(),
        shell=False,
    )


def _clean(output: str) -> str:
    return " ".join((output or "").replace("\r", " ").replace("\n", " ").split())


def _create_task(
    xml_text: str,
    *,
    xml_name: str,
    task_path: str,
    description: str,
) -> TaskOperation:
    """Registra (o actualiza) una tarea a partir de su XML."""
    if not IS_WINDOWS:
        return TaskOperation(False, "El Programador de tareas solo existe en Windows.")
    xml_path = _write_task_xml(xml_text, xml_name)
    arguments = schtasks_create_arguments(xml_path, task_path)
    try:
        result = run_schtasks(arguments)
    except (OSError, subprocess.SubprocessError) as exc:
        return TaskOperation(False, f"No se pudo ejecutar schtasks: {exc}", "schtasks " + " ".join(arguments))
    command = "schtasks " + " ".join(arguments)
    if result.returncode == 0:
        return TaskOperation(True, f"Tarea registrada: {description}", command, _clean(result.stdout))
    return TaskOperation(
        False,
        f"Windows rechazó la tarea (código {result.returncode}). {_clean(result.stderr) or _clean(result.stdout)}",
        command,
        _clean(result.stderr),
    )


def create_task(schedule: MaintenanceSchedule, *, moment: float | None = None) -> TaskOperation:
    """Crea (o actualiza) la tarea de mantenimiento."""
    schedule = schedule.normalized()
    schedule.enabled = True  # a partir de aquí la tarea existe de verdad
    return _create_task(
        task_xml(schedule, moment),
        xml_name="tarea_mantenimiento.xml",
        task_path=TASK_PATH,
        description=schedule.describe(),
    )


def create_cleanup_task(schedule: CleanupSchedule, *, moment: float | None = None) -> TaskOperation:
    """Crea (o actualiza) la tarea de limpieza programada, sin tocar la del mantenimiento."""
    schedule = schedule.normalized()
    if not schedule.targets:
        return TaskOperation(
            False,
            "No hay ninguna categoría que limpiar: marca al menos una antes de programar.",
        )
    schedule.enabled = True
    return _create_task(
        cleanup_task_xml(schedule, moment),
        xml_name="tarea_limpieza.xml",
        task_path=CLEANUP_TASK_PATH,
        description=schedule.describe(),
    )


def _write_task_xml(xml_text: str, name: str = "tarea_mantenimiento.xml") -> Path:
    """Guarda el XML de la tarea (UTF-16, como espera schtasks) en datos del usuario."""
    from .config import data_dir

    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_text(xml_text, encoding="utf-16")
    return target


def delete_task(task_path: str = TASK_PATH) -> TaskOperation:
    if not IS_WINDOWS:
        return TaskOperation(False, "El Programador de tareas solo existe en Windows.")
    arguments = schtasks_delete_arguments(task_path)
    try:
        result = run_schtasks(arguments)
    except (OSError, subprocess.SubprocessError) as exc:
        return TaskOperation(False, f"No se pudo ejecutar schtasks: {exc}")
    command = "schtasks " + " ".join(arguments)
    if result.returncode == 0:
        return TaskOperation(True, "Tarea eliminada.", command, _clean(result.stdout))
    return TaskOperation(False, f"No se pudo eliminar la tarea. {_clean(result.stderr)}", command)


def delete_cleanup_task() -> TaskOperation:
    """Quita la tarea de limpieza programada (el mantenimiento sigue donde estaba)."""
    return delete_task(CLEANUP_TASK_PATH)


def set_task_enabled(enabled: bool, task_path: str = TASK_PATH) -> TaskOperation:
    if not IS_WINDOWS:
        return TaskOperation(False, "El Programador de tareas solo existe en Windows.")
    arguments = schtasks_change_arguments(enabled, task_path)
    try:
        result = run_schtasks(arguments)
    except (OSError, subprocess.SubprocessError) as exc:
        return TaskOperation(False, f"No se pudo ejecutar schtasks: {exc}")
    command = "schtasks " + " ".join(arguments)
    if result.returncode == 0:
        return TaskOperation(True, "Tarea activada." if enabled else "Tarea desactivada.", command)
    return TaskOperation(False, f"No se pudo cambiar el estado. {_clean(result.stderr)}", command)


def run_task_now(task_path: str = TASK_PATH) -> TaskOperation:
    if not IS_WINDOWS:
        return TaskOperation(False, "El Programador de tareas solo existe en Windows.")
    arguments = schtasks_run_arguments(task_path)
    try:
        result = run_schtasks(arguments)
    except (OSError, subprocess.SubprocessError) as exc:
        return TaskOperation(False, f"No se pudo ejecutar schtasks: {exc}")
    command = "schtasks " + " ".join(arguments)
    if result.returncode == 0:
        # La misma orden sirve para las dos tareas (la del mantenimiento y la de solo limpieza),
        # así que el mensaje dice cuál se lanzó en vez de hablar siempre del mantenimiento.
        lanzada = "Limpieza programada lanzada." if task_path == CLEANUP_TASK_PATH else "Mantenimiento lanzado."
        return TaskOperation(True, lanzada, command)
    return TaskOperation(False, f"No se pudo lanzar la tarea. {_clean(result.stderr)}", command)


STATUS_SCRIPT = (
    # La salida se lee como UTF-8: sin esto, PowerShell escribe en la página del sistema
    # y las rutas con acentos (por ejemplo «C:\Users\José Luis\…») llegan corruptas.
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='Stop';"
    "$t = Get-ScheduledTask -TaskPath '\\{folder}\\' -TaskName '{name}' -ErrorAction Stop;"
    "$i = $t | Get-ScheduledTaskInfo;"
    "[pscustomobject]@{{"
    "existe=$true;"
    "estado=[string]$t.State;"
    "habilitada=[bool]$t.Settings.Enabled;"
    "siguiente=if($i.NextRunTime){{$i.NextRunTime.ToString('yyyy-MM-dd HH:mm')}}else{{''}};"
    "ultima=if($i.LastRunTime){{$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')}}else{{''}};"
    "resultado=[int]$i.LastTaskResult;"
    "perdidas=[int]$i.NumberOfMissedRuns;"
    "comando=($t.Actions | ForEach-Object {{ $_.Execute + ' ' + $_.Arguments }}) -join ' '"
    "}} | ConvertTo-Json -Compress"
)


def task_status(name: str = TASK_NAME) -> TaskStatus:
    """Consulta el estado real de una tarea de la carpeta propia (vacía si no existe)."""
    if not IS_WINDOWS:
        return TaskStatus(exists=False, message="El Programador de tareas solo existe en Windows.")
    script = STATUS_SCRIPT.format(folder=TASK_FOLDER, name=name.replace("'", "''"))
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            creationflags=_hidden_flags(),
            env=powershell_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TaskStatus(exists=False, message=f"No se pudo consultar la tarea: {exc}")
    payload = (result.stdout or "").strip()
    if result.returncode != 0 or not payload.startswith("{"):
        return TaskStatus(exists=False, message="La tarea no está registrada en este equipo.")
    try:
        import json

        data = json.loads(payload)
    except (ValueError, TypeError):
        return TaskStatus(exists=False, message="No se pudo interpretar el estado de la tarea.")
    outcome = data.get("resultado")
    return TaskStatus(
        exists=bool(data.get("existe")),
        enabled=bool(data.get("habilitada")),
        state=str(data.get("estado") or ""),
        next_run=str(data.get("siguiente") or ""),
        last_run=str(data.get("ultima") or ""),
        last_result=int(outcome) if outcome is not None else None,
        missed_runs=int(data.get("perdidas") or 0),
        command=str(data.get("comando") or "").strip(),
        message="",
    )


def cleanup_task_status() -> TaskStatus:
    """Estado real de la tarea de limpieza programada."""
    return task_status(CLEANUP_TASK_NAME)


def task_exists(task_path: str = TASK_PATH) -> bool:
    """Comprobación ligera con `schtasks` (sin PowerShell)."""
    if not IS_WINDOWS:
        return False
    try:
        result = run_schtasks(["/Query", "/TN", task_path], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def cleanup_task_exists() -> bool:
    """¿Está registrada la tarea de limpieza programada?"""
    return task_exists(CLEANUP_TASK_PATH)


@dataclass
class ScheduleState:
    """Resumen para la interfaz: estado real + último informe registrado."""

    status: TaskStatus = field(default_factory=TaskStatus)
    command_line: str = ""
    xml_path: Path | None = None
