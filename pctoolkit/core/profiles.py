"""Perfiles: qué ajustes y qué limpieza se quieren en un equipo, guardados en un archivo.

Un perfil es un archivo de texto (JSON) con las decisiones que alguien ha tomado: qué ajustes
de Windows marcar, qué categorías limpiar y cómo programar el mantenimiento y la limpieza. Se
guarda con la extensión `.pctkperfil` —que no es más que un nombre reconocible— y se puede
leer y editar a mano, que es justo lo que permite preparar un perfil para otro equipo sin
tenerlo delante.

Lo importante, y la razón de que esto viva en el núcleo y no en la ventana: **abrir un perfil
no aplica nada**. Un perfil de fuera es un archivo de texto que puede venir de otro equipo, de
otra versión o de otra persona, así que al leerlo solo se *marcan* las casillas —con la lista
de lo que no encaja delante— y se aplica después, con el mismo botón, la misma confirmación y
el mismo respaldo (.reg y Deshacer) que si se hubiera marcado a mano. Lo que no esté en el
catálogo de esta versión no se importa a medias: se cuenta aparte y se enseña, en vez de
desaparecer en silencio.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import logs
from .config import APP_NAME, APP_VERSION, data_dir
from .scheduler import CleanupSchedule, MaintenanceSchedule
from .windows_settings import (
    BY_ID,
    STATUS_OPTIMIZED,
    STATUS_UNAVAILABLE,
    Setting,
    SettingState,
    analyse,
    catalog,
)

# Lo que dice el archivo. El nombre y la versión del formato van dentro para poder cambiar
# de forma sin adivinar: un archivo con otro `formato` no es nuestro y no se abre.
FORMATO = "pctoolkit-perfil"
VERSION_FORMATO = 1
EXTENSION = ".pctkperfil"
FILTRO = f"Perfiles de PC Toolkit (*{EXTENSION})"

# Topes: un archivo de fuera puede ser cualquier cosa, y no se le deja llenar la memoria ni
# el historial con la lista de ajustes de nadie.
MAXIMO_AJUSTES = 500
MAXIMO_NOTA = 500
MAXIMO_LIMPIEZA = 64


def _texto(valor: Any, limite: int = 200) -> str:
    """Texto saneado de un campo del archivo (nunca más largo que su tope)."""
    return str(valor if valor is not None else "").strip()[:limite]


def _lista(valor: Any, limite: int = MAXIMO_AJUSTES) -> tuple[str, ...]:
    """Lista de identificadores, sin repetir, sin vacíos y sin pasar del tope."""
    if isinstance(valor, str):
        # Acepta tanto una lista como una cadena con saltos de línea: se edita a mano.
        valor = valor.replace(",", "\n").split("\n")
    if not isinstance(valor, (list, tuple, set)):
        return ()
    salida: list[str] = []
    for elemento in valor:
        clave = _texto(elemento, 120)
        if clave and clave not in salida:
            salida.append(clave)
        if len(salida) >= limite:
            break
    return tuple(salida)


def system_label() -> str:
    """Cómo se llama este sistema en el perfil («Windows 11 24H2», «Windows 10»…)."""
    if sys.platform != "win32":
        return platform.system() or "sistema desconocido"
    try:
        version = sys.getwindowsversion()
    except AttributeError:  # pragma: no cover - en Windows siempre está
        return "Windows"
    # El número mayor que informa Windows 11 sigue siendo 10: lo que distingue las dos
    # versiones es el **build** (22000 en adelante es Windows 11). Y el build es también lo
    # que distingue las revisiones de cada una (21H2, 22H2, 23H2…), que para un perfil
    # importan: un ajuste que existe en una puede no existir en la otra.
    nombre = "Windows 11" if version.build >= 22000 else "Windows 10"
    marcas = (
        (26100, "24H2"),
        (22631, "23H2"),
        (22621, "22H2"),
        (22000, "21H2"),
        (19045, "22H2"),
        (19044, "21H2"),
    )
    for build, etiqueta in marcas:
        if version.build >= build:
            return f"{nombre} {etiqueta}"
    return f"{nombre} (build {version.build})"


def equipment_label() -> str:
    """Nombre del equipo (vacío si el sistema no lo sabe)."""
    try:
        return _texto(platform.node(), 80)
    except OSError:  # pragma: no cover - `node()` no debería fallar
        return ""


# ---------------------------------------------------------------------------
# El perfil
# ---------------------------------------------------------------------------


@dataclass
class Profile:
    """Un perfil: qué se quiere y de qué equipo salió."""

    # Identificadores del catálogo de ajustes de Windows, en el orden del catálogo.
    ajustes: tuple[str, ...] = ()
    # Categorías de limpieza que el perfil quiere limpiar (claves del catálogo de `cleaner`).
    limpieza: tuple[str, ...] = ()
    # Programación del mantenimiento y de la limpieza, con la forma que ya usan los dos
    # (`MaintenanceSchedule.to_dict()` y `CleanupSchedule.to_dict()`): así se leen con el
    # mismo saneado de siempre en vez de con uno nuevo que se pueda desviar.
    mantenimiento: dict[str, Any] = field(default_factory=dict)
    limpieza_programada: dict[str, Any] = field(default_factory=dict)
    # De dónde salió (para la cabecera y para saber si encaja en este equipo).
    nota: str = ""
    version: str = APP_VERSION
    equipo: str = ""
    sistema: str = ""
    creado: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "formato": FORMATO,
            "version_formato": VERSION_FORMATO,
            "aplicacion": APP_NAME,
            "version": self.version,
            "creado": self.creado,
            "equipo": self.equipo,
            "sistema": self.sistema,
            "nota": self.nota,
            "ajustes": list(self.ajustes),
            "limpieza": list(self.limpieza),
            "mantenimiento": dict(self.mantenimiento),
            "limpieza_programada": dict(self.limpieza_programada),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Profile":
        """Lee un perfil de un diccionario, saneando lo que se pueda sanear.

        No lanza por datos raros: un archivo editado a mano puede traer de todo y lo peor que
        se puede hacer es dar un error del sistema por un campo que no importa. Lo que no
        encaje se recorta a su tope o se deja fuera, y quien lo abra ve después la lista de lo
        que no entró.
        """
        data = data if isinstance(data, dict) else {}
        mantenimiento = data.get("mantenimiento")
        programada = data.get("limpieza_programada")
        return cls(
            ajustes=_lista(data.get("ajustes")),
            limpieza=_lista(data.get("limpieza"), MAXIMO_LIMPIEZA),
            mantenimiento=dict(mantenimiento) if isinstance(mantenimiento, dict) else {},
            limpieza_programada=dict(programada) if isinstance(programada, dict) else {},
            nota=_texto(data.get("nota"), MAXIMO_NOTA),
            version=_texto(data.get("version"), 40),
            equipo=_texto(data.get("equipo"), 80),
            sistema=_texto(data.get("sistema"), 80),
            creado=_texto(data.get("creado"), 40),
        )

    @property
    def esta_vacio(self) -> bool:
        """¿No trae nada que marcar ni nada que programar?"""
        return not (self.ajustes or self.limpieza or self.mantenimiento or self.limpieza_programada)


def construir(
    *,
    ajustes: Iterable[str] | Iterable[Setting] = (),
    limpieza: Sequence[str] = (),
    mantenimiento: MaintenanceSchedule | None = None,
    limpieza_programada: CleanupSchedule | None = None,
    nota: str = "",
    con_equipo: bool = True,
) -> Profile:
    """Arma un perfil con lo que hay ahora mismo en la ventana.

    Se le pueden pasar los ajustes como identificadores o como ajustes enteros (lo que tiene
    la página a mano); el orden del catálogo se respeta en los dos casos, para que el archivo
    se lea igual que la lista que se ve.
    """
    elegidos = {a if isinstance(a, str) else a.id for a in ajustes}
    en_orden = tuple(setting.id for setting in catalog() if setting.id in elegidos)
    # Un identificador que no esté en el catálogo (un ajuste de un plugin que ahora mismo no
    # se ha cargado) no se pierde: se queda tal cual y se cuenta al abrirlo.
    resto = tuple(clave for clave in elegidos if clave not in set(en_orden))
    return Profile(
        ajustes=en_orden + resto,
        limpieza=tuple(limpieza),
        mantenimiento=(mantenimiento.normalized().to_dict() if mantenimiento else {}),
        limpieza_programada=(limpieza_programada.normalized().to_dict() if limpieza_programada else {}),
        nota=_texto(nota, MAXIMO_NOTA),
        version=APP_VERSION,
        equipo=equipment_label() if con_equipo else "",
        sistema=system_label() if con_equipo else "",
        creado=time.strftime("%Y-%m-%d %H:%M"),
    )


def guardar(perfil: Profile, ruta: Path | str) -> Path:
    """Escribe el perfil en un archivo (JSON legible, en UTF-8)."""
    destino = Path(ruta)
    if destino.suffix.lower() != EXTENSION:
        destino = destino.with_suffix(EXTENSION)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(perfil.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destino


def leer(ruta: Path | str) -> tuple[Profile | None, str]:
    """Lee un perfil. Devuelve `(perfil, motivo)`; el perfil es `None` si no se pudo abrir.

    Se comprueba lo justo para no abrir cualquier archivo: que sea JSON, que lleve el nombre
    del formato y que no venga de una versión de formato más nueva (que puede tener campos
    que esta versión no entendería, y aplicar la mitad sería peor que no abrir nada).
    """
    camino = Path(ruta)
    try:
        texto = camino.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, f"No se pudo abrir {camino.name}: {exc}"
    try:
        datos = json.loads(texto)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"{camino.name} no es un perfil de {APP_NAME}: {exc}"
    if not isinstance(datos, dict) or _texto(datos.get("formato"), 40) != FORMATO:
        return None, (
            f"{camino.name} no es un perfil de {APP_NAME} (falta «formato: {FORMATO}»). "
            "Se puede abrir con un editor de texto para mirarlo, pero no aplicarlo."
        )
    try:
        version_formato = int(datos.get("version_formato") or VERSION_FORMATO)
    except (TypeError, ValueError):
        version_formato = VERSION_FORMATO
    if version_formato > VERSION_FORMATO:
        return None, (
            f"{camino.name} lo escribió una versión más nueva de {APP_NAME} "
            f"(formato {version_formato}, y esta entiende hasta el {VERSION_FORMATO})."
        )
    perfil = Profile.from_dict(datos)
    if perfil.esta_vacio:
        return None, f"{camino.name} no trae ningún ajuste ni ninguna limpieza que aplicar."
    return perfil, ""


def ruta_sugerida(nombre: str = "") -> Path:
    """Dónde proponer guardar un perfil (en la carpeta de la aplicación, con la fecha)."""
    limpio = _texto(nombre, 40).replace("/", "-").replace("\\", "-") or "perfil"
    return data_dir() / "perfiles" / f"{limpio}-{time.strftime('%Y%m%d')}{EXTENSION}"


# ---------------------------------------------------------------------------
# Qué se puede hacer con un perfil en este equipo
# ---------------------------------------------------------------------------


def ajustes_del_perfil(perfil: Profile) -> list[Setting]:
    """Los ajustes del perfil que existen en el catálogo de **esta** versión.

    Los identificadores que no estén en el catálogo ni se intentan: esos los cuenta
    `ajustes_desconocidos`, para poder decirlo en vez de perderlos por el camino.
    """
    return [BY_ID[clave] for clave in perfil.ajustes if clave in BY_ID]


def ajustes_desconocidos(perfil: Profile) -> tuple[str, ...]:
    """Identificadores del perfil que esta versión no conoce (o no tiene cargados)."""
    return tuple(clave for clave in perfil.ajustes if clave not in BY_ID)


def limpieza_valida(perfil: Profile) -> tuple[str, ...]:
    """Categorías de limpieza del perfil que existen en esta versión."""
    from .cleaner import find_target

    return tuple(clave for clave in perfil.limpieza if find_target(clave) is not None)


def limpieza_desconocida(perfil: Profile) -> tuple[str, ...]:
    from .cleaner import find_target

    return tuple(clave for clave in perfil.limpieza if find_target(clave) is None)


def revisar(perfil: Profile, estados: Sequence[SettingState] | None = None) -> dict[str, Any]:
    """Compara el perfil con este equipo: qué marcar, qué ya está y qué no se puede.

    Es lo que se enseña al abrir un perfil y, a la vez, lo que decide qué se marca: lo que no
    se puede aplicar aquí (un ajuste de otro Windows, uno que necesita administrador y no se
    tiene…) no se marca, y se dice por qué.
    """
    conocidos = {estado.setting.id: estado for estado in (estados if estados is not None else analyse())}
    marcar: list[Setting] = []
    ya: list[Setting] = []
    bloqueados: list[tuple[Setting, str]] = []
    for clave in perfil.ajustes:
        estado = conocidos.get(clave)
        if estado is None:
            continue
        if estado.status == STATUS_UNAVAILABLE:
            bloqueados.append((estado.setting, estado.detail or "no disponible en este equipo"))
            continue
        if estado.status == STATUS_OPTIMIZED:
            ya.append(estado.setting)
            continue
        marcar.append(estado.setting)
    return {
        "marcar": marcar,
        "ya": ya,
        "bloqueados": bloqueados,
        "desconocidos": ajustes_desconocidos(perfil),
        "limpieza": limpieza_valida(perfil),
        "limpieza_desconocida": limpieza_desconocida(perfil),
    }


def resumen(perfil: Profile, revision: dict[str, Any] | None = None, *, con_equipo: bool = True) -> list[str]:
    """Líneas legibles del perfil, para la ventana que lo enseña antes de tocar nada."""
    revision = revision if revision is not None else revisar(perfil)
    lineas: list[str] = []
    if con_equipo:
        quien = perfil.sistema or "sistema sin identificar"
        if perfil.equipo:
            quien += f" · {perfil.equipo}"
        lineas.append(f"Guardado con {APP_NAME} {perfil.version or '?'} · {quien}")
        if perfil.creado:
            lineas[-1] += f" · {perfil.creado}"
    if perfil.nota:
        lineas.append(f"Nota: {perfil.nota}")

    total = len(perfil.ajustes)
    if total:
        lineas.append(
            f"Ajustes de Windows: {total} en el perfil · "
            f"{len(revision['marcar'])} por marcar en este equipo · "
            f"{len(revision['ya'])} ya aplicados · "
            f"{len(revision['bloqueados'])} que aquí no se pueden aplicar · "
            f"{len(revision['desconocidos'])} desconocidos para esta versión"
        )
        for setting, motivo in revision["bloqueados"][:4]:
            lineas.append(f"  · {setting.name}: {motivo}")
        if revision["desconocidos"]:
            lineas.append("  · desconocidos: " + ", ".join(revision["desconocidos"][:8]))
    else:
        lineas.append("Ajustes de Windows: ninguno en el perfil")

    if revision["limpieza"]:
        from .cleaner import find_target

        nombres = [find_target(clave).name for clave in revision["limpieza"] if find_target(clave)]
        lineas.append("Limpieza: " + ", ".join(nombres))
    if revision["limpieza_desconocida"]:
        lineas.append("  · categorías desconocidas: " + ", ".join(revision["limpieza_desconocida"]))

    if perfil.mantenimiento:
        from .scheduler import MaintenanceSchedule

        plan = MaintenanceSchedule.from_dict(perfil.mantenimiento).normalized()
        partes = []
        if plan.clean_temp:
            from .cleaner import keys_label

            partes.append(f"limpiar {keys_label(plan.clean_targets)}")
        if plan.release_memory:
            partes.append("liberar memoria")
        if plan.export_report:
            partes.append("generar informe")
        lineas.append(
            "Mantenimiento: " + ("; ".join(partes) or "ningún paso activado")
        )
    if perfil.limpieza_programada:
        plan_limpieza = CleanupSchedule.from_dict(perfil.limpieza_programada).normalized()
        if plan_limpieza.targets:
            lineas.append(
                f"Limpieza programada: {plan_limpieza.frequency.lower()} a las {plan_limpieza.time} "
                f"({', '.join(plan_limpieza.targets)})"
            )
    return lineas


def mantenimiento_del_perfil(perfil: Profile) -> MaintenanceSchedule | None:
    """La programación de mantenimiento del perfil, saneada (o nada si no trae)."""
    if not perfil.mantenimiento:
        return None
    return MaintenanceSchedule.from_dict(perfil.mantenimiento).normalized()


def limpieza_programada_del_perfil(perfil: Profile) -> CleanupSchedule | None:
    """La limpieza programada del perfil, saneada (o nada si no trae)."""
    if not perfil.limpieza_programada:
        return None
    return CleanupSchedule.from_dict(perfil.limpieza_programada).normalized()


def nota_de_resumen(perfil: Profile) -> str:
    """Una línea con el perfil, para la barra de estado (o el registro)."""
    return (
        f"{len(perfil.ajustes)} ajuste(s), {len(perfil.limpieza)} categoría(s) de limpieza"
        + (" y mantenimiento" if perfil.mantenimiento else "")
    )


def registrar(perfil: Profile, accion: str) -> None:
    """Deja el perfil en el registro de la aplicación (guardar y abrir se anotan).

    Se escribe con el registro de siempre, en vez de callarse: si alguien abre un perfil y
    después no encuentra lo que esperaba, en el archivo queda qué se abrió y de dónde salía.
    """
    logs.logger("perfiles").info("Perfil %s: %s", accion, nota_de_resumen(perfil))


def con_nota(perfil: Profile, nota: str) -> Profile:
    """Copia del perfil con otra nota (lo usa la ventana al guardar)."""
    return replace(perfil, nota=_texto(nota, MAXIMO_NOTA))
