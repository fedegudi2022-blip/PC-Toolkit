"""Lógica del instalador de PC Toolkit: copiar, accesos directos, registro y desinstalación.

Está separada de la interfaz para poder probarse sin tocar nada del equipo: todas
las rutas (carpeta de destino, escritorio, menú Inicio, nombre de la clave de
registro) se pueden sustituir, y las pruebas usan carpetas temporales y una clave
de registro propia.

Decisiones de diseño:

* **Instalación por usuario**: todo va a `%LOCALAPPDATA%\\Programs\\PC Toolkit`, así
  que **no hace falta ser administrador** ni pedir UAC.
* **Accesos directos** del escritorio y del menú Inicio, creados con el COM de
  Windows a través de PowerShell (sin dependencias extra).
* **Arranque automático** opcional: un valor en `HKCU\\...\\Run` que lanza la
  aplicación con `--bandeja`, es decir, sin mostrar la ventana.
* **Desinstalación limpia**: quita accesos directos, entradas de registro, la
  tarea programada si existía y la carpeta de programa; los datos del usuario
  (historial, informes, preferencias) se conservan salvo que se pidan borrar.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

IS_WINDOWS = os.name == "nt"

APP_KEY = "PCToolkit"
APP_DISPLAY_NAME = "PC Toolkit"
APP_PUBLISHER = "PCToolkit"
UNINSTALL_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "PCToolkit"

UNINSTALLER_NAME = "Desinstalar PC Toolkit.exe"
APP_EXE_NAME = "PC Toolkit.exe"

# Registro de lo que puso aquí el instalador. Es lo que permite que la desinstalación
# borre **solo** los archivos de la aplicación: sin él, la alternativa sería vaciar la
# carpeta entera, y esa carpeta la elige el usuario (podría ser D:\ o Documentos).
MANIFEST_NAME = ".pctoolkit_instalacion.json"

# La aplicación viaja comprimida dentro del propio instalador, así que la descarga es
# un solo archivo: se extrae a una carpeta temporal al instalar y se borra al terminar.
# Si el instalador se reparte junto a la carpeta de la aplicación (o se ejecuta desde
# el repositorio), la carga no existe y se usa la carpeta de al lado.
PAYLOAD_NAME = "pc_toolkit_app.zip"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- Rutas -----------------------------------------------------------------


def local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Local"


def roaming_app_data() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def default_install_dir() -> Path:
    """Carpeta de instalación por defecto (por usuario, sin permisos)."""
    return local_app_data() / "Programs" / APP_DISPLAY_NAME


def desktop_dir() -> Path:
    """Escritorio real del usuario (contempla el escritorio redirigido a OneDrive)."""
    return known_folder("Desktop", Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop")


def start_menu_dir() -> Path:
    """Carpeta de programas del menú Inicio (la real, no la supuesta)."""
    fallback = roaming_app_data() / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return known_folder("Programs", fallback)


def user_data_dir() -> Path:
    """Datos generados por la aplicación (historial, informes, preferencias)."""
    return roaming_app_data() / APP_KEY


@lru_cache(maxsize=8)
def known_folder(name: str, fallback: Path) -> Path:
    """Pregunta a Windows por una carpeta especial del usuario.

    Importa porque el escritorio puede estar redirigido (por ejemplo a OneDrive),
    y en ese caso `%USERPROFILE%\\.Desktop` no es la carpeta que ve el usuario. Si la
    consulta falla, se usa la ruta previsible.
    """
    if not IS_WINDOWS:
        return fallback
    try:
        result = _powershell(f"[Environment]::GetFolderPath({_ps_quote(name)})", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return fallback
    value = (result.stdout or "").strip()
    if value and Path(value).is_absolute():
        return Path(value)
    return fallback


def source_dir() -> Path:
    """Carpeta que contiene la aplicación a instalar.

    Empaquetado: la aplicación viaja junto al instalador, dentro de una subcarpeta
    con su nombre (o directamente a su lado, si se colocan juntos).
    En el repositorio: la compilación de `dist\\PC Toolkit`.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidata = base / APP_DISPLAY_NAME
        if (candidata / APP_EXE_NAME).exists():
            return candidata
        return base
    return repo_root() / "dist" / APP_DISPLAY_NAME


