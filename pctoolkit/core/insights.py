"""Avisos propios: lo que la aplicación ya mide, unido y dicho en una frase útil.

La portada enseña números (CPU, memoria, disco, procesos que más gastan) y la aplicación ya
tiene módulos que saben cosas: el historial de 24 h, las alertas por umbral, los programas de
inicio con su coste y sus entradas muertas, el análisis de disco y el catálogo de limpieza. Lo
que faltaba era **unirlos**: nadie dice «la CPU lleva un rato al 85 %», «te quedan 4 GB
libres» o «tienes 12 programas de inicio pesados». Eso es este módulo, y es lógica sin
interfaz a propósito: recibe datos y devuelve avisos, así que se prueba sin abrir ventanas.

Tres reglas se respetan para que esto sea útil y no ruido:

* **Ningún número inventado.** Cada aviso lleva dentro la cifra que lo justifica y de dónde
  sale (la media de las muestras guardadas, el espacio libre medido, el coste estimado de
  cada entrada de inicio). Si no hay dato, no hay aviso.
* **Los umbrales son los que ya usa la aplicación.** Los de CPU, memoria y disco son los de
  «Ajustes y alertas», no otros: quien los haya cambiado no verá dos criterios distintos.
  Los que no existen en ningún otro sitio (espacio libre, archivos de paginación, número de
  programas de inicio pesados) están aquí, arriba y con su motivo.
* **Un aviso se puede descartar.** Se guarda el descarte en `%APPDATA%\\PCToolkit\\avisos.jsonl`
  con una **huella** de la situación: si dentro de una semana sigue igual, no vuelve a salir;
  si empeora (menos espacio libre, más programas pesados), vuelve, porque entonces la
  situación ya no es la misma que se descartó.
* **Se puede resolver desde el propio aviso.** Los de limpieza llevan dentro qué categorías
  del catálogo señalan (`targets`), y la portada abre con ellas ya marcadas: el aviso no
  manda a buscar el sitio, pero **no borra nada** al pulsarlo —solo marca y enseña—, porque
  la confirmación sigue siendo de quien usa el programa.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .alerts import DEFAULT_THRESHOLDS, METRIC_CPU, METRIC_DISK, METRIC_RAM
from .config import data_dir

# --- Gravedad --------------------------------------------------------------
SEVERITY_INFO = "info"
SEVERITY_WARN = "aviso"
SEVERITY_BAD = "critico"

SEVERITY_ORDER = {SEVERITY_INFO: 0, SEVERITY_WARN: 1, SEVERITY_BAD: 2}
SEVERITY_LABELS = {
    SEVERITY_INFO: "Información",
    SEVERITY_WARN: "Aviso",
    SEVERITY_BAD: "Importante",
}

# --- Umbrales propios ------------------------------------------------------
# Los que no existen en el resto de la aplicación. Cada uno con su motivo, que es lo que
# permite defenderlo: no son cifras redondas puestas al azar.
#
#   CPU sostenida: una media de diez minutos es un «va lento de verdad», no un pico. Los
#     picos ya los avisa el motor de alertas con sus muestras sostenidas.
#   Espacio libre: el porcentaje engaña en discos grandes (un 5 % de 4 TB son 200 GB y un
#     5 % de 128 GB son 6 GB). Estos dos números son los que hacen falta para trabajar.
#   Archivo de paginación: a partir de la mitad, Windows está paginando de verdad y eso se
#     nota; por debajo es normal que haya algo escrito.
#   Programas de inicio pesados: cinco entradas medianas ya son un arranque lento.
#   Temporales: un giga acumulado es cuando merece la pena decirlo.
SUSTAINED_MINUTES = 10.0
# Y no se habla de «sostenido» con menos de media ventana de datos: una media de dos minutos
# no dice nada de cómo va el equipo, y avisar con ella sería exactamente el ruido que se
# quiere evitar.
CPU_MIN_MINUTES = SUSTAINED_MINUTES / 2
FREE_SPACE_WARN_BYTES = 10 * 1024**3
FREE_SPACE_BAD_BYTES = 5 * 1024**3
SWAP_WARN_PERCENT = 50.0
STARTUP_HEAVY_WARN = 5
TEMP_WARN_BYTES = 1024**3
HEALTH_WARN_SCORE = 45.0

# Cuántos avisos caben de una vez. Más de cuatro dejan de leerse, y esta pantalla es para
# mirarla de reojo; el resto se cuenta («y N más») sin desaparecer del registro.
MAX_VISIBLE = 4

# Cuánto dura un descarte. Una semana: si el problema sigue, vuelve a avisar; antes de eso,
# repetirlo solo conseguiría que se dejara de mirar la tarjeta.
DISMISS_DAYS = 7

# Tamaño de los tramos con los que se compara un aviso descartado. El espacio libre va de
# cinco en cinco gigas porque un cambio de megabytes no es «ha empeorado»; la CPU, de diez
# en diez puntos, y los temporales, de medio giga en medio giga.
FREE_SPACE_STEP = 5 * 1024**3
CPU_STEP = 10.0
TEMP_STEP = 512 * 1024**2


@dataclass(frozen=True)
class Insight:
    """Un aviso: qué pasa, con qué número y qué se puede hacer."""

    id: str
    severity: str
    title: str
    detail: str
    action: str = ""
    action_label: str = ""
    # Categorías de limpieza que señala el aviso: al abrir la ventana desde él vienen
    # marcadas, y **nada se borra hasta que se confirme**. Es la única acción que borra
    # algo, así que el aviso dice exactamente qué se marcaría y de dónde sale.
    targets: tuple[str, ...] = ()
    # Una segunda salida para el mismo aviso, cuando hay otra cosa razonable que hacer
    # (el analizador de disco, por ejemplo, que no libera nada pero dice qué ocupa).
    secondary_action: str = ""
    secondary_label: str = ""
    fingerprint: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABELS.get(self.severity, self.severity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "gravedad": self.severity,
            "titulo": self.title,
            "detalle": self.detail,
            "accion": self.action,
            "categorias": list(self.targets),
            "accion2": self.secondary_action,
            "huella": self.fingerprint,
            "datos": self.evidence,
        }


@dataclass
class Evidence:
    """Todo lo que hace falta para decidir los avisos, ya medido.

    Va aparte de las métricas a propósito: quien reúne estos datos sabe de dónde salen (el
    historial, el análisis de disco, los programas de inicio) y cuáles no se han podido
    mirar. Un campo a `None` es «no se sabe» y no produce aviso; un 0 es un dato.
    """

    cpu_avg: float | None = None
    cpu_minutes: float = 0.0
    cpu_top_process: str = ""
    cpu_top_percent: float = 0.0
    ram_percent: float = 0.0
    swap_percent: float = 0.0
    disk_percent: float = 0.0
    disk_free: int = 0
    disk_mount: str = ""
    health_score: float = 100.0
    startup_total: int = 0
    startup_heavy: int = 0
    startup_dead: int = 0
    startup_admin: int = 0
    temp_bytes: int = 0
    temp_files: int = 0
    clean_bytes: int = 0
    clean_categories: int = 0
    # Claves de las categorías medidas que tienen algo que liberar: son las que un aviso de
    # limpieza marca al abrir la ventana.
    clean_targets: tuple[str, ...] = ()
    thresholds: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))

    def threshold(self, metric: str) -> float:
        return float(self.thresholds.get(metric, DEFAULT_THRESHOLDS.get(metric, 90.0)))

    def to_dict(self) -> dict[str, Any]:
        datos = dict(self.__dict__)
        datos["thresholds"] = dict(self.thresholds)
        return datos


def _label(bytes_value: int) -> str:
    """Tamaño legible sin depender de la interfaz (el núcleo ya tiene su formateador)."""
    from .config import format_bytes

    return format_bytes(bytes_value)


def _cpu_bucket(value: float) -> int:
    return int(value // CPU_STEP)


def insight_cpu(evidence: Evidence) -> Insight | None:
    """CPU alta **sostenida**: la media de los últimos minutos, no el pico de ahora."""
    if evidence.cpu_avg is None or evidence.cpu_minutes < CPU_MIN_MINUTES:
        return None
    threshold = evidence.threshold(METRIC_CPU)
    if evidence.cpu_avg < threshold:
        return None
    proceso = ""
    if evidence.cpu_top_process and evidence.cpu_top_percent > 0:
        proceso = (
            f" El proceso que más consume es «{evidence.cpu_top_process}» "
            f"({evidence.cpu_top_percent:.0f} %)."
        )
    return Insight(
        id="cpu-sostenida",
        severity=SEVERITY_BAD if evidence.cpu_avg >= 95.0 else SEVERITY_WARN,
        title=f"La CPU lleva {evidence.cpu_minutes:.0f} minutos al {evidence.cpu_avg:.0f} %",
        detail=(
            f"La media de las muestras guardadas en ese tiempo está por encima del umbral "
            f"que tienes configurado ({threshold:.0f} %).{proceso} Suele venir de un programa "
            "trabajando de más, de un antivirus analizando o de demasiadas cosas abiertas."
        ),
        action="monitor",
        action_label="Ver el rendimiento",
        fingerprint=f"cpu-{_cpu_bucket(evidence.cpu_avg)}",
        evidence={"cpu_media": round(evidence.cpu_avg, 1), "minutos": round(evidence.cpu_minutes, 1)},
    )


def _cleaning_targets(evidence: Evidence) -> tuple[str, ...]:
    """Categorías que se marcan si se abre la limpieza desde un aviso.

    Las que la portada ha medido y tienen algo que liberar; si todavía no se ha medido nada
    (o no había nada), las recomendadas del catálogo. Ninguna de ellas pide administrador ni
    borra algo que no se recupere solo, así que marcarlas de entrada no promete de más: la
    papelera nunca entra por aquí.
    """
    from .cleaner import RECOMMENDED_TARGETS

    return tuple(evidence.clean_targets) or RECOMMENDED_TARGETS


def insight_free_space(evidence: Evidence) -> Insight | None:
    """Poco espacio libre, en gigas: el porcentaje engaña según el tamaño del disco."""
    if evidence.disk_free <= 0:
        return None
    if evidence.disk_free >= FREE_SPACE_WARN_BYTES:
        return None
    unidad = evidence.disk_mount.rstrip("\\/") or "el disco del sistema"
    critico = evidence.disk_free < FREE_SPACE_BAD_BYTES
    return Insight(
        id="poco-espacio",
        severity=SEVERITY_BAD if critico else SEVERITY_WARN,
        title=f"Quedan {_label(evidence.disk_free)} libres en {unidad}",
        detail=(
            "Con muy poco espacio libre Windows va más lento (no puede crecer el archivo de "
            "paginación ni preparar actualizaciones). La limpieza de temporales y cachés suele "
            "recuperar bastante sin tocar nada tuyo: el botón la abre con esas categorías ya "
            "marcadas, y no se borra nada hasta que lo confirmes."
        ),
        action="limpieza",
        action_label="Liberar espacio ahora",
        targets=_cleaning_targets(evidence),
        secondary_action="disco",
        secondary_label="Analizar el disco",
        fingerprint=f"libre-{int(evidence.disk_free // FREE_SPACE_STEP)}",
        evidence={"libre": evidence.disk_free, "unidad": unidad, "porcentaje": round(evidence.disk_percent, 1)},
    )


def insight_disk_full(evidence: Evidence) -> Insight | None:
    """Disco del sistema por encima del umbral configurado (el mismo que el de las alertas)."""
    if evidence.disk_percent <= 0:
        return None
    threshold = evidence.threshold(METRIC_DISK)
    if evidence.disk_percent < threshold:
        return None
    if evidence.disk_free < FREE_SPACE_WARN_BYTES:
        return None  # ya lo cuenta el aviso de espacio libre, con mejor número
    return Insight(
        id="disco-lleno",
        severity=SEVERITY_WARN,
        title=f"El disco del sistema está al {evidence.disk_percent:.0f} %",
        detail=(
            f"Por encima del {threshold:.0f} % que tienes configurado. Todavía quedan "
            f"{_label(evidence.disk_free)}, así que no corre prisa, pero conviene mirar qué "
            "ocupa. La limpieza abre con las categorías que se pueden liberar ya marcadas "
            "(nada se borra hasta que lo confirmes); el analizador dice, además, qué carpetas "
            "se llevan el espacio."
        ),
        action="limpieza",
        action_label="Liberar espacio ahora",
        targets=_cleaning_targets(evidence),
        secondary_action="disco",
        secondary_label="Analizar el disco",
        fingerprint=f"disco-{int(evidence.disk_percent // 5)}",
        evidence={"porcentaje": round(evidence.disk_percent, 1), "libre": evidence.disk_free},
    )


def insight_memory_pressure(evidence: Evidence) -> Insight | None:
    """Memoria al límite y con el archivo de paginación trabajando."""
    if evidence.ram_percent <= 0:
        return None
    threshold = evidence.threshold(METRIC_RAM)
    if evidence.ram_percent < threshold:
        return None
    paginando = evidence.swap_percent >= SWAP_WARN_PERCENT
    extra = (
        f" Además, el archivo de paginación va al {evidence.swap_percent:.0f} %, así que "
        "Windows ya está escribiendo memoria en el disco."
        if paginando
        else ""
    )
    return Insight(
        id="memoria-alta",
        severity=SEVERITY_BAD if paginando else SEVERITY_WARN,
        title=f"La memoria está al {evidence.ram_percent:.0f} %",
        detail=(
            f"Por encima del {threshold:.0f} % que tienes configurado.{extra} Cerrar lo que no "
            "uses devuelve el equipo a su sitio; el botón libera ahora la memoria reservada "
            "(pregunta antes y no cierra nada)."
        ),
        action="liberar-memoria",
        action_label="Liberar memoria ahora",
        secondary_action="memoria",
        secondary_label="Ver el reparto",
        fingerprint=f"ram-{int(evidence.ram_percent // 5)}",
        evidence={"ram": round(evidence.ram_percent, 1), "paginacion": round(evidence.swap_percent, 1)},
    )


def insight_health(evidence: Evidence) -> Insight | None:
    """Salud del equipo baja, con las tres métricas que la componen a la vista."""
    if evidence.health_score > 0 and evidence.health_score >= HEALTH_WARN_SCORE:
        return None
    if evidence.health_score <= 0:
        return None
    return Insight(
        id="salud-baja",
        severity=SEVERITY_WARN,
        title=f"La salud del equipo está en {evidence.health_score:.0f}/100",
        detail=(
            "La puntuación sale de la carga de CPU, memoria y disco de este momento. Con la "
            "CPU y la memoria altas a la vez, lo que más se nota es cerrar programas de fondo."
        ),
        action="monitor",
        action_label="Ver el rendimiento",
        fingerprint=f"salud-{int(evidence.health_score // 10)}",
        evidence={"salud": round(evidence.health_score, 1)},
    )


def insight_startup(evidence: Evidence) -> Insight | None:
    """Demasiadas cosas arrancando con Windows (los «programas de inicio lentos»)."""
    if evidence.startup_heavy < STARTUP_HEAVY_WARN:
        return None
    return Insight(
        id="inicio-pesado",
        severity=SEVERITY_WARN if evidence.startup_heavy < STARTUP_HEAVY_WARN * 2 else SEVERITY_BAD,
        title=f"Tienes {evidence.startup_heavy} programas de inicio pesados",
        detail=(
            f"De las {evidence.startup_total} entradas que arrancan con Windows, "
            f"{evidence.startup_heavy} son tareas o programas que trabajan de verdad al "
            "empezar. El botón los desactiva (pregunta antes, enumera lo que va a cambiar y no se "
            "borra nada: se pueden volver a activar desde su página o con «Deshacer»)."
        ),
        action="inicio-pesado",
        action_label="Desactivar las más pesadas",
        secondary_action="inicio",
        secondary_label="Revisar el inicio",
        fingerprint=f"inicio-{evidence.startup_heavy // 3}",
        evidence={
            "pesados": evidence.startup_heavy,
            "total": evidence.startup_total,
            "muertos": evidence.startup_dead,
        },
    )


def insight_startup_dead(evidence: Evidence) -> Insight | None:
    """Entradas de inicio que apuntan a programas que ya no están."""
    if evidence.startup_dead < 1:
        return None
    return Insight(
        id="inicio-muerto",
        severity=SEVERITY_INFO,
        title=f"{evidence.startup_dead} entrada(s) de inicio ya no sirven",
        detail=(
            "Apuntan a un programa que ya no está instalado (lo desinstalaste y dejó su "
            "entrada). No arrancan nada, pero retrasan el inicio; se pueden quitar desde la "
            "página de programas de inicio."
        ),
        action="inicio",
        action_label="Ver las entradas",
        fingerprint=f"muertos-{min(9, evidence.startup_dead)}",
        evidence={"muertos": evidence.startup_dead, "total": evidence.startup_total},
    )


def insight_temp_files(evidence: Evidence) -> Insight | None:
    """Temporales acumulados: lo que la limpieza puede recuperar sin tocar nada tuyo."""
    bytes_value = evidence.clean_bytes or evidence.temp_bytes
    files = evidence.temp_files if not evidence.clean_bytes else 0
    if bytes_value < TEMP_WARN_BYTES:
        return None
    categorias = _cleaning_targets(evidence)
    detalle = (
        f"{_label(bytes_value)} en temporales y cachés que se pueden borrar sin tocar tus "
        "documentos"
        + (f" ({files} archivos)" if files else "")
        + ". El botón abre la limpieza con esas categorías ya marcadas, y nada se borra hasta "
        "que lo confirmes."
    )
    return Insight(
        id="temporales",
        severity=SEVERITY_INFO,
        title=f"Se pueden liberar {_label(bytes_value)}",
        detail=detalle,
        action="limpieza",
        action_label="Ver qué se puede liberar",
        targets=categorias,
        fingerprint=f"temp-{int(bytes_value // TEMP_STEP)}",
        evidence={
            "bytes": bytes_value,
            "categorias": evidence.clean_categories,
            "objetivos": list(categorias),
        },
    )


# El orden de esta tabla es el orden en que se enseñan los avisos de la misma gravedad: de
# lo que más se nota a lo que menos.
RULES = (
    insight_cpu,
    insight_free_space,
    insight_memory_pressure,
    insight_startup,
    insight_disk_full,
    insight_startup_dead,
    insight_temp_files,
    insight_health,
)


def evaluate(
    evidence: Evidence,
    *,
    dismissed: Mapping[str, str] | None = None,
) -> list[Insight]:
    """Todos los avisos que corresponden a esta situación, ya filtrados y ordenados.

    `dismissed` relaciona el identificador de un aviso con la huella que se descartó: un
    aviso descartado **con la misma huella** no vuelve a salir, y si la situación ha
    cambiado de tramo (peor), la huella es otra y el aviso vuelve.
    """
    descartados = dict(dismissed or {})
    activos: list[Insight] = []
    for regla in RULES:
        aviso = regla(evidence)
        if aviso is None:
            continue
        if descartados.get(aviso.id) == aviso.fingerprint:
            continue
        activos.append(aviso)
    activos.sort(key=lambda aviso: -SEVERITY_ORDER.get(aviso.severity, 0))
    return activos


def visible(activos: Sequence[Insight]) -> tuple[list[Insight], int]:
    """Lo que cabe en la tarjeta y cuántos quedan fuera (para poder decirlo)."""
    lista = list(activos)
    return lista[:MAX_VISIBLE], max(0, len(lista) - MAX_VISIBLE)


# --- Descartes -------------------------------------------------------------
def insights_file() -> Path:
    return data_dir() / "avisos.jsonl"


class InsightStore:
    """Descarte de avisos, en un archivo por líneas (como el histórico de mantenimiento).

    Se guarda la última línea de cada aviso: el archivo es legible a mano y sobrevive a un
    cierre inesperado sin perder nada, que es la razón por la que la aplicación usa este
    formato en los sitios donde apunta cosas que pasan de una sesión a otra.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else insights_file()

    # ------------------------------------------------------------------
    def _entries(self) -> dict[str, dict[str, Any]]:
        """Última entrada por aviso (las líneas corruptas se ignoran, como en el histórico)."""
        ultimas: dict[str, dict[str, Any]] = {}
        try:
            lineas = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ultimas
        for linea in lineas:
            linea = linea.strip()
            if not linea:
                continue
            try:
                datos = json.loads(linea)
            except (ValueError, TypeError):
                continue
            if isinstance(datos, dict) and datos.get("id"):
                ultimas[str(datos["id"])] = datos
        return ultimas

    def dismissed(self, now: float | None = None) -> dict[str, str]:
        """Avisos descartados todavía vigentes: `{id: huella}`."""
        ahora = time.time() if now is None else float(now)
        vigentes: dict[str, str] = {}
        for clave, datos in self._entries().items():
            try:
                caduca = float(datos.get("caduca", 0) or 0)
            except (TypeError, ValueError):
                continue
            if caduca > ahora:
                vigentes[clave] = str(datos.get("huella") or "")
        return vigentes

    def dismiss(self, insight: Insight | str, fingerprint: str = "", now: float | None = None) -> None:
        """Descarta un aviso: no vuelve hasta que caduque o la situación cambie de tramo."""
        if isinstance(insight, Insight):
            clave, huella = insight.id, insight.fingerprint
        else:
            clave, huella = str(insight), str(fingerprint)
        momento = time.time() if now is None else float(now)
        entrada = {
            "id": clave,
            "huella": huella,
            "momento": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(momento)),
            "caduca": momento + DISMISS_DAYS * 86400,
            "caduca_en_dias": DISMISS_DAYS,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        except OSError:
            return  # sin sitio para apuntarlo, el aviso volverá: no es grave

    def clear(self) -> None:
        """Olvida todos los descartes (lo usa «volver a mostrar los avisos descartados»)."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def count(self) -> int:
        return len(self._entries())
