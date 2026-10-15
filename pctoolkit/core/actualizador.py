"""Actualizaciones desde las publicaciones (Releases) de GitHub.

Todo lo que hace falta para saber si hay una versión nueva y para traerla, **sin
dependencias nuevas**: se usa `urllib` de la biblioteca estándar y `hashlib`. Nada de
interfaz aquí: este módulo se puede usar desde la ventana, desde una prueba o desde una
consola.

Cómo se decide que hay algo nuevo
---------------------------------
Se pregunta por la última publicación del repositorio
(`/releases/latest`, que ya ignora los borradores y las versiones preliminares) y se
comparan los números de versión, no el texto: `v1.10.0` es más nueva que `v1.9.9`
(comparar cadenas diría lo contrario). Se aceptan etiquetas con o sin la `v` delante y
con sufijos (`v1.3.0-beta`, `1.3.0`), que se quedan en sus tres números.

De dónde sale el SHA-256
------------------------
Antes de ejecutar nada, el archivo descargado se comprueba contra el SHA-256 que publica
la propia publicación. Se busca por este orden, de lo más fiable a lo menos:

1. Un archivo adjunto `<nombre del instalador>.sha256` — lo escribe `publicar.yml` junto
   a cada ejecutable y solo lleva el hash y el nombre: no hay nada que interpretar.
2. El archivo adjunto `SHA256SUMS.txt`, con una línea por archivo.
3. Las notas de la versión, que repiten los mismos hashes dentro de un bloque de código.

Si no se encuentra ninguno, la actualización **no se descarga**: una versión cuyo hash no
se puede comprobar no se ejecuta (ninguna otra comprobación sustituye a esta). La opción
1 es la que se documenta como preferida, y las otras dos existen para que las
publicaciones antiguas —hechas antes de que existiera el actualizador— también funcionen.

Ni se descarga ni se instala nada sin que lo pida quien usa el programa: aquí solo hay
funciones; quien decide es la interfaz.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import logs
from .config import APP_NAME, APP_VERSION, GITHUB_REPO

# --- Direcciones y constantes ----------------------------------------------
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"

# GitHub pide un `User-Agent` propio: sin él responde 403 a cualquier consulta.
USER_AGENT = f"{APP_NAME.replace(' ', '-')}/{APP_VERSION} (+{RELEASES_PAGE})"

# Un tiempo de espera corto: comprobar si hay versión nueva no puede retrasar el arranque.
TIMEOUT = 15.0
# Trozo de lectura al descargar: con 256 KiB el progreso se mueve con suavidad y no se
# llena la memoria con un archivo de 80 MB.
CHUNK = 256 * 1024

# Nombres con los que `publicar.yml` deja los archivos de cada versión.
INSTALLER_SUFFIX = "-Setup.exe"
PORTABLE_SUFFIX = "-portable.exe"
SUMS_ASSET = "SHA256SUMS.txt"
SUMS_SUFFIX = ".sha256"

# Archivo que deja el instalador dentro de la carpeta instalada. Su presencia es lo que
# distingue una copia instalada de una portátil (la portátil no lo lleva).
MANIFEST_NAME = ".pctoolkit_instalacion.json"

# Un SHA-256 son 64 dígitos hexadecimales.
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateError(RuntimeError):
    """Fallo al comprobar o traer la actualización (el mensaje es para el usuario)."""


class Cancelled(UpdateError):
    """La descarga se canceló a petición de quien la pidió."""


@dataclass(frozen=True)
class Asset:
    """Archivo adjunto de una publicación."""

    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class Release:
    """Una versión publicada, ya interpretada."""

    version: str
    tag: str
    notes: str
    page_url: str
    asset: Asset
    sha256: str = ""
    published_at: str = ""

    @property
    def installable(self) -> bool:
        """Si se puede descargar y ejecutar (con hash comprobable)."""
        return bool(self.sha256)

    @property
    def portable(self) -> bool:
        """Si el archivo que reparte esta versión es el portátil (un solo `.exe`)."""
        return self.asset.name.lower().endswith(PORTABLE_SUFFIX.lower())

    def notes_excerpt(self, max_chars: int = 2600) -> str:
        """Notas de la versión, recortadas para que quepan en un diálogo.

        Se quita la parte de comprobación de la descarga (los hashes y el comando para
        verificarlos: es siempre la misma y no le interesa a quien solo quiere saber qué
        cambia) y se corta por línea, nunca a mitad de una frase. Las novedades van al
        principio de las notas, así que un recorte largo no se las lleva por delante.
        """
        texto = _without_verification(_strip_sums_block(self.notes)).strip()
        if not texto:
            return "Esta versión no trae notas."
        if len(texto) <= max_chars:
            return texto
        recorte = texto[:max_chars]
        corte = recorte.rfind("\n")
        if corte > max_chars // 2:
            recorte = recorte[:corte]
        return recorte.rstrip() + "\n\n(notas completas en la página de la versión)"


def _strip_sums_block(notes: str) -> str:
    """Quita de las notas los bloques de código que solo llevan hashes.

    Se mira el contenido y no el encabezado: las notas las escribe el flujo de
    publicación, pero el mismo texto se puede retocar a mano desde GitHub, así que la
    única forma de no equivocarse es ver qué hay dentro de cada bloque.
    """
    salida: list[str] = []
    bloque: list[str] | None = None
    for linea in notes.splitlines():
        if linea.strip().startswith("```"):
            if bloque is None:
                bloque = []
                continue
            # Se cierra el bloque: se conserva solo si no son hashes.
            if not _only_hashes(bloque):
                salida += ["```", *bloque, "```"]
            bloque = None
            continue
        if bloque is None:
            salida.append(linea)
        else:
            bloque.append(linea)
    if bloque is not None:  # bloque sin cerrar: se conserva tal cual
        salida += ["```", *bloque]
    return "\n".join(salida)


def _without_verification(notes: str) -> str:
    """Deja fuera el apartado de comprobación de la descarga (hashes y comando).

    Solo se quita cuando hay otras secciones delante: en las notas que escribe
    `publicar.yml` las novedades van primero (apartado «Qué cambia») y la comprobación
    detrás, así que se enseña lo que cambia y se deja fuera la letra pequeña. En unas
    notas que **solo** eran eso (las de versiones publicadas antes de que existiera este
    apartado), quitarlo dejaría el diálogo con un título y nada más, así que se conservan
    enteras.
    """
    patron = re.compile(r"^#{1,6}\s*comprobar la descarga\s*$", re.IGNORECASE | re.MULTILINE)
    encontrado = patron.search(notes)
    if not encontrado:
        return notes
    anteriores = [linea for linea in notes[: encontrado.start()].splitlines() if linea.strip()]
    if not any(linea.lstrip().startswith("#") for linea in anteriores):
        return notes
    return notes[: encontrado.start()]


def _only_hashes(lineas: list[str]) -> bool:
    """Si todas las líneas con contenido del bloque son «hash  nombre»."""
    vistas = 0
    for linea in lineas:
        trozos = linea.strip().split()
        if not trozos:
            continue
        if not _HASH.match(trozos[0]) or len(trozos) > 2:
            return False
        vistas += 1
    return vistas > 0


# --- Versiones --------------------------------------------------------------


def parse_version(text: str) -> tuple[int, ...]:
    """Números de una versión (`v1.3.0-beta` → `(1, 3, 0)`).

    Se acepta lo que publica la gente: con o sin `v`, con sufijos de preliminar
    (`-beta`, `+build`) y con menos de tres números (`v2` → `(2,)`). Lo que no sea un
    número se descarta; el texto que no lleva ninguno da `(0,)`.
    """
    limpio = (text or "").strip().lstrip("vV")
    # Se corta en el primer sufijo: `1.3.0-beta.2` cuenta como `1.3.0`.
    limpio = re.split(r"[-+]", limpio, maxsplit=1)[0]
    numeros = [int(trozo) for trozo in re.findall(r"\d+", limpio)]
    return tuple(numeros) or (0,)


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    """Si `candidate` es más nueva que `current` (comparando números, no texto)."""
    return _normalized(candidate) > _normalized(current)


def _normalized(text: str) -> tuple[int, ...]:
    """Versión comparable: siempre cuatro números (`1.3` → `(1, 3, 0, 0)`)."""
    partes = list(parse_version(text))[:4]
    while len(partes) < 4:
        partes.append(0)
    return tuple(partes)


# --- SHA-256 ----------------------------------------------------------------


def sha_for(sums: str, filename: str) -> str:
    """SHA-256 de `filename` dentro de un texto de sumas (o `""` si no está).

    Se entienden los formatos que se encuentran de verdad: las líneas
    `hash  nombre` de `SHA256SUMS.txt`, la variante `hash *nombre`, un archivo que
    solo lleva el hash en su primera línea y las notas de la versión (donde el mismo
    bloque va dentro de un bloque de código).
    """
    for linea in (sums or "").splitlines():
        limpia = linea.strip().strip("`").strip()
        if not limpia or limpia.startswith("#"):
            continue
        trozos = limpia.split()
        if len(trozos) == 1 and _HASH.match(trozos[0]):
            # Archivo con un único hash: por convención, es el del archivo que acompaña.
            return trozos[0].lower()
        if len(trozos) >= 2 and _HASH.match(trozos[0]):
            nombre = trozos[-1].lstrip("*")
            if Path(nombre).name == Path(filename).name:
                return trozos[0].lower()
    return ""


def file_sha256(path: Path, *, progress: Callable[[int], None] | None = None) -> str:
    """SHA-256 de un archivo, leído por trozos (vale para archivos de 80 MB)."""
    digest = hashlib.sha256()
    leido = 0
    with Path(path).open("rb") as archivo:
        while True:
            trozo = archivo.read(CHUNK)
            if not trozo:
                break
            digest.update(trozo)
            leido += len(trozo)
            if progress is not None:
                progress(leido)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    """Si el archivo coincide con el SHA-256 esperado (`expected` vacío → no)."""
    if not expected:
        return False
    return file_sha256(path) == expected.strip().lower()


# --- Lectura de la publicación ---------------------------------------------


def pick_asset(assets: Iterable[dict], suffix: str = INSTALLER_SUFFIX) -> Asset | None:
    """Primer adjunto cuyo nombre termina en `suffix` (el instalador, o el portátil)."""
    for bruto in assets or ():
        name = str(bruto.get("name") or "")
        if name.lower().endswith(suffix.lower()):
            return Asset(
                name=name,
                url=str(bruto.get("browser_download_url") or ""),
                size=int(bruto.get("size") or 0),
            )
    return None


def parse_release(payload: dict, *, sums: str = "") -> Release:
    """Convierte la respuesta de la API en un `Release`. Sin red: se le pasa el JSON.

    `sums` es el contenido del adjunto de sumas (`<instalador>.sha256` o
    `SHA256SUMS.txt`), que la interfaz descarga antes de llamar aquí porque para saber
    cuál pedir hay que mirar antes los adjuntos.
    """
    if not isinstance(payload, dict):
        raise UpdateError("La respuesta de GitHub no tiene el formato esperado.")
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("La publicación no dice su versión (falta la etiqueta).")
    assets = payload.get("assets") or []
    asset = pick_asset(assets, INSTALLER_SUFFIX)
    if asset is None:
        raise UpdateError(
            f"La versión {tag} no trae un instalador adjunto ({INSTALLER_SUFFIX})."
        )
    if not asset.url:
        raise UpdateError(f"El instalador de la versión {tag} no tiene dirección de descarga.")
    notes = str(payload.get("body") or "")
    version = _version_text(tag)
    # El hash se busca primero en los adjuntos (un archivo escrito para máquinas) y solo
    # si no está se mira en las notas, que las escribe una persona y pueden cambiar.
    sha = sha_for(sums, asset.name) or sha_for(notes, asset.name)
    return Release(
        version=version,
        tag=tag,
        notes=notes,
        page_url=str(payload.get("html_url") or f"{RELEASES_PAGE}/tag/{tag}"),
        asset=asset,
        sha256=sha,
        published_at=str(payload.get("published_at") or ""),
    )


def _version_text(tag: str) -> str:
    """Versión legible a partir de la etiqueta (`v1.3.0` → `1.3.0`)."""
    numeros = re.match(r"^[vV]?(\d+(?:\.\d+)*)", tag.strip())
    return numeros.group(1) if numeros else tag.strip()


# --- Red --------------------------------------------------------------------


def _request(url: str, timeout: float = TIMEOUT) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _explain(exc: BaseException, url: str) -> UpdateError:
    """Convierte un fallo de red en un mensaje que se pueda leer.

    Todo lo que falla queda anotado en el registro con la dirección que se consultaba: el
    mensaje de la ventana dice lo que puede entender quien lo lee, pero cuando alguien pide
    ayuda hace falta saber si fue el DNS, un certificado, un 403 o un tiempo agotado.
    """
    logs.warning(f"Fallo consultando {url}", exc, name="actualizador")
    # Sin soporte https no es la conexión de quien lo usa: es esta copia del programa. Se
    # mira antes que nada porque el síntoma puede llegar como `URLError`, como `ValueError`
    # («unknown url type») o como un fallo al cargar la DLL de OpenSSL.
    texto = f"{exc} {getattr(exc, 'reason', '')}".lower()
    if "unknown url type" in texto or "no module named 'ssl'" in texto or "dll load failed" in texto:
        return UpdateError(
            "Esta copia del programa no puede usar direcciones seguras (https), así que no "
            "puede comprobar actualizaciones. Es un fallo del paquete, no de tu conexión: "
            "vuelve a descargarlo desde la página del proyecto."
        )
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return UpdateError(
                "GitHub ha limitado las consultas desde este equipo (se hacen muchas "
                "comprobaciones seguidas). Vuelve a intentarlo dentro de un rato."
            )
        if exc.code == 404:
            return UpdateError(
                "GitHub no encuentra ninguna publicación de PC Toolkit en este repositorio."
            )
        return UpdateError(f"GitHub respondió con un error {exc.code}.")
    if isinstance(exc, json.JSONDecodeError):
        return UpdateError("La respuesta de GitHub no se pudo leer (no es el formato esperado).")
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        # Un `URLError` envuelve los fallos de socket (sin red, DNS, tiempo agotado) y
        # también el corte a mitad de una descarga. El motivo de verdad viene en `reason`, y
        # conviene decirlo: «revisa la conexión» no sirve de nada a quien **sí** tiene
        # conexión y lo que falla es el DNS de su red, un cortafuegos o el certificado de un
        # antivirus que revisa el tráfico.
        motivo = getattr(exc, "reason", None)
        if isinstance(motivo, socket.gaierror) or "getaddrinfo" in texto:
            return UpdateError(
                "No se pudo encontrar la dirección de GitHub (el nombre no se resuelve): "
                "suele ser el DNS del equipo o de la red, o que no hay internet."
            )
        if (
            isinstance(motivo, (TimeoutError, socket.timeout))
            or isinstance(exc, TimeoutError)
            or "timed out" in texto
        ):
            return UpdateError(
                "GitHub no respondió a tiempo. Si tu conexión va bien, puede ser un "
                "cortafuegos o un antivirus que está cortando la conexión."
            )
        if "certificate" in texto or "handshake" in texto or "wrong version number" in texto:
            return UpdateError(
                "La conexión con GitHub no se pudo cifrar (certificado o TLS). Suele pasar "
                "cuando un antivirus o un proxy revisa el tráfico: prueba a desactivarlo un "
                "momento o darte de alta en su lista de excepciones."
            )
        return UpdateError("No se pudo conectar con GitHub: revisa la conexión a internet.")
    return UpdateError(f"No se pudo comprobar si hay una versión nueva: {exc}")


def _get_json(url: str, timeout: float = TIMEOUT) -> dict:
    try:
        with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as respuesta:
            return json.loads(respuesta.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, json.JSONDecodeError, ValueError, OSError) as exc:
        raise _explain(exc, url) from exc


def _get_text(url: str, timeout: float = TIMEOUT) -> str:
    """Texto de un adjunto pequeño (las sumas). Nunca lanza: se queda en `""`."""
    try:
        with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as respuesta:
            return respuesta.read(64 * 1024).decode("utf-8", errors="replace")
    except (urllib.error.URLError, ValueError, OSError) as exc:
        logs.warning(f"No se pudieron leer las sumas de {url}", exc, name="actualizador")
        return ""


def _sums_for(assets: Iterable[dict], installers: Iterable[str]) -> str:
    """Contenido del adjunto de sumas de un instalador, si la publicación lo trae."""
    adjuntos = [str(bruto.get("name") or "") for bruto in assets or ()]
    candidatos: list[str] = []
    for nombre in installers:
        exacto = f"{nombre}{SUMS_SUFFIX}"
        if exacto in adjuntos:
            candidatos.append(exacto)
    if SUMS_ASSET in adjuntos:
        candidatos.append(SUMS_ASSET)
    for nombre in candidatos:
        url = next(
            (
                str(bruto.get("browser_download_url") or "")
                for bruto in assets
                if str(bruto.get("name") or "") == nombre
            ),
            "",
        )
        if url:
            texto = _get_text(url)
            if texto:
                return texto
    return ""


def fetch_latest(
    current: str = APP_VERSION,
    *,
    timeout: float = TIMEOUT,
    parser: Callable[[dict, str], Release] | None = None,
) -> Release | None:
    """Última publicación, o `None` si no hay nada más nuevo que `current`.

    `parser` existe para las pruebas: recibe el JSON y el texto de sumas y devuelve el
    `Release`, así que se puede simular una publicación entera sin tocar la red.
    """
    payload = _get_json(API_LATEST, timeout)
    tag = str(payload.get("tag_name") or "")
    if not tag:
        raise UpdateError("GitHub no devolvió ninguna versión (la publicación no tiene etiqueta).")
    if not is_newer(tag, current):
        return None
    assets = payload.get("assets") or []
    instalador = pick_asset(assets, INSTALLER_SUFFIX)
    nombres = [instalador.name] if instalador else [
        str(bruto.get("name") or "") for bruto in assets if str(bruto.get("name") or "").lower().endswith(".exe")
    ]
    sums = _sums_for(assets, nombres)
    construye = parser or (lambda datos, texto: parse_release(datos, sums=texto))
    return construye(payload, sums)


# --- Descarga ---------------------------------------------------------------


def download(
    url: str,
    destination: Path,
    *,
    sha256: str = "",
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = TIMEOUT,
) -> Path:
    """Descarga `url` en `destination` y **comprueba el SHA-256 antes de dar por buena**.

    El hash se calcula según llegan los datos, así que un archivo manipulado se detecta
    sin tener que leerlo dos veces. Si no coincide —o si se cancela— el archivo se borra
    y no queda nada a medias en la carpeta temporal. Nunca se devuelve una ruta sin
    verificar: quien llame a esto puede ejecutar lo que reciba.
    """
    destino = Path(destination)
    destino.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    leido = 0
    try:
        with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as respuesta:
            total = int(respuesta.headers.get("Content-Length") or 0)
            with destino.open("wb") as archivo:
                while True:
                    if cancel is not None and cancel():
                        raise Cancelled("Se canceló la descarga.")
                    trozo = respuesta.read(CHUNK)
                    if not trozo:
                        break
                    archivo.write(trozo)
                    digest.update(trozo)
                    leido += len(trozo)
                    if progress is not None:
                        progress(leido, total)
    except Cancelled:
        _drop(destino)
        raise
    except (urllib.error.URLError, ValueError, OSError) as exc:
        _drop(destino)
        raise _explain(exc, url) from exc

    if sha256 and digest.hexdigest() != sha256.strip().lower():
        _drop(destino)
        raise UpdateError(
            "El archivo descargado no coincide con el SHA-256 publicado: se ha borrado "
            "y no se ha ejecutado nada. Prueba de nuevo más tarde y, si vuelve a pasar, "
            "avisa en el repositorio."
        )
    if not sha256:
        # No debería llegar aquí (la interfaz no descarga sin hash), pero si llegara, el
        # archivo se borra igual: mejor sin actualizar que ejecutando algo sin comprobar.
        _drop(destino)
        raise UpdateError("La versión publicada no trae su SHA-256, así que no se puede verificar.")
    return destino


def _drop(path: Path) -> None:
    """Borra un archivo a medias sin que un fallo de borrado tape el error de verdad."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - Windows con el archivo en uso
        logs.warning(f"No se pudo borrar {path}", exc, name="actualizador")