def app_beside() -> bool:
    """¿Hay una aplicación ya lista junto al instalador?

    Repartida en carpeta (o al ejecutar el instalador desde `dist\\` mientras se
    desarrolla) la aplicación está al lado y se usa tal cual: no hace falta sacarla
    de la carga, que es más lento.
    """
    if not getattr(sys, "frozen", False):
        return False
    base = Path(sys.executable).resolve().parent
    return (base / APP_DISPLAY_NAME / APP_EXE_NAME).exists() or (base / APP_EXE_NAME).exists()


def payload_path() -> Path | None:
    """El ZIP con la aplicación que viaja dentro del instalador, si lo lleva.

    PyInstaller saca sus datos a `sys._MEIPASS`, y ahí está la carga cuando el
    instalador se compiló con `build_installer.py`. Si no hay carga (versión
    repartida junto a la carpeta), devuelve `None`.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    ruta = Path(base) / PAYLOAD_NAME
    return ruta if ruta.is_file() else None


def unpack_payload(destination: Path | None = None) -> Path | None:
    """Extrae la carga incrustada y devuelve la carpeta con la aplicación.

    Devuelve `None` si el instalador no lleva carga. El ZIP se revisa antes de
    extraerlo: una ruta absoluta o con `..` no puede escribir fuera de la carpeta
    temporal (el paquete podría haber sido manipulado).

    Sin `destination` crea una carpeta temporal, y **el que llama se encarga de
    borrarla**: `install()` la quita al terminar, salga bien o mal.
    """
    carga = payload_path()
    if carga is None:
        return None
    destino = Path(destination) if destination else Path(tempfile.mkdtemp(prefix="pctoolkit_carga_"))
    destino.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(carga) as archivo:
        for miembro in archivo.infolist():
            nombre = Path(miembro.filename)
            if nombre.is_absolute() or ".." in nombre.parts:
                raise ValueError(f"La carga trae una ruta que no se admite: {miembro.filename}")
        archivo.extractall(destino)
    if not app_executable(destino).exists():
        shutil.rmtree(destino, ignore_errors=True)
        raise ValueError("La carga incrustada no trae la aplicación.")
    return destino


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def app_executable(folder: Path | None = None) -> Path:
    return (folder or default_install_dir()) / APP_EXE_NAME


def uninstaller_executable(folder: Path | None = None) -> Path:
    return (folder or default_install_dir()) / UNINSTALLER_NAME


def uninstall_key(key_name: str = APP_KEY) -> str:
    return f"{UNINSTALL_SUBKEY}\\{key_name}"


# --- Modelo ----------------------------------------------------------------


@dataclass
class InstallOptions:
    """Qué se instala y dónde."""

    target_dir: Path = field(default_factory=default_install_dir)
    desktop_shortcut: bool = True
    start_menu_shortcut: bool = True
    autostart: bool = False
    launch_after: bool = True
    source: Path | None = None
    key_name: str = APP_KEY
    run_value: str = RUN_VALUE
    desktop: Path | None = None
    start_menu: Path | None = None

    @property
    def source_path(self) -> Path:
        return Path(self.source) if self.source else source_dir()

    @property
    def desktop_path(self) -> Path:
        return Path(self.desktop) if self.desktop else desktop_dir()

    @property
    def start_menu_path(self) -> Path:
        return Path(self.start_menu) if self.start_menu else start_menu_dir()

    def shortcut_paths(self) -> list[Path]:
        paths: list[Path] = []
        if self.desktop_shortcut:
            paths.append(self.desktop_path / f"{APP_DISPLAY_NAME}.lnk")
        if self.start_menu_shortcut:
            paths.append(self.start_menu_path / f"{APP_DISPLAY_NAME}.lnk")
        return paths


@dataclass
class InstallResult:
    ok: bool
    message: str = ""
    files: int = 0
    bytes: int = 0
    shortcuts: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    installed_dir: str = ""
    seconds: float = 0.0

    def summary(self) -> str:
        if not self.ok:
            return self.message or "La operación no se pudo completar."
        if self.removed:
            return f"Desinstalado: {len(self.removed)} elementos eliminados."
        size = self.bytes / (1024 * 1024)
        return f"Instalado: {self.files} archivos ({size:.1f} MB) y {len(self.shortcuts)} accesos directos."


# --- Utilidades ------------------------------------------------------------


def directory_size(path: Path) -> tuple[int, int]:
    """Tamaño total y número de archivos de una carpeta."""
    total = 0
    count = 0
    for item in Path(path).rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
                count += 1
        except OSError:
            continue
    return total, count


def is_running(exclude_pid: int | None = None) -> list[int]:
    """Procesos de PC Toolkit abiertos (para pedir que se cierren antes de instalar)."""
    if not IS_WINDOWS:
        return []
    exclude = exclude_pid if exclude_pid is not None else os.getpid()
    found: list[int] = []
    try:
        import psutil

        for process in psutil.process_iter(["pid", "name"]):
            try:
                name = process.info["name"] or ""
                pid = int(process.info["pid"])
            except (KeyError, TypeError, ValueError):
                continue
            if pid == exclude:
                continue
            if name.lower() in ("pc toolkit.exe", "pc toolkit"):
                found.append(pid)
    except ImportError:  # pragma: no cover - psutil siempre está instalado
        return []
    return found


def close_running_apps(timeout: float = 8.0) -> tuple[bool, str]:
    """Cierra las instancias abiertas de la aplicación (pide permiso antes de llamar)."""
    import psutil

    pids = is_running()
    if not pids:
        return True, "No había ninguna instancia abierta."
    for pid in pids:
        try:
            process = psutil.Process(pid)
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running():
            return True, f"Se cerraron {len(pids)} instancia(s) abiertas de PC Toolkit."
        time.sleep(0.25)
    return False, "No se pudieron cerrar todas las instancias; ciérralas a mano e inténtalo de nuevo."


# --- Accesos directos ------------------------------------------------------


def _powershell(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    # Se fuerza la salida en UTF-8 para que los mensajes y las rutas con acentos
    # lleguen legibles (si no, PowerShell usa la página de códigos de la consola).
    script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script
    from pctoolkit.core.tools import powershell_environment

    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
        # El `PSModulePath` heredado (por ejemplo de PowerShell 7) deja a Windows
        # PowerShell sin poder cargar sus módulos, y con ellos el COM de los accesos
        # directos o las carpetas del sistema fallan.
        env=powershell_environment(),
    )


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(
    link: Path,
    target: Path,
    *,
    arguments: str = "",
    description: str = "",
    workdir: Path | None = None,
    icon: Path | None = None,
    attempts: int = 2,
) -> tuple[bool, str]:
    """Crea un acceso directo (.lnk) de Windows (con un reintento si falla)."""
    if not IS_WINDOWS:
        return False, "Los accesos directos solo existen en Windows."
    try:
        Path(link).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"No se pudo preparar la carpeta del acceso directo: {exc}"
    script = (
        f"$w = New-Object -ComObject WScript.Shell;"
        f"$s = $w.CreateShortcut({_ps_quote(str(link))});"
        f"$s.TargetPath = {_ps_quote(str(target))};"
        f"$s.WorkingDirectory = {_ps_quote(str(workdir or Path(target).parent))};"
        f"$s.Description = {_ps_quote(description or APP_DISPLAY_NAME)};"
        f"$s.IconLocation = {_ps_quote(str(icon or target) + ',0')};"
        f"$s.Arguments = {_ps_quote(arguments)};"
        f"$s.Save()"
    )
    last_error = ""
    for intento in range(max(1, attempts)):
        try:
            result = _powershell(script)
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = str(exc)
            continue
        if result.returncode == 0 and Path(link).exists():
            return True, str(link)
        last_error = _clean_error(result.stderr or result.stdout)
        time.sleep(0.4)
    return False, f"No se pudo crear el acceso directo {Path(link).name}: {last_error}"


def _clean_error(text: str) -> str:
    """Mensaje de error de una sola línea, sin caracteres raros."""
    limpio = " ".join((text or "").split())
    return limpio.replace("\ufffd", "?")


def read_shortcut_target(link: Path) -> str:
    """Destino real de un acceso directo (útil para comprobar la instalación)."""
    if not IS_WINDOWS or not Path(link).exists():
        return ""
    script = f"(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(link))}).TargetPath"
    try:
        result = _powershell(script, timeout=45)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def _norm(path: str | os.PathLike[str] | None) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(str(path)))


def unsafe_target_reason(target: Path | None) -> str:
    """Por qué no se debe instalar (ni borrar) en esa carpeta; cadena vacía si vale.

    La carpeta de destino la elige el usuario, y tanto la instalación como la
    desinstalación escriben y borran dentro. Apuntar a la raíz de una unidad, al
    perfil, al escritorio o a una carpeta del sistema sería catastrófico, así que
    se rechaza de entrada en vez de confiar en que nadie lo haga.
    """
    if target is None:
        return "No se indicó ninguna carpeta de instalación."
    ruta = Path(target)
    normal = _norm(ruta)
    if os.path.splitdrive(normal)[1] in ("", os.sep):
        return (
            f"No se puede instalar en {ruta}: es la raíz de una unidad. "
            "Elige una carpeta dentro, por ejemplo una nueva llamada «PC Toolkit»."
        )
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    unidad = os.environ.get("SystemDrive") or os.path.splitdrive(system_root)[0] or "C:"
    perfil = os.environ.get("USERPROFILE") or str(Path.home())
    candidatos = [
        system_root,
        os.path.join(system_root, "System32"),
        os.path.join(unidad + os.sep),
        os.path.join(unidad + os.sep, "Users"),
        perfil,
        os.path.join(perfil, "Desktop"),
        os.path.join(perfil, "Documents"),
        os.path.join(perfil, "Downloads"),
        local_app_data(),
        roaming_app_data(),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramData"),
    ]
    prohibidas = {_norm(item) for item in candidatos if item}
    if normal in prohibidas:
        return (
            f"No se puede instalar en {ruta}: es una carpeta del sistema, del perfil o una "
            "carpeta personal. Elige una carpeta propia, por ejemplo una nueva llamada «PC Toolkit»."
        )
    return ""


def manifest_path(folder: Path | None = None) -> Path:
    return Path(folder or default_install_dir()) / MANIFEST_NAME


def read_manifest(folder: Path | None = None) -> dict[str, Any] | None:
    """Registro de los archivos instalados (None si no hay o no se puede leer)."""
    try:
        datos = json.loads(manifest_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("archivos"), list):
        return None
    return datos


def installed_files(folder: Path | None = None) -> list[str]:
    """Rutas relativas de los archivos que hay ahora en la carpeta de la aplicación."""
    raiz = Path(folder or default_install_dir())
    if not raiz.is_dir():
        return []
    return sorted(
        item.relative_to(raiz).as_posix()
        for item in raiz.rglob("*")
        if item.is_file() and item.name != MANIFEST_NAME
    )


def write_manifest(folder: Path, files: list[str], version: str) -> bool:
    """Deja por escrito qué se instaló, con escritura atómica."""
    ruta = manifest_path(folder)
    contenido = json.dumps(
        {
            "version": version,
            "instalado": time.strftime("%Y-%m-%d %H:%M:%S"),
            "archivos": sorted(files),
        },
        ensure_ascii=False,
        indent=2,
    )
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_text(contenido, encoding="utf-8")
    os.replace(temporal, ruta)
    return True


def _remove_files(folder: Path, relatives: list[str]) -> tuple[int, list[str]]:
    """Borra los archivos indicados (rutas relativas). Devuelve (borrados, en uso).

    Se ignoran las rutas absolutas y las que se salen de la carpeta: el registro es un
    archivo de texto y no se le concede el poder de escribir en otro sitio.
    """
    borrados = 0
    fallidos: list[str] = []
    for relativo in relatives:
        parte = Path(str(relativo))
        if parte.is_absolute() or ".." in parte.parts:
            continue
        ruta = Path(folder) / parte
        try:
            if ruta.is_file():
                ruta.unlink()
                borrados += 1
        except OSError:
            fallidos.append(str(relativo))
    return borrados, fallidos


def _prune_empty_dirs(folder: Path) -> None:
    """Retira las subcarpetas que hayan quedado vacías (no toca la carpeta raíz)."""
    raiz = Path(folder)
    for root, _dirs, _files in os.walk(raiz, topdown=False):
        if Path(root) == raiz:
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass  # todavía tiene algo o está en uso


def remove_path(path: Path) -> bool:
    """Elimina un archivo o una carpeta si existe."""
    try:
        if Path(path).is_dir():
            shutil.rmtree(path, ignore_errors=False)
        elif Path(path).exists():
            Path(path).unlink()
        return True
    except OSError:
        return False


def schedule_directory_removal(path: Path, attempts: int = 20, wait_seconds: int = 2) -> bool:
    """Programa el borrado de una carpeta cuando este proceso haya terminado.

    Es lo que permite que el desinstalador viva dentro de la misma carpeta que
    borra. El trabajo lo hace un pequeño archivo `.bat` suelto que insiste varias
    veces (con un archivo por medio) hasta que la carpeta se puede borrar, porque
    hasta que este proceso no termina, sus archivos están en uso.
    """
    if not IS_WINDOWS:
        return False
    script = (
        "@echo off\r\n"
        f'set "objetivo={path}"\r\n'
        f"for /l %%i in (1,1,{max(1, attempts)}) do (\r\n"
        '  if not exist "%objetivo%" goto fin\r\n'
        '  rmdir /s /q "%objetivo%" 2>nul\r\n'
        f"  ping -n {max(1, wait_seconds)} 127.0.0.1 >nul\r\n"
        ")\r\n"
        ":fin\r\n"
        'del "%~f0" >nul 2>nul\r\n'
    )
    try:
        handle = Path(tempfile.gettempdir()) / f"pctoolkit_borrar_{abs(hash(str(path))) % 100000}.bat"
        handle.write_text(script, encoding="cp1252", errors="replace")
        # DETACHED_PROCESS deja el proceso suelto (no depende de nosotros) y
        # CREATE_NO_WINDOW no puede combinarse con él, así que no se usa aquí.
        subprocess.Popen(
            ["cmd", "/c", str(handle)],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        return False
    return True


# --- Registro de Windows ---------------------------------------------------


def _winreg():
    import winreg  # noqa: PLC0415 (solo existe en Windows)

    return winreg


def write_uninstall_entry(
    options: InstallOptions,
    *,
    version: str,
    size_bytes: int = 0,
) -> tuple[bool, str]:
    """Registra la aplicación en «Aplicaciones instaladas» y define su desinstalador."""
    if not IS_WINDOWS:
        return False, "El registro de Windows no existe en este sistema."
    winreg = _winreg()
    folder = Path(options.target_dir)
    values = {
        "DisplayName": APP_DISPLAY_NAME,
        "DisplayVersion": version,
        "Publisher": APP_PUBLISHER,
        "InstallLocation": str(folder),
        "UninstallString": f'"{uninstaller_executable(folder)}"',
        "QuietUninstallString": f'"{uninstaller_executable(folder)}" --silencioso',
        "DisplayIcon": f"{app_executable(folder)},0",
        "EstimatedSize": int(max(0, size_bytes) / 1024),
        "NoModify": 1,
        "NoRepair": 1,
        "InstallDate": time.strftime("%Y%m%d"),
    }
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, uninstall_key(options.key_name), 0, winreg.KEY_WRITE)
        with key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ if isinstance(value, str) else winreg.REG_DWORD, value)
    except OSError as exc:
        return False, f"No se pudo registrar la aplicación: {exc}"
    return True, "Aplicación registrada en «Aplicaciones instaladas»."


def read_uninstall_entry(key_name: str = APP_KEY) -> dict[str, Any] | None:
    """Lee lo que quedó registrado (o None si no está instalada)."""
    if not IS_WINDOWS:
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key(key_name)) as key:
            data: dict[str, Any] = {}
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                data[name] = value
                index += 1
            return data
    except OSError:
        return None


def remove_uninstall_entry(key_name: str = APP_KEY) -> bool:
    if not IS_WINDOWS:
        return False
    winreg = _winreg()
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, uninstall_key(key_name))
        return True
    except OSError:
        return False


def set_autostart(enabled: bool, target: Path | None = None, value_name: str = RUN_VALUE) -> tuple[bool, str]:
    """Activa o desactiva el arranque automático al iniciar sesión."""
    if not IS_WINDOWS:
        return False, "El registro de Windows no existe en este sistema."
    winreg = _winreg()
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_SUBKEY, 0, winreg.KEY_WRITE)
        with key:
            if enabled:
                command = f'"{target or app_executable()}" --bandeja'
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, value_name)
                except FileNotFoundError:
                    pass  # ya estaba desactivado
    except OSError as exc:
        return False, f"No se pudo cambiar el arranque automático: {exc}"
    return True, "Arranque automático activado." if enabled else "Arranque automático desactivado."


def autostart_command(value_name: str = RUN_VALUE) -> str:
    """Comando registrado para el arranque automático (vacío si no está activo)."""
    if not IS_WINDOWS:
        return ""
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_SUBKEY) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
            return str(value)
    except OSError:
        return ""


def remove_scheduled_task() -> str:
    """Quita la tarea del Programador de Windows si el usuario la había creado."""
    try:
        from pctoolkit.core import scheduler

        if not scheduler.task_exists():
            return ""
        result = scheduler.delete_task()
        return result.message
    except Exception:  # noqa: BLE001 - la desinstalación no debe fallar por esto
        return ""


# --- Instalación y desinstalación -----------------------------------------


def install(options: InstallOptions, progress: Callable[[str], None] | None = None) -> InstallResult:
    """Copia la aplicación, crea accesos directos y registra todo.

    Si el instalador lleva la aplicación dentro (la descarga de un solo archivo), se
    extrae a una carpeta temporal que se borra al terminar, salga bien o mal.
    """
    temporal: Path | None = None
    if options.source is None and not app_beside() and payload_path() is not None:
        if progress:
            progress("Preparando los archivos de la aplicación…")
        try:
            temporal = unpack_payload()
        except (OSError, ValueError) as exc:
            return InstallResult(
                ok=False,
                installed_dir=str(options.target_dir),
                message=f"No se pudo preparar la aplicación: {exc}",
            )
    try:
        return _install(options, temporal, progress)
    finally:
        if temporal is not None:
            shutil.rmtree(temporal, ignore_errors=True)


def _install(
    options: InstallOptions,
    origin: Path | None,
    progress: Callable[[str], None] | None,
) -> InstallResult:
    """Instalación propiamente dicha, con la carpeta de origen ya preparada."""
    started = time.perf_counter()
    result = InstallResult(ok=False, installed_dir=str(options.target_dir))

    def note(message: str) -> None:
        if progress:
            progress(message)

    if origin is None:
        origin = options.source_path
    if not origin.exists() or not app_executable(origin).exists():
        result.message = (
            f"No se encuentra la aplicación en {origin}. Genera antes el ejecutable con «build_exe.bat»."
        )
        return result
    destino = Path(options.target_dir)
    if destino.resolve() == Path(origin).resolve():
        result.message = "La carpeta de destino es la misma que la de origen."
        return result

    motivo = unsafe_target_reason(destino)
    if motivo:
        result.message = motivo
        return result

    running = is_running()
    if running:
        result.warnings.append(
            f"Hay {len(running)} instancia(s) de PC Toolkit abiertas; ciérralas para que la "
            "instalación quede del todo limpia."
        )

    anterior = read_manifest(destino)
    if anterior is not None:
        # Actualización: se quitan antes los archivos de la versión anterior para que no
        # queden restos (DLL viejas o módulos que ya no se usan) al copiar encima.
        note("Quitando la versión anterior…")
        _quitados, bloqueados = _remove_files(
            destino, [*anterior.get("archivos", []), MANIFEST_NAME]
        )
        if bloqueados:
            result.warnings.append(
                f"{len(bloqueados)} archivo(s) de la versión anterior estaban en uso; se han sobrescrito."
            )
    elif destino.is_dir() and any(item.is_file() for item in destino.rglob("*")):
        result.warnings.append(
            f"En {destino} ya había archivos que no puso PC Toolkit: la aplicación se instala "
            "junto a ellos y al desinstalar se conservarán."
        )

    note("Copiando archivos…")
    try:
        destino.mkdir(parents=True, exist_ok=True)
        shutil.copytree(origin, destino, dirs_exist_ok=True)
    except OSError as exc:
        result.message = f"No se pudieron copiar los archivos: {exc}"
        return result
    result.bytes, result.files = directory_size(destino)
    if result.files == 0:
        result.message = "La copia se quedó vacía: revisa los permisos de la carpeta de destino."
        return result

    # El registro se escribe **después** de medir, para que la medida describa lo que se
    # ha instalado y no se cuente a sí misma.
    from pctoolkit.core.config import APP_VERSION

    try:
        write_manifest(destino, installed_files(destino), APP_VERSION)
    except OSError as exc:
        result.warnings.append(
            f"No se pudo dejar el registro de archivos instalados: {exc}. "
            "La desinstalación conservará la carpeta para no borrar nada ajeno."
        )

    uninstaller = uninstaller_executable(options.target_dir)
    if not uninstaller.exists():
        # El desinstalador se construye aparte; si no viene con el paquete, se
        # avisa pero la instalación sigue siendo válida y desinstalable a mano.
        result.warnings.append(
            "No se encontró el desinstalador en el paquete; se puede desinstalar desde el Panel de control."
        )

    note("Creando accesos directos…")
    icon = app_executable(options.target_dir)
    for link in options.shortcut_paths():
        ok, message = create_shortcut(
            link,
            icon,
            description="Optimiza y mantén tu PC",
            workdir=options.target_dir,
            icon=icon,
        )
        if ok:
            result.shortcuts.append(str(link))
        else:
            result.warnings.append(message)

    note("Registrando la aplicación…")
    ok, message = write_uninstall_entry(options, version=APP_VERSION, size_bytes=result.bytes)
    if not ok:
        result.warnings.append(message)

    if options.autostart:
        note("Activando el arranque automático…")
        ok, message = set_autostart(True, icon, options.run_value)
        if not ok:
            result.warnings.append(message)

    result.seconds = time.perf_counter() - started
    result.ok = True
    result.message = result.summary()
    return result


def uninstall(
    *,
    target_dir: Path | None = None,
    key_name: str = APP_KEY,
    run_value: str = RUN_VALUE,
    desktop: Path | None = None,
    start_menu: Path | None = None,
    remove_user_data: bool = False,
    remove_task: bool = True,
    keep_folder: bool = False,
    progress: Callable[[str], None] | None = None,
) -> InstallResult:
    """Borra accesos directos, registro, tarea y la carpeta de programa."""
    result = InstallResult(ok=False, installed_dir=str(target_dir or default_install_dir()))

    def note(message: str) -> None:
        if progress:
            progress(message)

    folder = Path(target_dir or default_install_dir())
    desktop_path = Path(desktop) if desktop else desktop_dir()
    menu_path = Path(start_menu) if start_menu else start_menu_dir()
    # La carpeta la eligió el usuario: si apunta al sistema, al perfil o a la raíz de
    # una unidad, se quitan los accesos directos y el registro pero no se toca nada ahí.
    motivo = unsafe_target_reason(folder)

    for link in (desktop_path / f"{APP_DISPLAY_NAME}.lnk", menu_path / f"{APP_DISPLAY_NAME}.lnk"):
        if link.exists() and remove_path(link):
            result.removed.append(str(link))
    note("Accesos directos eliminados.")

    # El menú Inicio puede quedar con una carpeta propia vacía.
    group = menu_path / APP_DISPLAY_NAME
    if group.is_dir() and not any(group.iterdir()):
        if remove_path(group):
            result.removed.append(str(group))

    if remove_uninstall_entry(key_name):
        result.removed.append(f"Registro: {uninstall_key(key_name)}")
    if set_autostart(False, value_name=run_value)[0] and not autostart_command(run_value):
        result.removed.append("Arranque automático")
    if remove_task:
        message = remove_scheduled_task()
        if message:
            result.removed.append(message)

    if remove_user_data:
        if remove_path(user_data_dir()):
            result.removed.append(str(user_data_dir()))
        note("Datos del usuario eliminados.")

    if keep_folder:
        result.ok = True
        result.message = "Desinstalado (se ha conservado la carpeta de programa)."
        return result

    if motivo:
        result.warnings.append(f"{motivo} La carpeta se ha conservado.")
        result.message = "Desinstalado; se ha conservado la carpeta indicada."
        result.ok = True
        return result

    if not Path(folder).is_dir():
        result.message = "No se encontró la carpeta de la aplicación; no había archivos que borrar."
        result.ok = True
        return result

    note("Eliminando los archivos instalados…")
    manifest = read_manifest(folder)
    if manifest is None:
        # Sin el registro de lo que puso aquí la aplicación, vaciar la carpeta se
        # llevaria también lo que haya puesto el usuario: se conserva tal cual.
        result.warnings.append(
            f"No hay registro de los archivos instalados en {folder}: se han quitado los accesos "
            "directos, el registro y el arranque automático, pero la carpeta se conserva. "
            "Bórrala a mano si quieres liberar el espacio."
        )
        result.message = "Desinstalación parcial: se ha conservado la carpeta de programa."
        result.ok = True
        return result

    borrados, en_uso = _remove_files(folder, [*manifest.get("archivos", []), MANIFEST_NAME])
    if borrados:
        result.removed.append(f"{borrados} archivos de {folder}")
    if en_uso:
        result.warnings.append(
            f"{len(en_uso)} archivo(s) seguían en uso (el desinstalador se está ejecutando); "
            "se quitarán al borrar la carpeta."
        )
    _prune_empty_dirs(folder)

    # Lo que quede y no esté en el registro no es nuestro: si lo hay, la carpeta se
    # queda donde está y se dice qué se ha respetado.
    instalados = {str(item) for item in manifest.get("archivos", [])}
    ajenos = [
        item
        for item in Path(folder).rglob("*")
        if item.is_file() and item.relative_to(folder).as_posix() not in instalados
    ]
    if ajenos:
        result.warnings.append(
            f"Se conservaron {len(ajenos)} archivo(s) que no instaló PC Toolkit ("
            + ", ".join(sorted(item.name for item in ajenos)[:5])
            + "). La carpeta no se ha borrado para no llevárselos por delante."
        )
        result.message = "Desinstalado; se ha conservado la carpeta con tus archivos."
    else:
        note("Eliminando la carpeta de programa…")
        if remove_path(folder) or schedule_directory_removal(folder):
            result.removed.append(str(folder))
            result.message = "Desinstalado por completo."
        else:
            result.warnings.append(
                "No se pudo borrar la carpeta del programa; ciérrala y bórrala a mano si queda algo."
            )
            result.message = "Desinstalación incompleta."
    result.ok = True
    if not result.message:
        result.message = result.summary()
    return result
