"""Gestor de aplicaciones preinstaladas: ver qué hay y quitarlo **solo para este usuario**.

Windows 10/11 trae decenas de aplicaciones de la tienda que nadie pidió. Aquí se listan con
su editor, su versión y lo que ocupan en disco, se marcan las que son una pieza del sistema
(esas nunca se pueden quitar) y se quitan las demás **con el mismo `Remove-AppxPackage` que
usa el propio Windows** para el usuario que tiene la sesión abierta: no se toca la instalación
de los demás usuarios ni hace falta ser administrador.

Tres reglas que no se saltan:

* **Nunca se quita a la ligera.** La lista blanca (`protected_reason`) cubre la tienda, el
  instalador de paquetes, los marcos de trabajo, los componentes del shell y las extensiones
  de imagen y vídeo. Sin ellos, Windows deja de poder instalar o reparar aplicaciones.
* **Todo se anota para poder volver atrás.** Antes de quitar nada se guarda el paquete exacto
  y su carpeta, y el cambio entra en el historial de «Deshacer» como cualquier otro.
* **Nada se ejecuta a ciegas.** Comprobar si un paquete se puede quitar es cálculo puro;
  PowerShell solo se lanza cuando el usuario confirma la lista que ha revisado.

Idea inspirada en CrapFixer (builtbybel, licencia MIT) y reimplementada para esta
arquitectura; ver el apartado de créditos del README.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import logs, undo
from .config import APP_NAME, APP_VERSION, format_bytes
from . import native
from .tools import powershell_environment

try:  # pragma: no cover - fuera de Windows no hay paquetes que quitar
    import winreg  # noqa: F401  (solo para saber si estamos en Windows)

    IS_WINDOWS = True
except ImportError:  # pragma: no cover
    IS_WINDOWS = False

# Tipo con el que se anotan en el historial de deshacer.
KIND_APLICACIONES = "aplicaciones"

# --- Riesgos (los mismos nombres que en los ajustes de Windows) -------------
RISK_LOW = "bajo"
RISK_MEDIUM = "medio"
RISK_HIGH = "alto"
RISK_LABELS = {
    RISK_LOW: "Riesgo bajo",
    RISK_MEDIUM: "Riesgo medio",
    RISK_HIGH: "Riesgo alto",
}

# --- Lista blanca ----------------------------------------------------------
# Prefijos: familias enteras que son dependencias de otras aplicaciones. Quitar una de
# estas deja a las demás sin poder arrancar o sin poder actualizarse.
PROTECTED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("microsoft.vclibs", "es la biblioteca de C++ que usan las aplicaciones de la tienda"),
    ("microsoft.net.native", "es el marco de .NET que usan las aplicaciones de la tienda"),
    ("microsoft.ui.xaml", "es el marco de interfaz que usan las aplicaciones de la tienda"),
    ("microsoft.windowsappruntime", "es el tiempo de ejecución de las aplicaciones modernas"),
    ("microsoft.services.store.engagement", "es lo que mantiene al día las aplicaciones de la tienda"),
    ("microsoftwindows.client", "es parte del shell de Windows 11"),
    ("microsoft.windowsappinstaller", "es el instalador de paquetes de Windows"),
    ("microsoft.storepurchaseapp", "es la pieza que termina las compras de la tienda"),
    ("microsoft.winget", "es el administrador de paquetes de Windows"),
)

# Nombres exactos: piezas del sistema que Windows necesita para arrancar el escritorio.
PROTECTED_EXACT: dict[str, str] = {
    "microsoft.windowsstore": "es la tienda: sin ella no se puede volver a instalar nada",
    "microsoft.desktopappinstaller": "es el instalador de paquetes de Windows",
    "microsoft.storepurchaseapp": "es la pieza que termina las compras de la tienda",
    "microsoft.windows.shellexperiencehost": "dibuja el escritorio y el menú de inicio",
    "microsoft.windows.startmenuexperiencehost": "es el menú de inicio",
    "microsoft.windows.search": "es el cuadro de búsqueda de la barra de tareas",
    "microsoft.windows.cortana": "",  # (se decide por la tabla de molestas)
    "microsoft.lockapp": "dibuja la pantalla de bloqueo y la de inicio de sesión",
    "microsoft.accountscontrol": "es el diálogo de cuentas de Windows",
    "microsoft.aad.brokerplugin": "es el inicio de sesión con cuenta de trabajo o escuela",
    "microsoft.windows.cloudexperiencehost": "es el asistente de configuración de Windows",
    "microsoft.windows.immersivecontrolpanel": "es la aplicación de Configuración",
    "microsoft.sechealthui": "es la interfaz de Seguridad de Windows",
    "microsoft.windows.secureassessmentbrowser": "es el navegador de exámenes de Windows",
    "microsoft.windows.client": "es parte del shell de Windows",
    "microsoft.windowsnotepad": "",  # (opcional: se decide por la tabla de molestas)
    "microsoft.windows.photos": "",  # (opcional)
    "microsoft.webpimageextension": "es la extensión que permite ver imágenes WebP",
    "microsoft.heifimageextension": "es la extensión que permite ver fotos HEIF/HEIC",
    "microsoft.av1videoextension": "es la extensión que permite ver vídeo AV1",
    "microsoft.mpeg2videoextension": "es la extensión que permite ver vídeo MPEG-2",
    "microsoft.vp9videoextensions": "es la extensión que permite ver vídeo VP9",
    "microsoft.webmediaextensions": "es la extensión que permite ver vídeo en la web",
    "microsoft.rawimageextension": "es la extensión que permite ver fotos RAW",
    "microsoft.hevcvideoextension": "es la extensión que permite ver vídeo HEVC/H.265",
}
# Los que llevan el motivo vacío son aplicaciones normales: su nombre está aquí solo para
# que quede claro que se revisaron y se decidió que sí se pueden quitar.
PROTECTED_EXACT = {clave: motivo for clave, motivo in PROTECTED_EXACT.items() if motivo}

# --- Lo que sí conviene quitar ---------------------------------------------
# Nombre del paquete: (riesgo, qué se pierde si se quita). La lista es corta a propósito:
# cada entrada es una aplicación que Windows trae puesta y que la mayoría no usa.
BLOAT: dict[str, tuple[str, str]] = {
    "microsoft.bingnews": (RISK_LOW, "las noticias de la tienda"),
    "microsoft.bingweather": (RISK_LOW, "la aplicación del tiempo de la tienda"),
    "microsoft.bingfinance": (RISK_LOW, "la aplicación de finanzas de la tienda"),
    "microsoft.bingsports": (RISK_LOW, "la aplicación de deportes de la tienda"),
    "microsoft.bingsearch": (RISK_MEDIUM, "la búsqueda de Bing del menú de inicio"),
    "microsoft.gethelp": (RISK_LOW, "el acceso rápido a Soporte técnico"),
    "microsoft.getstarted": (RISK_LOW, "los consejos de Windows («Sugerencias»)"),
    "microsoft.microsoftofficehub": (RISK_LOW, "el anuncio de Office del menú de inicio"),
    "microsoft.microsoftsolitairecollection": (RISK_LOW, "el Solitario"),
    "microsoft.microsoft3dviewer": (RISK_LOW, "el visor de objetos 3D"),
    "microsoft.paint3d": (RISK_LOW, "Paint 3D"),
    "microsoft.mixedreality.portal": (RISK_MEDIUM, "el portal de realidad mixta"),
    "microsoft.people": (RISK_MEDIUM, "los contactos del menú Inicio"),
    "microsoft.skypeapp": (RISK_LOW, "Skype (se puede instalar aparte desde la tienda)"),
    "microsoft.windowsfeedbackhub": (RISK_LOW, "el Centro de opiniones"),
    "microsoft.windowsmaps": (RISK_LOW, "la aplicación Mapas"),
    "microsoft.zunemusic": (RISK_MEDIUM, "el Reproductor multimedia de Windows"),
    "microsoft.zunevideo": (RISK_MEDIUM, "Películas y TV"),
    "microsoft.todos": (RISK_LOW, "Microsoft To Do"),
    "microsoft.windowscamera": (RISK_MEDIUM, "la aplicación Cámara"),
    "microsoft.windowscommunicationsapps": (RISK_LOW, "Correo y Calendario"),
    "microsoft.powerautomatedesktop": (RISK_LOW, "Power Automate Desktop"),
    "microsoft.teams": (RISK_LOW, "Microsoft Teams tal y como viene de fábrica"),
    "mstteams": (RISK_LOW, "Microsoft Teams (nueva versión de la tienda)"),
    "microsoft.outlookforwindows": (RISK_LOW, "el Outlook nuevo de la tienda"),
    "microsoft.yourphone": (RISK_MEDIUM, "Vínculo con el teléfono"),
    "microsoftwindows.crossdevice": (RISK_MEDIUM, "Vínculo con el teléfono"),
    "microsoft.copilot": (RISK_MEDIUM, "el acceso a Copilot del escritorio"),
    "microsoft.startexperiencesapp": (RISK_MEDIUM, "la aplicación de experiencias de inicio"),
    "clipchamp.clipchamp": (RISK_LOW, "Clipchamp"),
    "microsoft.windowsterminal": (RISK_HIGH, "la Terminal de Windows (la usa el propio PC Toolkit)"),
    "microsoft.gamingservices": (RISK_HIGH, "los juegos de la tienda y el Xbox Game Pass"),
    "microsoft.xboxgamingoverlay": (RISK_HIGH, "la barra de juegos de Xbox"),
    "microsoft.xboxgamecallableui": (RISK_HIGH, "parte de la barra de juegos de Xbox"),
    "microsoft.xboxidentityprovider": (RISK_HIGH, "el inicio de sesión de los juegos de Xbox"),
    "microsoft.xboxtcui": (RISK_HIGH, "parte del sistema de juegos de Xbox"),
}

# Tope de tiempo para medir lo que ocupa **cada** aplicación: recorrer una carpeta enorme
# (Teams pasa del giga con miles de archivos) no puede bloquear la lista, y hay decenas de
# paquetes. Medio segundo por aplicación mide de sobra lo normal (miles de archivos se cuentan
# en milisegundos) y deja el peor caso —un equipo con muchas aplicaciones grandes— en medio
# minuto de trabajo en segundo plano, con la ventana respondiendo.
SIZE_BUDGET_SECONDS = 0.5
MAX_SIZE_FILES = 4000

# Variable de entorno con la que las pruebas lanzan un PowerShell de mentira en vez del real.
FAKE_SCRIPT_ENV = "PCTOOLKIT_PS_AUXILIAR"


# ---------------------------------------------------------------------------
# Fichas
# ---------------------------------------------------------------------------


@dataclass
class AppEntry:
    """Una aplicación instalada del usuario (paquete de la tienda)."""

    name: str  # Microsoft.BingNews
    family: str = ""  # Microsoft.BingNews_8wekyb3d8bbwe
    full_name: str = ""  # Microsoft.BingNews_4.55_x64__8wekyb3d8bbwe (lo que borra el comando)
    publisher: str = ""
    version: str = ""
    location: str = ""
    label: str = ""  # nombre visible del menú Inicio, si lo tiene
    framework: bool = False
    system: bool = False
    signature: str = ""
    size_bytes: int = -1  # -1: todavía sin medir

    @property
    def key(self) -> str:
        """Identificador estable del paquete (lo que se usa para deshacer)."""
        return self.full_name or self.name

    @property
    def display_name(self) -> str:
        return self.label or self.name

    @property
    def protected_reason(self) -> str:
        return protected_reason(self)

    @property
    def removable(self) -> bool:
        return not self.protected_reason

    @property
    def risk(self) -> str:
        return risk_of(self)

    @property
    def risk_label(self) -> str:
        return RISK_LABELS.get(self.risk, self.risk)

    @property
    def note(self) -> str:
        """Qué se pierde si se quita (lista para enseñar)."""
        return note_of(self)

    @property
    def size_label(self) -> str:
        return "sin medir" if self.size_bytes < 0 else format_bytes(self.size_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nombre": self.name,
            "familia": self.family,
            "completo": self.full_name,
            "editor": self.publisher,
            "version": self.version,
            "carpeta": self.location,
            "visible": self.label,
            "marco": self.framework,
            "sistema": self.system,
            "firma": self.signature,
        }


@dataclass
class RemoveResult:
    """Lo que pasó al quitar varias aplicaciones."""

    removed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def message(self) -> str:
        partes = []
        if self.removed:
            partes.append(f"{len(self.removed)} aplicación(es) quitada(s)")
        if self.failed:
            partes.append("; ".join(f"«{nombre}»: {motivo}" for nombre, motivo in self.failed))
        return ". ".join(partes) or "No había nada que quitar."


# ---------------------------------------------------------------------------
# Lista blanca y clasificación (cálculo puro: se prueba sin tocar el equipo)
# ---------------------------------------------------------------------------


def protected_reason(entry: AppEntry) -> str:
    """Por qué **no** se puede quitar esta aplicación (texto vacío si sí se puede)."""
    nombre = (entry.name or "").strip().lower()
    if entry.framework:
        return "es un marco de trabajo: otras aplicaciones dependen de él"
    motivo = PROTECTED_EXACT.get(nombre)
    if motivo:
        return motivo
    for prefijo, texto in PROTECTED_PREFIXES:
        if nombre.startswith(prefijo):
            return texto
    return ""


def risk_of(entry: AppEntry) -> str:
    """Riesgo de quitarla: alto para lo que rompe algo, bajo para lo que solo es un extra."""
    conocida = BLOAT.get((entry.name or "").strip().lower())
    if conocida:
        return conocida[0]
    if entry.system:
        return RISK_HIGH
    return RISK_LOW


def note_of(entry: AppEntry) -> str:
    """Qué se pierde al quitarla, en una frase que se pueda enseñar."""
    protegida = protected_reason(entry)
    if protegida:
        return f"No se puede quitar: {protegida}."
    conocida = BLOAT.get((entry.name or "").strip().lower())
    if conocida:
        return f"Si la quitas, pierdes {conocida[1]}. Se puede volver a instalar desde la tienda."
    return (
        "PC Toolkit no la tiene en su lista de aplicaciones molestas: quítala solo si sabes "
        "para qué sirve. Se puede volver a instalar desde la tienda."
    )


def is_recommended(entry: AppEntry) -> bool:
    """¿Está en la lista de las que PC Toolkit recomienda quitar?"""
    return entry.removable and (entry.name or "").strip().lower() in BLOAT


def summary(entries: Sequence[AppEntry]) -> dict[str, int]:
    """Cuántas hay de cada tipo, para el resumen de la pantalla."""
    recomendadas = [entrada for entrada in entries if is_recommended(entrada)]
    return {
        "total": len(entries),
        "recomendadas": len(recomendadas),
        "protegidas": sum(1 for entrada in entries if not entrada.removable),
        "desconocidas": sum(1 for entrada in entries if entrada.removable and not is_recommended(entrada)),
        "tamano": sum(entrada.size_bytes for entrada in recomendadas if entrada.size_bytes > 0),
    }


def sort_key(entry: AppEntry) -> tuple[int, str]:
    """Orden de la lista: primero las que conviene quitar, y dentro de cada grupo por nombre."""
    grupo = 0 if is_recommended(entry) else (2 if not entry.removable else 1)
    return (grupo, entry.display_name.lower())


# ---------------------------------------------------------------------------
# Lectura de paquetes (PowerShell)
# ---------------------------------------------------------------------------


def script_runner() -> str:
    """Programa que ejecuta los guiones de PowerShell (las pruebas ponen uno de mentira)."""
    return os.environ.get(FAKE_SCRIPT_ENV) or "powershell"


def _run_script(script: str, *, timeout: int = 120, extra_env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Lanza un guion de PowerShell y devuelve `(código, salida, error)`.

    Los paquetes se pasan **por variables de entorno**, nunca dentro del texto del guion:
    un nombre de paquete viene del sistema y no tiene por qué llevar comillas ni nada raro,
    pero interpolarlo en el comando sería la forma de que una comilla se convirtiera en otra
    orden. La salida se lee como UTF-8 para que los acentos no lleguen corruptos.
    """
    entorno = powershell_environment()
    if extra_env:
        entorno.update(extra_env)
    try:
        resultado = subprocess.run(
            [script_runner(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=entorno,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logs.warning("No se pudo consultar las aplicaciones instaladas", exc, name="aplicaciones")
        return 1, "", str(exc)
    return resultado.returncode, resultado.stdout or "", resultado.stderr or ""


LIST_SCRIPT = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='SilentlyContinue';"
    # Los nombres visibles (los del menú Inicio) se piden de una vez: consultarlos uno a uno
    # costaría un PowerShell por aplicación.
    "$inicio=@(Get-StartApps);"
    "$lista=@();"
    "foreach($p in (Get-AppxPackage)){"
    "$visible='';"
    "foreach($app in $inicio){ if([string]$app.AppID -like ($p.PackageFamilyName + '!*')){ $visible=[string]$app.Name; break } };"
    "$sistema=$false;"
    "if($p.PSObject.Properties.Name -contains 'IsPartOfSystem'){ $sistema=[bool]$p.IsPartOfSystem };"
    "$lista += [pscustomobject]@{"
    "nombre=[string]$p.Name;familia=[string]$p.PackageFamilyName;"
    "completo=[string]$p.PackageFullName;editor=[string]$p.PublisherDisplayName;"
    "version=[string]$p.Version;carpeta=[string]$p.InstallLocation;"
    "visible=$visible;marco=[bool]$p.IsFramework;sistema=$sistema;"
    "firma=[string]$p.SignatureKind}};"
    "ConvertTo-Json -InputObject @($lista) -Compress -Depth 3"
)


def parse_packages(output: str) -> list[AppEntry]:
    """Convierte la salida de PowerShell en fichas (función pura, se prueba con texto fijo)."""
    payload = (output or "").strip()
    if not payload.startswith("[") and not payload.startswith("{"):
        return []
    try:
        datos = json.loads(payload)
    except (ValueError, TypeError):
        return []
    filas = datos if isinstance(datos, list) else [datos]
    entradas: list[AppEntry] = []
    for fila in filas:
        if not isinstance(fila, dict) or not fila.get("nombre"):
            continue
        entradas.append(
            AppEntry(
                name=str(fila.get("nombre") or "").strip(),
                family=str(fila.get("familia") or "").strip(),
                full_name=str(fila.get("completo") or "").strip(),
                publisher=str(fila.get("editor") or "").strip(),
                version=str(fila.get("version") or "").strip(),
                location=str(fila.get("carpeta") or "").strip(),
                label=str(fila.get("visible") or "").strip(),
                framework=bool(fila.get("marco")),
                system=bool(fila.get("sistema")),
                signature=str(fila.get("firma") or "").strip(),
            )
        )
    entradas.sort(key=sort_key)
    return entradas


def available() -> bool:
    """¿Se puede consultar la lista en este equipo?"""
    return IS_WINDOWS


def folder_size(folder: str | Path, *, budget: float = SIZE_BUDGET_SECONDS) -> int:
    """Lo que ocupa una carpeta, con tope de tiempo.

    Medir «Teams» entero puede tardar minutos y la lista no vale eso: se recorre hasta
    agotar el tiempo (o los archivos) y se devuelve lo contado. El número sirve para
    comparar unas aplicaciones con otras, que es para lo que se enseña.
    """
    if not folder:
        return -1
    raiz = Path(folder)
    if not raiz.is_dir():
        return -1
    limite = time.monotonic() + max(0.1, budget)
    total = 0
    vistos = 0
    pila = [raiz]
    try:
        while pila:
            actual = pila.pop()
            for entrada in os.scandir(actual):
                vistos += 1
                if vistos > MAX_SIZE_FILES or time.monotonic() > limite:
                    return total
                try:
                    if entrada.is_dir(follow_symlinks=False):
                        pila.append(entrada.path)
                    elif entrada.is_file(follow_symlinks=False):
                        total += entrada.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _descomponer(identificador: str) -> tuple[str, str, str, str]:
    """Nombre, versión, familia y editor a partir del identificador del paquete.

    Windows escribe los paquetes como `Nombre_Versión_Arquitectura_Recurso_Editor`
    (`Microsoft.BingNews_4.55.0.0_x64__8wekyb3d8bbwe`), así que los datos que PowerShell
    contaba por separado salen de aquí. La familia —lo que identifica a la aplicación entre
    versiones— es el nombre con el editor.
    """
    partes = (identificador or "").split("_")
    nombre = partes[0] if partes else ""
    version = partes[1] if len(partes) > 1 else ""
    editor = partes[-1] if len(partes) > 1 else ""
    familia = f"{nombre}_{editor}" if editor else nombre
    return nombre, version, familia, editor


def _editor_del_manifiesto(carpeta: str) -> str:
    """Editor visible («Microsoft Corporation») leyendo el `AppxManifest.xml` del paquete.

    Es lo único que el registro no guarda, y son unos milisegundos por paquete: leer los 97
    del equipo cuesta ~50 ms, mucho menos que las ocho consultas por aplicación que haría
    falta abrir a PowerShell para lo mismo.
    """
    if not carpeta:
        return ""
    from xml.etree import ElementTree

    try:
        raiz = ElementTree.parse(Path(carpeta) / "AppxManifest.xml").getroot()
    except (OSError, ElementTree.ParseError):
        return ""
    for nodo in raiz.iter():
        if nodo.tag.rsplit("}", 1)[-1] == "PublisherDisplayName" and (nodo.text or "").strip():
            return nodo.text.strip()
    return ""


def list_apps_from_registry() -> list[AppEntry] | None:
    """Aplicaciones instaladas leídas del registro por el módulo nativo.

    `Get-AppxPackage` tarda varios segundos (medido: 5,73 s con 97 paquetes) porque arranca
    un PowerShell y le pide el catálogo entero; los mismos datos están en el registro, al día,
    y leerlos cuesta milisegundos. El módulo lee **dos** sitios —lo que la cuenta tiene
    registrado y lo que la máquina tiene puesto para ella, quedándose con lo que existe en el
    disco—, porque uno solo deja fuera aplicaciones instaladas de verdad. Devuelve `None`
    cuando el módulo no está compilado, para que quien llame decida si pregunta a PowerShell.
    """
    if not IS_WINDOWS:
        return None
    paquetes = native.listar_paquetes()
    if not paquetes:
        return None
    entradas: list[AppEntry] = []
    for fila in paquetes:
        identificador = str(fila.get("id") or "").strip()
        if not identificador:
            continue
        nombre, version, familia, editor = _descomponer(identificador)
        carpeta = str(fila.get("carpeta") or "").strip()
        entradas.append(
            AppEntry(
                name=nombre,
                family=familia,
                full_name=identificador,
                publisher=_editor_del_manifiesto(carpeta) or editor,
                version=version,
                location=carpeta,
                label=str(fila.get("visible") or "").strip(),
                framework=bool(fila.get("marco")),
                system=bool(fila.get("sistema")),
            )
        )
    if not entradas:
        return None
    entradas.sort(key=sort_key)
    return entradas


def list_apps(*, with_size: bool = True, timeout: int = 120) -> list[AppEntry]:
    """Aplicaciones instaladas del usuario, ordenadas (las recomendadas primero).

    Primero se le pide la lista al **módulo nativo** (el registro, milisegundos) y, si no
    está compilado, se pregunta a PowerShell como siempre, que es además lo que se usa si el
    registro no tiene el listado (pasa en algunas ediciones de Windows Server).

    En Windows 10 y 11 los dos caminos coinciden de sobra —el registro trae incluso más—,
    pero en ediciones de servidor el registro puede traer menos: Windows guarda ahí las
    aplicaciones que trae provisionadas **solo en su base de datos interna**, que es la que
    consulta `Get-AppxPackage`, y no las escribe en ninguna clave. Está contado, con lo que se
    midió, en DOCUMENTACION.md (apartado 3.12).
    """
    if not IS_WINDOWS:
        return []
    entradas = list_apps_from_registry()
    if entradas is None:
        codigo, salida, error = _run_script(LIST_SCRIPT, timeout=timeout)
        if codigo != 0 and not salida.strip():
            logs.warning(f"PowerShell devolvió {codigo} al listar aplicaciones: {error.strip()[:200]}", None, name="aplicaciones")
            return []
        entradas = parse_packages(salida)
    if with_size:
        for entrada in entradas:
            entrada.size_bytes = folder_size(entrada.location)
    return entradas


def format_entries_report(entries: Sequence[AppEntry]) -> str:
    """Texto plano con la lista, para copiar y pedir ayuda en un foro."""
    cifras = summary(entries)
    lineas = [
        f"{APP_NAME} {APP_VERSION} · aplicaciones preinstaladas",
        time.strftime("%d/%m/%Y %H:%M"),
        "",
        f"{cifras['total']} aplicación(es): {cifras['recomendadas']} que conviene quitar, "
        f"{cifras['protegidas']} piezas del sistema, {cifras['desconocidas']} por decidir.",
        "",
    ]
    for entrada in entries:
        marca = "quitar" if is_recommended(entrada) else ("protegida" if not entrada.removable else "opcional")
        lineas.append(
            f"[{marca}] {entrada.display_name} ({entrada.name}) · {entrada.publisher or 'editor desconocido'}"
            f" · {entrada.version or 'sin versión'} · {entrada.size_label}"
        )
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Quitar y volver a poner
# ---------------------------------------------------------------------------

REMOVE_SCRIPT = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='Stop';"
    "try{"
    # Sin `-AllUsers`: se quita solo de la cuenta con la sesión abierta. Hacerlo para todos
    # los usuarios necesita administrador y rompe lo que hayan instalado los demás.
    "Remove-AppxPackage -Package $env:PCTK_PAQUETE -Confirm:$false -ErrorAction Stop;"
    "Write-Output 'ok'"
    "}catch{ Write-Output ('no: ' + $_.Exception.Message); exit 1 }"
)

RESTORE_SCRIPT = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='Stop';"
    "$manifest=(Join-Path $env:PCTK_CARPETA 'AppxManifest.xml');"
    "try{"
    "if($env:PCTK_CARPETA -and (Test-Path $manifest)){"
    "Add-AppxPackage -Register $manifest -DisableDevelopmentMode -ErrorAction Stop;"
    "Write-Output 'ok'; exit 0 };"
    "$paquete=Get-AppxPackage -Name $env:PCTK_NOMBRE -ErrorAction SilentlyContinue | Select-Object -First 1;"
    "if($paquete){"
    "Add-AppxPackage -Register (Join-Path $paquete.InstallLocation 'AppxManifest.xml') -DisableDevelopmentMode -ErrorAction Stop;"
    "Write-Output 'ok'; exit 0 };"
    "Write-Output 'no: la aplicación ya no está en este equipo';exit 1"
    "}catch{ Write-Output ('no: ' + $_.Exception.Message); exit 1 }"
)


