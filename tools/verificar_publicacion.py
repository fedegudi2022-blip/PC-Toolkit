"""Comprueba una publicación descargando lo que reparte, como lo descarga cualquiera.

    .venv\\Scripts\\python.exe tools\\verificar_publicacion.py --etiqueta v1.5.0

Lo lanza `publicar.yml` en cuanto la publicación está creada, antes de dar la ejecución por
buena, y también se puede lanzar a mano contra cualquier etiqueta —una vieja, la última o la
de un ensayo— para saber si lo que reparte sigue cuadrando con lo que promete.

No hace falta ningún secreto: la descarga se pide **sin credencial ninguna**, por la misma
dirección que abre el navegador de quien pincha el enlace, con el `User-Agent` que usa la
propia aplicación. Un ensayo es un borrador y GitHub no lo sirve sin sesión (a propósito:
no lo ve nadie), así que solo en ese caso —y diciéndolo en el informe— se usa el token del
flujo, que es el único que puede verlo.

Lo que se comprueba, y por qué en esos sitios:

* **Las sumas, en los cuatro sitios donde aparecen.** Con que uno se quede atrás ya hay dos
  verdades distintas sobre el mismo archivo: el `<nombre>.sha256` de cada ejecutable (el
  primero que mira la comprobación de actualizaciones, `actualizador._sums_for`),
  `SHA256SUMS.txt`, el bloque de hashes de las notas de la publicación y el `digest` que
  calcula GitHub al subir cada adjunto.
* **Lo que está colgado es lo que se construyó**: con `--entrega dist\\entrega`, lo
  descargado tiene que pesar lo mismo que esos archivos, byte a byte.
* **El camino de la aplicación**: cuando esta publicación es la última, se le pregunta al
  propio actualizador qué versión ve alguien que tenga instalada una anterior, qué archivo
  repartiría y con qué hash; y que no se ofrezca a quien ya tiene esta versión.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pctoolkit.core import actualizador  # noqa: E402
from pctoolkit.core.config import APP_VERSION  # noqa: E402

# Un adjunto de 95 MB a 3 MB/s son 32 s: aquí se espera de sobra y no se corta una descarga
# que va lenta por la red del servidor.
TIMEOUT = 300.0

# El bloque de código de las notas que solo lleva hashes (el que lee la aplicación).
BLOQUE = re.compile(r"^```[^\n]*\n(.*?)^```", re.S | re.M)

issues: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    """Anota el resultado de una comprobación (mismo formato que el resto de herramientas)."""
    print(f"  [{'OK  ' if ok else 'FALLO'}] {nombre}" + (f"  ->  {detalle}" if detalle else ""))
    if not ok:
        issues.append(nombre)


def _sha256(ruta: Path) -> str:
    """SHA-256 de un archivo, leído por trozos (son ejecutables de decenas de MB)."""
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while trozo := archivo.read(actualizador.CHUNK):
            digest.update(trozo)
    return digest.hexdigest()


def _get_json(url: str, token: str = "") -> tuple[dict | None, int]:
    """Pide un JSON a la API de GitHub y devuelve el resultado con el código HTTP.

    Sin token va como un desconocido, que es lo que interesa: lo que vea un desconocido es
    lo que de verdad está publicado. El código se devuelve aunque la respuesta no traiga
    JSON, porque un 404 y un 403 no significan lo mismo (no existe / no deja sin sesión,
    frente a un límite de peticiones).
    """
    peticion = urllib.request.Request(url)
    peticion.add_header("User-Agent", actualizador.USER_AGENT)
    peticion.add_header("Accept", "application/vnd.github+json")
    if token:
        peticion.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(peticion, timeout=actualizador.TIMEOUT) as respuesta:
            return json.load(respuesta), respuesta.status
    except urllib.error.HTTPError as error:
        return None, error.code
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None, 0


def _descargar(url: str, destino: Path, token: str = "") -> str:
    """Descarga un adjunto y devuelve el motivo del fallo (o `""` si fue bien)."""
    peticion = urllib.request.Request(url)
    peticion.add_header("User-Agent", actualizador.USER_AGENT)
    if token:
        # Con token hay que pedir el contenido a la API: la dirección pública del navegador
        # (`/releases/download/…`) exige una sesión que pueda ver el borrador.
        peticion.add_header("Authorization", f"Bearer {token}")
        peticion.add_header("Accept", "application/octet-stream")
    try:
        with urllib.request.urlopen(peticion, timeout=TIMEOUT) as respuesta:
            with destino.open("wb") as archivo:
                shutil.copyfileobj(respuesta, archivo, actualizador.CHUNK)
    except urllib.error.HTTPError as error:
        return f"GitHub responde {error.code}"
    except (urllib.error.URLError, OSError) as error:
        return f"{type(error).__name__}: {error}"
    return ""


def _bloque_de_sumas(notas: str) -> str:
    """Bloque de las notas que solo lleva hashes, que es el que enseña la publicación.

    Se busca por contenido y no por el encabezado que hay delante: las notas las escribe el
    flujo, pero el mismo texto se puede retocar a mano desde GitHub.
    """
    for encontrado in BLOQUE.finditer(notas or ""):
        if actualizador._only_hashes(encontrado.group(1).splitlines()):  # noqa: SLF001
            return encontrado.group(1)
    return ""


def _formato_del_sha256(crudo: bytes, nombre: str) -> str:
    """Qué tiene de malo un archivo `<nombre>.sha256` (o `""` si está bien).

    La aplicación lo lee esperando un hash y nada más, así que se mira justo eso: que no
    lleve marca de orden de bytes, que no tenga finales de línea de Windows y que sea una
    sola línea con la suma y el nombre.
    """
    problemas: list[str] = []
    if crudo.startswith(b"\xef\xbb\xbf"):
        problemas.append("empieza con marca de orden de bytes")
    if b"\r" in crudo:
        problemas.append("lleva finales de línea de Windows")
    texto = crudo.decode("utf-8", errors="replace")
    lineas = [linea for linea in texto.splitlines() if linea.strip()]
    if len(lineas) != 1:
        problemas.append(f"{len(lineas)} líneas en vez de una")
        return ", ".join(problemas)
    trozos = lineas[0].split()
    if not trozos or not re.fullmatch(r"[0-9a-fA-F]{64}", trozos[0]):
        problemas.append("la primera cosa no es un SHA-256")
    if len(trozos) < 2 or Path(trozos[-1].lstrip("*")).name != nombre:
        problemas.append(f"no dice el nombre del archivo ({nombre})")
    return ", ".join(problemas)


def _leer_publicacion(api: str, etiqueta: str, token: str) -> tuple[dict | None, bool, int]:
    """La publicación, primero sin sesión y, si no la deja ver, con el token del flujo.

    Devuelve los datos, si se pudo leer sin sesión y el código de la negativa. Un ensayo
    **no** se puede pedir por etiqueta ni con permiso de escritura —la API contesta lo
    mismo que si la etiqueta no existiera—, así que ahí se busca en el listado, que sí lo
    trae cuando se pide con credencial.
    """
    datos, codigo = _get_json(f"{api}/releases/tags/{etiqueta}")
    if datos is not None:
        return datos, True, codigo
    if token:
        datos, _ = _get_json(f"{api}/releases/tags/{etiqueta}", token)
        if datos is not None:
            return datos, False, codigo
        listado, codigo_listado = _get_json(f"{api}/releases?per_page=100", token)
        for publicacion in listado or []:
            if publicacion.get("tag_name") == etiqueta:
                return publicacion, False, codigo
        return None, False, codigo_listado or codigo
    return None, False, codigo


def _version_anterior(api: str, etiqueta: str) -> str:
    """Versión de la publicación anterior, para preguntar como preguntaría una copia vieja.

    Es lo que hace la comprobación de actualizaciones de una copia instalada: preguntar si
    hay algo más nuevo que **lo suyo**. Si no hay ninguna anterior (la primera publicación),
    se pregunta como si la copia fuera la 0.0.0, que da el mismo resultado.
    """
    listado, _ = _get_json(f"{api}/releases?per_page=30")
    for publicacion in listado or []:
        if publicacion.get("draft") or publicacion.get("prerelease"):
            continue
        if publicacion.get("tag_name") == etiqueta:
            continue
        return str(publicacion.get("tag_name") or "").lstrip("vV")
    return "0.0.0"


def _analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        description="Comprueba una publicación descargando sus propios adjuntos.",
    )
    analizador.add_argument("--etiqueta", required=True, help="Etiqueta de la publicación (v1.5.0)")
    analizador.add_argument(
        "--version",
        default=APP_VERSION,
        help=f"Versión que debe llevar la publicación (por defecto, la del código: {APP_VERSION})",
    )
    analizador.add_argument(
        "--entrega",
        help="Carpeta con los archivos construidos (por ejemplo dist\\entrega) para comparar tamaños",
    )
    analizador.add_argument(
        "--repositorio",
        default=os.environ.get("GITHUB_REPOSITORY") or actualizador.GITHUB_REPO,
        help="Repositorio «dueño/nombre» (por defecto, el del proyecto)",
    )
    analizador.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "",
        help="Token para leer un ensayo (borrador); sin él se comprueba sin sesión",
    )
    return analizador


def main(argv: list[str] | None = None) -> int:
    argumentos = _analizador().parse_args(argv)
    version = argumentos.version
    etiqueta = argumentos.etiqueta
    repositorio = argumentos.repositorio
    token = argumentos.token
    api = f"https://api.github.com/repos/{repositorio}"
    publica = f"https://github.com/{repositorio}/releases/download/{etiqueta}"

    esperados = [
        f"PC-Toolkit-{version}-Setup.exe",
        f"PC-Toolkit-{version}-Setup.exe.sha256",
        f"PC-Toolkit-{version}-portable.exe",
        f"PC-Toolkit-{version}-portable.exe.sha256",
        actualizador.SUMS_ASSET,
    ]
    ejecutables = [nombre for nombre in esperados if nombre.endswith(".exe")]
    instalador = f"PC-Toolkit-{version}-Setup.exe"

    # --- La publicación, leída primero sin ninguna credencial --------------------
    datos, leida_sin_sesion, codigo = _leer_publicacion(api, etiqueta, token)
    if datos is None:
        print(f"No se pudo leer la publicación {etiqueta} de {repositorio}: GitHub responde {codigo}.")
        return 1

    es_borrador = bool(datos.get("draft"))
    print(f"Publicación {etiqueta} de {repositorio}: «{datos.get('name')}».")
    print(
        f"Se ha leído {'sin sesión' if leida_sin_sesion else f'con el token del flujo (sin sesión, GitHub responde {codigo})'}."
        f" Estado: {'borrador' if es_borrador else 'publicada'}"
        f"{' y preliminar' if datos.get('prerelease') else ''}."
    )

    # Un borrador no se puede descargar sin sesión: no es un defecto de la publicación, es
    # que todavía no es pública. En una publicación de verdad, en cambio, poder leerla y
    # descargarla sin credencial ninguna es justo lo que hay que comprobar.
    if es_borrador:
        print("Ensayo en borrador: se descarga con el token del flujo (sin sesión no lo sirve GitHub, a propósito).")
    else:
        check(
            "La publicación se puede leer sin sesión, como cualquiera",
            leida_sin_sesion,
            f"GitHub responde {codigo} a un desconocido",
        )

    # --- Los adjuntos, descargados ------------------------------------------------
    carpeta = Path(tempfile.mkdtemp(prefix="pctk_publicacion_"))
    print(f"\nDescargando los adjuntos de la publicación en {carpeta}"
          + (" (sin credencial ninguna)" if not es_borrador else "") + ":")
    colgados = {str(adjunto.get("name") or ""): adjunto for adjunto in datos.get("assets") or []}
    check(
        f"La publicación lleva los {len(esperados)} archivos de la {version}, ni uno más",
        sorted(colgados) == sorted(esperados),
        ", ".join(sorted(colgados)) if sorted(colgados) != sorted(esperados) else f"{len(colgados)} archivos",
    )

    for nombre in esperados:
        adjunto = colgados.get(nombre) or {}
        destino = carpeta / nombre
        url = (
            f"{publica}/{nombre}"
            if not es_borrador
            else str(adjunto.get("url") or "")
        )
        if not url:
            check(f"{nombre}: descargado", False, "la publicación no trae ese adjunto")
            continue
        fallo = _descargar(url, destino, token if es_borrador else "")
        if fallo:
            check(f"{nombre}: descargado", False, fallo)
            continue
        bytes_descargados = destino.stat().st_size
        anunciado = int(adjunto.get("size") or 0)
        check(
            f"{nombre}: descargado ({bytes_descargados} bytes)",
            bytes_descargados == anunciado,
            f"la API anuncia {anunciado} bytes",
        )

    # --- Lo que hay colgado es lo que se construyó --------------------------------
    if argumentos.entrega:
        entrega = Path(argumentos.entrega)
        if not entrega.is_dir():
            check("La entrega que se quería comparar existe", False, str(entrega))
        else:
            for nombre in esperados:
                descargado, construido = carpeta / nombre, entrega / nombre
                if not descargado.exists():
                    continue
                if not construido.exists():
                    check(f"{nombre}: está en la entrega", False, str(construido))
                    continue
                check(
                    f"{nombre}: lo publicado es lo construido (mismos bytes)",
                    descargado.stat().st_size == construido.stat().st_size
                    and _sha256(descargado) == _sha256(construido),
                    f"{descargado.stat().st_size} y {construido.stat().st_size} bytes",
                )
    else:
        print("\nSin --entrega: no se comparan los adjuntos con lo construido en este equipo.")

    # --- Las sumas, en los cuatro sitios donde aparecen ---------------------------
    sumas = (carpeta / actualizador.SUMS_ASSET).read_text(encoding="utf-8", errors="replace") if (carpeta / actualizador.SUMS_ASSET).exists() else ""
    notas = str(datos.get("body") or "")
    bloque = _bloque_de_sumas(notas)
    print("\nLas sumas, en los cuatro sitios donde aparecen:")
    if not bloque:
        check("Las notas de la publicación llevan el bloque de sumas", False, "no hay ningún bloque que solo lleve hashes")
    for nombre in ejecutables:
        ruta = carpeta / nombre
        if not ruta.exists():
            continue
        real = _sha256(ruta)
        adjunto = colgados.get(nombre) or {}

        propio = carpeta / f"{nombre}.sha256"
        crudo = propio.read_bytes() if propio.exists() else b""
        problemas = _formato_del_sha256(crudo, nombre) if crudo else "no se descargó"
        del_propio = actualizador.sha_for(crudo.decode("utf-8", errors="replace"), nombre)
        check(
            f"{nombre}: su archivo .sha256 coincide y está escrito como lo lee la aplicación",
            problemas == "" and del_propio == real,
            problemas or ("la suma no coincide" if del_propio != real else f"{real[:16]}…"),
        )
        del_resumen = actualizador.sha_for(sumas, nombre)
        check(
            f"{nombre}: el SHA256SUMS.txt coincide",
            del_resumen == real,
            "" if del_resumen == real else f"{del_resumen or 'sin la línea'} frente a {real}",
        )
        del_notas = actualizador.sha_for(bloque, nombre)
        check(
            f"{nombre}: el bloque de las notas coincide",
            del_notas == real,
            "" if del_notas == real else f"{del_notas or 'sin la línea'} frente a {real}",
        )
        digest = str(adjunto.get("digest") or "")
        check(
            f"{nombre}: el digest que calcula GitHub coincide",
            digest == f"sha256:{real}",
            digest or "GitHub no devolvió ningún digest",
        )

    # --- El camino de la aplicación ------------------------------------------------
    print("\nLo que vería quien tenga instalada una versión anterior:")
    ultima, _ = _get_json(f"{api}/releases/latest")
    tag_ultima = str((ultima or {}).get("tag_name") or "")
    if es_borrador:
        check(
            "Un ensayo no se ofrece a nadie: /releases/latest sigue siendo la última publicada",
            tag_ultima != etiqueta,
            f"/releases/latest devuelve {tag_ultima or 'nada'}",
        )
    elif tag_ultima != etiqueta:
        check(
            "Esta publicación es la última, que es la que mira la aplicación",
            False,
            f"/releases/latest devuelve {tag_ultima or 'nada'} y no {etiqueta}",
        )
    else:
        anterior = _version_anterior(api, etiqueta)
        nueva = actualizador.fetch_latest(current=anterior)
        check(
            f"Con la {anterior} instalada, la aplicación ve la {version}",
            nueva is not None and nueva.tag == etiqueta and nueva.version == version,
            f"ve {nueva.version} ({nueva.tag})" if nueva else "no ve nada nuevo",
        )
        if nueva is not None:
            instalado = carpeta / instalador
            esperado = _sha256(instalado) if instalado.exists() else ""
            check(
                "… con el instalador de esta publicación y su hash",
                nueva.asset.name == instalador and bool(esperado) and nueva.sha256 == esperado,
                f"{nueva.asset.name}, espera {nueva.sha256[:16]}…",
            )
            resumen = nueva.notes_excerpt()
            check(
                "… y con las notas del diálogo, sin la parte de comprobación",
                bool(resumen.strip()) and "comprobar la descarga" not in resumen.lower()
                and "Get-FileHash" not in resumen,
                f"{len(resumen)} caracteres",
            )
        check(
            f"La publicación no se ofrece a quien ya tiene la {version}",
            actualizador.fetch_latest(current=version) is None,
        )

    # Los archivos se quedan si algo no cuadró: mirarlos es lo primero que se quiere hacer,
    # y el servidor que los descargó se tira de todas formas.
    if issues:
        print(f"\nLos adjuntos descargados se quedan en {carpeta} para mirarlos.")
        print("\nFallos: " + ", ".join(issues))
        return 1
    shutil.rmtree(carpeta, ignore_errors=True)
    # El cierre dice lo mismo que se ha podido comprobar: en un ensayo no hay descarga sin
    # sesión ni aplicación que vea nada, y decir lo contrario sería dar por bueno lo que no
    # se ha mirado.
    if es_borrador:
        print(
            "\nTodo cuadra: el ensayo se descarga y sus sumas coinciden en los cuatro sitios; "
            "no se ofrece a nadie (es un borrador)."
        )
    else:
        print(
            "\nTodo cuadra: la publicación se descarga sin sesión, las sumas coinciden en los "
            "cuatro sitios y la aplicación ve esta versión."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