def download_installer(
    release: Release,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    folder: Path | None = None,
) -> Path:
    """Descarga el instalador de `release` a una carpeta temporal y lo verifica."""
    if not release.installable:
        raise UpdateError("Esta versión no publica su SHA-256: no se puede actualizar sola.")
    carpeta = Path(folder) if folder else Path(tempfile.mkdtemp(prefix="pctoolkit_actualizacion_"))
    return download(
        release.asset.url,
        carpeta / release.asset.name,
        sha256=release.sha256,
        progress=progress,
        cancel=cancel,
    )


# --- Instalación ------------------------------------------------------------


def running_folder() -> Path:
    """Carpeta desde la que se está ejecutando esta copia.

    Con el ejecutable empaquetado es la carpeta del `.exe`; desde el código fuente, la
    raíz del proyecto (dos niveles por encima de `pctoolkit/core/`).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def installation_folder(folder: Path | None = None) -> Path | None:
    """Carpeta de la instalación, o `None` si esta copia no salió del instalador.

    La marca es el archivo que el instalador deja junto al ejecutable
    (`.pctoolkit_instalacion.json`, con la lista de lo que copió). Un `.exe` portátil no
    lo lleva: se puede copiar a cualquier sitio y entonces no hay nada que actualizar,
    solo un archivo nuevo que descargar.
    """
    carpeta = Path(folder) if folder else running_folder()
    try:
        if (carpeta / MANIFEST_NAME).is_file():
            return carpeta
    except OSError as exc:  # pragma: no cover - carpeta ilegible
        logs.warning(f"No se pudo mirar si {carpeta} es una instalación", exc, name="actualizador")
    return None


def is_portable(folder: Path | None = None) -> bool:
    """Si esta copia se ejecuta como versión portátil (`True`) o instalada (`False`)."""
    return installation_folder(folder) is None


def launch_installer(path: Path, target_dir: Path | None = None) -> None:
    """Ejecuta el instalador descargado, sin ventana, y vuelve enseguida.

    Se lanza en modo silencioso (`--silent`) y con la carpeta de la instalación actual
    (`--dir`): la aplicación se cierra para que el instalador pueda reemplazar sus
    archivos, así que no habría nadie para pulsar «Instalar» en el asistente. El
    instalador cierra cualquier copia que siguiera abierta, instala encima conservando
    los datos del usuario y vuelve a abrir la versión nueva al terminar.

    El proceso se despega del nuestro (`DETACHED_PROCESS`): tiene que sobrevivir a que
    la aplicación se cierre un instante después.
    """
    ruta = Path(path)
    if not ruta.exists():
        raise UpdateError(f"No se encuentra el instalador descargado ({ruta}).")
    comando = [str(ruta), "--silent"]
    if target_dir is not None:
        comando += ["--dir", str(target_dir)]
    try:
        subprocess.Popen(  # noqa: S603 - es el instalador que se acaba de verificar
            comando,
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as exc:
        raise UpdateError(f"No se pudo abrir el instalador: {exc}") from exc