def can_remove(entry: AppEntry) -> tuple[bool, str]:
    """¿Se puede quitar ahora mismo? (lista blanca y sistema)."""
    if not IS_WINDOWS:
        return False, "Estas aplicaciones son de Windows 10 y 11."
    motivo = protected_reason(entry)
    if motivo:
        return False, f"«{entry.display_name}» no se quita: {motivo}."
    if not entry.key:
        return False, f"«{entry.display_name}» no trae el nombre del paquete, así que no se puede quitar."
    return True, ""


def remove_app(
    entry: AppEntry,
    *,
    store: undo.UndoStore | None = None,
    origin: str = undo.ORIGIN_MANUAL,
    journal=None,
) -> tuple[bool, str]:
    """Quita una aplicación del usuario actual y lo anota para poder volver atrás.

    El historial guarda con qué carpeta estaba instalada: al reinstalarla se registra otra vez
    desde ahí, que es la vía que funciona sin volver a descargarla de la tienda.
    """
    from . import windows_settings  # el registro legible de cambios es el mismo

    anotar = journal or windows_settings.registrar_cambio
    ok, motivo = can_remove(entry)
    if not ok:
        anotar("bloqueado", entry.name, motivo, [entry.key])
        return False, motivo

    codigo, salida, error = _run_script(
        REMOVE_SCRIPT,
        timeout=180,
        extra_env={"PCTK_PAQUETE": entry.key},
    )
    if codigo != 0 or "no:" in (salida or "").lower():
        detalle = (salida or error or "").strip()
        if detalle.lower().startswith("no:"):
            detalle = detalle[3:].strip()
        mensaje = f"No se pudo quitar «{entry.display_name}»: {detalle[:200] or 'Windows no dio detalles'}"
        logs.warning(mensaje, None, name="aplicaciones")
        anotar("error", entry.name, mensaje, [entry.key])
        return False, mensaje

    if store is not None:
        store.add(
            kind=KIND_APLICACIONES,
            title=entry.display_name,
            before=f"Instalada ({entry.version or 'versión desconocida'})",
            after="Quitada de este usuario",
            data={
                "paquete": entry.key,
                "nombre": entry.name,
                "familia": entry.family,
                "carpeta": entry.location,
                "riesgo": entry.risk,
                "version": entry.version,
            },
            origin=origin,
        )
    detalle = f"«{entry.display_name}» quitada de este usuario"
    anotar("quitado", entry.name, detalle, [entry.key])
    return True, detalle


