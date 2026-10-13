"""Firma digital de los ejecutables de PC Toolkit con signtool (Authenticode).

Un ejecutable sin firma hace que Windows SmartScreen avise («Windows protegió tu
PC») y que algunos antivirus lo miren con lupa. Firmarlo con un certificado de
firma de código resuelve el aviso de «editor desconocido» y permite comprobar
que el archivo no se ha modificado desde que se firmó.

Uso habitual (después de compilar con build_exe.bat y build_installer.bat):

    python firmar.py --estado                  Informe: signtool, certificados y firmas actuales
    python firmar.py --traer-signtool          Descarga signtool (SDK BuildTools) a tools\\bin
    python firmar.py --huella ABC123…          Firma con un certificado de tu almacén de Windows
    python firmar.py --pfx llave.pfx           Firma con un archivo PFX
    python firmar.py --sujeto "Mi Empresa SL"  Firma buscando el certificado por nombre
    python firmar.py --autoprueba              Comprueba todo el proceso con un certificado de prueba
    python firmar.py --comprobar               Falla (código 1) si algo no está bien firmado

Las mismas opciones sirven para firmar al terminar de compilar:

    python build_exe.py --firmar --pfx llave.pfx
    python build_installer.py --firmar --huella 1A2B3C…

Si se prefiere no repetir las opciones (o no se pueden escribir en el comando,
como en un servidor de compilación), se dejan en la variable de entorno
`PCTOOLKIT_SIGN_ARGS`, que es lo que usan `.github/workflows/firmar.yml` y
cualquier integración continua:

    set PCTOOLKIT_SIGN_ARGS=--dlib C:\\tools\\Azure.CodeSigning.Dlib.dll --metadatos metadata.json
    python build_installer.py --firmar

Certificados con la clave en un HSM o en un servicio remoto (que es como se
emiten hoy los de confianza pública):

    python firmar.py --csp "DigiCert Signing Manager KSP" --clave ALIAS --cert-publico cert.crt
    python firmar.py --dlib Azure.CodeSigning.Dlib.dll --metadatos metadata.json

La contraseña del PFX nunca se pasa en la línea de comandos si se puede evitar:
se lee de `PCTOOLKIT_CERT_PASSWORD` o del archivo indicado con `--password-file`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:  # la consola de Windows puede usar cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from pctoolkit.core.config import APP_NAME, APP_VERSION  # noqa: E402
from pctoolkit.core.tools import powershell_environment  # noqa: E402

DIST = ROOT / "dist"
APP_EXE = DIST / APP_NAME / "PC Toolkit.exe"
UNINSTALLER_EXE = DIST / APP_NAME / "Desinstalar PC Toolkit.exe"
SETUP_EXE = DIST / "PC Toolkit Setup.exe"
ONEFILE_EXE = DIST / "PC Toolkit.exe"
APP_FOLDER = DIST / APP_NAME

SIGTOOL_LOCAL = ROOT / "tools" / "bin" / "signtool" / "signtool.exe"
NUGET_PACKAGE = "microsoft.windows.sdk.buildtools"
NUGET_INDEX = f"https://api.nuget.org/v3-flatcontainer/{NUGET_PACKAGE}/index.json"
DESCRIPTION = f"{APP_NAME} - caja de herramientas para Windows"

# Sello de tiempo RFC 3161: deja constancia de cuándo se firmó, de modo que la
# firma siga siendo válida cuando el certificado caduque.
SELLO_POR_DEFECTO = "http://timestamp.digicert.com"
SELLOS_CONOCIDOS = (
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
    "http://timestamp.globalsign.com/tsa/r45standard",
    "http://timestamp.acs.microsoft.com",
)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IS_WINDOWS = os.name == "nt"

# Variable de entorno que leen los scripts de compilación con `--firmar`. Evita
# tener que escribir las opciones de firma en cada orden y permite dejar el
# certificado en los secretos de un servidor de compilación.
SIGN_ARGS_ENV = "PCTOOLKIT_SIGN_ARGS"

CARACTERES_CERTIFICADO = (
    "--pfx",
    "--huella",
    "--sujeto",
    "--csp",
)

# Estados que devuelve `Get-AuthenticodeSignature` cuando el archivo **sí** lleva firma
# pero esta no vale en este equipo, con la explicación que se enseña al comprobarla.
# Es lo que ocurre con un certificado de prueba o con una cadena de confianza incompleta.
FIRMA_SIN_CONFIANZA = {
    "UnknownError": "la cadena termina en una raíz que este equipo no confía",
    "NotTrusted": "el certificado no es de confianza en este equipo",
    "HashMismatch": "la firma no coincide con el contenido del archivo",
}


# --- Localizar signtool ----------------------------------------------------

def signtool_candidates() -> list[Path]:
    """Sitios donde suele estar signtool, del más probable al menos."""
    encontrados: list[Path] = []
    en_path = shutil.which("signtool") or shutil.which("signtool.exe")
    if en_path:
        encontrados.append(Path(en_path))
    if SIGTOOL_LOCAL.exists():
        encontrados.append(SIGTOOL_LOCAL)
    kits = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ]
    for base in kits:
        for patron in ("Windows Kits/10/bin/*/x64/signtool.exe", "Windows Kits/10/bin/*/x86/signtool.exe"):
            encontrados += sorted(base.glob(patron), reverse=True)
    cache = Path.home() / ".nuget" / "packages" / NUGET_PACKAGE
    if cache.is_dir():
        for version in sorted(cache.iterdir(), reverse=True):
            encontrados += sorted(version.glob("bin/*/x64/signtool.exe"), reverse=True)
    # Windows de 64 bits: también la versión x64 de herramientas de escritorio.
    return [ruta for ruta in encontrados if ruta.exists()]


def find_signtool(explicit: str | None = None) -> Path | None:
    if explicit:
        ruta = Path(explicit)
        return ruta if ruta.exists() else None
    candidatos = signtool_candidates()
    return candidatos[0] if candidatos else None


def fetch_signtool(destination: Path | None = None, version: str | None = None, log=print) -> Path | None:
    """Descarga signtool del paquete oficial Microsoft.Windows.SDK.BuildTools.

    `version` permite fijar la versión del paquete (además de la última estable),
    que es lo que se hace en el flujo de compilación para que la firma no dependa
    del signtool que traiga el equipo: algunos SDK antiguos no valen para firmar
    en la nube.
    """
    destino = destination or SIGTOOL_LOCAL
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        log("  Consultando el paquete oficial en nuget.org…")
        with urllib.request.urlopen(NUGET_INDEX, timeout=60) as respuesta:
            versiones = json.load(respuesta)["versions"]
        if version:
            if version not in versiones:
                log(f"  [!] El paquete no tiene la versión {version} (revisa --signtool-version).")
                return None
        else:
            estables = [v for v in versiones if "-" not in v]
            if not estables:
                log("  [!] No se encontró ninguna versión estable del paquete.")
                return None
            version = estables[-1]
        url = f"https://api.nuget.org/v3-flatcontainer/{NUGET_PACKAGE}/{version}/{NUGET_PACKAGE}.{version}.nupkg"
        log(f"  Descargando Microsoft.Windows.SDK.BuildTools {version}…")
        with tempfile.TemporaryDirectory(prefix="pctoolkit_signtool_") as tmp:
            paquete = Path(tmp) / "buildtools.nupkg"
            with urllib.request.urlopen(url, timeout=300) as respuesta, paquete.open("wb") as salida:
                shutil.copyfileobj(respuesta, salida)
            log(f"  Paquete descargado ({paquete.stat().st_size / 1048576:.1f} MB); extrayendo signtool…")
            with zipfile.ZipFile(paquete) as archivo:
                dentro = [
                    n for n in archivo.namelist()
                    if n.lower().endswith("x64/signtool.exe") and "bin/" in n.lower()
                ]
                if not dentro:
                    log("  [!] El paquete no contiene signtool.exe para x64.")
                    return None
                # Se prefiere la carpeta de SDK de versión más alta.
                dentro.sort(reverse=True)
                with archivo.open(dentro[0]) as origen:
                    with destino.open("wb") as salida:
                        shutil.copyfileobj(origen, salida)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        log(f"  [!] No se pudo descargar signtool: {exc}")
        return None
    log(f"  signtool listo en {display_path(destino)}")
    return destino


def signtool_version(exe: Path) -> str:
    """Versión de signtool, tomada del Windows SDK del que sale la carpeta."""
    # `signtool.exe` no lleva la versión del SDK en ningún recurso consultable de
    # forma fiable, pero la carpeta que lo contiene sí (`...\bin\10.0.28000.0\x64`
    # o la versión del paquete NuGet).
    for parte in exe.parts[::-1]:
        if parte[:2].isdigit() and parte.count(".") >= 2:
            return parte
    return ""


# --- Certificado -----------------------------------------------------------

@dataclass
class CertificateSpec:
    """Cómo se selecciona el certificado con el que se firma."""

    modo: str = "auto"  # auto | pfx | almacen | hsm | azure
    pfx: Path | None = None
    password: str = ""
    huella: str = ""
    sujeto: str = ""
    cert_publico: Path | None = None
    csp: str = ""
    clave: str = ""
    dlib: Path | None = None
    metadatos: Path | None = None

    def describe(self) -> str:
        if self.modo == "pfx":
            return f"archivo PFX ({Path(self.pfx).name})"
        if self.modo == "almacen" and self.huella:
            return f"certificado del almacén con huella {self.huella[:12]}…"
        if self.modo == "almacen" and self.sujeto:
            return f"certificado del almacén de «{self.sujeto}»"
        if self.modo == "hsm":
            return f"clave en HSM/token ({self.csp} · {self.clave})"
        if self.modo == "azure":
            return f"firma en la nube (Azure Artifact Signing · {Path(self.metadatos).name})"
        return "certificado disponible en el almacén (/a)"

    def arguments(self) -> list[str]:
        if self.modo == "pfx":
            argumentos = ["/f", str(self.pfx)]
            if self.password:
                argumentos += ["/p", self.password]
            return argumentos
        if self.modo == "almacen" and self.huella:
            return ["/sha1", self.huella]
        if self.modo == "almacen" and self.sujeto:
            return ["/n", self.sujeto]
        if self.modo == "hsm":
            argumentos = ["/csp", self.csp, "/kc", self.clave]
            if self.cert_publico:
                argumentos += ["/f", str(self.cert_publico)]
            return argumentos
        if self.modo == "azure":
            return ["/dlib", str(self.dlib), "/dmdf", str(self.metadatos)]
        return ["/a"]


# --- Ejecución -------------------------------------------------------------

def _powershell(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    preparado = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", preparado],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
        env=powershell_environment(),
    )


def run_signtool(signtool: Path, args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(signtool), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
    )


def sign_command(signtool: Path, cert: CertificateSpec, files: list[Path], sello: str, descripcion: str) -> list[str]:
    """Comando exacto de `signtool sign` (se muestra para poder copiarlo)."""
    args = ["sign", "/v", "/fd", "SHA256"]
    if sello:
        args += ["/tr", sello, "/td", "SHA256"]
    args += cert.arguments()
    args += ["/d", descripcion]
    return args + [str(archivo) for archivo in files]


def visible_command(signtool: Path, args: list[str], password: str = "") -> str:
    """El comando tal y como se puede copiar, con la contraseña oculta."""
    limpio = [("«contraseña»" if password and parte == password else parte) for parte in args]
    return " ".join([f'"{signtool}"', *[f'"{p}"' if " " in p else p for p in limpio]])


def signature_info(archivo: Path) -> dict:
    """Estado de la firma de un archivo (firma presente, quién y cuándo)."""
    if not IS_WINDOWS or not archivo.exists():
        return {}
    script = (
        f"$s = Get-AuthenticodeSignature -LiteralPath '{archivo}'; "
        "$o = [pscustomobject]@{ Status = $s.Status.ToString(); "
        "Thumbprint = if ($s.SignerCertificate) { $s.SignerCertificate.Thumbprint } else { '' }; "
        "Subject = if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' }; "
        "Sello = if ($s.TimeStamperCertificate) { $s.TimeStamperCertificate.Subject } else { '' } }; "
        "$o | ConvertTo-Json -Compress"
    )
    try:
        salida = _powershell(script, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    texto = (salida.stdout or "").strip()
    if not texto:
        return {}
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return {}


def verify_signature(signtool: Path, archivo: Path) -> tuple[bool, str, str]:
    """Verifica la firma. Devuelve (correcta, resumen, salida completa)."""
    try:
        resultado = run_signtool(signtool, ["verify", "/pa", "/v", str(archivo)])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"no se pudo ejecutar signtool: {exc}", ""
    salida = (resultado.stdout or "") + (resultado.stderr or "")
    if resultado.returncode == 0:
        correcta = True
        resumen = "firma verificada correctamente (cadena de confianza completa)"
    else:
        correcta = False
        resumen = _motivo_verificacion(salida)
    return correcta, resumen, salida


def _motivo_verificacion(salida: str) -> str:
    """Traduce el error de signtool a algo entendible."""
    texto = " ".join(salida.split())
    if "root certificate which is not trusted" in texto or "no reconoce" in texto or "not trusted" in texto:
        return (
            "la firma existe, pero la cadena termina en una raíz que Windows no confía "
            "(lo normal con certificados autofirmados o de prueba)"
        )
    if "No signature found" in texto or "not signed" in texto.lower():
        return "el archivo no está firmado"
    if "hash" in texto.lower() and "does not match" in texto.lower():
        return "la firma no coincide con el archivo: puede estar modificado o dañado"
    ultimas = [linea.strip() for linea in salida.splitlines() if "Error" in linea or "error" in linea]
    return ultimas[-1] if ultimas else "no se pudo verificar la firma"


def display_path(archivo: Path) -> str:
    """Ruta legible: relativa al proyecto si está dentro y completa si no lo está.

    Los archivos suelen estar en `dist\\`, pero también se puede firmar cualquier
    otro (una copia, un ejecutable suelto), y ahí `relative_to` fallaría.
    """
    try:
        return str(Path(archivo).relative_to(ROOT))
    except ValueError:
        return str(archivo)


def artifacts(selected: list[str] | None = None) -> list[Path]:
    """Ejecutables que hay que firmar (los que existan)."""
    if selected:
        return [Path(item) for item in selected if Path(item).exists()]
    candidatos = [APP_EXE, UNINSTALLER_EXE, SETUP_EXE, ONEFILE_EXE]
    return [archivo for archivo in candidatos if archivo.exists()]


def split_arguments(texto: str) -> list[str]:
    """Divide una cadena de argumentos respetando las comillas y las barras invertidas.

    No se usa `shlex.split` porque en modo POSIX se come las barras de las rutas de
    Windows (`C:\\tools\\llave.pfx`); en modo texto las conserva y solo hay que
    quitar las comillas que envuelven al argumento.
    """
    lexer = shlex.shlex(texto, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return [parte.strip("\"'") for parte in lexer if parte.strip("\"'")]


def environment_arguments() -> list[str]:
    """Argumentos de firma que deja el entorno en PCTOOLKIT_SIGN_ARGS.

    Es la forma de firmar en un servidor de compilación sin escribir el comando
    completo, y la que usa el flujo de `.github/workflows/firmar.yml`:

        set PCTOOLKIT_SIGN_ARGS=--dlib C:\\tools\\Azure.CodeSigning.Dlib.dll --metadatos metadata.json
    """
    texto = os.environ.get(SIGN_ARGS_ENV, "").strip()
    return split_arguments(texto) if texto else []


def prepare_signing(args, log=print) -> tuple[Path | None, CertificateSpec | None]:
    """Localiza signtool y decide con qué certificado se firma.

    Devuelve `(signtool, certificado)` o `(None, None)` explicando el motivo. Lo
    comparten la línea de comandos de `firmar.py` y los scripts de compilación que
    firman con `--firmar`, para que los mensajes de error sean los mismos.
    """
    signtool = find_signtool(args.signtool)
    if signtool is None:
        log("  [!] No se encontró signtool.")
        log("      Instálalo con el Windows SDK, añade --signtool <ruta> o descárgalo con:")
        log("      python firmar.py --traer-signtool")
        return None, None

    if args.password:
        log("  Aviso: la contraseña pasada con --password queda visible en la lista de procesos.")
        log("         Es mejor usar PCTOOLKIT_CERT_PASSWORD o --password-file.")

    cert = build_spec(args)
    if cert.modo == "auto" and not certificate_store():
        log("  [!] No hay ningún certificado de firma de código en tu almacén y no se ha indicado")
        log("      ninguno (--pfx, --huella, --sujeto, --csp o --dlib); sin certificado no se firma.")
        log("      Pruébalo con uno de prueba:  python firmar.py --autoprueba")
        return None, None
    if cert.modo == "pfx" and (cert.pfx is None or not Path(cert.pfx).exists()):
        log(f"  [!] No existe el archivo PFX: {cert.pfx}")
        return None, None
    if cert.modo == "hsm" and not (cert.csp and cert.clave):
        log("  [!] Para firmar con la clave en un token o HSM hacen falta --csp y --clave.")
        return None, None
    if cert.modo == "azure" and (cert.dlib is None or not Path(cert.dlib).exists()):
        log(f"  [!] No existe la biblioteca de firma en la nube: {cert.dlib}")
        return None, None
    if cert.modo == "azure" and (cert.metadatos is None or not Path(cert.metadatos).exists()):
        log("  [!] Para firmar en la nube hacen falta --dlib y --metadatos (archivos existentes).")
        return None, None
    return signtool, cert


def sign_paths(
    files: list[Path],
    argv: list[str] | None = None,
    descripcion: str | None = None,
    log=print,
) -> bool:
    """Firma los archivos indicados con los mismos argumentos que `firmar.py`.

    Es la puerta que usan `build_exe.py --firmar` y `build_installer.py --firmar`:
    recibe los argumentos de firma tal cual (`--pfx`, `--huella`, `--dlib`…) y firma
    esos archivos, comprobando el resultado. Nunca firma algo que no exista, y sin
    certificado explica cómo conseguir uno en lugar de fallar en silencio.
    """
    archivos = [Path(archivo) for archivo in files]
    if not archivos:
        log("  [!] No hay ningún archivo que firmar.")
        return False
    faltan = [str(archivo) for archivo in archivos if not archivo.exists()]
    if faltan:
        log("  [!] No se puede firmar algo que no existe: " + ", ".join(faltan))
        return False

    args = build_parser().parse_args([str(parte) for parte in (argv or [])])
    if args.traer_signtool and not fetch_signtool(version=args.signtool_version, log=log):
        return False
    if args.estado:
        show_status(find_signtool(args.signtool), Path(args.signtool) if args.signtool else None)
        return True
    if args.comprobar:
        return check_signatures(archivos, log=log)

    sello = "" if args.sin_sello else args.sello
    signtool, cert = prepare_signing(args, log=log)
    if signtool is None or cert is None:
        return False
    return sign_artifacts(signtool, cert, archivos, sello, descripcion or args.descripcion)


def check_signatures(files: list[Path] | None = None, log=print) -> bool:
    """¿Están firmados con una firma válida en este equipo? (para la compilación)

    Devuelve False en cuanto uno no lo esté, que es lo que queremos tanto en un
    servidor de compilación (`firmar.py --comprobar`) como en una orden manual.
    """
    archivos = [Path(archivo) for archivo in (files if files is not None else artifacts())]
    if not archivos:
        log("  [!] No hay ningún ejecutable que comprobar en dist/.")
        return False
    correcto = True
    for archivo in archivos:
        info = signature_info(archivo)
        estado = info.get("Status") or "sin datos"
        firmante = _nombre_corto(info.get("Subject") or "sin firma")
        sello = "con sello de tiempo" if info.get("Sello") else "sin sello de tiempo"
        if estado == "Valid":
            log(f"  OK    {archivo.name}: {firmante} · {sello}")
            continue
        # «Sin firma» y «firma que existe pero no es de fiar» fallan igual (es lo que
        # necesita un servidor de compilación), pero no significan lo mismo: en un
        # servidor, lo segundo casi siempre es un certificado de prueba o una cadena de
        # confianza incompleta, y saberlo ahorra media hora de búsqueda. Se distinguen.
        motivo = FIRMA_SIN_CONFIANZA.get(estado)
        if motivo and info.get("Thumbprint"):
            log(f"  FALLO {archivo.name}: hay firma de {firmante} ({sello}), pero {motivo}.")
        else:
            log(f"  FALLO {archivo.name}: {estado} · {sello}")
        correcto = False
    return correcto


# --- Órdenes ---------------------------------------------------------------

def show_status(signtool: Path | None, exe: Path | None = None) -> int:
    """Informe del estado: signtool, certificados disponibles y firmas actuales."""
    print(f"{APP_NAME} {APP_VERSION} · firma digital con signtool\n")
    if signtool:
        version = signtool_version(signtool)
        print(f"signtool: {signtool}" + (f"  (versión {version})" if version else ""))
    else:
        print("signtool: NO ENCONTRADO")
        print("  - Instálalo con el Windows SDK («Windows SDK Signing Tools»), o bien")
        print("  - descárgalo del paquete oficial con:  python firmar.py --traer-signtool")

    print("\nCertificados de firma de código en tu almacén (CurrentUser\\My):")
    certificados = certificate_store()
    if certificados:
        for item in certificados:
            print(f"  - {item.get('Subject', '?')}")
            print(f"      huella: {item.get('Thumbprint', '?')}   caduca: {item.get('NotAfter', '?')[:10]}")
    else:
        print("  (ninguno)  Los de confianza pública se emiten con la clave en un token o HSM,")
        print("  o desde un servicio como Azure Artifact Signing (antes Trusted Signing).")

    print("\nFirmas de los ejecutables compilados:")
    archivos = artifacts()
    if not archivos:
        print("  (no hay ejecutables en dist/; compila antes con build_exe.bat y build_installer.bat)")
    for archivo in archivos:
        info = signature_info(archivo)
        estado = info.get("Status") or "sin datos"
        quien = info.get("Subject") or "sin firma"
        sello = "con sello de tiempo" if info.get("Sello") else "sin sello de tiempo"
        print(f"  - {display_path(archivo)}: {estado} · {quien} · {sello}")
        if info.get("Status") == "NotSigned":
            print("      -> se firmará con `python firmar.py --huella <huella>` (o --pfx, --dlib…)")
    return 0


def certificate_store() -> list[dict]:
    """Certificados de firma de código del almacén del usuario."""
    if not IS_WINDOWS:
        return []
    script = (
        "Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert -ErrorAction SilentlyContinue | "
        "Select-Object Subject, Thumbprint, @{n='NotAfter';e={$_.NotAfter.ToString('s')}} | "
        "ConvertTo-Json -Compress"
    )
    try:
        salida = _powershell(script, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    texto = (salida.stdout or "").strip()
    if not texto:
        return []
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        return []
    return datos if isinstance(datos, list) else [datos]


def build_spec(args) -> CertificateSpec:
    """Deduce de los argumentos cómo se firma."""
    if args.dlib:
        return CertificateSpec(
            modo="azure", dlib=Path(args.dlib), metadatos=Path(args.metadatos) if args.metadatos else None
        )
    if args.csp:
        return CertificateSpec(
            modo="hsm", csp=args.csp, clave=args.clave or "", cert_publico=Path(args.cert_publico) if args.cert_publico else None
        )
    if args.pfx:
        password = args.password or os.environ.get("PCTOOLKIT_CERT_PASSWORD", "")
        if not password and args.password_file:
            try:
                password = Path(args.password_file).read_text(encoding="utf-8").strip().splitlines()[0]
            except (OSError, IndexError):
                password = ""
        return CertificateSpec(modo="pfx", pfx=Path(args.pfx), password=password)
    if args.huella:
        return CertificateSpec(modo="almacen", huella=args.huella.replace(" ", ""))
    if args.sujeto:
        return CertificateSpec(modo="almacen", sujeto=args.sujeto)
    return CertificateSpec(modo="auto")


def sign_artifacts(signtool: Path, cert: CertificateSpec, files: list[Path], sello: str, descripcion: str) -> bool:
    """Firma los archivos y verifica el resultado."""
    print(f"Firmando con {cert.describe()}")
    print(f"  Sello de tiempo: {sello or '(ninguno)'}")
    correcto = True
    for archivo in files:
        before = signature_info(archivo)
        if before.get("Status") == "Valid":
            # Se reemplaza la firma: hay que quitarla antes de volver a firmar.
            run_signtool(signtool, ["remove", "/s", str(archivo)])
        args = sign_command(signtool, cert, [archivo], sello, descripcion)
        print(f"\n  {display_path(archivo)}")
        print(f"    {visible_command(signtool, args, cert.password)}")
        inicio = time.perf_counter()
        try:
            resultado = run_signtool(signtool, args)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"    [!] No se pudo ejecutar signtool: {exc}")
            correcto = False
            continue
        segundos = time.perf_counter() - inicio
        if resultado.returncode != 0:
            print(f"    [!] Falló la firma (código {resultado.returncode}):")
            print("    " + " ".join((resultado.stdout or resultado.stderr or "").split())[:400])
            correcto = False
            continue
        ok, resumen, _salida = verify_signature(signtool, archivo)
        info = signature_info(archivo)
        firmante = _nombre_corto(info.get("Subject") or "")
        sello_info = f" · sello: {_hora_sello(info.get('Sello'))}" if info.get("Sello") else " · sin sello de tiempo"
        if ok:
            print(f"    OK en {segundos:.1f} s: firma de {firmante}, verificada{sello_info}")
        elif "raíz" in resumen:
            # Firma creada y correcta en lo criptográfico, pero de una CA que este
            # equipo no confía (certificado de prueba o autofirmado).
            print(f"    Firma creada en {segundos:.1f} s con {firmante}{sello_info}")
            print(f"    Aviso: {resumen}")
            print("    Para que SmartScreen la acepte hace falta un certificado de confianza pública.")
        else:
            print(f"    [!] {resumen}")
            correcto = False
    return correcto


def _atributo(subject: str, clave: str) -> str:
    """Valor de un campo del nombre distinguido (CN, O…).

    Se corta solo en las comas que separan campos del nombre, para que un CN que
    lleve una coma dentro no se recorte (por ejemplo «PC Toolkit (prueba, no usar)»).
    """
    partes = re.split(r",\s*(?=[A-Za-z]{1,4}=)", str(subject or ""))
    for parte in partes:
        if parte.strip().upper().startswith(f"{clave}="):
            return parte.strip()[len(clave) + 1 :]
    return ""


def _nombre_corto(subject: str) -> str:
    return _atributo(subject, "CN") or subject or "certificado desconocido"


def _hora_sello(subject: str) -> str:
    return _atributo(subject, "CN") or "sí"


def autotest(signtool: Path, sello: str) -> bool:
    """Prueba el proceso completo con un certificado autofirmado y temporales.

    Se firman copias, nunca los ejecutables buenos, y al terminar se borra el
    certificado de prueba y todo lo demás. Así se comprueba que la firma, el
    sello de tiempo y la verificación funcionan antes de comprar un certificado.
    """
    print("Autoprueba: se firma con un certificado de PRUEBA (no sirve para repartir).\n")
    originales = artifacts()
    if not originales:
        print("[!] No hay ejecutables en dist/ que copiar. Compila primero.")
        return False
    # Se prueban el programa y el instalador, que son los dos que se reparten.
    muestras = [archivo for archivo in (APP_EXE, SETUP_EXE) if archivo.exists()] or originales[:2]

    huella = ""
    with tempfile.TemporaryDirectory(prefix="pctoolkit_firma_") as tmp:
        carpeta = Path(tmp)
        password_file = carpeta / "clave.txt"
        password = _random_password()
        password_file.write_text(password + "\n", encoding="utf-8")
        pfx = carpeta / "prueba.pfx"
        try:
            huella, mensaje = _create_test_certificate(pfx, password)
            print(f"  {mensaje}")
            if not huella:
                return False
            copias = []
            for archivo in muestras:
                copia = carpeta / archivo.name
                shutil.copyfile(archivo, copia)
                copias.append(copia)
            print(f"  Copias para la prueba: {', '.join(copia.name for copia in copias)}")

            cert = CertificateSpec(modo="pfx", pfx=pfx, password=password)
            correctas = 0
            for copia in copias:
                args = sign_command(signtool, cert, [copia], sello, DESCRIPTION)
                try:
                    resultado = run_signtool(signtool, args)
                except (OSError, subprocess.SubprocessError) as exc:
                    print(f"  [!] signtool no se pudo ejecutar: {exc}")
                    return False
                if resultado.returncode != 0 and sello:
                    # Sin red el sello falla; se reintenta sin sello para separar
                    # el problema de la firma del de la conexión.
                    texto = " ".join(((resultado.stdout or "") + (resultado.stderr or "")).split())
                    print(f"  [!] Con sello de tiempo falló; se reintenta sin sello ({texto[:120]}…)")
                    resultado = run_signtool(signtool, sign_command(signtool, cert, [copia], "", DESCRIPTION))
                    sello = ""
                if resultado.returncode != 0:
                    print(f"  [!] No se pudo firmar {copia.name}: {resultado.returncode}")
                    return False
                ok, resumen, _salida = verify_signature(signtool, copia)
                info = signature_info(copia)
                firmante = _nombre_corto(info.get("Subject") or "")
                if ok:
                    estado = "firma verificada (cadena de confianza completa)"
                    correctas += 1
                elif "raíz" in resumen:
                    estado = "firma presente y legible; solo falta una CA de confianza (esperado con autofirmado)"
                    correctas += 1
                else:
                    estado = resumen
                print(f"  - {copia.name}: {estado}")
                print(f"      firmante: {firmante} · huella: {(info.get('Thumbprint') or '')[:16]}…")
                print(f"      sello de tiempo: {'sí' if info.get('Sello') else 'no'}")

            # Los buenos no se han tocado: siguen sin firma.
            intactos = all((signature_info(archivo).get("Status") == "NotSigned") for archivo in originales)
            print(f"  Los ejecutables de dist/ siguen sin tocar: {'sí' if intactos else 'NO'}")
            if not intactos:
                correctas = 0

            if correctas == len(copias) and intactos:
                print("\nAutoprueba correcta: firma, verificación y sello de tiempo funcionan.")
                print("Para distribuir hace falta un certificado de confianza pública (token/HSM o servicio en la nube).")
                return True
            print("\n[!] La autoprueba no ha salido bien.")
            return False
        finally:
            if huella:
                _remove_test_certificate(huella)
    return False


def _random_password() -> str:
    import secrets
    import string

    alfabeto = string.ascii_letters + string.digits + "!#%+"
    return "".join(secrets.choice(alfabeto) for _ in range(24))


def _create_test_certificate(pfx: Path, password: str) -> tuple[str, str]:
    """Crea un certificado autofirmado de prueba y lo exporta a PFX."""
    script = (
        "$seguro = ConvertTo-SecureString -String $env:PCTOOLKIT_TEST_PASSWORD -AsPlainText -Force; "
        "$cert = New-SelfSignedCertificate -Type CodeSigningCert "
        "-Subject 'CN=PC Toolkit (prueba, no usar para distribuir)' "
        "-KeyUsage DigitalSignature -KeyExportPolicy Exportable "
        "-CertStoreLocation 'Cert:\\CurrentUser\\My' -NotAfter (Get-Date).AddDays(7); "
        f"Export-PfxCertificate -Cert $cert -FilePath '{pfx}' -Password $seguro | Out-Null; "
        "$cert.Thumbprint"
    )
    # El `PSModulePath` heredado deja a Windows PowerShell sin el módulo PKI, que es
    # justo el que trae `New-SelfSignedCertificate`.
    entorno = dict(powershell_environment(), PCTOOLKIT_TEST_PASSWORD=password)
    try:
        resultado = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=entorno,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"No se pudo crear el certificado de prueba: {exc}"
    huella = (resultado.stdout or "").strip().splitlines()[-1].strip() if (resultado.stdout or "").strip() else ""
    if resultado.returncode != 0 or not huella or not pfx.exists():
        # Se devuelve lo que dijo PowerShell: sin esto, un fallo del almacén y uno del
        # módulo PKI se ven igual («¿permisos del almacén?») y no hay por dónde tirar.
        detalle = " ".join((resultado.stderr or resultado.stdout or "").split())
        return "", f"No se pudo crear el certificado de prueba (código {resultado.returncode}): {detalle[:300]}"
    return huella, f"Certificado de prueba creado (huella {huella[:12]}…), exportado a PFX con contraseña temporal"


def _remove_test_certificate(huella: str) -> None:
    """Borra el certificado de prueba del almacén y avisa si no puede.

    No basta con lanzar el `Remove-Item` y olvidarse: si falla, queda en el almacén del
    usuario una clave privada con la que se puede firmar código, y como `signtool` sin
    certificado indicado usa `/a` (el que encuentre), una prueba podría acabar firmando
    con el certificado de prueba en vez de con el de verdad.
    """
    if not huella:
        return
    ruta = f"Cert:\\CurrentUser\\My\\{huella}"
    script = (
        f"Remove-Item -LiteralPath '{ruta}' -Force -ErrorAction SilentlyContinue; "
        f"if (Test-Path -LiteralPath '{ruta}') {{ 'SIGUE' }} else {{ 'BORRADO' }}"
    )
    for _intento in range(2):
        try:
            salida = _powershell(script, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if "BORRADO" in (salida.stdout or ""):
            return
        time.sleep(0.5)
    print(
        f"  [!] El certificado de prueba {huella[:12]}… sigue en el almacén «Personal» del "
        "usuario. Bórralo con certmgr.msc: es una clave con la que se puede firmar código."
    )


# --- Línea de comandos -----------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Firma digital de los ejecutables de {APP_NAME} con signtool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python firmar.py --estado\n"
            "  python firmar.py --comprobar\n"
            "  python firmar.py --traer-signtool\n"
            "  python firmar.py --huella 1A2B3C…\n"
            "  python firmar.py --pfx llave.pfx --password-file clave.txt\n"
            "  python firmar.py --csp \"DigiCert Signing Manager KSP\" --clave alias --cert-publico cert.crt\n"
            "  python firmar.py --dlib Azure.CodeSigning.Dlib.dll --metadatos metadata.json\n"
            "  python firmar.py --autoprueba\n"
        ),
    )
    parser.add_argument("archivos", nargs="*", help="Archivos a firmar (por defecto, los de dist/)")
    parser.add_argument("--estado", action="store_true", help="Solo muestra el informe del estado y sale")
    parser.add_argument(
        "--comprobar",
        action="store_true",
        help="Comprueba las firmas y falla (código 1) si alguna no es válida",
    )
    parser.add_argument("--traer-signtool", action="store_true", help="Descarga signtool del paquete oficial de Microsoft")
    parser.add_argument(
        "--signtool-version",
        help="Versión del paquete Microsoft.Windows.SDK.BuildTools (por defecto, la última estable)",
    )
    parser.add_argument("--signtool", help="Ruta a signtool.exe (si no está en el PATH ni en tools\\bin)")
    parser.add_argument("--autoprueba", action="store_true", help="Prueba el proceso con un certificado autofirmado")
    parser.add_argument("--pfx", help="Archivo PFX con el certificado y su clave")
    parser.add_argument("--password-file", help="Archivo con la contraseña del PFX (primera línea)")
    parser.add_argument("--password", help="Contraseña del PFX (mejor usar PCTOOLKIT_CERT_PASSWORD o --password-file)")
    parser.add_argument("--huella", help="Huella del certificado en el almacén de Windows")
    parser.add_argument("--sujeto", help="Nombre del certificado en el almacén (búsqueda por sujeto)")
    parser.add_argument("--csp", help="Proveedor criptográfico para la clave en token/HSM")
    parser.add_argument("--clave", help="Alias o contenedor de la clave en el token/HSM")
    parser.add_argument("--cert-publico", help="Certificado público (.crt/.cer) para la firma con HSM")
    parser.add_argument("--dlib", help="Biblioteca de firma en la nube (Azure Artifact Signing)")
    parser.add_argument("--metadatos", help="Archivo JSON de metadatos de la firma en la nube")
    parser.add_argument("--sello", default=SELLO_POR_DEFECTO, help=f"Sello de tiempo RFC 3161 (por defecto {SELLO_POR_DEFECTO})")
    parser.add_argument("--sin-sello", action="store_true", help="Firma sin sello de tiempo (no recomendado)")
    parser.add_argument("--descripcion", default=DESCRIPTION, help="Descripción que acompaña a la firma")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sello = "" if args.sin_sello else args.sello

    if args.traer_signtool:
        destino = fetch_signtool(version=args.signtool_version)
        if not destino:
            return 1
        print(f"signtool disponible en {destino}")

    signtool = find_signtool(args.signtool)
    if args.estado:
        return show_status(signtool, Path(args.signtool) if args.signtool else None)

    if args.comprobar:
        return 0 if check_signatures(artifacts(args.archivos or None)) else 1

    if signtool is None:
        print("No se encontró signtool.")
        print("  Instálalo con el Windows SDK o ejecuta:  python firmar.py --traer-signtool")
        return 1

    if args.autoprueba:
        return 0 if autotest(signtool, sello) else 1

    archivos = artifacts(args.archivos or None)
    if not archivos:
        print("No hay nada que firmar: no existen los ejecutables en dist/.")
        print("Compila primero con build_exe.bat y build_installer.bat (o build_installer.py).")
        return 1

    signtool, cert = prepare_signing(args)
    if signtool is None or cert is None:
        return 1

    ok = sign_artifacts(signtool, cert, archivos, sello, args.descripcion)
    print()
    if ok:
        print("Firma terminada. Comprobación final:")
        for archivo in archivos:
            correcta, resumen, _salida = verify_signature(signtool, archivo)
            print(f"  {'OK ' if correcta else 'AVISO'} {archivo.name}: {resumen}")
        return 0
    print("La firma no se completó. Revisa los mensajes anteriores.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
