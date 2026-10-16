"""Ajustes recomendados de Windows: catálogo, análisis y aplicación reversible.

Está inspirado en **CrapFixer** (https://github.com/builtbybel/CrapFixer, licencia MIT): la
idea de tener un catálogo de ajustes que se pueden analizar, revisar y aplicar, con vuelta
atrás. Aquí no hay nada copiado de allí: el catálogo, la detección y la vuelta atrás son
código propio, con la arquitectura de PC Toolkit (lógica sin interfaz y cambios anotados en
el historial de deshacer).

Tres reglas que se respetan siempre:

* **Nada se toca al analizar.** `analyse()` solo lee el registro; quien decide es la
  interfaz, y solo aplica lo que se haya marcado.
* **Todo se puede deshacer.** Cada ajuste aplicado se anota en el sistema de cambios
  reversibles con los valores que había antes, así que aparece en el panel de deshacer de la
  página de Inicio junto al resto de cambios del programa.
* **Sin permisos de administrador por defecto.** Casi todo el catálogo vive en
  `HKEY_CURRENT_USER` (no hace falta elevar nada); lo que toca `HKEY_LOCAL_MACHINE` se marca
  con `needs_admin` y ni se analiza ni se aplica si la aplicación no va como administrador.

Para las pruebas hay una raíz de registro propia: con `PCTOOLKIT_RAIZ_PRUEBAS` puesta, todas
las claves se leen y se escriben **colgando de esa raíz**, así que la suite nunca toca una
clave de verdad del sistema.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import logs, undo
from .config import APP_NAME, APP_VERSION, data_dir

try:  # solo Windows
    import ctypes
    import winreg

    IS_WINDOWS = True
except ImportError:  # pragma: no cover - en Linux/macOS el catálogo no se puede usar
    ctypes = None  # type: ignore[assignment]
    winreg = None  # type: ignore[assignment]
    IS_WINDOWS = False

# Tipo de cambio con el que se anotan en el historial de deshacer.
KIND_AJUSTES = "ajustes_windows"

# --- Riesgos ----------------------------------------------------------------
RISK_LOW = "bajo"
RISK_MEDIUM = "medio"
RISK_HIGH = "alto"
RISK_ORDER = (RISK_LOW, RISK_MEDIUM, RISK_HIGH)
RISK_LABELS = {
    RISK_LOW: "Riesgo bajo",
    RISK_MEDIUM: "Riesgo medio",
    RISK_HIGH: "Riesgo alto",
}

# --- Estado de un ajuste ----------------------------------------------------
STATUS_PENDING = "recomendado"
STATUS_OPTIMIZED = "optimizado"
STATUS_UNAVAILABLE = "no_disponible"
# Estado inicial de la página, antes de que se haya analizado nada: no es un resultado, es
# lo que se enseña para que el catálogo esté a la vista sin afirmar nada del equipo.
STATUS_UNKNOWN = "sin_analizar"
# El valor que hay puesto no es ni el recomendado ni el de fábrica: alguien lo cambió (el
# usuario, otro programa o un ajuste anterior). No es un error ni un problema —puede ser
# justo lo que se quería—, pero conviene saberlo antes de pisarlo, así que se enseña como
# un estado propio en vez de como «recomendado» a secas.
STATUS_CUSTOM = "personalizado"
STATUS_LABELS = {
    STATUS_PENDING: "Recomendado",
    STATUS_OPTIMIZED: "Ya optimizado",
    STATUS_CUSTOM: "Personalizado",
    STATUS_UNAVAILABLE: "No disponible",
    STATUS_UNKNOWN: "Sin analizar",
}

# --- Valor de fábrica -------------------------------------------------------
# Lo que Windows trae de serie en cada valor del registro. `SIN_DATO` es «no se sabe»: en ese
# caso el ajuste no lleva distintivo y no se ofrece devolverlo a fábrica, porque afirmar un
# valor sin comprobarlo es peor que no decir nada. `AUSENTE` es «de fábrica ese valor no
# existe», que es un estado más del registro y no un hueco: hay ajustes cuyo valor de fábrica
# es, justamente, que la clave no esté.
SIN_DATO = object()
AUSENTE = object()

# De dónde viene el valor que hay puesto ahora mismo.
ORIGIN_DEFAULT = "predeterminado"
ORIGIN_CUSTOM = "personalizado"
ORIGIN_LABELS = {ORIGIN_DEFAULT: "Predeterminado", ORIGIN_CUSTOM: "Personalizado"}

# --- Categorías ------------------------------------------------------------
CATEGORY_PRIVACY = "privacidad"
CATEGORY_INTERFACE = "interfaz"
CATEGORY_PERFORMANCE = "rendimiento"
CATEGORY_TELEMETRY = "telemetria"
CATEGORY_ADS = "anuncios"

CATEGORIES: dict[str, tuple[str, str]] = {
    # identificador: (nombre para enseñar, icono)
    CATEGORY_ADS: ("Anuncios y sugerencias", "warn"),
    CATEGORY_PRIVACY: ("Privacidad", "shield"),
    CATEGORY_TELEMETRY: ("Telemetría", "cloud"),
    CATEGORY_INTERFACE: ("Interfaz", "home"),
    CATEGORY_PERFORMANCE: ("Rendimiento", "gauge"),
}
CATEGORY_ORDER = (
    CATEGORY_ADS,
    CATEGORY_PRIVACY,
    CATEGORY_TELEMETRY,
    CATEGORY_INTERFACE,
    CATEGORY_PERFORMANCE,
)

# --- Tipos de valor del registro -------------------------------------------
KIND_DWORD = "dword"
KIND_STRING = "string"
KIND_DELETE = "delete"  # lo recomendado es que ese valor *no* exista

KIND_TO_REGISTRY = {
    KIND_DWORD: getattr(winreg, "REG_DWORD", 4),
    KIND_STRING: getattr(winreg, "REG_SZ", 1),
}

HIVE_USER = "HKCU"
HIVE_MACHINE = "HKLM"
HIVE_LABELS = {HIVE_USER: "HKEY_CURRENT_USER", HIVE_MACHINE: "HKEY_LOCAL_MACHINE"}

# Variable de entorno con la raíz de pruebas: todas las rutas cuelgan de ahí.
TEST_ROOT_ENV = "PCTOOLKIT_RAIZ_PRUEBAS"


# ---------------------------------------------------------------------------
# Fichas del catálogo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueSpec:
    """Un valor del registro y lo que se recomienda que valga."""

    path: str
    name: str
    recommended: Any
    kind: str = KIND_DWORD
    hive: str = HIVE_USER
    # Lo que Windows trae de fábrica (`SIN_DATO` si no se sabe, `AUSENTE` si no existe).
    # Con esto se distingue un valor que nadie ha tocado de uno que alguien cambió, y se
    # puede devolver un solo ajuste a como vino Windows sin tocar los demás.
    default: Any = SIN_DATO

    @property
    def sabe_default(self) -> bool:
        """¿Se sabe cuál es el valor de fábrica de este valor?"""
        return self.default is not SIN_DATO

    @property
    def default_label(self) -> str:
        """Cómo se cuenta el valor de fábrica en la ayuda de cada ajuste."""
        if self.default is SIN_DATO:
            return "no se sabe (por eso este ajuste no se puede devolver a fábrica)"
        if self.default is AUSENTE:
            return "no existe: Windows no trae este valor"
        return repr(self.default)

    def es_de_fabrica(self, existe: bool, valor: Any) -> bool | None:
        """¿Lo que hay es el valor de fábrica? `None` cuando no se sabe cuál es.

        `None` no es un «no»: es «no lo sé», y quien lo mire tiene que tratarlo aparte
        (enseñar el ajuste como hasta ahora) en vez de deducir que alguien lo tocó.

        Un valor que **no está** cuenta como de fábrica: en este catálogo todos los valores
        son preferencias que sustituyen al comportamiento de serie de Windows, así que sin el
        valor el sistema hace justo lo que hace recién instalado. (Que sea el usuario quien
        lo haya borrado a mano no se puede distinguir, y da igual: el comportamiento es el
        mismo. Y si de fábrica el valor existía, devolverlo lo vuelve a escribir.)
        """
        if self.default is SIN_DATO:
            return None
        if not existe:
            return True
        if self.default is AUSENTE:
            return False
        return _same_value(valor, self.default)

    def write_default(self) -> None:
        """Deja este valor como viene de fábrica (o lo borra, si no existe de fábrica)."""
        if self.default is SIN_DATO:
            raise OSError(f"No se sabe cuál es el valor de fábrica de {self.describe()}.")
        if self.default is AUSENTE:
            delete_value(self)
            return
        write_value(self, self.default)

    @property
    def hive_label(self) -> str:
        return HIVE_LABELS.get(self.hive, self.hive)

    @property
    def full_path(self) -> str:
        """Ruta tal y como se enseña en la ayuda (sin la raíz de pruebas)."""
        return f"{self.hive_label}\\{self.path}"

    def describe(self) -> str:
        return f"{self.full_path}\\{self.name}" if self.name else f"{self.full_path} (valor predeterminado)"

    def to_dict(self) -> dict[str, Any]:
        return {"hive": self.hive, "path": self.path, "name": self.name, "kind": self.kind}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ValueSpec | None":
        if not isinstance(data, dict) or not data.get("path"):
            return None
        return cls(
            path=str(data.get("path")),
            name=str(data.get("name") or ""),
            recommended=None,
            kind=str(data.get("kind") or KIND_DWORD),
            hive=str(data.get("hive") or HIVE_USER),
        )


@dataclass(frozen=True)
class KeyRef:
    """Una clave que el ajuste crea y que hay que borrar al deshacerlo.

    Algunos ajustes funcionan por la **mera existencia** de la clave (el del menú
    contextual clásico: una clave con el valor predeterminado vacío), así que devolver el
    valor anterior no basta: hay que quitar la clave o el ajuste seguiría puesto.
    """

    path: str
    hive: str = HIVE_USER

    @property
    def full_path(self) -> str:
        return f"{HIVE_LABELS.get(self.hive, self.hive)}\\{self.path}"

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "hive": self.hive}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KeyRef | None":
        if not isinstance(data, dict) or not data.get("path"):
            return None
        return cls(path=str(data.get("path")), hive=str(data.get("hive") or HIVE_USER))


@dataclass(frozen=True)
class Setting:
    """Un ajuste del catálogo: qué es, qué toca y cómo se vuelve atrás."""

    id: str
    name: str
    category: str
    description: str
    why: str = ""
    risk: str = RISK_LOW
    needs_admin: bool = False
    values: tuple[ValueSpec, ...] = ()
    created_keys: tuple[KeyRef, ...] = ()
    note: str = ""
    search: str = ""
    requires_restart: str = ""
    # Un ajuste puede venir del catálogo del programa o de un plugin del usuario. El plugin
    # se apunta con su huella: si el archivo cambia, el ajuste vuelve a pedir confirmación.
    plugin: str = ""
    plugin_name: str = ""
    plugin_fingerprint: str = ""

    @property
    def from_plugin(self) -> bool:
        """¿Lo aporta un plugin en vez del propio PC Toolkit?"""
        return bool(self.plugin)

    @property
    def risk_label(self) -> str:
        return RISK_LABELS.get(self.risk, self.risk)

    @property
    def category_label(self) -> str:
        return CATEGORIES.get(self.category, (self.category, ""))[0]

    @property
    def registry_lines(self) -> list[str]:
        """Las claves que toca, para la ayuda de cada ajuste."""
        return [spec.describe() for spec in self.values]

    @property
    def sabe_predeterminado(self) -> bool:
        """¿Se sabe qué trae Windows de fábrica en **todos** sus valores?

        Si falta uno, no se puede devolver el ajuste a fábrica: se dejaría el valor
        desconocido como esté y el ajuste quedaría a medias.
        """
        return bool(self.values) and all(spec.sabe_default for spec in self.values)

    @property
    def default_lines(self) -> list[str]:
        """Los valores de fábrica, para la ayuda (con los que no se saben, dichos tal cual)."""
        return [f"• {spec.describe()} → {spec.default_label}" for spec in self.values]

    def help_text(self) -> str:
        """Texto de «¿Qué hace esto?»: qué cambia, riesgo, claves y cómo se revierte."""
        partes = [self.description]
        if self.why:
            partes.append(f"Por qué se recomienda: {self.why}")
        partes.append(f"Riesgo: {self.risk_label.lower()}.")
        if self.values:
            partes.append("Claves del registro:\n" + "\n".join(f"• {linea}" for linea in self.registry_lines))
            partes.append(
                "De fábrica (lo que trae Windows antes de tocar nada):\n" + "\n".join(self.default_lines)
            )
        if self.note:
            partes.append(f"Qué se pierde: {self.note}")
        if self.requires_restart:
            partes.append(f"Para que se note: {self.requires_restart}")
        if self.plugin:
            partes.append(
                f"Este ajuste lo aporta el plugin «{self.plugin_name or self.plugin}», no PC Toolkit: "
                "revísalo en la tarjeta de Plugins antes de confiar en él."
            )
        reversiones = [
            "Cómo se revierte: cada cambio queda anotado en Deshacer (página de Inicio) y "
            "el valor anterior se guarda al aplicarlo, así que se puede volver atrás con un "
            "clic. También se exporta un archivo .reg con lo que había antes."
        ]
        if self.sabe_predeterminado:
            reversiones.append(
                "Solo este ajuste se puede devolver a fábrica sin tocar los demás: en su fila, "
                "«Dejar como vino Windows»."
            )
        partes.append(" ".join(reversiones))
        return "\n\n".join(partes)

    def search_url(self) -> str:
        """Búsqueda en internet sobre este ajuste (para «Buscar en internet»)."""
        from urllib.parse import quote_plus

        consulta = self.search or f"{self.name} Windows {self.category_label}"
        return f"https://www.bing.com/search?q={quote_plus(consulta + ' registro')}"


CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
ADVANCED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
CURRENT = r"Software\Microsoft\Windows\CurrentVersion"

CATALOG: tuple[Setting, ...] = (
    # --- Anuncios y sugerencias -------------------------------------------
    Setting(
        id="inicio_recomendaciones",
        name="Anuncios y sugerencias en el menú Inicio",
        category=CATEGORY_ADS,
        description="Quita los bloques de «recomendado» que Windows mete en el menú Inicio.",
        why="Son anuncios de aplicaciones y servicios de la Tienda: ocupan sitio y no aportan nada al equipo.",
        values=(
            ValueSpec(ADVANCED, "Start_IrisRecommendations", 0, default=1),
            ValueSpec(CDM, "SubscribedContent-338388Enabled", 0, default=1),
        ),
        search="desactivar recomendaciones menú Inicio Windows 11 registro",
    ),
    Setting(
        id="bloqueo_anuncios",
        name="Sugerencias en la pantalla de bloqueo",
        category=CATEGORY_ADS,
        description="Deja la pantalla de bloqueo con la foto y sin «trucos» ni sugerencias encima.",
        why="Windows usa la pantalla de bloqueo para promocionar sus servicios.",
        values=(
            ValueSpec(CDM, "RotatingLockScreenOverlayEnabled", 0, default=1),
            ValueSpec(CDM, "SubscribedContent-338387Enabled", 0, default=1),
        ),
        search="quitar sugerencias pantalla de bloqueo Windows registro",
        requires_restart="se bloquea la sesión o se reinicia el equipo.",
    ),
    Setting(
        id="consejos_windows",
        name="Consejos y sugerencias de Windows",
        category=CATEGORY_ADS,
        description="Corta los avisos del tipo «prueba esta función» después de una actualización.",
        why="Aparecen encima de las ventanas y, en un equipo de trabajo, solo estorban.",
        values=(
            ValueSpec(CDM, "SubscribedContent-338389Enabled", 0, default=1),
            ValueSpec(CDM, "SoftLandingEnabled", 0, default=1),
        ),
        search="desactivar consejos de Windows sugerencias registro",
    ),
    Setting(
        id="configuracion_anuncios",
        name="Anuncios dentro de la Configuración",
        category=CATEGORY_ADS,
        description="Quita los bloques publicitarios que la aplicación de Configuración enseña arriba.",
        why="Microsoft vende ahí sus suscripciones; no hacen falta para configurar el equipo.",
        values=(ValueSpec(CDM, "SubscribedContent-310093Enabled", 0, default=1),),
        search="quitar anuncios aplicación Configuración Windows",
    ),
    Setting(
        id="explorador_avisos",
        name="Avisos del Explorador de archivos",
        category=CATEGORY_ADS,
        description="Desactiva los avisos de sincronización y promociones de OneDrive en la Vista.",
        why="Son anuncios dentro del Explorador, donde se va a trabajar con archivos.",
        values=(ValueSpec(ADVANCED, "ShowSyncProviderNotifications", 0, default=1),),
        search="quitar anuncios explorador de archivos Windows registro",
    ),
    # --- Privacidad --------------------------------------------------------
    Setting(
        id="id_publicidad",
        name="Identificador de publicidad",
        category=CATEGORY_PRIVACY,
        description="Impide que las aplicaciones usen tu identificador de publicidad para perfilarte.",
        why="Es un número único por usuario que Windows reparte a las aplicaciones de la Tienda.",
        values=(ValueSpec(rf"{CURRENT}\AdvertisingInfo", "Enabled", 0, default=1),),
        search="desactivar identificador de publicidad Windows registro",
    ),
    Setting(
        id="busqueda_web_inicio",
        name="Resultados web en la búsqueda del menú Inicio",
        category=CATEGORY_PRIVACY,
        description="La búsqueda del menú Inicio busca solo en el equipo, sin mandar lo que escribes a Bing.",
        why="Cada búsqueda sale a internet con lo que escribes, aunque busques un programa local.",
        values=(
            ValueSpec(rf"{CURRENT}\Search", "BingSearchEnabled", 0, default=1),
            ValueSpec(rf"{CURRENT}\Search", "CortanaConsent", 0, default=1),
        ),
        search="desactivar búsqueda web menú Inicio BingSearchEnabled",
        requires_restart="se reinicia el Explorador o el equipo.",
    ),
    Setting(
        id="tracker_inicio",
        name="Seguimiento de programas abiertos",
        category=CATEGORY_PRIVACY,
        description="Windows deja de llevar la cuenta de qué programas abres para «mejorar» el menú Inicio.",
        why="Es un registro de uso que no aporta nada si no quieres recomendaciones personalizadas.",
        values=(ValueSpec(ADVANCED, "Start_TrackProgs", 0, default=1),),
        search="desactivar Start_TrackProgs Windows registro privacidad",
    ),
    # --- Telemetría --------------------------------------------------------
    Setting(
        id="experiencias_personalizadas",
        name="Experiencias personalizadas con datos de diagnóstico",
        category=CATEGORY_TELEMETRY,
        description="Windows deja de usar tus datos de diagnóstico para personalizar consejos y anuncios.",
        why="Es la parte de la telemetría que se puede desactivar sin permisos de administrador.",
        values=(ValueSpec(rf"{CURRENT}\Privacy", "TailoredExperiencesWithDiagnosticDataEnabled", 0, default=1),),
        search="desactivar experiencias personalizadas datos diagnóstico Windows",
    ),
    Setting(
        id="recordatorios_feedback",
        name="Recordatorios de comentarios",
        category=CATEGORY_TELEMETRY,
        description="Windows deja de pedirte opinión cada cierto tiempo sobre lo que acabas de usar.",
        why="Son encuestas del programa de comentarios que interrumpen; el envío manual sigue disponible.",
        values=(ValueSpec(r"Software\Microsoft\Siuf\Rules", "NumberOfSIUFInPeriod", 0, default=AUSENTE),),
        search="desactivar recordatorios comentarios Windows Siuf",
    ),
    Setting(
        id="telemetria_diagtrack",
        name="Servicio de telemetría (DiagTrack) desactivado",
        category=CATEGORY_TELEMETRY,
        description="Detiene el servicio que envía datos de diagnóstico a Microsoft.",
        why="Es el servicio que más datos manda; desactivarlo es lo que hace un equipo de trabajo.",
        risk=RISK_MEDIUM,
        needs_admin=True,
        values=(
            ValueSpec(
                r"SYSTEM\CurrentControlSet\Services\DiagTrack",
                "Start",
                4,
                hive=HIVE_MACHINE,
                default=2,
            ),
        ),
        note="algunas herramientas de diagnóstico de empresa (o el Visor de sucesos de Microsoft) "
        "dejan de enviar sus datos; Windows Update sigue funcionando igual.",
        search="desactivar DiagTrack Connected User Experiences telemetry servicio",
        requires_restart="se reinicia el equipo.",
    ),
    Setting(
        id="allowtelemetry_politica",
        name="Telemetría al mínimo por política",
        category=CATEGORY_TELEMETRY,
        description="Fija la telemetría del sistema en «Seguridad» (la opción más baja que permite Windows).",
        why="Es el ajuste que las empresas aplican por directiva; deja Windows Update intacto.",
        risk=RISK_MEDIUM,
        needs_admin=True,
        values=(
            ValueSpec(
                r"SOFTWARE\Policies\Microsoft\Windows\DataCollection",
                "AllowTelemetry",
                0,
                hive=HIVE_MACHINE,
                default=AUSENTE,
            ),
        ),
        note="en Windows Home la opción mínima que admite el sistema es «Básico» (1); poniendo 0 "
        "Windows lo interpreta como el mínimo que permita esa edición.",
        search="AllowTelemetry política DataCollection Windows",
    ),
    # --- Interfaz ----------------------------------------------------------
    Setting(
        id="menu_contextual_clasico",
        name="Menú contextual clásico (Windows 11)",
        category=CATEGORY_INTERFACE,
        description="Devuelve el menú del botón derecho completo, sin el «Mostrar más opciones».",
        why="El menú nuevo esconde la mitad de las acciones detrás de un clic extra.",
        values=(
            ValueSpec(
                r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32",
                "",
                "",
                kind=KIND_STRING,
                default=AUSENTE,
            ),
        ),
        # La clave se crea solo para desactivar el menú nuevo: al deshacer el ajuste hay
        # que quitarla (de dentro hacia fuera) o el menú seguiría siendo el clásico.
        created_keys=(
            KeyRef(r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"),
            KeyRef(r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"),
        ),
        search="menú contextual clásico Windows 11 86ca1aa0 registro",
        requires_restart="se reinicia el Explorador (o se cierra la sesión).",
    ),
    Setting(
        id="noticias_barra",
        name="Noticias e intereses en la barra de tareas",
        category=CATEGORY_INTERFACE,
        description="Quita el bloque del tiempo y las noticias que Windows pone a la izquierda.",
        why="Además de ocupar sitio, carga contenido de internet en la barra de tareas.",
        # Este se queda sin valor de fábrica a propósito: Windows no documenta qué significa
        # cada número de este valor y cambia entre ediciones, así que no se puede afirmar
        # cuál trae. Sin ese dato, el ajuste se enseña como recomendado, sin distintivo y sin
        # ofrecer devolverlo a fábrica: no se inventa.
        values=(ValueSpec(rf"{CURRENT}\Feeds", "ShellFeedsTaskbarViewMode", 2),),
        search="quitar noticias e intereses barra de tareas Windows 11",
        requires_restart="se reinicia el Explorador (o se cierra la sesión).",
    ),
    # --- Rendimiento -------------------------------------------------------
    Setting(
        id="apps_segundo_plano",
        name="Aplicaciones de la Tienda en segundo plano",
        category=CATEGORY_PERFORMANCE,
        description="Suspende las aplicaciones de la Tienda que quedan trabajando en segundo plano.",
        why="Son las que siguen gastando batería, red y memoria sin que las estés usando.",
        risk=RISK_MEDIUM,
        values=(ValueSpec(rf"{CURRENT}\BackgroundAccessApplications", "GlobalUserDisabled", 1, default=AUSENTE),),
        note="esas aplicaciones no podrán recibir avisos ni sincronizar hasta que las abras.",
        search="desactivar aplicaciones en segundo plano Windows GlobalUserDisabled",
        requires_restart="se cierra la sesión o se reinicia el equipo.",
    ),
    Setting(
        id="game_dvr",
        name="Barra de juegos y grabación en segundo plano",
        category=CATEGORY_PERFORMANCE,
        description="Desactiva la grabación de pantalla de Xbox Game Bar, que se queda escuchando.",
        why="Game DVR graba en segundo plano por si quieres guardar un clip; en un equipo normal solo gasta.",
        values=(
            ValueSpec(r"System\GameConfigStore", "GameDVR_Enabled", 0, default=1),
            ValueSpec(rf"{CURRENT}\GameDVR", "AppCaptureEnabled", 0, default=1),
        ),
        note="se pierden los atajos para grabar clips (Win+Alt+R) de la Barra de juegos.",
        search="desactivar Game DVR grabación en segundo plano Windows",
        requires_restart="se cierra la sesión o se reinicia el equipo.",
    ),
    Setting(
        id="transparencia",
        name="Efectos de transparencia",
        category=CATEGORY_PERFORMANCE,
        description="Quita las transparencias del menú Inicio, la barra de tareas y las ventanas.",
        why="En equipos con gráfica integrada o batería, las transparencias cuestan y se notan al arrastrar.",
        values=(ValueSpec(rf"{CURRENT}\Themes\Personalize", "EnableTransparency", 0, default=1),),
        note="la interfaz se ve plana; es el cambio más visible de la lista.",
        search="desactivar efectos de transparencia Windows registro",
    ),
)

BY_ID: dict[str, Setting] = {setting.id: setting for setting in CATALOG}

# Ajustes que traen los plugins: se añaden al catálogo en marcha, sin tocar este archivo.
_extra_catalog: list[Setting] = []

# Permiso de aplicación que puede instalar otro módulo (lo usan los plugins: un ajuste de
# un plugin que no se ha revisado no se aplica). Se guarda como función para que este módulo
# no tenga que conocer a quien lo pregunta.
_apply_guard: "Callable[[Setting], str] | None" = None


def catalog() -> tuple[Setting, ...]:
    """El catálogo completo: los ajustes del programa y los que aportan los plugins."""
    return tuple(CATALOG) + tuple(_extra_catalog)


def set_apply_guard(guard: "Callable[[Setting], str] | None") -> None:
    """Fija la comprobación extra que se hace antes de aplicar un ajuste."""
    global _apply_guard
    _apply_guard = guard


def register_settings(settings: Iterable[Setting]) -> int:
    """Añade ajustes al catálogo (los de un plugin) y devuelve cuántos entraron.

    Se ignora el que repita identificador: el catálogo no puede tener dos ajustes con el
    mismo nombre, porque el apunte de «Deshacer» solo guarda ese identificador.
    """
    nuevos = 0
    for setting in settings:
        if setting.id in BY_ID:
            continue
        _extra_catalog.append(setting)
        BY_ID[setting.id] = setting
        nuevos += 1
    return nuevos


def forget_plugin_settings() -> None:
    """Vacía los ajustes de plugins (se vuelven a leer al recargar la carpeta)."""
    for setting in _extra_catalog:
        BY_ID.pop(setting.id, None)
    _extra_catalog.clear()


# ---------------------------------------------------------------------------
# Registro del sistema
# ---------------------------------------------------------------------------


def is_elevated() -> bool:
    """¿La aplicación va como administrador? (lo decide Windows, no una preferencia)."""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[union-attr]
    except (AttributeError, OSError):
        return False


def test_root() -> str:
    """Raíz de registro que se usa cuando se está probando (vacío en uso normal)."""
    return str(os.environ.get(TEST_ROOT_ENV) or "").strip()


def _hive_handle(hive: str):
    return winreg.HKEY_LOCAL_MACHINE if hive == HIVE_MACHINE else winreg.HKEY_CURRENT_USER


def full_registry_path(spec: ValueSpec) -> str:
    """Ruta real del valor: con la raíz de pruebas por delante, si la hay."""
    raiz = test_root()
    # La raíz de pruebas solo se aplica a HKCU: en las pruebas no se toca HKLM nunca.
    if raiz and spec.hive == HIVE_USER:
        return rf"{raiz}\{spec.path}" if spec.path else raiz
    return spec.path


def key_exists(spec: ValueSpec) -> bool:
    """¿Existe la clave donde vive este valor?"""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(_hive_handle(spec.hive), full_registry_path(spec), 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def read_value(spec: ValueSpec) -> tuple[bool, Any]:
    """Lee el valor: `(existe, valor)`. Nunca lanza por una clave que no esté."""
    if not IS_WINDOWS:
        return False, None
    try:
        with winreg.OpenKey(_hive_handle(spec.hive), full_registry_path(spec), 0, winreg.KEY_READ) as clave:
            valor, _tipo = winreg.QueryValueEx(clave, spec.name)
            return True, valor
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        logs.warning(f"No se pudo leer {spec.describe()}", exc, name="ajustes")
        return False, None


def remove_key_if_empty(key: KeyRef) -> bool:
    """Borra una clave si está vacía (si tiene algo dentro, no se toca).

    `DeleteKey` falla con una clave con subclaves o valores, y eso es justo lo que se
    quiere: un ajuste solo debe quitar la clave que creó él.
    """
    if not IS_WINDOWS:
        return False
    raiz = test_root()
    ruta = f"{raiz}\\{key.path}" if raiz and key.hive == HIVE_USER else key.path
    try:
        winreg.DeleteKey(_hive_handle(key.hive), ruta)
        return True
    except OSError:
        return False


def write_value(spec: ValueSpec, value: Any) -> None:
    """Escribe el valor (creando la clave si hace falta)."""
    if not IS_WINDOWS:
        raise OSError("El registro de Windows no está disponible en este sistema.")
    with winreg.CreateKeyEx(
        _hive_handle(spec.hive), full_registry_path(spec), 0, winreg.KEY_SET_VALUE | winreg.KEY_WRITE
    ) as clave:
        if spec.kind == KIND_DELETE:
            try:
                winreg.DeleteValue(clave, spec.name)
            except FileNotFoundError:
                pass
            return
        tipo = KIND_TO_REGISTRY.get(spec.kind, KIND_TO_REGISTRY[KIND_DWORD])
        winreg.SetValueEx(clave, spec.name, 0, tipo, value)


def delete_value(spec: ValueSpec) -> bool:
    """Borra el valor. Devuelve si había algo que borrar."""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(_hive_handle(spec.hive), full_registry_path(spec), 0, winreg.KEY_SET_VALUE) as clave:
            winreg.DeleteValue(clave, spec.name)
            return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logs.warning(f"No se pudo borrar {spec.describe()}", exc, name="ajustes")
        return False


def _same_value(current: Any, recommended: Any) -> bool:
    """¿El valor actual ya es el recomendado? (el tipo puede no coincidir)"""
    if isinstance(recommended, int):
        try:
            return int(current) == int(recommended)
        except (TypeError, ValueError):
            return False
    return str(current) == str(recommended)


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------


@dataclass
class SettingState:
    """Lo que se sabe de un ajuste después de analizarlo."""

    setting: Setting
    status: str
    detail: str = ""
    current: list[dict[str, Any]] = field(default_factory=list)
    # De dónde viene lo que hay puesto: vacío (no se sabe), `predeterminado` (el valor de
    # fábrica de Windows) o `personalizado` (ni el de fábrica ni el recomendado).
    origin: str = ""
    # ¿Merece la pena ofrecer «dejar como vino Windows»? Solo cuando se conocen todos los
    # valores de fábrica y algo está fuera de ellos (si no, no habría nada que devolver).
    restorable: bool = False

    @property
    def id(self) -> str:
        return self.setting.id

    @property
    def pending(self) -> bool:
        """¿Queda algo por hacer aquí? Sí tanto si está recomendado como si alguien lo cambió."""
        return self.status in (STATUS_PENDING, STATUS_CUSTOM)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def origin_label(self) -> str:
        """El distintivo del ajuste: «Predeterminado», «Personalizado» o nada."""
        return ORIGIN_LABELS.get(self.origin, "")

    @property
    def available(self) -> bool:
        return self.status != STATUS_UNAVAILABLE

    @property
    def can_restore_default(self) -> bool:
        return self.restorable and self.status != STATUS_UNAVAILABLE


def _unavailable_reason(setting: Setting) -> str:
    if not IS_WINDOWS:
        return "este sistema no es Windows, así que no hay registro que ajustar."
    if setting.needs_admin and not is_elevated():
        return "hace falta ejecutar PC Toolkit como administrador para tocar esta clave."
    return "Windows no tiene esta parte en este equipo."


def analyse_one(setting: Setting) -> SettingState:
    """Mira el estado de un ajuste sin tocar nada."""
    if not IS_WINDOWS:
        return SettingState(setting, STATUS_UNAVAILABLE, _unavailable_reason(setting))
    if _apply_guard is not None:
        # Un ajuste de un plugin sin revisar se enseña, pero como no disponible: así se ve
        # qué trae el plugin sin que se pueda aplicar por descuido.
        motivo = _apply_guard(setting)
        if motivo:
            return SettingState(setting, STATUS_UNAVAILABLE, motivo)
    if setting.needs_admin:
        # Sin permisos no se lee ni se escribe: leer HKLM se puede, pero aplicarlo no, y
        # enseñarlo como «recomendado» para luego fallar al aplicarlo sería engañoso.
        if not is_elevated():
            return SettingState(setting, STATUS_UNAVAILABLE, _unavailable_reason(setting))
        if not key_exists(setting.values[0]):
            return SettingState(setting, STATUS_UNAVAILABLE, _unavailable_reason(setting))

    pendientes: list[str] = []
    actuales: list[dict[str, Any]] = []
    existe_clave = False
    # Por cada valor: si ya está como se recomienda y si está como vino de fábrica (None
    # cuando no se sabe). Con las dos listas se decide el estado y el distintivo al final.
    cumplen: list[bool] = []
    de_fabrica: list[bool | None] = []
    for spec in setting.values:
        existe, valor = read_value(spec)
        existe_clave = existe_clave or existe
        actuales.append(
            {"clave": spec.describe(), "existe": existe, "valor": valor, "espec": spec.to_dict()}
        )
        if spec.kind == KIND_DELETE:
            cumple = not existe
            if existe:
                pendientes.append(f"{spec.describe()} existe y no debería")
        else:
            cumple = bool(existe) and _same_value(valor, spec.recommended)
            if not cumple:
                pendientes.append(f"{spec.describe()} vale {valor!r} y se recomienda {spec.recommended!r}")
        cumplen.append(cumple)
        de_fabrica.append(spec.es_de_fabrica(existe, valor))

    if not pendientes:
        return SettingState(
            setting, STATUS_OPTIMIZED, "todos sus valores ya están como se recomienda", actuales
        )
    detalle = pendientes[0]
    if len(pendientes) > 1:
        detalle += f" (y {len(pendientes) - 1} valor(es) más)"
    aviso = "" if existe_clave else " (la clave aún no existe: se creará al aplicarlo)"

    if any(dato is None for dato in de_fabrica):
        # No se sabe qué trae Windows de fábrica en alguno de sus valores: se enseña como
        # siempre, sin distintivo y sin ofrecer devolverlo a fábrica.
        return SettingState(setting, STATUS_PENDING, detalle + aviso, actuales)

    restorable = any(not dato for dato in de_fabrica)
    personalizado = any(
        not cumple and not dato for cumple, dato in zip(cumplen, de_fabrica)
    )
    if not personalizado:
        # Lo que falta está como vino Windows: se distingue de un ajuste que alguien cambió.
        return SettingState(
            setting,
            STATUS_PENDING,
            detalle + aviso + " (ahora está en el valor de fábrica)",
            actuales,
            origin=ORIGIN_DEFAULT,
            restorable=restorable,
        )
    return SettingState(
        setting,
        STATUS_CUSTOM,
        detalle
        + aviso
        + "; no coincide con el valor de fábrica, así que el análisis no puede saber por qué "
        "está así (lo cambió otro programa o se puso a mano)",
        actuales,
        origin=ORIGIN_CUSTOM,
        restorable=restorable,
    )


def analyse(settings: Iterable[Setting] | None = None) -> list[SettingState]:
    """Analiza el catálogo entero (o el que se pase) sin cambiar nada."""
    estados = [analyse_one(setting) for setting in (settings if settings is not None else catalog())]
    registrar_analisis(estados)
    return estados


def summary(states: Sequence[SettingState]) -> dict[str, Any]:
    """Cifras para la cabecera de la página."""
    return {
        "total": len(states),
        "recomendados": sum(1 for estado in states if estado.status == STATUS_PENDING),
        "optimizados": sum(1 for estado in states if estado.status == STATUS_OPTIMIZED),
        "personalizados": sum(1 for estado in states if estado.status == STATUS_CUSTOM),
        "no_disponibles": sum(1 for estado in states if estado.status == STATUS_UNAVAILABLE),
        "con_admin": sum(1 for estado in states if estado.setting.needs_admin),
        "riesgo_medio_alto": sum(
            1 for estado in states if estado.setting.risk in (RISK_MEDIUM, RISK_HIGH)
        ),
    }


def grouped(states: Sequence[SettingState]) -> list[tuple[str, str, list[SettingState]]]:
    """Agrupa por categoría en el orden del catálogo: `(id, nombre, estados)`."""
    salida: list[tuple[str, str, list[SettingState]]] = []
    for clave in CATEGORY_ORDER:
        del_grupo = [estado for estado in states if estado.setting.category == clave]
        if del_grupo:
            salida.append((clave, CATEGORIES.get(clave, (clave, ""))[0], del_grupo))
    return salida


# ---------------------------------------------------------------------------
# Aplicar y revertir
# ---------------------------------------------------------------------------


def _backup_folder(folder: Path | None = None) -> Path:
    destino = Path(folder) if folder else data_dir() / "respaldo_ajustes"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _reg_line(spec: ValueSpec, existe: bool, valor: Any) -> list[str]:
    """Líneas de un valor en formato `.reg` (para poder restaurar a mano)."""
    if not existe:
        return [f'"{spec.name}"=-'] if spec.name else []
    if spec.kind == KIND_STRING or isinstance(valor, str):
        texto = str(valor).replace("\\", "\\\\").replace('"', '\\"')
        return [f'"{spec.name}"="{texto}"']
    try:
        return [f'"{spec.name}"=dword:{int(valor) & 0xFFFFFFFF:08x}']
    except (TypeError, ValueError):
        texto = str(valor).replace("\\", "\\\\").replace('"', '\\"')
        return [f'"{spec.name}"="{texto}"']


def export_reg_backup(
    setting: Setting,
    *,
    folder: Path | None = None,
    when: float | None = None,
    valores: Sequence[dict[str, Any]] | None = None,
) -> Path | None:
    """Guarda un `.reg` con los valores que había antes de aplicar un ajuste.

    El archivo se puede abrir con doble clic para devolver las claves a como estaban, sin
    depender de PC Toolkit. Se escribe en UTF-16LE con BOM, que es como los lee el Editor
    del Registro.
    """
    if not IS_WINDOWS:
        return None
    lecturas = list(valores) if valores is not None else []
    if not lecturas:
        for spec in setting.values:
            existe, valor = read_value(spec)
            lecturas.append(
                {"clave": spec.describe(), "existe": existe, "valor": valor, "espec": spec.to_dict()}
            )
    try:
        carpeta = _backup_folder(folder)
        momento = time.strftime("%Y%m%d-%H%M%S", time.localtime(when or time.time()))
        destino = carpeta / f"{setting.id}-{momento}.reg"
        lineas = [
            "Windows Registry Editor Version 5.00",
            "",
            f"; Respaldo de «{setting.name}» hecho por {APP_NAME} {APP_VERSION}",
            f"; Antes de aplicar el ajuste, por si hay que volver atrás a mano.",
            "",
        ]
        for lectura in lecturas:
            spec = ValueSpec.from_dict(lectura.get("espec") or {})
            if spec is None:
                continue
            lineas.append(f"[{HIVE_LABELS.get(spec.hive, spec.hive)}\\{full_registry_path(spec)}]")
            lineas.extend(_reg_line(spec, bool(lectura.get("existe")), lectura.get("valor")))
            lineas.append("")
        destino.write_text("\r\n".join(lineas), encoding="utf-16")
        return destino
    except OSError as exc:
        logs.warning(f"No se pudo guardar el respaldo de «{setting.name}»", exc, name="ajustes")
        return None


def can_apply(setting: Setting) -> tuple[bool, str]:
    """¿Se puede aplicar ahora mismo? (permisos y sistema)"""
    if not IS_WINDOWS:
        return False, "Este sistema no es Windows: no hay registro que ajustar."
    if setting.needs_admin and not is_elevated():
        return (
            False,
            f"«{setting.name}» toca HKEY_LOCAL_MACHINE y necesita permisos de administrador: "
            "abre PC Toolkit como administrador para aplicarlo.",
        )
    if _apply_guard is not None:
        # Lo que añade un módulo de fuera (los plugins): sin su visto bueno, no se aplica.
        motivo = _apply_guard(setting)
        if motivo:
            return False, motivo
    return True, ""


def apply_setting(
    setting: Setting,
    *,
    store: undo.UndoStore | None = None,
    origin: str = undo.ORIGIN_MANUAL,
) -> tuple[bool, str]:
    """Aplica un ajuste y lo anota en el historial de deshacer.

    Se guarda el valor anterior de cada clave **y** un `.reg` con lo que había, antes de
    escribir nada. Si algo falla a mitad, se devuelve todo a como estaba: no se deja el
    equipo con medio ajuste aplicado.
    """
    ok, motivo = can_apply(setting)
    if not ok:
        registrar_cambio("bloqueado", setting.name, motivo, [setting.id])
        return False, motivo

    antes: list[dict[str, Any]] = []
    for spec in setting.values:
        existe, valor = read_value(spec)
        antes.append(
            {"clave": spec.describe(), "existe": existe, "valor": valor, "espec": spec.to_dict()}
        )
    respaldo = export_reg_backup(setting, valores=antes)

    hechos: list[ValueSpec] = []
    try:
        for spec, lectura in zip(setting.values, antes):
            if spec.kind == KIND_DELETE:
                delete_value(spec)
            else:
                write_value(spec, spec.recommended)
            hechos.append(spec)
    except OSError as exc:
        # Se deshace lo que se había hecho: mejor sin el ajuste que a medias.
        for spec, lectura in zip(setting.values, antes):
            if spec not in hechos:
                continue
            try:
                if lectura["existe"]:
                    write_value(spec, lectura["valor"])
                else:
                    delete_value(spec)
            except OSError:
                continue
        mensaje = f"No se pudo aplicar «{setting.name}»: {exc}"
        logs.warning(mensaje, exc, name="ajustes")
        registrar_cambio("error", setting.name, mensaje, [setting.id])
        return False, mensaje

    if store is not None:
        store.add(
            kind=KIND_AJUSTES,
            title=setting.name,
            before=antes[0]["clave"] if antes else setting.name,
            after="Ajuste recomendado aplicado",
            data={
                "ajuste": setting.id,
                "valores": antes,
                "claves": [clave.to_dict() for clave in setting.created_keys],
                "respaldo": str(respaldo) if respaldo else "",
                "riesgo": setting.risk,
            },
            origin=origin,
        )
    detalle = f"«{setting.name}» aplicado"
    if respaldo is not None:
        detalle += f"; respaldo en {respaldo.name}"
    registrar_cambio("aplicado", setting.name, detalle, [setting.id], respaldo)
    return True, detalle


def can_restore_default(setting: Setting) -> tuple[bool, str]:
    """¿Se puede devolver **este** ajuste a como vino Windows?"""
    if not IS_WINDOWS:
        return False, "Este sistema no es Windows: no hay registro que tocar."
    if not setting.values:
        return False, f"«{setting.name}» no dice cuáles son sus valores de fábrica."
    desconocidos = [spec for spec in setting.values if not spec.sabe_default]
    if desconocidos:
        return (
            False,
            f"No se sabe qué trae Windows de fábrica en «{setting.name}» "
            f"({desconocidos[0].describe()}): devolverlo podría dejarlo peor que como está.",
        )
    ok, motivo = can_apply(setting)
    if not ok:
        return False, motivo
    return True, ""


def restore_default(
    setting: Setting,
    *,
    store: undo.UndoStore | None = None,
    origin: str = undo.ORIGIN_MANUAL,
) -> tuple[bool, str]:
    """Deja un solo ajuste como venía de fábrica, sin tocar los demás.

    Escribe el valor de fábrica de cada clave —o la borra, cuando Windows no trae ese valor—
    y, antes de tocar nada, guarda el respaldo `.reg` y el valor que había, igual que al
    aplicar: se puede deshacer con un clic y queda anotado en el registro de cambios.

    Las claves que el propio ajuste creó (el menú contextual clásico funciona por la mera
    existencia de la suya) se quitan al final: devolver los *valores* a fábrica sin quitar la
    clave dejaría el ajuste puesto igualmente.
    """
    ok, motivo = can_restore_default(setting)
    if not ok:
        registrar_cambio("bloqueado", setting.name, motivo, [setting.id])
        return False, motivo

    antes: list[dict[str, Any]] = []
    for spec in setting.values:
        existe, valor = read_value(spec)
        antes.append(
            {"clave": spec.describe(), "existe": existe, "valor": valor, "espec": spec.to_dict()}
        )
    respaldo = export_reg_backup(setting, valores=antes)

    hechos: list[ValueSpec] = []
    try:
        for spec in setting.values:
            spec.write_default()
            hechos.append(spec)
    except OSError as exc:
        # Igual que al aplicar: se devuelve lo escrito y no se deja el ajuste a medias.
        for spec, lectura in zip(setting.values, antes):
            if spec not in hechos:
                continue
            try:
                if lectura["existe"]:
                    write_value(spec, lectura["valor"])
                else:
                    delete_value(spec)
            except OSError:
                continue
        mensaje = f"No se pudo devolver «{setting.name}» a como vino Windows: {exc}"
        logs.warning(mensaje, exc, name="ajustes")
        registrar_cambio("error", setting.name, mensaje, [setting.id])
        return False, mensaje

    for clave in setting.created_keys:
        remove_key_if_empty(clave)

    if store is not None:
        store.add(
            kind=KIND_AJUSTES,
            title=f"{setting.name} (como vino Windows)",
            before=antes[0]["clave"] if antes else setting.name,
            after="Valor de fábrica devuelto",
            data={
                "ajuste": setting.id,
                "valores": antes,
                "claves": [clave.to_dict() for clave in setting.created_keys],
                "respaldo": str(respaldo) if respaldo else "",
                "riesgo": setting.risk,
            },
            origin=origin,
        )
    detalle = f"«{setting.name}»: devuelto el valor de fábrica"
    if respaldo is not None:
        detalle += f"; respaldo en {respaldo.name}"
    registrar_cambio("predeterminado", setting.name, detalle, [setting.id], respaldo)
    return True, detalle


@dataclass
class ApplyResult:
    """Lo que pasó al aplicar varios ajustes: para contarlo en la ventana."""

    applied: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def message(self) -> str:
        partes = []
        if self.applied:
            partes.append(f"{len(self.applied)} ajuste(s) aplicado(s)")
        if self.failed:
            partes.append("; ".join(f"«{nombre}»: {motivo}" for nombre, motivo in self.failed))
        if self.skipped:
            partes.append(f"{len(self.skipped)} sin tocar")
        return ". ".join(partes) or "No había nada que aplicar."


def apply_settings(
    settings: Sequence[Setting],
    *,
    store: undo.UndoStore | None = None,
    origin: str = undo.ORIGIN_MANUAL,
) -> ApplyResult:
    """Aplica una lista de ajustes, uno detrás de otro, sin parar por un fallo."""
    resultado = ApplyResult()
    for setting in settings:
        ok, mensaje = apply_setting(setting, store=store, origin=origin)
        if ok:
            resultado.applied.append(setting.name)
        else:
            resultado.failed.append((setting.name, mensaje))
    return resultado


def revert(action: undo.UndoAction, store: undo.UndoStore) -> tuple[bool, str]:
    """Devuelve un ajuste a como estaba (lo llama el panel de Deshacer)."""
    payload = action.data if isinstance(action.data, dict) else {}
    setting = BY_ID.get(str(payload.get("ajuste") or ""))
    lecturas = payload.get("valores")
    if not isinstance(lecturas, list) or not lecturas:
        return False, "El apunte no guarda qué valores había antes, así que no se puede deshacer."
    fallos: list[str] = []
    for lectura in lecturas:
        if not isinstance(lectura, dict):
            continue
        spec = ValueSpec.from_dict(lectura.get("espec") or {})
        if spec is None:
            continue
        try:
            if lectura.get("existe"):
                write_value(spec, lectura.get("valor"))
            else:
                delete_value(spec)
        except OSError as exc:
            fallos.append(f"{spec.describe()}: {exc}")
    # Y las claves que el ajuste hubiera creado: si no se quitan, el ajuste sigue puesto
    # aunque sus valores hayan vuelto a como estaban (el del menú contextual clásico
    # funciona precisamente por la existencia de la clave).
    claves = payload.get("claves")
    if not isinstance(claves, list):
        claves = [clave.to_dict() for clave in (setting.created_keys if setting else ())]
    for dato in claves:
        key = KeyRef.from_dict(dato) if isinstance(dato, dict) else None
        if key is not None:
            remove_key_if_empty(key)
    if fallos:
        mensaje = "No se pudo devolver todo a como estaba: " + "; ".join(fallos[:2])
        registrar_cambio("error", action.title, mensaje, [setting.id] if setting else [])
        return False, mensaje
    nombre = setting.name if setting else action.title
    registrar_cambio("revertido", nombre, f"«{nombre}» vuelve a como estaba", [setting.id] if setting else [])
    return True, f"«{nombre}» vuelve a como estaba antes de aplicarlo."


# El panel de Deshacer de la aplicación funciona con este registro: al importar este
# módulo, sus ajustes pasan a poder deshacerse desde donde se deshacen los demás.
undo.register(KIND_AJUSTES, revert)


# ---------------------------------------------------------------------------
# Registro de lo que se hizo (idea 6: registro legible y «copiar resumen»)
# ---------------------------------------------------------------------------


def changes_file() -> Path:
    return data_dir() / "ajustes_windows.jsonl"


LIMITE_REGISTRO = 400


def registrar_cambio(
    tipo: str,
    titulo: str,
    detalle: str,
    ajustes: Sequence[str] = (),
    respaldo: Path | None = None,
    when: float | None = None,
) -> dict[str, Any] | None:
    """Anota una línea del registro de cambios de ajustes (JSONL, una por línea)."""
    entrada = {
        "at": float(when if when is not None else time.time()),
        "tipo": tipo,
        "titulo": titulo,
        "detalle": detalle,
        "ajustes": list(ajustes),
        "version": APP_VERSION,
    }
    if respaldo is not None:
        entrada["respaldo"] = str(respaldo)
    try:
        destino = changes_file()
        lineas: list[str] = []
        if destino.exists():
            lineas = [linea for linea in destino.read_text(encoding="utf-8", errors="replace").splitlines() if linea.strip()]
        lineas.append(json.dumps(entrada, ensure_ascii=False))
        destino.write_text("\n".join(lineas[-LIMITE_REGISTRO:]) + "\n", encoding="utf-8")
    except OSError as exc:
        logs.warning("No se pudo anotar el cambio de ajustes", exc, name="ajustes")
        return None
    return entrada


def registrar_analisis(states: Sequence[SettingState]) -> None:
    """Anota el resultado de un análisis (sin listar los que ya están optimizados)."""
    pendientes = [estado.setting.id for estado in states if estado.status == STATUS_PENDING]
    registrar_cambio(
        "analisis",
        "Análisis de ajustes de Windows",
        f"{len(states)} ajuste(s) analizado(s): {len(pendientes)} por aplicar",
        pendientes,
    )


def recent_changes(limit: int = 12) -> list[dict[str, Any]]:
    """Últimas entradas del registro de cambios, de la más reciente a la más antigua."""
    destino = changes_file()
    if not destino.exists():
        return []
    entradas: list[dict[str, Any]] = []
    try:
        for linea in destino.read_text(encoding="utf-8", errors="replace").splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                datos = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if isinstance(datos, dict):
                entradas.append(datos)
    except OSError:
        return []
    return list(reversed(entradas[-max(1, limit) :]))


def changes_for_report(limit: int = 40) -> list[dict[str, Any]]:
    """Los últimos cambios del registro, listos para el informe (del más antiguo al más nuevo).

    El registro guarda la fecha en segundos desde 1970 (que es lo que hace falta para
    ordenarlo); un informe se lee, así que aquí se convierte a la fecha con hora. Los ajustes
    se unen en una línea: en el informe son una columna, no una lista de tres pantallas.
    """
    filas: list[dict[str, Any]] = []
    for entrada in recent_changes(limit):
        filas.append(
            {
                "cuando": time.strftime("%d/%m/%Y %H:%M", time.localtime(float(entrada.get("at") or 0))),
                "tipo": str(entrada.get("tipo") or ""),
                "titulo": str(entrada.get("titulo") or ""),
                "detalle": str(entrada.get("detalle") or ""),
                "ajustes": ", ".join(str(identificador) for identificador in (entrada.get("ajustes") or [])),
            }
        )
    filas.reverse()
    return filas


def summary_text(limit: int = 12) -> str:
    """Resumen legible para copiar y pegar cuando se pide ayuda en un foro."""
    entradas = recent_changes(limit)
    lineas = [
        f"{APP_NAME} {APP_VERSION} · cambios en los ajustes de Windows",
        time.strftime("%d/%m/%Y %H:%M"),
        "",
    ]
    if not entradas:
        lineas.append("Todavía no se ha analizado ni aplicado ningún ajuste.")
        return "\n".join(lineas)
    for entrada in entradas:
        momento = time.strftime("%d/%m %H:%M", time.localtime(float(entrada.get("at") or 0)))
        lineas.append(f"[{momento}] {entrada.get('tipo', '?')}: {entrada.get('detalle', '')}")
    estados = [analyse_one(setting) for setting in catalog()]
    cifras = summary(estados)
    lineas += [
        "",
        f"Estado ahora: {cifras['recomendados']} recomendado(s), "
        f"{cifras['personalizados']} personalizado(s), "
        f"{cifras['optimizados']} ya optimizado(s), {cifras['no_disponibles']} no disponible(s).",
    ]
    return "\n".join(lineas)


def create_restore_point(description: str = "") -> tuple[bool, str]:
    """Pide a Windows un punto de restauración (necesita administrador).

    Se usa `Checkpoint-Computer` de PowerShell, que es la vía que documenta Microsoft. Sin
    permisos de administrador devuelve el motivo en vez de intentarlo: no tiene sentido
    pedir una ventana de UAC desde una función del núcleo.
    """
    if not IS_WINDOWS:
        return False, "Los puntos de restauración son de Windows."
    if not is_elevated():
        return False, "Hace falta ejecutar PC Toolkit como administrador para crear un punto de restauración."
    import subprocess

    texto = description or f"Antes de aplicar ajustes con {APP_NAME}"
    try:
        resultado = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"Checkpoint-Computer -Description '{texto}' -RestorePointType 'MODIFY_SETTINGS'",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"No se pudo crear el punto de restauración: {exc}"
    if resultado.returncode != 0:
        detalle = (resultado.stderr or resultado.stdout or "").strip()[:200]
        return False, f"Windows no pudo crear el punto de restauración: {detalle}"
    registrar_cambio("punto_restauracion", "Punto de restauración", texto, [])
    return True, "Punto de restauración creado."