def remove_apps(
    entries: Sequence[AppEntry],
    *,
    store: undo.UndoStore | None = None,
    origin: str = undo.ORIGIN_MANUAL,
    journal=None,
) -> RemoveResult:
    """Quita una lista de aplicaciones, una detrás de otra, sin parar por un fallo."""
    resultado = RemoveResult()
    for entrada in entries:
        ok, mensaje = remove_app(entrada, store=store, origin=origin, journal=journal)
        if ok:
            resultado.removed.append(entrada.display_name)
        else:
            resultado.failed.append((entrada.display_name, mensaje))
    return resultado


def restore(action: undo.UndoAction, store: undo.UndoStore) -> tuple[bool, str]:
    """Vuelve a instalar una aplicación quitada (lo llama el panel de «Deshacer»)."""
    payload = action.data if isinstance(action.data, dict) else {}
    nombre = str(payload.get("nombre") or "").strip()
    carpeta = str(payload.get("carpeta") or "").strip()
    if not nombre:
        return False, "El apunte no guarda qué aplicación era, así que no se puede reinstalar."
    codigo, salida, error = _run_script(
        RESTORE_SCRIPT,
        timeout=240,
        extra_env={"PCTK_NOMBRE": nombre, "PCTK_CARPETA": carpeta},
    )
    if codigo != 0 or not (salida or "").strip().startswith("ok"):
        detalle = (salida or error or "").strip()
        if detalle.lower().startswith("no:"):
            detalle = detalle[3:].strip()
        return (
            False,
            f"No se pudo volver a instalar «{action.title}»: {detalle[:200] or 'Windows no dio detalles'}. "
            "Búscala en la tienda de Microsoft para instalarla otra vez.",
        )
    from . import windows_settings

    windows_settings.registrar_cambio("restaurado", nombre, f"«{action.title}» vuelta a instalar", [nombre])
    store.mark_reverted(action.id)
    return True, f"«{action.title}» vuelta a instalar."


# El historial de «Deshacer» pregunta por aquí cómo se vuelve atrás una aplicación quitada.
undo.register(KIND_APLICACIONES, restore)
