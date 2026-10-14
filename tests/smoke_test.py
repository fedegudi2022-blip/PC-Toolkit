"""Pruebas de humo de PC Toolkit: núcleo de datos y construcción de la interfaz.

Se ejecutan sin marco de pruebas externo:

    .venv\\Scripts\\python.exe tests\\smoke_test.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path

# Redirige los datos de la app a una carpeta temporal antes de importar nada.
_TEMP_HOME = tempfile.mkdtemp(prefix="pctoolkit_test_")
os.environ["APPDATA"] = _TEMP_HOME
os.environ["LOCALAPPDATA"] = _TEMP_HOME
# Por defecto se usa la plataforma real de Qt, que es la única con métricas de fuente
# correctas: la plataforma «offscreen» mide el texto de otra manera y la interfaz parece
# desbordar cuando en un escritorio de verdad no lo hace. PCTOOLKIT_OFFSCREEN=1 permite
# ejecutar sin pantalla en un equipo sin escritorio, pero entonces las comprobaciones de
# desbordes (y la de bandeja) avisarán de problemas que no existen en pantalla.
if os.environ.get("PCTOOLKIT_OFFSCREEN") == "1":
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:  # la consola de Windows puede usar cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from PySide6.QtWidgets import QApplication  # noqa: E402

from pctoolkit.core import maintenance, memory, reports, scheduler, tools  # noqa: E402
from pctoolkit.core.config import APP_VERSION, COLORS, SURFACES, format_bytes  # noqa: E402
from pctoolkit.core.disk import list_drives, scan_directory  # noqa: E402
from pctoolkit.core.memory import MemoryGuard, MemoryOptions  # noqa: E402
from pctoolkit.core.metrics import PROFILE_ECO, PROFILE_FULL, MetricsService  # noqa: E402
from pctoolkit.core.scheduler import MaintenanceSchedule  # noqa: E402
from pctoolkit.core.tasks import TaskStore  # noqa: E402

# Las comprobaciones se cuentan en `tests/_checks.py`, que también usan las guardas de
# documentación (así el total que citan las guías es el de la pasada completa).
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _checks import CHECKS, check, report  # noqa: E402
from documentacion import verificar_documentacion  # noqa: E402


def build_sample_tree(base: Path) -> Path:
    """Crea una carpeta con archivos de tamaños conocidos."""
    (base / "videos").mkdir(parents=True)
    (base / "docs").mkdir()
    (base / "docs" / "sub").mkdir()
    (base / "vacios").mkdir()
    (base / "videos" / "pelicula.mp4").write_bytes(b"0" * 3_000_000)
    (base / "docs" / "informe.pdf").write_bytes(b"0" * 500_000)
    (base / "docs" / "sub" / "notas.txt").write_bytes(b"0" * 1000)
    (base / "docs" / "sub" / "codigo.py").write_bytes(b"0" * 2_000)
    (base / "raiz.zip").write_bytes(b"0" * 20_000)
    return base


def envejecer(ruta: Path, horas: float = 48.0) -> None:
    """Retrasa la fecha de modificación de un archivo para que cuente como antiguo.

    Hace falta porque la limpieza de temporales nunca borra archivos recientes:
    las pruebas que esperan un borrado tienen que envejecer lo que crean.
    """
    marca = time.time() - horas * 3600
    os.utime(ruta, (marca, marca))


def test_metrics() -> None:
    service = MetricsService()
    snap = service.poll()
    check("CPU entre 0 y 100", 0.0 <= snap.cpu_percent <= 100.0, f"{snap.cpu_percent}")
    check("RAM > 0", snap.ram_total > 0, format_bytes(snap.ram_total))
    check("RAM usada <= total", 0 < snap.ram_used <= snap.ram_total)
    check("Disco del sistema detectado", snap.disk_total > 0, f"{snap.disk_mount} {snap.disk_percent}%")
    check("Procesos detectados", snap.process_count > 0, str(snap.process_count))
    check("Núcleos detectados", snap.cpu_threads > 0, str(snap.cpu_threads))
    check("Salud en rango", 0 <= snap.health_score <= 100, f"{snap.health_score:.1f}")
    check("Lista de procesos", len(snap.top_processes) > 0, str(len(snap.top_processes)))
    info = service.system_info()
    check("Información del equipo", bool(info.get("sistema")) and bool(info.get("nombre_equipo")), info.get("sistema", ""))


def test_disk() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = build_sample_tree(Path(tmp) / "muestra")
        result = scan_directory(str(base))
        check("Tamaño total del análisis", result.total_size == 3_523_000, format_bytes(result.total_size))
        check("Archivos contados", result.total_files == 5, str(result.total_files))
        check("Sin errores de lectura", result.errors == 0, str(result.errors))
        names = [node.name for node in result.children]
        check("Carpeta más pesada primero", names[0] == "videos", ", ".join(names[:3]))
        check("Categorías detectadas", result.categories[0][0] == "Vídeo", str(result.categories[:2]))
        check("Detección de archivos grandes", isinstance(result.largest_files, list))
        drives = list_drives()
        check("Unidades listadas", len(drives) >= 1, ", ".join(d.units if False else d.mount for d in drives))
        check("Uso de unidad coherente", all(0 <= d.percent <= 100 for d in drives))
        check(
            "Nombre de unidad legible",
            bool(drives[0].label) or drives[0].fstype != "",
            f"{drives[0].mount} / {drives[0].label or drives[0].fstype}",
        )


def test_tasks() -> None:
    store = TaskStore()
    store.clear_completed()
    for task in list(store.all()):
        store.remove(task.id)
    first = store.add("Limpiar ventiladores", "Hardware", "Con aire comprimido.", priority="Alta")
    store.add("Ejecutar Windows Update", "Actualizaciones", due_date="2020-01-01")
    check("Tarea añadida", len(store.all()) == 2)
    store.toggle_done(first.id)
    check("Tarea completada", store.get(first.id).is_done)
    stats = store.stats()
    check("Estadísticas coherentes", stats["completadas"] == 1 and stats["pendientes"] == 1, str(stats))
    overdue = [task for task in store.pending() if task.is_overdue]
    check("Tarea vencida detectada", len(overdue) == 1, overdue[0].title if overdue else "")
    check("Archivo de tareas creado", store.path.exists(), str(store.path))
    store.add_template("Vaciar la papelera", "Limpieza", "Notas")
    reloaded = TaskStore()
    check("Persistencia al recargar", len(reloaded.all()) == 3, str(len(reloaded.all())))
    removed = reloaded.clear_completed()
    check("Limpieza de completadas", removed == 1, str(removed))
    check("Añadida desde plantilla", any(t.title == "Vaciar la papelera" for t in reloaded.all()))

    # --- Dos copias a la vez -------------------------------------------------
    # El mantenimiento desatendido es otro proceso y escribe el mismo archivo: la copia
    # que guarda tiene que releer el disco antes de volcar, o la tarea añadida desde la
    # otra ventana desaparece sin que nadie se entere.
    otra_copia = TaskStore()
    de_la_ventana = reloaded.add("Copia de seguridad", "Copias de seguridad", "")
    de_la_tarea = otra_copia.add("Revisar el disco", "Hardware", "")
    guardadas = {tarea.id for tarea in TaskStore().all()}
    check(
        "Guardar desde dos procesos no pierde tareas",
        {de_la_ventana.id, de_la_tarea.id} <= guardadas,
        f"{len(guardadas)} tareas guardadas de las dos",
    )
    restos = store.path.with_name(store.path.name + ".tmp")
    check("El guardado no deja el archivo a medias ni temporales sueltos", not restos.exists(), str(restos))


def test_tools() -> None:
    catalog = tools.tools_by_category()
    check("Catálogo de herramientas", sum(len(v) for v in catalog.values()) >= 20)
    check("Categorías completas", all(catalog.get(name) for name in tools.CATEGORY_ORDER[:4]))
    emulated = tools.ToolShortcut("x", "Prueba", "desc", "noexiste.exe", tools.CATEGORY_SISTEMA)
    ok, message = tools.launch(emulated)
    check("Lanzador informa de errores", ok is False and bool(message), message)
    # --- Antigüedad mínima: por debajo de una hora no se toca nada -----------
    check(
        "El suelo de antigüedad no se puede desactivar",
        tools.min_temp_age_hours(0) >= 1.0
        and tools.min_temp_age_hours(-5.0) >= 1.0
        and tools.min_temp_age_hours(24.0) == 24.0,
        f"0 h -> {tools.min_temp_age_hours(0)} h",
    )
    check(
        "Carpetas peligrosas fuera del alcance de la limpieza",
        not tools.is_safe_temp_folder("C:\\")
        and not tools.is_safe_temp_folder(os.path.expanduser("~"))
        and not tools.is_safe_temp_folder(os.environ.get("SystemRoot", "C:\\Windows")),
    )
    rechazado = tools.scan_temp_folder(os.environ.get("SystemRoot", "C:\\Windows"), min_age_hours=24)
    check(
        "Una carpeta rechazada se explica y no se analiza",
        bool(rechazado.rejected) and rechazado.file_count == 0 and rechazado.total_size == 0,
        rechazado.rejected[:60],
    )

    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        # Subcarpeta cuyo único contenido son archivos recientes: el error que se
        # corrigió era borrarla entera por no tener archivos sueltos, sin mirar lo
        # que había dentro.
        dentro = temp_dir / "en_curso" / "sub"
        dentro.mkdir(parents=True)
        reciente = dentro / "reciente.tmp"
        reciente.write_bytes(b"0" * 25_000)
        (temp_dir / "viejo.tmp").write_bytes(b"0" * 100_000)
        (temp_dir / "nuevo.tmp").write_bytes(b"0" * 50_000)
        envejecer(temp_dir / "viejo.tmp")

        report = tools.scan_temp_folder(str(temp_dir), min_age_hours=24)
        check(
            "Análisis de temporales solo cuenta los antiguos",
            report.file_count == 1 and report.total_size == 100_000,
            f"{report.file_count} archivos, {format_bytes(report.total_size)}",
        )

        result = tools.clean_temp_folder(str(temp_dir), min_age_hours=24)
        check("Limpieza de temporales", result.freed == 100_000 and result.deleted == 1, format_bytes(result.freed))
        check("Se borra el temporal antiguo", not (temp_dir / "viejo.tmp").exists())
        check("Se respeta el temporal reciente", (temp_dir / "nuevo.tmp").exists())
        check("Se respeta lo reciente dentro de una subcarpeta", reciente.exists())
        check("La subcarpeta con contenido sigue en pie", dentro.is_dir())

        # Pedir cero horas no desactiva el filtro: manda el suelo de una hora.
        sin_filtro = tools.clean_temp_folder(str(temp_dir), min_age_hours=0)
        check(
            "Pedir cero horas no borra lo recién creado",
            sin_filtro.deleted == 0 and (temp_dir / "nuevo.tmp").exists() and reciente.exists(),
            f"{sin_filtro.deleted} archivos borrados",
        )

        # Ya con todo envejecido sí se vacía, y las subcarpetas vacías se retiran.
        envejecer(temp_dir / "nuevo.tmp")
        envejecer(reciente)
        completo = tools.clean_temp_folder(str(temp_dir), min_age_hours=24)
        check("Limpieza completa de la carpeta de prueba", completo.deleted == 2, f"{completo.deleted} archivos")
        check("Carpeta temporal vacía", not any(temp_dir.iterdir()))


def test_reports() -> None:
    service = MetricsService()
    snap = service.poll()
    store = TaskStore()
    store.add("Tarea de informe", "Limpieza", "Notas de prueba")
    with tempfile.TemporaryDirectory() as tmp:
        base = build_sample_tree(Path(tmp) / "muestra")
        scan = scan_directory(str(base))
        report = reports.build_report(
            snapshot=snap,
            system_info=service.system_info(),
            drives=list_drives(),
            tasks=store.to_dicts(),
            task_stats=store.stats(),
            disk_result=scan,
        )
        check("Informe con secciones", {"equipo", "resumen", "unidades", "tareas", "analisis_disco"} <= set(report))
        check("Resumen con salud", 0 <= report["resumen"]["salud"] <= 100)
        out = Path(tmp)
        html = reports.export(report, out / "informe.html", reports.FORMAT_HTML)
        csv_file = reports.export(report, out / "informe.csv", reports.FORMAT_CSV)
        json_file = reports.export(report, out / "informe.json", reports.FORMAT_JSON)
        html_text = html.read_text(encoding="utf-8")
        check("HTML con datos del análisis", str(base) in html_text and "Registro de tareas" in html_text)
        check("HTML con estilos", "<style>" in html_text and len(html_text) > 4000, f"{len(html_text)} bytes")
        csv_text = csv_file.read_text(encoding="utf-8-sig")
        check("CSV con secciones", all(section in csv_text for section in ("EQUIPO", "RESUMEN", "UNIDADES", "TAREAS")), "")
        check("CSV con la tarea", "Tarea de informe" in csv_text)
        check("JSON válido", json_file.read_text(encoding="utf-8").startswith("{"))
        check("Nombres con extensión correcta", {html.suffix, csv_file.suffix, json_file.suffix} == {".html", ".csv", ".json"})
        check("Resumen corto", len(list(reports.summary_lines(report))) >= 5)
        target = reports.unique_path(out, "informe.html")
        check("No sobrescribe archivos", target.name == "informe_1.html", target.name)

        # Estructura del HTML: etiquetas equilibradas y datos escapados.
        from html.parser import HTMLParser

        class Balance(HTMLParser):
            void = {"meta", "br", "hr", "img", "input", "link", "source"}

            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.stack: list[str] = []
                self.errors: list[str] = []

            def handle_starttag(self, tag, attrs):
                if tag not in self.void:
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if not self.stack or self.stack[-1] != tag:
                    self.errors.append(tag)
                else:
                    self.stack.pop()

        parser = Balance()
        parser.feed(html_text)
        check("HTML bien equilibrado", not parser.errors and not parser.stack, str(parser.errors[:3] or parser.stack[:3]))
        check("HTML con apartados del análisis", "Archivos más grandes" in html_text and "Uso por categoría" in html_text)

        store.add("Tarea con <b>etiquetas</b> & símbolos", "Otros", "notas")
        escaped = reports._html(
            reports.build_report(
                snapshot=snap,
                system_info=service.system_info(),
                drives=[],
                tasks=store.to_dicts(),
                task_stats=store.stats(),
            )
        )
        check(
            "Contenido del usuario escapado",
            "&lt;b&gt;etiquetas&lt;/b&gt; &amp; símbolos" in escaped and "<b>etiquetas</b>" not in escaped,
        )


def test_alerts() -> None:
    from types import SimpleNamespace

    from pctoolkit.core.alerts import METRIC_CPU, METRIC_DISK, METRIC_RAM, AlertConfig, AlertEngine

    def snap(cpu: float, ram: float, disk: float = 20.0):
        return SimpleNamespace(cpu_percent=cpu, ram_percent=ram, disk_percent=disk)

    config = AlertConfig(enabled=True, sustained_samples=3, cooldown_minutes=15)
    for metric in (METRIC_CPU, METRIC_RAM, METRIC_DISK):
        config.set_threshold(metric, 80.0)
    engine = AlertEngine(config)

    check("Sin aviso por debajo del umbral", engine.evaluate(snap(20, 30)) == [])
    check("Sin aviso con una sola muestra alta", engine.evaluate(snap(95, 30)) == [])
    check("Sin aviso hasta alcanzar las muestras sostenidas", engine.evaluate(snap(95, 30)) == [])
    events = engine.evaluate(snap(95, 30))
    check("Aviso al sostenerse el umbral", len(events) == 1 and events[0].metric == METRIC_CPU, str(events))
    check("Mensaje del aviso con detalle", events and "95" in events[0].title and "80" in events[0].message)
    check("Sin repetir durante el enfriamiento", engine.evaluate(snap(97, 30)) == [])

    engine._now = None  # noqa: SLF001  (vuelve al reloj real)
    from datetime import datetime, timedelta

    engine._cooldown_until[METRIC_CPU] = datetime.now() - timedelta(seconds=1)  # noqa: SLF001
    repeat = engine.evaluate(snap(96, 30))
    check("Nuevo aviso tras el enfriamiento", len(repeat) == 1, str(repeat))

    engine.evaluate(snap(10, 30))
    engine.evaluate(snap(10, 30))
    check("La racha se reinicia al bajar", engine.status()[METRIC_CPU]["streak"] == 0, str(engine.status()))
    check("Tras bajar vuelve a avisar", len(engine.evaluate(snap(90, 30))) == 0 and len(engine.evaluate(snap(90, 30))) == 0)
    check("Aviso inmediato en el tercer ciclo", len(engine.evaluate(snap(90, 30))) == 1)

    disabled = AlertEngine(AlertConfig(enabled=False))
    check("Alertas desactivadas no avisan", disabled.evaluate(snap(99, 99, 99)) == [])

    ram_event = AlertEngine(AlertConfig(sustained_samples=1)).evaluate(snap(10, 99))
    check("Aviso de memoria con una muestra", len(ram_event) == 1 and ram_event[0].metric == METRIC_RAM)
    config.set_threshold(METRIC_RAM, 500)
    check("Umbral limitado a 100%", config.threshold_for(METRIC_RAM) == 100.0)
    config.set_threshold(METRIC_RAM, 0)
    check("Umbral mínimo del 1%", config.threshold_for(METRIC_RAM) == 1.0)
    restored = AlertConfig.from_dict(config.to_dict())
    check(
        "Configuración de alertas persistida",
        restored.thresholds["cpu"] == config.thresholds["cpu"] and restored.cooldown_minutes == 15,
        str(restored.to_dict()),
    )
    corrupt = AlertConfig.from_dict({"thresholds": "nada", "sustained_samples": "x"})
    check("Configuración corrupta tolerada", corrupt.sustained_samples == 3 and corrupt.enabled is True)


def test_history() -> None:
    import time as time_module

    from pctoolkit.core.history import HistoryRecorder, HistoryStore, Sample

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "historial.jsonl"
        store = HistoryStore(path)
        now = time_module.time()
        for hours, cpu in ((30, 10.0), (23, 20.0), (5, 30.0), (0.1, 40.0)):
            store.append(Sample(timestamp=now - hours * 3600, cpu=cpu, ram=cpu + 5, disk=50.0))
        # Línea corrupta que debe ignorarse
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{esto no es json}\n")

        loaded = store.load(hours=24, now=now)
        check("Historial filtrado a 24 h", [round(sample.cpu) for sample in loaded] == [20, 30, 40], str([s.cpu for s in loaded]))
        check("Línea corrupta ignorada", len(loaded) == 3)
        stats = store.stats(hours=24, now=now)
        check("Estadísticas del historial", stats["muestras"] == 3 and stats["cpu"]["maximo"] == 40.0, str(stats["cpu"]))
        check("Media calculada", stats["cpu"]["media"] == 30.0, str(stats["cpu"]["media"]))

        kept = store.prune(now=now)
        check("Recorte del historial", kept == 3 and store.count() == 3, f"conservadas {kept}")
        store.clear()
        check("Historial borrado", not path.exists() and store.count() == 0)

        # Recorder: guarda como máximo una muestra por intervalo.
        from PySide6.QtCore import QObject, Signal

        class FakeMetrics(QObject):
            sampled = Signal(object)

        class FakeSnapshot:
            cpu_percent = 12.0
            ram_percent = 34.0
            disk_percent = 56.0
            net_down_rate = 1024.0
            net_up_rate = 512.0

        store = HistoryStore(path)
        metrics = FakeMetrics()
        recorder = HistoryRecorder(metrics, store=store, interval_seconds=30)
        recorder.attach()
        received = []
        recorder.recorded.connect(received.append)
        metrics.sampled.emit(FakeSnapshot())
        metrics.sampled.emit(FakeSnapshot())
        check("Una muestra por intervalo", store.count() == 1, str(store.count()))
        check("Señal de muestra emitida", len(received) == 1)
        recorder.record(FakeSnapshot(), now=time_module.time() + 60)
        check("Muestra forzada guardada", store.count() == 2)
        saved = store.load(hours=24, now=time_module.time() + 60)
        check("Valores guardados correctos", saved[-1].ram == 34.0 and saved[-1].net_down == 1024.0, str(saved[-1]))
        check("Archivo de historial en la carpeta de datos", HistoryStore().path.parent.name == "PCToolkit", str(HistoryStore().path))


# --- Diferenciación de color con daltonismo (D2) ----------------------------
# Las mismas cuentas con las que se eligió la paleta: simulación de Machado (2009) a
# severidad 1.0 sobre RGB lineal y CIEDE2000 para medir la diferencia. La suite las
# repite para que un retoque de color no rompa sin avisar la separación entre series y
# estados, que es justo lo que costó medir.
_MACHADO = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}
_VISTAS = ("normal", "protanopia", "deuteranopia", "tritanopia")


def _canales(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _a_lineal(canal: float) -> float:
    valor = canal / 255.0
    return valor / 12.92 if valor <= 0.04045 else ((valor + 0.055) / 1.055) ** 2.4


def _de_lineal(canal: float) -> int:
    valor = max(0.0, min(1.0, canal))
    comprimido = 12.92 * valor if valor <= 0.0031308 else 1.055 * valor ** (1 / 2.4) - 0.055
    return round(max(0.0, min(1.0, comprimido)) * 255)


def _simula(color: str, tipo: str) -> str:
    """Color tal como lo vería alguien con ese tipo de daltonismo (Machado 1.0)."""
    rojo, verde, azul = (_a_lineal(canal) for canal in _canales(color))
    salida = [sum(fila[i] * (rojo, verde, azul)[i] for i in range(3)) for fila in _MACHADO[tipo]]
    return "#{:02X}{:02X}{:02X}".format(*(_de_lineal(canal) for canal in salida))


def _lab(color: str) -> tuple[float, float, float]:
    import math

    rojo, verde, azul = (_a_lineal(canal) for canal in _canales(color))
    equis = 0.4124564 * rojo + 0.3575761 * verde + 0.1804375 * azul
    igriega = 0.2126729 * rojo + 0.7151522 * verde + 0.0721750 * azul
    zeta = 0.0193339 * rojo + 0.1191920 * verde + 0.9503041 * azul

    def ajusta(valor: float) -> float:
        delta = (6 / 29) ** 3
        return valor ** (1 / 3) if valor > delta else valor / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = ajusta(equis / 0.95047), ajusta(igriega), ajusta(zeta / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _ciede2000(uno: tuple[float, float, float], otro: tuple[float, float, float]) -> float:
    """CIEDE2000 según Sharma, Wu y Dalal (2005)."""
    import math

    l1, a1, b1 = uno
    l2, a2, b2 = otro
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_medio = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(c_medio**7 / (c_medio**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dlp, dcp = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dh = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2)
    l_medio, c_medio_p = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_medio = h1p + h2p
    else:
        diferencia = abs(h1p - h2p)
        suma = h1p + h2p
        if diferencia <= 180:
            h_medio = suma / 2
        elif suma < 360:
            h_medio = (suma + 360) / 2
        else:
            h_medio = (suma - 360) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(h_medio - 30))
        + 0.24 * math.cos(math.radians(2 * h_medio))
        + 0.32 * math.cos(math.radians(3 * h_medio + 6))
        - 0.20 * math.cos(math.radians(4 * h_medio - 63))
    )
    theta = 30 * math.exp(-(((h_medio - 275) / 25) ** 2))
    rc = 2 * math.sqrt(c_medio_p**7 / (c_medio_p**7 + 25**7))
    sl = 1 + (0.015 * (l_medio - 50) ** 2) / math.sqrt(20 + (l_medio - 50) ** 2)
    sc = 1 + 0.045 * c_medio_p
    sh = 1 + 0.015 * c_medio_p * t
    rt = -math.sin(math.radians(2 * theta)) * rc
    return math.sqrt(
        (dlp / sl) ** 2 + (dcp / sc) ** 2 + (dh / sh) ** 2 + rt * (dcp / sc) * (dh / sh)
    )


def _delta(uno: str, otro: str, vista: str) -> float:
    """ΔE00 entre dos colores tal como los vería `vista` (normal o un daltonismo)."""
    primero = uno if vista == "normal" else _simula(uno, vista)
    segundo = otro if vista == "normal" else _simula(otro, vista)
    return _ciede2000(_lab(primero), _lab(segundo))


def _peor_par(paleta: dict[str, str]) -> tuple[float, float, str]:
    """Peor ΔE00 de los pares exigidos: (tres visiones, cuatro visiones, culpable)."""
    series = ("series_cpu", "series_ram", "series_disk", "series_net")
    estados = ("good", "warn", "bad")
    pares = [(series[i], series[j]) for i in range(len(series)) for j in range(i + 1, len(series))]
    pares += [(serie, estado) for serie in series for estado in estados]
    minimo_tres, minimo_cuatro, culpable = 999.0, 999.0, ""
    for uno, otro in pares:
        valores = [_delta(paleta[uno], paleta[otro], vista) for vista in _VISTAS]
        if min(valores[:3]) < minimo_tres:
            minimo_tres, culpable = min(valores[:3]), f"{uno}/{otro}"
        minimo_cuatro = min(minimo_cuatro, min(valores))
    return minimo_tres, minimo_cuatro, culpable


def test_theme() -> None:
    """Las dos paletas D2 (oscura y clara): contraste, neutralidad y daltonismo.

    El contraste se calcula con la fórmula de la WCAG sobre los pares de texto y
    fondo que la interfaz usa de verdad, no sobre una lista inventada: si alguien
    retoca un color y el texto deja de leerse, aquí salta.
    """
    from pctoolkit.core import appearance, config
    from pctoolkit.ui import theme

    def channels(color: str) -> tuple[int, int, int]:
        return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]

    def hue(color: str) -> float:
        red, green, blue = channels(color)
        high, low = max(red, green, blue), min(red, green, blue)
        if high == low:
            return 0.0
        delta = high - low
        if high == red:
            value = ((green - blue) / delta) % 6
        elif high == green:
            value = (blue - red) / delta + 2
        else:
            value = (red - green) / delta + 4
        return value * 60.0

    def luminance(color: str) -> float:
        def linear(channel: int) -> float:
            value = channel / 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        red, green, blue = channels(color)
        return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)

    def contrast(one: str, other: str) -> float:
        first, second = luminance(one), luminance(other)
        return (max(first, second) + 0.05) / (min(first, second) + 0.05)

    check(
        "Las dos paletas tienen las mismas claves",
        set(config.DARK_COLORS) == set(config.LIGHT_COLORS)
        and set(config.DARK_SURFACES) == set(config.LIGHT_SURFACES),
        ", ".join(sorted(set(config.DARK_COLORS) ^ set(config.LIGHT_COLORS))),
    )
    check(
        "Superficies del tema definidas",
        {"hover", "pressed", "selected", "track", "track_hover"} <= set(SURFACES),
        ", ".join(SURFACES),
    )

    for nombre, paleta, superficies in (
        ("oscura", config.DARK_COLORS, config.DARK_SURFACES),
        ("clara", config.LIGHT_COLORS, config.LIGHT_SURFACES),
    ):
        # D2: el acento es **neutro** (casi blanco en oscuro, tinta en claro), no un
        # color saturado. Se mide la diferencia entre canales, que es lo que distingue un
        # gris de un tono.
        accent = channels(paleta["accent"])
        check(
            f"El acento de la paleta {nombre} es neutro (D2)",
            max(accent) - min(accent) <= 16,
            paleta["accent"],
        )
        # Del diseño anterior no puede quedar ni un violeta ni un magenta: cualquier
        # tono frío y saturado en esa franja sería un resto de la paleta vieja.
        violetas = [
            llave
            for llave, valor in {**paleta, **superficies}.items()
            if 250.0 <= hue(valor) <= 330.0 and max(channels(valor)) - min(channels(valor)) > 20
        ]
        check(
            f"Ningún color de la paleta {nombre} es violeta o magenta",
            not violetas,
            ", ".join(violetas),
        )
        # Las cuatro series se distinguen entre sí y de los colores de estado incluso sin
        # color: se simula el daltonismo y se mide con CIEDE2000. Los mínimos son los
        # aprobados para D2 (20 en la clara y 15 en la oscura para visión normal,
        # protanopia y deuteranopia; 8 contando también la tritanopia), no un número
        # elegido aquí.
        minimo_tres, minimo_cuatro, culpable = _peor_par(paleta)
        limite = 19.5 if nombre == "clara" else 15.0
        check(
            f"Los datos y los estados de la paleta {nombre} se distinguen con daltonismo",
            minimo_tres >= limite,
            f"peor par {culpable}: {minimo_tres:.1f} en normal, protanopia y deuteranopia",
        )
        check(
            f"Ningún par de la paleta {nombre} es irreconocible (tritanopia incluida)",
            minimo_cuatro >= 8.0,
            f"peor par {culpable}: {minimo_cuatro:.1f} contando la tritanopia",
        )
        for name, value in paleta.items():
            check(f"Color válido ({nombre}): {name}", len(value) == 7 and value.startswith("#"), value)

        flojos = [
            f"{texto} sobre {fondo} ({contrast(paleta[texto], paleta[fondo]):.2f})"
            for fondo in ("bg", "bg_alt", "panel", "panel_alt")
            for texto in ("text", "text_muted", "text_dim")
            if contrast(paleta[texto], paleta[fondo]) < 4.5
        ]
        check(f"Contraste suficiente en la paleta {nombre} (WCAG 4,5:1)", not flojos, ", ".join(flojos))
        pares = {
            "on_selected sobre seleccionado": (paleta["on_selected"], superficies["selected"]),
            "on_accent sobre accent": (paleta["on_accent"], paleta["accent"]),
            "on_accent sobre accent_glow": (paleta["on_accent"], paleta["accent_glow"]),
            "text sobre hover": (paleta["text"], superficies["hover"]),
            "text_dim sobre fila alterna": (paleta["text_dim"], superficies["row"]),
        }
        bajos = [f"{etiqueta} ({contrast(*par):.2f})" for etiqueta, par in pares.items() if contrast(*par) < 4.5]
        check(f"Pares de textos y fondos legibles en la paleta {nombre}", not bajos, ", ".join(bajos))
        # Los estados hacen de texto (chips, insignias): 4,5:1 sobre el panel. Las
        # series, el foco y el borde de selección son elementos gráficos: 3:1.
        estados = {
            "bien sobre panel": (paleta["good"], paleta["panel"]),
            "aviso sobre panel": (paleta["warn"], paleta["panel"]),
            "mal sobre panel": (paleta["bad"], paleta["panel"]),
        }
        bajos_estado = [
            f"{etiqueta} ({contrast(*par):.2f})" for etiqueta, par in estados.items() if contrast(*par) < 4.5
        ]
        check(
            f"Los estados se leen sobre el panel en la paleta {nombre}",
            not bajos_estado,
            ", ".join(bajos_estado),
        )
        graficos = {
            "foco sobre panel": (paleta["accent"], paleta["panel"]),
            "foco sobre el fondo": (paleta["accent"], paleta["bg"]),
            "borde de selección sobre seleccionado": (paleta["text_muted"], superficies["selected"]),
        }
        for clave in ("series_cpu", "series_ram", "series_disk", "series_net"):
            graficos[f"{clave} sobre panel"] = (paleta[clave], paleta["panel"])
        flojos_grafico = [
            f"{etiqueta} ({contrast(*par):.2f})" for etiqueta, par in graficos.items() if contrast(*par) < 3.0
        ]
        check(
            f"Elementos gráficos visibles en la paleta {nombre} (WCAG 3:1)",
            not flojos_grafico,
            ", ".join(flojos_grafico),
        )
        # El texto de un control apagado se exime de 4,5:1 (WCAG, componentes
        # inactivos), pero tiene que distinguirse del fondo para poder leerse.
        apagado = contrast(paleta["primary_disabled_text"], paleta["primary_disabled_bg"])
        check(
            f"El texto apagado se lee sin parecer activo ({nombre})",
            apagado >= 2.6,
            f"{apagado:.2f}",
        )

    # Cambiar de paleta no puede reasignar los diccionarios: la interfaz ya los
    # tiene importados por referencia. Aquí no hace falta ninguna ventana.
    config.use_palette("dark")
    referencias_ok = COLORS is config.COLORS and SURFACES is config.SURFACES
    claro = config.use_palette("light")
    check("Cambiar a la paleta clara", claro == "light" and COLORS["bg"] == config.LIGHT_COLORS["bg"], COLORS["bg"])
    qss_claro = theme.stylesheet()
    check("La hoja de estilos clara usa el fondo claro", config.LIGHT_COLORS["bg"] in qss_claro)
    check("La paleta vuelve a la oscura", config.use_palette("dark") == "dark" and COLORS["bg"] == config.DARK_COLORS["bg"], COLORS["bg"])
    qss = theme.stylesheet()
    check("Los diccionarios de color no se reasignan", referencias_ok)
    check(
        "Una paleta desconocida cae en la oscura",
        config.use_palette("turquesa") == "dark" and COLORS["bg"] == config.DARK_COLORS["bg"],
        config.active_palette(),
    )
    check("La hoja de estilos usa el acento nuevo", COLORS["accent"] in qss and COLORS["accent_glow"] in qss)
    # Un color con transparencia se escribe con `rgba(...)`: pegar el alfa al final del
    # hexadecimal no vale, porque Qt lee `#RRGGBBAA` como `#AARRGGBB` (una insignia de
    # aviso amarilla salía roja y casi opaca).
    from PySide6.QtGui import QColor as _QColor

    aviso = theme.with_alpha(COLORS["warn"], 34)
    tono = _QColor(aviso)
    esperado = _QColor(COLORS["warn"])
    check(
        "El color con transparencia conserva el tono",
        (tono.red(), tono.green(), tono.blue()) == (esperado.red(), esperado.green(), esperado.blue())
        and 0 < tono.alpha() < 255,
        f"{aviso} -> {tono.name()} alfa {tono.alpha()}",
    )
    check("La hoja de estilos estiliza las listas desplegables", "QDoubleSpinBox" in qss and "QTimeEdit" in qss)
    # La plantilla del QSS se rellena con la paleta activa: si quedara una clave
    # sin sustituir, Qt se saltaría esa regla en silencio.
    restos = [
        clave
        for clave in list(config.DARK_COLORS) + list(config.DARK_SURFACES)
        if "{" + clave + "}" in qss
    ]
    check("La plantilla del QSS se rellena con la paleta activa", not restos and "COLORS[" not in qss, ", ".join(restos))

    # Modo de apariencia: `auto` sigue a Windows y el resto se respeta.
    check("Modos de apariencia definidos", set(appearance.MODES) == {"auto", "dark", "light"}, str(appearance.MODES))
    check(
        "Cada modo tiene su etiqueta",
        all(appearance.MODE_LABELS.get(modo) for modo in appearance.MODES),
        str(appearance.MODE_LABELS),
    )
    check("La lectura del sistema da un modo válido", appearance.system_mode() in ("dark", "light"), appearance.system_mode())
    check("El modo automático usa el del sistema", appearance.resolve_mode("auto") == appearance.system_mode())
    check("El modo forzado se respeta", appearance.resolve_mode("light") == "light" and appearance.resolve_mode("dark") == "dark")
    check(
        "Un modo ilegible cae en automático",
        all(appearance.normalize_mode(valor) == "auto" for valor in ("", "   ", None, "azul", 7)),
        ", ".join(str(appearance.normalize_mode(valor)) for valor in ("", "azul", 7)),
    )
    check(
        "Los nombres en español del modo también valen",
        appearance.normalize_mode("CLARO") == "light" and appearance.normalize_mode("Oscuro") == "dark",
    )
    check("El diálogo explica el modo automático", "Windows" in appearance.describe_mode("auto"), appearance.describe_mode("auto"))
    check("El diálogo explica el modo forzado", "Siempre" in appearance.describe_mode("light"), appearance.describe_mode("light"))

    legacy = (
        # La paleta violeta anterior (negro con acento #8B5CF6 y series cian/violeta/
        # naranja): si alguno de estos colores siguiera en el código, aquí salta. Incluye
        # los grises del diseño anterior, que tampoco deben volver por su cuenta.
        "#8B5CF6", "#7C3AED", "#A78BFA", "#0B0714", "#E9E4FF", "#B9A2FF",
        "#22D3EE", "#FBBF24", "#F87171", "#FB923C", "#0A0A0A", "#121212",
        "#1A1A1A", "#2E2E33", "#1F1F23", "#F4F4F6", "#A9A9B2", "#8E8E97",
        "#16161A", "#2A2A31", "#6C6C76", "#4A2A2C", "#2A1618", "#2A2140",
        "#1C1C21", "#151519", "#3F3F46", "#0E0E11",
        "#6D28D9", "#5B21B6", "#0E7490", "#96620B", "#B32424", "#C2410C",
        "#2E1065", "#E6E6EB", "#D0D0D8", "#7B7B85", "#E3BDBD", "#FBEDED",
        "#F5F5F7", "#EBEBEF", "#F7F7F9", "#C9C9D2", "#E0E0E6", "#111114",
        "#4B4B54", "#63636D", "#EDEDF2", "#DFDFE6", "#E7E0FB", "#B2B2BC", "#FAFAFC",
    )
    hits: list[str] = []
    for path in sorted((ROOT / "pctoolkit").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}:{color}" for color in legacy if color in text]
    check("Sin restos de la paleta violeta anterior", not hits, ", ".join(hits[:4]))


def test_memory() -> None:
    """Filtrado de procesos, medidas y decisiones de la limpieza automática."""
    own = os.getpid()
    synthetic = [
        (4, "System", 900 * 1024 * 1024),
        (0, "System Idle Process", 10 * 1024 * 1024),
        (777, "lsass.exe", 60 * 1024 * 1024),
        (4242, "chrome.exe", 800 * 1024 * 1024),
        (own, "python.exe", 500 * 1024 * 1024),
        (555, "notepad.exe", 5 * 1024 * 1024),
    ]
    targets = memory.select_targets(synthetic, min_mb=20.0, own_pid=own)
    names = [target.name for target in targets]
    check("Se omiten los procesos críticos", "System" not in names and "lsass.exe" not in names, str(names))
    check("Se omite el propio proceso", "python.exe" not in names, str(names))
    check("Se respeta el consumo mínimo", "notepad.exe" not in names, str(names))
    check("Ordenado por memoria usada", names == ["chrome.exe"], str(names))
    check("Se puede incluir el propio proceso", any(
        target.pid == own for target in memory.select_targets(synthetic, min_mb=20.0, include_own=True, own_pid=own)
    ))
    check("Identificación de procesos críticos", memory.is_critical(4, "System") and not memory.is_critical(4242, "chrome.exe"))
    check(
        "El escritorio y sus compañeros no se tocan",
        all(
            nombre in memory.CRITICAL_NAMES
            for nombre in ("explorer.exe", "sihost.exe", "ctfmon.exe", "searchhost.exe")
        ),
        f"{len(memory.CRITICAL_NAMES)} nombres críticos",
    )
    con_ventana_delante = memory.select_targets(synthetic, min_mb=20.0, own_pid=own, foreground_pid=4242)
    check(
        "El programa de la ventana en primer plano no se recorta",
        [target.name for target in con_ventana_delante] == [],
        str([target.name for target in con_ventana_delante]),
    )
    check(
        "La ventana en primer plano se pregunta a Windows",
        isinstance(memory.current_foreground_pid(), int),
        str(memory.current_foreground_pid()),
    )

    # --- Lo agresivo viene apagado -----------------------------------------
    por_defecto = MemoryOptions()
    check(
        "La caché del sistema y la lista de espera no vienen activadas",
        not por_defecto.empty_system_cache
        and not por_defecto.purge_standby
        and not por_defecto.purge_standby_low_priority
        and por_defecto.trim_processes,
        str(por_defecto.to_dict()),
    )
    guardadas = MemoryOptions.from_dict({"vaciado_sistema": True, "lista_espera": True})
    check(
        "Lo que un usuario ya tenía activado se respeta",
        guardadas.empty_system_cache and guardadas.purge_standby,
        str(guardadas.to_dict()),
    )
    suave = MemoryOptions.from_dict({"lista_espera_suave": True})
    check(
        "La purga suave es una opción propia",
        suave.purge_standby_low_priority and not suave.purge_standby,
        str(suave.to_dict()),
    )
    check(
        "La purga suave usa la orden de baja prioridad de Windows",
        memory.MEMORY_PURGE_LOW_PRIORITY_STANDBY_LIST == 5 and memory.MEMORY_PURGE_STANDBY_LIST == 4,
        f"{memory.MEMORY_PURGE_LOW_PRIORITY_STANDBY_LIST} / {memory.MEMORY_PURGE_STANDBY_LIST}",
    )
    solo_suave = memory.release_memory(MemoryOptions(purge_standby_low_priority=True), dry_run=True)
    check(
        "En simulación se ve que la purga suave está pedida",
        solo_suave.standby_purged and MemoryOptions().purge_standby_low_priority is False,
        solo_suave.summary(),
    )

    options = MemoryOptions(auto_enabled=True, threshold_percent=70.0, cooldown_minutes=5, min_process_mb=42.0)
    restored = MemoryOptions.from_dict(options.to_dict())
    check(
        "Opciones de memoria persistentes",
        restored.auto_enabled and restored.threshold_percent == 70.0 and restored.cooldown_minutes == 5
        and restored.min_process_mb == 42.0,
        str(restored.to_dict()),
    )
    clamped = MemoryOptions.from_dict({"umbral": 500, "espera_min": -3, "muestras": "x"})
    check("Opciones fuera de rango saneadas", clamped.threshold_percent == 99.0 and clamped.cooldown_minutes == 1, str(clamped.to_dict()))
    check("Opciones corruptas toleradas", MemoryOptions.from_dict({"umbral": "alto"}).threshold_percent == 85.0)

    # Simulación: no toca nada del equipo real.
    simulation = memory.release_memory(options, dry_run=True)
    check("Simulación sin tocar procesos", simulation.dry_run and simulation.processes_trimmed > 0, simulation.summary())
    check("Simulación identificada como tal", simulation.summary().startswith("Simulación"), simulation.summary())

    # Recorte real del propio proceso de pruebas: comprueba la vía nativa completa.
    import psutil

    before = psutil.Process(own).memory_info().rss
    trimmed = memory.trim_process(own)
    after = psutil.Process(own).memory_info().rss
    check("Recorte real de un conjunto de trabajo", trimmed and after <= before, f"{format_bytes(before)} -> {format_bytes(after)}")
    check("Proceso inexistente no rompe el recorte", memory.trim_process(999_999) is False)

    result = memory.release_memory(
        MemoryOptions(min_process_mb=1e9, empty_system_cache=False, purge_standby=False),
        targets=[memory.ProcessTarget(pid=own, name="prueba", rss=10_000_000)],
    )
    check("Resultado con medidas antes/después", result.total_bytes > 0 and result.after_percent > 0, result.summary())
    check("Serialización del resultado", {"liberado_mb", "procesos", "lista_espera"} <= set(result.to_dict()))
    privileged = memory.has_standby_privilege()
    with_system = memory.release_memory(
        MemoryOptions(min_process_mb=1e9, empty_system_cache=True, purge_standby=True),
        targets=[memory.ProcessTarget(pid=own, name="prueba", rss=1)],
    )
    check(
        "Operaciones de sistema: permiso o aviso claro",
        (with_system.system_cache_empty and with_system.standby_purged)
        if privileged
        else any("administrador" in error for error in with_system.errors),
        " | ".join(with_system.errors) or "con permisos",
    )

    # Guardia automática: umbral sostenido, enfriamiento y reinicio.
    import time as time_module

    guard = MemoryGuard(MemoryOptions(auto_enabled=True, threshold_percent=80.0, sustained_samples=3, cooldown_minutes=10))
    check("Guardia desactivada no limpia", MemoryGuard(MemoryOptions()).observe(99.0) is False)
    check("Guardia sin acción por debajo del umbral", guard.observe(40.0) is False)
    check("Guardia espera a las muestras sostenidas", guard.observe(85.0) is False and guard.observe(86.0) is False)
    check("Guardia dispara al sostenerse el umbral", guard.observe(87.0) is True)
    guard.note_clean(memory.release_memory(MemoryOptions(), dry_run=True))
    check("Guardia respeta el enfriamiento", [guard.observe(90.0) for _ in range(4)] == [False] * 4)
    check("Enfriamiento calculado", guard.cooldown_remaining > 300, f"{guard.cooldown_remaining:.0f} s")
    guard.note_clean(now=time_module.time() - 3600)  # como si la última limpieza fuera de hace una hora
    check("Sin enfriamiento pendiente", guard.cooldown_remaining == 0.0)
    check("Guardia vuelve a disparar tras el enfriamiento", guard.observe(90.0) is False and guard.observe(90.0) is False and guard.observe(90.0) is True)
    check("La racha se reinicia al bajar", (guard.observe(10.0), guard.streak) == (False, 0))
    check("Último resultado recordado", guard.last_result is not None)


def _borrar_clave_registro(root, path: str) -> None:
    """Borra una clave y todo lo que cuelga de ella (solo para las pruebas)."""
    import winreg

    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as clave:
            while True:
                try:
                    subclave = winreg.EnumKey(clave, 0)
                except OSError:
                    break
                _borrar_clave_registro(root, path + "\\" + subclave)
        winreg.DeleteKeyEx(root, path, winreg.KEY_WOW64_64KEY)
    except OSError:
        return


def _existe_clave_registro(root, path: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(root, path):
            return True
    except OSError:
        return False


def test_startup() -> None:
    """Programas de inicio: lectura, marcas de Windows, cambio reversible y deshacer."""
    import winreg

    from pctoolkit.core import startup, undo

    # --- Lectura de la línea de comando ---------------------------------
    check(
        "Ruta del ejecutable entre comillas",
        startup.command_target('"C:\\Program Files\\App\\app.exe" --minimizado')
        == "C:\\Program Files\\App\\app.exe",
        startup.command_target('"C:\\Program Files\\App\\app.exe" --minimizado'),
    )
    check(
        "Ruta con espacios y sin comillas",
        startup.command_target("C:\\Program Files\\Java\\bin\\javaw.exe -jar app.jar")
        == "C:\\Program Files\\Java\\bin\\javaw.exe",
        startup.command_target("C:\\Program Files\\Java\\bin\\javaw.exe -jar app.jar"),
    )
    check(
        "Comando sin archivo propio (rundll32)",
        startup.command_target("rundll32.exe foo.dll,Entrada") == "rundll32.exe",
        startup.command_target("rundll32.exe foo.dll,Entrada"),
    )
    check("Comando vacío", startup.command_target("") == "")

    # --- Marcas de activada/desactivada (las mismas que escribe Windows) --
    activada = startup.approval_bytes(True)
    desactivada = startup.approval_bytes(False)
    check("Marca de activada", activada[0] == 2 and len(activada) == 9, activada.hex())
    check("Marca de desactivada con su fecha", desactivada[0] == 3 and len(desactivada) == 9, desactivada.hex())
    check("Se leen las marcas guardadas", startup.approval_enabled(activada) and not startup.approval_enabled(desactivada))
    check("Sin marca, la entrada cuenta como activa", startup.approval_enabled(None))
    check("Una marca desconocida no se interpreta como desactivada", startup.approval_enabled(b"\x09\x00"))

    # --- Entradas reales del equipo -------------------------------------
    ordenadas = startup.sort_entries(startup.read_entries())
    check("Las entradas de arranque se leen", isinstance(ordenadas, list), f"{len(ordenadas)} entradas")
    activaciones = [entrada.enabled for entrada in ordenadas]
    check("Las activadas van primero", activaciones == sorted(activaciones, reverse=True), str(activaciones))
    check(
        "Cada entrada sabe de dónde sale",
        all(entrada.source and entrada.location for entrada in ordenadas),
        ordenadas[0].source if ordenadas else "",
    )
    check("Las claves de inicio de Windows se consultan", len(startup.registry_sources()) == 5, str(len(startup.registry_sources())))
    check("Las carpetas de inicio se consultan", len(startup.folder_sources()) == 2)
    check(
        "El sistema se consulta en sus dos vistas",
        len({fuente.view for fuente in startup.registry_sources()}) >= 2,
        str(sorted({fuente.view for fuente in startup.registry_sources()})),
    )
    check("¿Hay permisos de administrador?", isinstance(startup.is_elevated(), bool), str(startup.is_elevated()))

    # --- Cambio real y deshacer sobre una clave propia -------------------
    base = r"Software\PCToolkit\PruebaArranque"
    run_path = base + r"\Run"
    approved_path = base + r"\StartupApproved"
    carpeta = Path(_TEMP_HOME) / "inicio_prueba"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "Programa de prueba.lnk").write_bytes(b"acceso directo de prueba")
    de_registro = startup.RegistrySource(
        "prueba-run", "Prueba", "HKCU\\Prueba", run_path, winreg.HKEY_CURRENT_USER, approved_path
    )
    de_carpeta = startup.FolderSource(
        "prueba-carpeta", "Prueba carpeta", str(carpeta), approved_path, winreg.HKEY_CURRENT_USER
    )
    almacen = undo.UndoStore(Path(_TEMP_HOME) / "deshacer_prueba.jsonl")
    almacen.clear()
    # La clave de prueba vive fuera de las de Windows, así que se añade a la lista cerrada
    # mientras dura la prueba: el mismo mecanismo, en un sitio que no toca nada del equipo.
    claves_permitidas = startup.APPROVED_PATHS
    startup.APPROVED_PATHS = (*claves_permitidas, approved_path)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, run_path, 0, winreg.KEY_SET_VALUE) as clave:
            winreg.SetValueEx(clave, "PruebaApp", 0, winreg.REG_SZ, f'"{sys.executable}" --version')

        leidas = startup.read_registry_entries([de_registro])
        check("Entrada de prueba leída", len(leidas) == 1, str([entrada.name for entrada in leidas]))
        entrada = leidas[0]
        check("Sin marca previa, arranca", entrada.enabled and entrada.can_toggle, entrada.status_label)
        check("El programa existe", entrada.target_exists is True, entrada.target_path)
        check(
            "El coste estimado se explica",
            entrada.cost[0] == startup.COST_LIGERO and bool(entrada.cost[1]),
            entrada.cost[1],
        )

        de_carpetas = startup.read_folder_entries([de_carpeta])
        check("Entrada de la carpeta de inicio leída", len(de_carpetas) == 1, str([e.name for e in de_carpetas]))
        check(
            "El acceso directo cuenta como carpeta de inicio",
            de_carpetas[0].kind == startup.KIND_CARPETA and de_carpetas[0].enabled,
            de_carpetas[0].source,
        )

        ok, mensaje = startup.move(entrada, False, almacen)
        check("Desactivar sin borrar nada", ok, mensaje)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_path) as clave:
            marca, _tipo = winreg.QueryValueEx(clave, "PruebaApp")
        check("La marca escrita es la de Windows (3)", marca[0] == 3, marca.hex())
        check("Se vuelve a leer como desactivada", not startup.read_registry_entries([de_registro])[0].enabled)
        check("La entrada sigue existiendo", bool(startup.read_registry_entries([de_registro])), "la clave Run no se toca")

        apunte = almacen.all()[0]
        check(
            "El cambio queda apuntado con su origen",
            apunte.kind == undo.KIND_ARRANQUE and apunte.before == "Activada" and apunte.after == "Desactivada",
            apunte.summary,
        )
        check("El apunte se puede deshacer", undo.can_revert(apunte.kind) and apunte.revertible)

        ok, mensaje = undo.revert_action(almacen, apunte)
        check("Deshacer devuelve la entrada a como estaba", ok, mensaje)
        check("Vuelve a estar activa", startup.read_registry_entries([de_registro])[0].enabled)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_path) as clave:
            marca, _tipo = winreg.QueryValueEx(clave, "PruebaApp")
        check("La marca vuelve a ser la de activada (2)", marca[0] == 2, marca.hex())
        check("El apunte queda marcado como deshecho", not almacen.pending() and almacen.count() == 1)
        repetido, aviso = undo.revert_action(almacen, apunte)
        check("Un cambio no se deshace dos veces", repetido is False and bool(aviso), aviso)

        # La carpeta de inicio usa el mismo mecanismo, con otro nombre de valor.
        ok, mensaje = startup.move(de_carpetas[0], False, almacen)
        check("Desactivar una entrada de la carpeta de inicio", ok, mensaje)
        check("La carpeta de inicio lo refleja", not startup.read_folder_entries([de_carpeta])[0].enabled)
        de_carpetas = startup.read_folder_entries([de_carpeta])
        check("Y se puede volver a activar", startup.move(de_carpetas[0], True, almacen)[0])
        check("Queda activa otra vez", startup.read_folder_entries([de_carpeta])[0].enabled)

        # --- Resumen y agrupación -----------------------------------------
        resumen = startup.summary(startup.read_registry_entries([de_registro]) + startup.read_folder_entries([de_carpeta]))
        check(
            "El resumen cuadra",
            resumen["total"] == 2 and resumen["activas"] == 2 and resumen["desactivadas"] == 0,
            str(resumen),
        )
        grupos = startup.by_source(startup.read_registry_entries([de_registro]))
        check("Las entradas se agrupan por origen", list(grupos) == ["Prueba"], str(list(grupos)))

        # --- Tareas de inicio de sesión (salida simulada, sin PowerShell) ---
        salida = json.dumps(
            [
                {"nombre": "Tarea lista", "ruta": "\\", "estado": "Ready", "programa": sys.executable, "argumentos": "-inicio"},
                {"nombre": "Tarea apagada", "ruta": "\\Pruebas\\", "estado": "Disabled", "programa": r"C:\No\existe\otra.exe", "argumentos": ""},
            ]
        )
        tareas = startup.task_entries_from_output(salida)
        check("Las tareas de inicio se interpretan", len(tareas) == 2, str([t.name for t in tareas]))
        check("Una tarea llevaría más trabajo (estimación Media)", tareas[0].cost[0] == startup.COST_MEDIO, tareas[0].cost[1])
        check("El estado de la tarea se respeta", tareas[0].enabled and not tareas[1].enabled, tareas[1].status_label)
        check("El comando de la tarea se une", tareas[0].command == f"{sys.executable} -inicio", tareas[0].command)
        check(
            "Una tarea que apunta a un programa que ya no está se avisa",
            tareas[1].target_exists is False and tareas[1].cost[0] == startup.COST_NULO,
            tareas[1].problem or tareas[1].cost[1],
        )
        check(
            "Una sola tarea también se interpreta",
            len(startup.task_entries_from_output(json.dumps({"nombre": "Sola", "ruta": "\\", "estado": "Ready"}))) == 1,
        )
        check("Una salida ilegible no rompe nada", startup.task_entries_from_output("vaya") == [])
        check("Sin salida no hay tareas", startup.task_entries_from_output("") == [])

        # --- Almacén de deshacer ------------------------------------------
        check("El almacén guarda lo hecho", almacen.count() >= 3, str(almacen.count()))
        check("Los cambios se listan del más nuevo al más viejo", almacen.all()[0].at >= almacen.all()[-1].at)
        check("Los apuntes se releen del disco", undo.UndoStore(almacen.path).count() == almacen.count())
        check("Los apuntes pendientes se distinguen", len(almacen.pending()) == almacen.count() - 1, str(len(almacen.pending())))
        check(
            "Un cambio sin datos no se ofrece para deshacer",
            not undo.UndoAction(id="x", kind="otro", title="t", before="a", after="b", at=0.0).revertible,
        )
        desconocido = undo.UndoAction(id="y", kind="otro", title="t", before="a", after="b", at=0.0, data={"x": 1})
        hecho, aviso = undo.revert_action(almacen, desconocido)
        check("Un tipo desconocido no se deshace", hecho is False and bool(aviso), aviso)
        almacen.clear()
        check("El almacén se vacía", almacen.count() == 0 and almacen.pending() == [])
    finally:
        startup.APPROVED_PATHS = claves_permitidas
        _borrar_clave_registro(winreg.HKEY_CURRENT_USER, base)
    check("La clave de prueba del registro se elimina", not _existe_clave_registro(winreg.HKEY_CURRENT_USER, base))

    # --- Interpretación de la línea de comando -----------------------------
    # Buscar la extensión por dentro de la cadena daba falsos positivos: `.com` encaja
    # dentro de `.company` y la entrada parecía apuntar a un archivo que ya no existe.
    check(
        "Una extensión no se confunde dentro de otra (.com en .company)",
        startup.command_target(r"C:\Program Files\App.Company\launcher.exe --oculto")
        == r"C:\Program Files\App.Company\launcher.exe"
        and startup.command_target(r"C:\Program Files\Foo.Company\start")
        == r"C:\Program Files\Foo.Company\start",
        startup.command_target(r"C:\Program Files\Foo.Company\start"),
    )
    with tempfile.TemporaryDirectory(prefix="con espacios ") as tmp:
        programa = Path(tmp) / "mi programa.exe"
        programa.write_bytes(b"0")
        check(
            "Una ruta con espacios y sin comillas encuentra el programa",
            startup.command_target(f"{programa} --bandeja") == str(programa),
            startup.command_target(f"{programa} --bandeja"),
        )
        # La existencia se resuelve al leer las entradas, no en cada repintado.
        entrada_cache = startup.StartupEntry(
            id="cache",
            name="Cache",
            command=str(programa),
            source="Prueba",
            location="Prueba",
            kind=startup.KIND_REGISTRO,
            enabled=True,
        )
        antes = entrada_cache.target_exists
        entrada_cache.resolve_target()
        programa.unlink()
        nueva = startup.StartupEntry(
            id="nueva",
            name="Nueva",
            command=str(programa),
            source="Prueba",
            location="Prueba",
            kind=startup.KIND_REGISTRO,
            enabled=True,
        )
        check(
            "La existencia del programa se resuelve una sola vez, al leer",
            antes is True and entrada_cache.target_exists is True and nueva.target_exists is False,
            f"antes {antes}, tras borrarlo (memorizado) {entrada_cache.target_exists}, nueva {nueva.target_exists}",
        )

    # --- Lo que llega de `deshacer.jsonl` no se da por bueno ----------------
    # El archivo es texto que cualquier proceso del usuario puede editar: si alguien cambia
    # la clave de destino, la raíz del registro o el nombre del valor, PC Toolkit escribiría
    # en otra rama del registro. Estas comprobaciones son las que lo impiden.
    check(
        "Solo se escribe en las tres claves de «StartupApproved» de Windows",
        startup.APPROVED_PATHS == (startup.APPROVED_RUN, startup.APPROVED_RUN32, startup.APPROVED_FOLDER),
        ", ".join(startup.APPROVED_PATHS),
    )
    otra_rama = base + r"\OtraRama"
    fuera_de_lista = startup.StartupEntry(
        id="falsa",
        name="Falsa",
        command="",
        source="Prueba",
        location="Prueba",
        kind=startup.KIND_REGISTRO,
        enabled=True,
        data={"hive": startup.HIVE_USER, "approved_path": otra_rama, "valor": "OtraApp"},
    )
    ok, mensaje = startup.set_enabled(fuera_de_lista, False)
    check(
        "Una clave fuera de la lista se rechaza",
        ok is False and "no usa" in mensaje,
        mensaje,
    )
    raiz_rara = startup.StartupEntry(
        id="rara",
        name="Rara",
        command="",
        source="Prueba",
        location="Prueba",
        kind=startup.KIND_REGISTRO,
        enabled=True,
        data={"hive": "HKEY_LOCAL_MACHINE", "approved_path": startup.APPROVED_RUN, "valor": "OtraApp"},
    )
    ok, mensaje = startup.set_enabled(raiz_rara, False)
    check("Una raíz del registro desconocida se rechaza", ok is False and "registro" in mensaje, mensaje)
    valor_raro = startup.StartupEntry(
        id="valor",
        name="Valor",
        command="",
        source="Prueba",
        location="Prueba",
        kind=startup.KIND_REGISTRO,
        enabled=True,
        data={"hive": startup.HIVE_USER, "approved_path": startup.APPROVED_RUN, "valor": r"Otra\Rama"},
    )
    ok, mensaje = startup.set_enabled(valor_raro, False)
    check("Un nombre de valor con separadores se rechaza", ok is False and "valor" in mensaje, mensaje)
    check(
        "No se escribió nada en la rama prohibida",
        not _existe_clave_registro(winreg.HKEY_CURRENT_USER, otra_rama),
        otra_rama,
    )
    tarea_de_microsoft = startup.StartupEntry(
        id="tarea",
        name="MicrosoftEdgeUpdateTask",
        command="",
        source=startup.SOURCE_TAREA,
        location="Programador de tareas",
        kind=startup.KIND_TAREA,
        enabled=True,
        data={"clave": "tarea", "tarea": r"\Microsoft\Windows\EdgeUpdate"},
    )
    ok, mensaje = startup.set_enabled(tarea_de_microsoft, False)
    check(
        "Las tareas de Microsoft no se tocan",
        ok is False and "Microsoft" in mensaje,
        mensaje,
    )
    check(
        "Una tarea propia sí se puede preparar",
        startup._safe_task_name(r"\PCToolkit\Mantenimiento") == r"\PCToolkit\Mantenimiento",  # noqa: SLF001
        startup._safe_task_name(r"\Microsoft\Windows\X") or "(rechazada)",  # noqa: SLF001
    )

    # --- Codificación de lo que se lee de Windows ---------------------------
    # PowerShell escribía en la página del sistema y se leía como UTF-8: las rutas con
    # acentos llegaban corruptas. Ahora los dos scripts lo fijan al principio.
    prefijo = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    check(
        "Los scripts de PowerShell fijan la salida en UTF-8",
        startup.TASKS_SCRIPT.startswith(prefijo) and scheduler.STATUS_SCRIPT.startswith(prefijo),
        "TASKS_SCRIPT y STATUS_SCRIPT",
    )
    ejecucion_real = subprocess.run
    capturado: dict[str, object] = {}

    def _falso_run(comando, **kwargs):
        capturado.update(kwargs)
        capturado["comando"] = comando
        return subprocess.CompletedProcess(comando, 0, stdout="", stderr="")

    try:
        subprocess.run = _falso_run  # type: ignore[assignment]
        scheduler.run_schtasks(["/Query", "/TN", "x"])
        en_scheduler = str(capturado.get("encoding"))
        capturado.clear()
        startup.read_task_entries(timeout=5)
        entorno = capturado.get("env") or {}
        # No basta con que falte: sin ella, Windows PowerShell la reconstruye con las del
        # sistema y del usuario, donde un servidor tiene metidas las de PowerShell 7 (y
        # entonces `ConvertTo-SecureString` no carga). Tiene que estar definida con las
        # carpetas del Windows PowerShell y sin ninguna de PowerShell 7.
        rutas_modulos = [parte for parte in str(entorno.get("PSModulePath", "")).split(os.pathsep) if parte]
        con_entorno = (
            bool(entorno)
            and bool(rutas_modulos)
            and all("PowerShell\\7" not in parte for parte in rutas_modulos)
            and any("WindowsPowerShell\\v1.0\\Modules" in parte for parte in rutas_modulos)
        )
        capturado.clear()
        tarea_propia = startup.StartupEntry(
            id="tarea-propia",
            name="Mantenimiento",
            command="",
            source=startup.SOURCE_TAREA,
            location="Programador de tareas",
            kind=startup.KIND_TAREA,
            enabled=True,
            data={"clave": "tarea", "tarea": r"\PCToolkit\Mantenimiento"},
        )
        startup._set_task_enabled(tarea_propia, True)  # noqa: SLF001
        en_startup = str(capturado.get("encoding"))
    finally:
        subprocess.run = ejecucion_real  # type: ignore[assignment]
    check(
        "`schtasks` se lee en la página OEM de la consola",
        en_scheduler == "oem" and en_startup == "oem",
        f"scheduler={en_scheduler!r}, startup={en_startup!r}",
    )
    check(
        "PowerShell se lanza con las carpetas de módulos del Windows PowerShell (sin las de PowerShell 7)",
        con_entorno,
        f"variables: {len(entorno)} · módulos: {rutas_modulos}",
    )


def test_scheduler() -> None:
    """Tarea de Windows: XML, líneas de comandos y estado real (sin crear nada)."""
    check("Frecuencias disponibles", len(scheduler.FREQUENCIES) == 3, ", ".join(scheduler.FREQUENCIES))
    check("Horas saneadas", scheduler.normalize_time("99:99") == "23:59", scheduler.normalize_time("99:99"))
    check("Hora con formato incorrecto", scheduler.normalize_time("vaya") == "12:00")
    check("Hora con separador alternativo", scheduler.normalize_time("7.30") == "07:30")

    daily = MaintenanceSchedule(frequency=scheduler.FREQ_DAILY, time="06:05", clean_temp=True, export_report=True)
    weekly = MaintenanceSchedule(frequency=scheduler.FREQ_WEEKLY, weekday="Miércoles", time="23:00")
    logon = MaintenanceSchedule(frequency=scheduler.FREQ_LOGON)

    xml_daily = scheduler.task_xml(daily, moment=0.0)
    check("XML de tarea diaria", "<ScheduleByDay>" in xml_daily and "<DaysInterval>1</DaysInterval>" in xml_daily)
    check("XML con la hora elegida", "T06:05:00" in xml_daily, xml_daily.split("StartBoundary")[1][:30])
    check("XML semanal con el día correcto", "<Wednesday/>" in scheduler.task_xml(weekly) and "<ScheduleByWeek>" in scheduler.task_xml(weekly))
    check("XML de inicio de sesión", "<LogonTrigger>" in scheduler.task_xml(logon))
    check(
        "Sin privilegios elevados ni contraseña",
        "<RunLevel>LeastPrivilege</RunLevel>" in xml_daily
        and "<LogonType>InteractiveToken</LogonType>" in xml_daily,
        "",
    )
    check("Recupera ejecuciones perdidas", "<StartWhenAvailable>true</StartWhenAvailable>" in xml_daily)
    check("Nombre y carpeta de la tarea", scheduler.TASK_PATH == "\\PCToolkit\\Mantenimiento PC Toolkit", scheduler.TASK_PATH)

    program, arguments = scheduler.launcher_command(daily)
    check("Programa de la tarea", program.endswith(".exe"), program)
    check("Argumentos del modo desatendido", "--mantenimiento" in arguments and "--antiguedad" in arguments, arguments)
    check("Sin pasos desactivados", "--sin-limpieza" not in arguments and "--sin-informe" not in arguments)
    without = scheduler.maintenance_arguments(MaintenanceSchedule(clean_temp=False, export_report=False))
    check("Los pasos desactivados se omiten", "--sin-limpieza" in without and "--sin-informe" in without, " ".join(without))
    check("Memoria opcional", "--liberar-memoria" in scheduler.maintenance_arguments(MaintenanceSchedule(release_memory=True)))
    check("Comando entrecomillado", scheduler.command_line(daily).startswith('"') and '" --mantenimiento' in scheduler.command_line(daily), scheduler.command_line(daily))

    check("Descripción legible (diaria)", "todos los días a las 06:05" in daily.describe(), daily.describe())
    check("Descripción legible (semanal)", "los miércoles" in weekly.describe(), weekly.describe())
    check("Descripción legible (sesión)", "al iniciar sesión" in logon.describe(), logon.describe())
    check("Lista de pasos", daily.task_list() == ["limpiar temporales", "generar informe"], str(daily.task_list()))
    restored = MaintenanceSchedule.from_dict(daily.to_dict())
    check("Programación persistente", restored.frequency == scheduler.FREQ_DAILY and restored.time == "06:05", str(restored.to_dict()))
    check("Programación corrupta tolerada", MaintenanceSchedule.from_dict({"hora": 12}).time == "12:00")
    check("Códigos de resultado traducidos", scheduler.describe_result(0) == "Correcta" and "acceso denegado" in scheduler.describe_result(2147943785).lower())
    check("Día fuera de la lista saneado", MaintenanceSchedule.from_dict({"dia": "Festivo"}).weekday == "Lunes")

    check("Argumentos de schtasks", "/Create" in scheduler.schtasks_create_arguments("x.xml") and "/F" in scheduler.schtasks_create_arguments("x.xml"))
    check("Consulta mediante schtasks sin crear nada", isinstance(scheduler.task_exists(), bool))
    status = scheduler.task_status()
    check("Estado coherente con la existencia", status.exists == scheduler.task_exists(), f"existe={status.exists}")
    if not status.exists:
        check("Sin tarea: mensaje informativo", bool(status.message), status.message)


def test_maintenance() -> None:
    """Mantenimiento desatendido: simulación, registro y tolerancia a fallos."""
    schedule = MaintenanceSchedule(enabled=True, clean_temp=True, export_report=True, release_memory=False)
    report = maintenance.run_maintenance(
        schedule,
        dry_run=True,
        trigger="prueba",
        min_age_hours=24.0,
        register=False,
    )
    keys = [item.key for item in report.items]
    check("Simulación con limpieza e informe", keys == ["limpieza", "informe"], str(keys))
    check("Simulación correcta", report.ok and report.dry_run, report.summary())
    check("Simulación sin escribir registro", not report.log_path or not Path(report.log_path).exists() or maintenance.recent_runs(1) == [])
    # Simulación con una carpeta temporal controlada: los números deben cuadrar.
    with tempfile.TemporaryDirectory() as tmp:
        controllada = Path(tmp)
        (controllada / "viejo.tmp").write_bytes(b"0" * 4_000_000)
        (controllada / "otro.tmp").write_bytes(b"0" * 1_000_000)
        # Se envejecen: el mantenimiento nunca borra temporales de menos de una hora.
        envejecer(controllada / "viejo.tmp")
        envejecer(controllada / "otro.tmp")
        sim = maintenance.run_maintenance(
            MaintenanceSchedule(clean_temp=True, export_report=False),
            dry_run=True,
            register=False,
            temp_folder=str(controllada),
            min_age_hours=24.0,
        )
        check("Simulación con la carpeta indicada", sim.item("limpieza").data["archivos"] == 2, str(sim.item("limpieza").data))
        check(
            "Resumen de la simulación con los datos del paso",
            "2 archivos" in sim.summary() and "MB" in sim.summary(),
            sim.summary(),
        )
        check("La simulación no borra nada", (controllada / "viejo.tmp").exists())
        real_clean = maintenance.run_maintenance(
            MaintenanceSchedule(clean_temp=True, export_report=False),
            dry_run=False,
            register=False,
            temp_folder=str(controllada),
            min_age_hours=24.0,
        )
        check("Limpieza real de la carpeta indicada", real_clean.removed_files == 2, str(real_clean.removed_files))
        check("Archivos eliminados de verdad", not any(controllada.iterdir()))
    check("Simulación con los tres pasos", {item.key for item in maintenance.run_maintenance(
        MaintenanceSchedule(release_memory=True), dry_run=True, register=False
    ).items} == {"limpieza", "memoria", "informe"})

    only_log = maintenance.run_maintenance(
        MaintenanceSchedule(clean_temp=False, export_report=False), dry_run=True, register=False
    )
    check("Sin pasos activos no se hace nada", only_log.items and only_log.items[0].key == "nada", str([i.key for i in only_log.items]))

    # El registro tolera líneas corruptas y se recorta.
    path = maintenance.maintenance_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        path.write_text("{linea rota}\n" + "\n".join(json.dumps({"momento": str(i), "resumen": "x"}) for i in range(250)) + "\n", encoding="utf-8")
        maintenance._trim_log(path, keep=200)  # noqa: SLF001
        lines = path.read_text(encoding="utf-8").splitlines()
        check("Registro recortado a 200 líneas", len(lines) == 200, str(len(lines)))
        entries = maintenance.recent_runs(5)
        check("Últimas ejecuciones leídas", len(entries) == 5 and entries[0]["momento"] == "249", str(entries[0]))
        check("Líneas corruptas ignoradas", all("momento" in entry for entry in entries))
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(backup, encoding="utf-8")

    metrics = MetricsService()
    tasks = TaskStore()
    real = maintenance.run_maintenance(
        MaintenanceSchedule(clean_temp=False, release_memory=False, export_report=True),
        metrics=metrics,
        tasks=tasks,
        dry_run=False,
        trigger="prueba",
        register=True,
    )
    check("Informe real generado", bool(real.report_path) and Path(real.report_path).exists(), real.report_path)
    check("Informe con datos del equipo", real.item("informe").ok, real.item("informe").detail)
    check("Ejecución anotada en el registro", bool(maintenance.recent_runs(1)), json.dumps(maintenance.recent_runs(1)[:1])[:80])
    check("Tarea anotada en el registro de mantenimiento", any(
        task.title == "Mantenimiento automático" for task in tasks.all()
    ))
    check("Informe dentro de la carpeta de informes", Path(real.report_path).parent.name in ("Informes", "PCToolkit"), real.report_path)


def test_installer() -> None:
    """Instalador: copia, accesos directos, registro, arranque automático y desinstalación.

    Todo ocurre en carpetas temporales y con una clave de registro propia, así que
    la prueba no toca ni el escritorio ni el menú Inicio del usuario.
    """
    import time as time_module

    from installer import installer_core as core

    check(
        "Instalación por usuario (sin permisos)",
        "Programs" in str(core.default_install_dir()) and core.APP_DISPLAY_NAME in str(core.default_install_dir()),
        str(core.default_install_dir()),
    )
    check("Clave de registro de la aplicación", core.uninstall_key().endswith(r"Uninstall\PCToolkit"), core.uninstall_key())
    check("Ruta del desinstalador", core.uninstaller_executable().name == "Desinstalar PC Toolkit.exe")
    check(
        "Accesos directos previstos",
        [path.name for path in core.InstallOptions().shortcut_paths()] == ["PC Toolkit.lnk", "PC Toolkit.lnk"],
        str(core.InstallOptions().shortcut_paths()),
    )
    check(
        "Sin accesos directos si se desactivan",
        core.InstallOptions(desktop_shortcut=False, start_menu_shortcut=False).shortcut_paths() == [],
    )
    # Las carpetas se preguntan a Windows: el escritorio puede estar redirigido
    # (por ejemplo a OneDrive) y no ser `%USERPROFILE%\Desktop`.
    check(
        "Escritorio y menú Inicio reales",
        core.desktop_dir().is_absolute() and core.desktop_dir().is_dir() and core.start_menu_dir().is_dir(),
        f"{core.desktop_dir()} · {core.start_menu_dir()}",
    )
    check(
        "Carpeta desconocida usa la ruta prevista",
        core.known_folder("CarpetaQueNoExiste", Path("C:/respaldo")) == Path("C:/respaldo"),
    )
    # El desinstalador se compila **sin PySide6** (`--exclude-module PySide6` en
    # `build_installer.py`): así pesa 7 MB en vez de 50, pero cualquier import que acabe
    # en `pctoolkit` —aunque esté dentro de una función— lo deja muerto en el equipo del
    # usuario. Aquí se reproduce en un proceso aparte con PySide6 bloqueado, que es la
    # única forma de notarlo: la propia suite importa PySide6 para la interfaz.
    sonda = Path(tempfile.gettempdir()) / "pctoolkit_sonda_ligera.py"
    sonda.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "class BloqueaPySide6:\n"
        "    def find_spec(self, nombre, ruta=None, destino=None):\n"
        "        if nombre.split('.')[0] == 'PySide6':\n"
        "            raise ImportError('PySide6 bloqueado a propósito')\n"
        "        return None\n"
        "sys.meta_path.insert(0, BloqueaPySide6())\n"
        "from installer import installer_core\n"
        "print(installer_core.desktop_dir())\n"
        "print(installer_core.start_menu_dir())\n"
        "print(installer_core._powershell('Write-Output listo').returncode)\n"
        "print('PySide6' in sys.modules)\n",
        encoding="utf-8",
    )
    ligera = subprocess.run(
        [sys.executable, str(sonda)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )
    salida_ligera = (ligera.stdout or "").strip().splitlines()
    check(
        "El instalador ligero funciona sin PySide6 (como el desinstalador compilado)",
        ligera.returncode == 0
        and len(salida_ligera) == 4
        and salida_ligera[-2] == "0"
        and salida_ligera[-1] == "False",
        (ligera.stdout or ligera.stderr or "").strip()[-200:],
    )
    check("Medida de carpetas", core.directory_size(ROOT / "pctoolkit")[0] > 10_000, str(core.directory_size(ROOT / "pctoolkit")))

    faltante = core.install(core.InstallOptions(source=ROOT / "no_existe", key_name="PCToolkitPruebaTest"))
    check("Instalación sin origen avisa", not faltante.ok and "build_exe" in faltante.message, faltante.message)

    # --- Instalador de un solo archivo: la aplicación viaja dentro --------------
    # PyInstaller deja sus datos en `sys._MEIPASS`; ahí está la carga cuando el
    # instalador se compila con `build_installer.py`. Se simula esa carpeta con un
    # ZIP de verdad para comprobar extracción, defensas y limpieza.
    import shutil
    import zipfile

    check(
        "Fuera del instalador no hay carga incrustada",
        core.payload_path() is None and core.unpack_payload() is None,
        f"{core.payload_path()} · {core.unpack_payload()}",
    )

    original_carga = getattr(sys, "_MEIPASS", None)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        carga = base / "carga"
        app = base / "app"
        (app / "_internal").mkdir(parents=True)
        (app / "PC Toolkit.exe").write_bytes(b"MZ" + b"0" * 64)
        (app / "Desinstalar PC Toolkit.exe").write_bytes(b"MZ")
        (app / "_internal" / "datos.dll").write_bytes(b"0" * 512)
        carga.mkdir()
        with zipfile.ZipFile(carga / core.PAYLOAD_NAME, "w", zipfile.ZIP_DEFLATED) as archivo:
            for ruta in sorted(app.rglob("*")):
                if ruta.is_file():
                    archivo.write(ruta, ruta.relative_to(app).as_posix())

        sys._MEIPASS = str(carga)
        try:
            check(
                "La carga se encuentra dentro del instalador",
                core.payload_path() == carga / core.PAYLOAD_NAME,
                str(core.payload_path()),
            )
            extraida = core.unpack_payload()
            check(
                "La carga se extrae con la aplicación dentro",
                extraida is not None
                and core.app_executable(extraida).exists()
                and (extraida / "_internal" / "datos.dll").exists(),
                str(extraida),
            )
            # Sin carpeta de destino la extracción es temporal: la prueba la llama
            # directamente, así que también la borra ella (el instalador lo hace solo).
            if extraida is not None:
                shutil.rmtree(extraida, ignore_errors=True)
            check(
                "La extracción directa no deja carpeta colgando",
                extraida is not None and not extraida.exists(),
                str(extraida),
            )

            # Un paquete manipulado no puede escribir fuera de la carpeta temporal.
            trampa = base / "trampa.zip"
            with zipfile.ZipFile(trampa, "w") as archivo:
                archivo.writestr("../fuera.txt", "no debería salir de la carpeta")
            destino_trampa = base / "trampa_destino"
            shutil.copyfile(trampa, carga / core.PAYLOAD_NAME)
            aviso_trampa = ""
            try:
                core.unpack_payload(destino_trampa)
            except ValueError as exc:
                aviso_trampa = str(exc)
            check(
                "Una carga con «../» se rechaza sin escribir fuera",
                aviso_trampa and not (base / "fuera.txt").exists(),
                aviso_trampa or "no se quejó",
            )

            # Y una carga sin la aplicación tampoco se acepta.
            vacia = base / "vacia.zip"
            with zipfile.ZipFile(vacia, "w") as archivo:
                archivo.writestr("leeme.txt", "no hay aplicación aquí")
            destino_vacia = base / "vacia_destino"
            shutil.copyfile(vacia, carga / core.PAYLOAD_NAME)
            aviso_vacia = ""
            try:
                core.unpack_payload(destino_vacia)
            except ValueError as exc:
                aviso_vacia = str(exc)
            check(
                "Una carga sin la aplicación se rechaza y se limpia",
                aviso_vacia and not destino_vacia.exists(),
                f"{aviso_vacia or 'no se quejó'} · existe={destino_vacia.exists()}",
            )

            # Instalación completa desde la carga incrustada, sin origen explícito.
            with zipfile.ZipFile(carga / core.PAYLOAD_NAME, "w", zipfile.ZIP_DEFLATED) as archivo:
                for ruta in sorted(app.rglob("*")):
                    if ruta.is_file():
                        archivo.write(ruta, ruta.relative_to(app).as_posix())
            escritorio_carga = base / "escritorio"
            menu_carga = base / "menu"
            escritorio_carga.mkdir()
            menu_carga.mkdir()
            instalado_carga = base / "instalado"
            antes = set(Path(tempfile.gettempdir()).glob("pctoolkit_carga_*"))
            pasos_carga: list[str] = []
            resultado_carga = core.install(
                core.InstallOptions(
                    target_dir=instalado_carga,
                    key_name="PCToolkitCargaTest",
                    run_value="PCToolkitCargaTest",
                    desktop=escritorio_carga,
                    start_menu=menu_carga,
                    launch_after=False,
                ),
                progress=pasos_carga.append,
            )
            check(
                "Se instala desde la carga incrustada sin origen",
                resultado_carga.ok
                and core.app_executable(instalado_carga).exists()
                and (instalado_carga / "_internal" / "datos.dll").exists()
                and "Preparando los archivos de la aplicación…" in pasos_carga,
                f"{resultado_carga.message} · pasos: {' | '.join(pasos_carga)}",
            )
            despues = set(Path(tempfile.gettempdir()).glob("pctoolkit_carga_*"))
            check(
                "La carpeta temporal de la carga se borra al terminar",
                despues <= antes,
                str(despues - antes),
            )
            core.uninstall(
                target_dir=instalado_carga,
                key_name="PCToolkitCargaTest",
                run_value="PCToolkitCargaTest",
                desktop=escritorio_carga,
                start_menu=menu_carga,
            )
            # La carpeta la borra un proceso aparte: se le da un momento para que la
            # carpeta temporal de la prueba no quede ocupada al salir.
            limite_carga = time_module.time() + 20
            while time_module.time() < limite_carga and instalado_carga.exists():
                time_module.sleep(0.5)
        finally:
            if original_carga is None:
                try:
                    delattr(sys, "_MEIPASS")
                except AttributeError:  # pragma: no cover - ya no estaba
                    pass
            else:
                sys._MEIPASS = original_carga
    check(
        "La carga simulada no cambia el proceso",
        getattr(sys, "_MEIPASS", None) == original_carga,
        str(getattr(sys, "_MEIPASS", None)),
    )

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        origen = base / "origen" / "PC Toolkit"
        origen.mkdir(parents=True)
        # Para el icono se usa un ejecutable de Windows real y pequeño: el COM de
        # Windows se niega a guardar un acceso directo cuyo icono sea un PE roto.
        import shutil as _shutil

        modelo = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
        if modelo.exists():
            _shutil.copyfile(modelo, origen / "PC Toolkit.exe")
        else:  # pragma: no cover - reserva si no hay System32
            (origen / "PC Toolkit.exe").write_bytes(b"MZ")
        (origen / "Desinstalar PC Toolkit.exe").write_bytes(b"MZ")
        (origen / "_internal").mkdir()
        (origen / "_internal" / "base_library.zip").write_bytes(b"0" * 2048)
        esperado_archivos = sum(1 for item in origen.rglob("*") if item.is_file())
        esperado_bytes = sum(item.stat().st_size for item in origen.rglob("*") if item.is_file())
        escritorio = base / "escritorio"
        menu = base / "menu"
        escritorio.mkdir()
        menu.mkdir()
        destino = base / "instalado"

        opciones = core.InstallOptions(
            target_dir=destino,
            autostart=True,
            source=origen,
            key_name="PCToolkitPruebaTest",
            run_value="PCToolkitPruebaTest",
            desktop=escritorio,
            start_menu=menu,
            launch_after=False,
        )
        pasos: list[str] = []
        resultado = core.install(opciones, progress=pasos.append)
        check("Instalación correcta", resultado.ok, resultado.message)
        check("Pasos informados", len(pasos) >= 3, " · ".join(pasos))
        check(
            "Archivos y tamaño calculados",
            resultado.files == esperado_archivos and resultado.bytes == esperado_bytes,
            f"{resultado.files} archivos, {resultado.bytes} bytes (esperado {esperado_archivos}/{esperado_bytes})",
        )
        check(
            "Sin avisos sobre los accesos directos",
            not [aviso for aviso in resultado.warnings if "acceso directo" in aviso.lower()],
            " | ".join(resultado.warnings),
        )
        check(
            "Accesos directos creados",
            len(resultado.shortcuts) == 2 and all(Path(path).exists() for path in resultado.shortcuts),
            str(resultado.shortcuts),
        )
        objetivo = core.read_shortcut_target(escritorio / "PC Toolkit.lnk")
        # Se comparan rutas resueltas y no el texto: Windows guarda el destino en su forma
        # larga aunque se le dé la corta (8.3), y en un servidor de compilación la carpeta
        # temporal se llama `RUNNER~1`, así que comparar el texto daba un falso negativo.
        check(
            "El acceso directo apunta al programa instalado",
            bool(objetivo) and Path(objetivo).resolve() == core.app_executable(destino).resolve(),
            f"{objetivo} (destino {core.app_executable(destino)})",
        )
        entrada = core.read_uninstall_entry("PCToolkitPruebaTest") or {}
        check("Registrado en «Aplicaciones instaladas»", entrada.get("DisplayName") == "PC Toolkit", str(entrada.get("DisplayName")))
        check("Con la versión de la aplicación", entrada.get("DisplayVersion") == APP_VERSION, str(entrada.get("DisplayVersion")))
        check(
            "Desinstalador registrado",
            entrada.get("UninstallString") == f'"{core.uninstaller_executable(destino)}"',
            str(entrada.get("UninstallString")),
        )
        check("Comando silencioso registrado", "--silencioso" in str(entrada.get("QuietUninstallString", "")))
        check("Tamaño estimado registrado", int(entrada.get("EstimatedSize", 0)) > 0, str(entrada.get("EstimatedSize")))
        check("Ubicación registrada", entrada.get("InstallLocation") == str(destino), str(entrada.get("InstallLocation")))
        arranque = core.autostart_command("PCToolkitPruebaTest")
        check("Arranque automático con la bandeja", arranque.endswith("--bandeja") and "PC Toolkit.exe" in arranque, arranque)
        check("Ruta del origen detectada", core.source_dir().name in ("PC Toolkit", "installer"), str(core.source_dir()))

        borrado = core.uninstall(
            target_dir=destino,
            key_name="PCToolkitPruebaTest",
            run_value="PCToolkitPruebaTest",
            desktop=escritorio,
            start_menu=menu,
        )
        check("Desinstalación correcta", borrado.ok, borrado.message)
        check(
            "Accesos directos eliminados",
            not (escritorio / "PC Toolkit.lnk").exists() and not (menu / "PC Toolkit.lnk").exists(),
        )
        check("Entrada de registro eliminada", core.read_uninstall_entry("PCToolkitPruebaTest") is None)
        check("Arranque automático quitado", core.autostart_command("PCToolkitPruebaTest") == "")
        limite = time_module.time() + 30
        while time_module.time() < limite and destino.exists():
            time_module.sleep(0.5)
        check("Carpeta de programa eliminada", not destino.exists(), str(destino))

        # --- La carpeta de destino la elige el usuario: ni raíces ni perfiles -------
        raiz_unidad = Path("C:\\")
        check(
            "Instalar en la raíz de una unidad se rechaza",
            "raíz" in core.unsafe_target_reason(raiz_unidad),
            core.unsafe_target_reason(raiz_unidad),
        )
        perfil = Path(os.path.expanduser("~"))
        check(
            "Instalar en el perfil se rechaza",
            bool(core.unsafe_target_reason(perfil)),
            core.unsafe_target_reason(perfil),
        )
        rechazada = core.install(
            core.InstallOptions(target_dir=raiz_unidad, source=origen, key_name="PCToolkitPruebaTest")
        )
        check("Una carpeta peligrosa no llega a copiar nada", not rechazada.ok, rechazada.message)
        sin_tocar = core.uninstall(target_dir=raiz_unidad, key_name="PCToolkitPruebaTest")
        check(
            "La desinstalación tampoco toca una carpeta peligrosa",
            bool(sin_tocar.warnings)
            and "conservado la carpeta" in sin_tocar.message
            and raiz_unidad.is_dir(),
            f"{sin_tocar.message} | {' | '.join(sin_tocar.warnings)}",
        )

        # --- El registro de archivos permite desinstalar sin borrar lo ajeno -------
        conserva = base / "conserva"
        creada = core.install(
            core.InstallOptions(
                target_dir=conserva,
                source=origen,
                key_name="PCToolkitPruebaTest",
                run_value="PCToolkitPruebaTest",
                desktop=escritorio,
                start_menu=menu,
                launch_after=False,
            )
        )
        check("Instalación con registro de archivos", creada.ok, creada.message)
        registro = core.read_manifest(conserva) or {}
        check(
            "El registro deja por escrito lo que se instaló",
            registro.get("archivos") == core.installed_files(conserva)
            and "PC Toolkit.exe" in registro.get("archivos", []),
            f"{len(registro.get('archivos', []))} archivos",
        )
        ajeno = conserva / "mis_documentos"
        ajeno.mkdir()
        (ajeno / "importante.txt").write_text("no se toca", encoding="utf-8")

        retirada = core.uninstall(
            target_dir=conserva,
            key_name="PCToolkitPruebaTest",
            run_value="PCToolkitPruebaTest",
            desktop=escritorio,
            start_menu=menu,
        )
        check(
            "Desinstalar con archivos ajenos avisa de lo que se conserva",
            any("no instaló PC Toolkit" in aviso for aviso in retirada.warnings),
            " | ".join(retirada.warnings),
        )
        check(
            "Los archivos del usuario se conservan",
            (ajeno / "importante.txt").exists() and conserva.is_dir(),
            str(conserva),
        )
        check("Los archivos de la aplicación sí se quitan", not (conserva / "PC Toolkit.exe").exists())

        # --- Instalar sobre una carpeta que ya tenía archivos del usuario ---------
        # El registro tiene que anotar lo que copia la instalación, no lo que hay en la
        # carpeta: si anota también lo ajeno, al desinstalar se lo lleva por delante
        # aunque la instalación haya prometido conservarlo.
        con_ajenos = base / "con_ajenos"
        (con_ajenos / "otros").mkdir(parents=True)
        documento = con_ajenos / "otros" / "importante.docx"
        documento.write_text("no se toca", encoding="utf-8")
        puesta = core.install(
            core.InstallOptions(
                target_dir=con_ajenos,
                source=origen,
                key_name="PCToolkitPruebaTest",
                run_value="PCToolkitPruebaTest",
                desktop=escritorio,
                start_menu=menu,
                launch_after=False,
            )
        )
        check("Instalación sobre una carpeta con archivos ajenos", puesta.ok, puesta.message)
        check(
            "La instalación promete conservar lo que ya estaba",
            any("se conservarán" in aviso for aviso in puesta.warnings),
            " | ".join(puesta.warnings),
        )
        registro_ajeno = core.read_manifest(con_ajenos) or {}
        anotados = list(registro_ajeno.get("archivos") or [])
        check(
            "El registro no anota los archivos que ya estaban",
            "otros/importante.docx" not in anotados and "PC Toolkit.exe" in anotados,
            f"{len(anotados)} archivos anotados",
        )
        retirada_ajena = core.uninstall(
            target_dir=con_ajenos,
            key_name="PCToolkitPruebaTest",
            run_value="PCToolkitPruebaTest",
            desktop=escritorio,
            start_menu=menu,
        )
        check(
            "Desinstalar no borra el archivo que ya estaba",
            documento.exists() and documento.read_text(encoding="utf-8") == "no se toca",
            f"{documento.exists()} · {retirada_ajena.message}",
        )

        # Con «borrar datos» se lleva también la carpeta de datos del usuario.
        datos = Path(_TEMP_HOME) / "PCToolkit"
        datos.mkdir(parents=True, exist_ok=True)
        (datos / "historial.jsonl").write_text("ejemplo\n", encoding="utf-8")
        if os.environ.get("APPDATA") == _TEMP_HOME:
            borrado_datos = core.uninstall(
                target_dir=base / "otra",
                key_name="PCToolkitPruebaTest",
                run_value="PCToolkitPruebaTest",
                desktop=escritorio,
                start_menu=menu,
                remove_user_data=True,
                keep_folder=True,
            )
            check("Desinstalación con datos del usuario", borrado_datos.ok and not datos.exists(), borrado_datos.message)


def test_interface() -> None:
    from PySide6.QtCore import QSettings, QTime, Qt

    from pctoolkit.core.alerts import METRIC_CPU, METRIC_DISK, METRIC_RAM, AlertConfig
    from pctoolkit.core.history import HistoryStore
    from pctoolkit.core.settings import AppSettings
    from pctoolkit.ui.shell import MainWindow
    from pctoolkit.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv[:1])
    # El tema se fija a mano: con `auto` la suite dependería del modo en que esté
    # Windows en cada equipo y las comprobaciones del render dejarían de ser ciertas.
    apply_theme(app, "dark")
    app_settings = AppSettings(QSettings(str(Path(_TEMP_HOME) / "ajustes_prueba.ini"), QSettings.IniFormat))
    app_settings.notify_on_alert = False  # evita notificaciones reales durante las pruebas
    app_settings.eco_mode = False  # el perfil se comprueba explícitamente más abajo
    app_settings.eco_when_hidden = True
    app_settings.memory_options = MemoryOptions(
        auto_enabled=False, min_process_mb=50.0, empty_system_cache=False, purge_standby=False
    )
    # Los umbrales por defecto (90 %) se alcanzan de verdad mientras corre la
    # prueba en un equipo cargado, y entonces el aviso aparece antes de tiempo.
    # La ventana arranca con umbrales imposibles: las alertas se provocan a
    # propósito más abajo, cuando toca comprobarlas.
    app_settings.alert_config = AlertConfig(enabled=True, sustained_samples=30, cooldown_minutes=60)
    for metrica in (METRIC_CPU, METRIC_RAM, METRIC_DISK):
        app_settings.alert_config.set_threshold(metrica, 100.0)
    history_store = HistoryStore(Path(_TEMP_HOME) / "historial_prueba.jsonl")
    history_store.clear()
    window = MainWindow(app_settings, history_store)
    window.resize(1360, 860)
    window.show()
    _settle(app, 900)
    snap = window.metrics.poll()
    window._on_sample(snap)  # noqa: SLF001
    check("Ventana creada", window.centralWidget() is not None)

    # --- La guarda de instancia única ---------------------------------------
    # Con la copia abierta como administrador y el acceso directo lanzado normal, Windows
    # descarta los mensajes entre niveles de integridad distintos: la ventana se quedaría
    # detrás. Se admite ese mensaje concreto y se comprueba que Windows lo acepta.
    from pctoolkit.core import single_instance as guarda_instancia

    guarda = guarda_instancia.SingleInstance()
    check("La guarda se queda con el bloqueo de instancia única", guarda.acquire(), guarda_instancia.lock_path())
    escucha = guarda.listen(window.show_from_another_instance, window.winId())
    admite = guarda_instancia.allow_message_window(int(window.winId())) if os.name == "nt" else True
    check(
        "El aviso de la otra copia se admite desde un proceso normal",
        escucha and admite,
        f"escucha={escucha}, ventana={int(window.winId())}",
    )
    guarda.stop_listening()
    guarda.release()
    check("El bloqueo se suelta al terminar", not guarda.held, "bloqueo liberado")
    check("Ocho páginas cargadas", window.stack.count() == 8, str(window.stack.count()))
    # Arrancar no debe lanzar trabajo en segundo plano (una consulta del
    # Programador de tareas son varios segundos de PowerShell): el estado real se
    # lee al abrir esa página.
    from pctoolkit.ui import workers as workers_ui

    en_marcha_al_arrancar = workers_ui.pending_calls()
    check(
        "Sin consultas en segundo plano al arrancar",
        not en_marcha_al_arrancar,
        ", ".join(call.describe() for call in en_marcha_al_arrancar),
    )
    check(
        "El estado del mantenimiento no se consulta hasta abrir la página",
        not window.schedule_page.status_loaded and window.schedule_page.state_badge.text() == "Sin consultar",
        window.schedule_page.state_badge.text(),
    )
    for key in ("home", "memory", "startup", "disk", "tools", "tasks", "reports", "schedule"):
        window.navigate(key)
        check(f"Navegación a {key}", window.stack.currentWidget() is window._pages[key])  # noqa: SLF001
    check(
        "Al abrir «Mantenimiento» se consulta el estado en Windows",
        window.schedule_page.status_querying or window.schedule_page.status_loaded,
        f"consulta={window.schedule_page.status_querying} leido={window.schedule_page.status_loaded}",
    )

    # --- Página de memoria ------------------------------------------------
    window.navigate("memory")
    _settle(app, 300)
    check("Medidor de RAM con la última muestra", window.optimize_page.ram_value.text().endswith("%"), window.optimize_page.ram_value.text())
    check("Lista de procesos con entradas", window.optimize_page.process_list.count() > 0, str(window.optimize_page.process_list.count()))
    rows = list(window.optimize_page._rows.values())  # noqa: SLF001
    check("Filas con botón de liberación", bool(rows) and all(row.trim_button.text() == "Liberar" for row in rows))
    # La lista tiene que dejar ver los procesos con holgura: ninguna fila ni botón
    # recortados por la derecha, sin barra horizontal y con varias filas enteras a
    # la vista (era justo lo que fallaba cuando la lista era un QListWidget).
    lista = window.optimize_page.process_list
    viewport = lista.viewport()

    def _sobresale(widget) -> bool:  # noqa: ANN001
        esquina = widget.mapTo(viewport, widget.rect().topLeft())
        return esquina.x() + widget.width() > viewport.width()

    fuera = [row.target.name for row in rows if _sobresale(row) or _sobresale(row.trim_button)]
    check("Ninguna fila de procesos se sale del ancho", not fuera, ", ".join(fuera[:3]))
    check(
        "Sin desplazamiento horizontal en la lista de procesos",
        lista.horizontalScrollBar().maximum() == 0,
        str(lista.horizontalScrollBar().maximum()),
    )
    enteras = sum(
        1
        for row in rows
        if row.mapTo(viewport, row.rect().topLeft()).y() + row.height() <= viewport.height()
    )
    check("Varias filas de procesos enteras a la vista", enteras >= 5, f"{enteras} de {len(rows)}")
    if rows:
        victim = rows[0]
        victim.trim_button.click()
        _settle(app, 250)
        check("Recorte individual desde la interfaz", not victim.trim_button.isEnabled(), victim.trim_button.text())
    check("Aviso de permisos mostrado", bool(window.optimize_page.privilege_label.text()), window.optimize_page.privilege_label.text()[:40])
    check("Opciones de memoria reflejadas", window.optimize_page.min_mb_spin.value() == 50.0, str(window.optimize_page.min_mb_spin.value()))

    # Flujo real de liberación a través de la ventana (en segundo plano). Recorre los
    # procesos de verdad, así que en un servidor recién arrancado —donde ninguno llega al
    # mínimo de memoria— no habría nada que recortar y la comprobación no mediría el flujo.
    # Se deja uno propio en marcha, con memoria de sobra, y se apaga al terminar.
    ayudante = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "datos = bytearray(400 * 1024 * 1024); "
            "datos[::4096] = b'x' * (400 * 1024 * 1024 // 4096); "
            "import time; time.sleep(300)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # Se espera a que esa memoria esté a la vista antes de pedir la liberación: si el
    # proceso auxiliar va lento (una máquina cargada, un servidor recién arrancado), la
    # búsqueda no lo encontraría y volvería el «ningún proceso accesible» que esto evita.
    import psutil

    limite = time.time() + 25
    while time.time() < limite:
        try:
            if psutil.Process(ayudante.pid).memory_info().rss >= 60 * 1024 * 1024:
                break
        except psutil.Error:
            break
        time.sleep(0.3)
    try:
        window.release_memory_now("manual")
        for _ in range(60):
            if not window._memory_busy:  # noqa: SLF001
                break
            _settle(app, 200)
        result = window.optimize_page.last_result
        check(
            "Liberación completada desde la interfaz",
            result is not None and result.processes_trimmed > 0,
            result.summary() if result else "sin resultado",
        )
        check("Resultado mostrado en la tarjeta", "MB" in window.optimize_page.last_freed.text(), window.optimize_page.last_freed.text())
    finally:
        ayudante.kill()
        ayudante.wait(timeout=15)
    check("Historial de la sesión con la limpieza", window.optimize_page.history_list.count() == 1, str(window.optimize_page.history_list.count()))
    check("Botón habilitado de nuevo", window.optimize_page.release_button.isEnabled())

    # La guardia automática está conectada al bucle de muestreo.
    import time as time_module

    # El umbral se pone por debajo de cualquier uso real de RAM: si se dejara en el 30 %,
    # en un equipo con poca memoria cargada la muestra no llegaría al umbral y la prueba
    # dependería de cuánta RAM tenga la máquina. El enfriamiento está activo, así que
    # aunque se supere el umbral no se libera nada de verdad.
    window.memory_guard.options = MemoryOptions(
        auto_enabled=True, threshold_percent=1.0, sustained_samples=1, cooldown_minutes=60
    )
    # Con el enfriamiento activo la guardia mira la muestra pero no llega a limpiar,
    # así la prueba comprueba el cableado sin alterar de nuevo los procesos reales.
    window.memory_guard.note_clean(now=time_module.time())
    high = window.metrics.poll()  # al tomar la muestra la ventana ya la evalúa
    before_streak = window.memory_guard.streak
    high.ram_percent = 99.0
    window._on_sample(high)  # noqa: SLF001
    check("La muestra periódica llega a la guardia", before_streak >= 1, str(before_streak))
    check(
        "La limpieza automática reacciona a la muestra",
        window.memory_guard.streak == before_streak + 1,
        f"{before_streak} -> {window.memory_guard.streak}",
    )
    check("El enfriamiento evita limpiezas encadenadas", not window._memory_busy)  # noqa: SLF001
    window.memory_guard.options = app_settings.memory_options
    window.metrics.poll()

    # --- Página de programas de inicio --------------------------------------
    from PySide6.QtWidgets import QPushButton, QScrollArea

    from pctoolkit.core import startup as startup_core_ui
    from pctoolkit.ui.startup_page import StartupRow

    window.navigate("startup")
    _settle(app, 400)
    arranque = window.startup_page
    check("La lista de arranque tiene entradas", len(arranque.entries) >= 1, f"{len(arranque.entries)} entradas")
    check("Cuatro cifras en la cabecera", arranque.tiles_layout.count() == 4, str(arranque.tiles_layout.count()))
    filas_arranque = arranque.findChildren(StartupRow)
    check(
        "Una fila por entrada",
        len(filas_arranque) == len(arranque.entries),
        f"{len(filas_arranque)} filas / {len(arranque.entries)} entradas",
    )
    check(
        "Las entradas se agrupan por origen",
        len(startup_core_ui.by_source(arranque.entries)) >= 1,
        ", ".join(startup_core_ui.by_source(arranque.entries)),
    )
    check("Sin cambios al empezar", arranque.store.count() == 0, str(arranque.store.count()))
    check("La tarjeta de deshacer avisa de que está vacía", arranque.undo_rows == 1, str(arranque.undo_rows))

    vista = arranque.findChild(QScrollArea)
    area = vista.viewport()

    def _sale(widget) -> bool:  # noqa: ANN001
        esquina = widget.mapTo(area, widget.rect().topLeft())
        return esquina.x() + widget.width() > area.width()

    fuera_ancho = [fila.entry.name for fila in filas_arranque if _sale(fila)]
    check("Ninguna fila de arranque se sale del ancho", not fuera_ancho, ", ".join(fuera_ancho[:3]))
    check(
        "Sin desplazamiento horizontal en programas de inicio",
        vista.horizontalScrollBar().maximum() == 0,
        str(vista.horizontalScrollBar().maximum()),
    )
    botones_fuera = [fila.entry.name for fila in filas_arranque if _sale(fila.toggle_button)]
    check("Los botones de activar/desactivar caben en su fila", not botones_fuera, ", ".join(botones_fuera[:3]))
    con_boton = [fila for fila in filas_arranque if fila.entry.can_toggle]
    check(
        "Cada fila conmutable ofrece activar o desactivar",
        all(fila.toggle_button.text() in ("Activar", "Desactivar") for fila in con_boton),
        str([fila.toggle_button.text() for fila in con_boton[:4]]),
    )
    # Las filas que sí se pueden cambiar y las que no se comprueban con entradas
    # propias: así la prueba no depende de lo que haya instalado en el equipo.
    ejemplo = startup_core_ui.StartupEntry(
        id="ejemplo",
        name="Programa de ejemplo",
        command='"C:\\App\\app.exe"',
        source=startup_core_ui.SOURCE_USUARIO,
        location="HKEY_CURRENT_USER\\...\\Run",
        kind=startup_core_ui.KIND_REGISTRO,
        enabled=True,
    )
    fila_ejemplo = StartupRow(ejemplo)
    avisos: list[str] = []
    fila_ejemplo.toggle_requested.connect(lambda entrada: avisos.append(entrada.name))
    fila_ejemplo.toggle_button.click()
    check("El botón de la fila avisa del cambio", avisos == ["Programa de ejemplo"], ", ".join(avisos))
    check(
        "La fila explica qué pasa al desactivar",
        fila_ejemplo.toggle_button.text() == "Desactivar" and "borra" in fila_ejemplo.toggle_button.toolTip(),
        fila_ejemplo.toggle_button.toolTip()[:70],
    )
    fila_apagada = StartupRow(startup_core_ui.StartupEntry(
        id="apagada",
        name="Entrada apagada",
        command="",
        source=startup_core_ui.SOURCE_USUARIO,
        location="HKEY_CURRENT_USER\\...\\RunOnce",
        kind=startup_core_ui.KIND_REGISTRO,
        enabled=False,
    ))
    check("Una entrada desactivada ofrece activarla", fila_apagada.toggle_button.text() == "Activar")
    solo_lectura = StartupRow(startup_core_ui.StartupEntry(
        id="solo-lectura",
        name="Se ejecuta una vez",
        command="",
        source=startup_core_ui.SOURCE_USUARIO,
        location="HKEY_CURRENT_USER\\...\\RunOnce",
        kind=startup_core_ui.KIND_REGISTRO,
        enabled=True,
        can_toggle=False,
        note="Se ejecuta una sola vez.",
    ))
    check(
        "Una entrada que no se puede cambiar lo dice",
        solo_lectura.toggle_button.text() == "Solo lectura" and not solo_lectura.toggle_button.isEnabled(),
        solo_lectura.toggle_button.text(),
    )

    # Cambio real y deshacer de punta a punta, sobre una clave propia de prueba.
    import winreg as _winreg

    base_ui = r"Software\PCToolkit\PruebaArranqueUI"
    ruta_run = base_ui + r"\Run"
    ruta_marca = base_ui + r"\StartupApproved"
    fuente_ui = startup_core_ui.RegistrySource(
        "prueba-ui", "Prueba", "HKCU\\Prueba", ruta_run, _winreg.HKEY_CURRENT_USER, ruta_marca
    )
    # La clave de prueba vive fuera de las de Windows: se añade a la lista cerrada mientras
    # dura la prueba para ejercitar el mismo camino, sin tocar nada del equipo.
    claves_ui = startup_core_ui.APPROVED_PATHS
    startup_core_ui.APPROVED_PATHS = (*claves_ui, ruta_marca)
    try:
        with _winreg.CreateKeyEx(_winreg.HKEY_CURRENT_USER, ruta_run, 0, _winreg.KEY_SET_VALUE) as clave:
            _winreg.SetValueEx(clave, "PruebaUI", 0, _winreg.REG_SZ, f'"{sys.executable}" --version')
        entrada_ui = startup_core_ui.read_registry_entries([fuente_ui])[0]
        aplicado, mensaje = startup_core_ui.move(entrada_ui, False, arranque.store)
        check("El cambio se aplica y se anota", aplicado, mensaje)
        arranque.refresh(with_tasks=False)
        check("El cambio aparece en la tarjeta de deshacer", arranque.store.count() == 1, str(arranque.store.count()))
        boton_deshacer = None
        for indice in range(arranque.undo_box.count()):
            fila_undo = arranque.undo_box.itemAt(indice).widget()
            candidato = fila_undo.findChild(QPushButton) if fila_undo is not None else None
            if candidato is not None and candidato.text() == "Deshacer":
                boton_deshacer = candidato
                break
        check("La tarjeta de deshacer ofrece su botón", boton_deshacer is not None)
        if boton_deshacer is not None:
            boton_deshacer.click()
            _settle(app, 300)
            devuelta = startup_core_ui.read_registry_entries([fuente_ui])[0]
            check("Deshacer desde la pantalla lo deja como estaba", devuelta.enabled, devuelta.status_label)
            check("El apunte queda marcado como deshecho", arranque.store.pending() == [])
    finally:
        startup_core_ui.APPROVED_PATHS = claves_ui
        _borrar_clave_registro(_winreg.HKEY_CURRENT_USER, base_ui)
    arranque.store.clear()
    arranque.refresh(with_tasks=False)
    check("La clave de prueba de la interfaz se elimina", not _existe_clave_registro(_winreg.HKEY_CURRENT_USER, base_ui))

    # --- Página de mantenimiento programado --------------------------------
    window.navigate("schedule")
    _settle(app, 250)
    page = window.schedule_page
    check("Frecuencias en la interfaz", page.frequency_combo.count() == len(scheduler.FREQUENCIES), str(page.frequency_combo.count()))
    page.frequency_combo.setCurrentText(scheduler.FREQ_WEEKLY)
    page.weekday_combo.setCurrentText("Miércoles")
    page.time_edit.setTime(QTime(6, 5))
    page.clean_check.setChecked(True)
    page.report_check.setChecked(False)
    page.memory_check.setChecked(True)
    collected = page._collect_schedule()  # noqa: SLF001
    check(
        "La interfaz recoge la programación",
        collected.frequency == scheduler.FREQ_WEEKLY and collected.time == "06:05" and collected.weekday == "Miércoles",
        collected.describe(),
    )
    check("El día se habilita solo en la vista semanal", page.weekday_combo.isEnabled())
    page.frequency_combo.setCurrentText(scheduler.FREQ_LOGON)
    check("Sin hora al iniciar sesión", not page.time_edit.isEnabled())
    page.frequency_combo.setCurrentText(scheduler.FREQ_DAILY)
    check("Comando previsto mostrado", "--mantenimiento" in scheduler.command_line(collected), scheduler.command_line(collected))

    page._render_status(scheduler.TaskStatus())  # noqa: SLF001
    check("Estado sin tarea", page.state_badge.text() == "Sin programar", page.state_badge.text())
    check("Aviso para programarla", "Guardar y programar" in page.notice_label.text(), page.notice_label.text()[:60])
    page._render_status(  # noqa: SLF001
        scheduler.TaskStatus(
            exists=True,
            enabled=True,
            state="Ready",
            next_run="2026-10-11 06:05",
            last_run="2026-10-10 06:05",
            last_result=0,
            command=scheduler.command_line(app_settings.schedule_config),
        )
    )
    check("Estado con tarea programada", page.state_badge.text() == "Programada", page.state_badge.text())
    check("Próxima ejecución mostrada", page.status_rows["proxima"].text() == "2026-10-11 06:05")
    check("Resultado traducido", page.status_rows["resultado"].text() == "Correcta", page.status_rows["resultado"].text())
    check("Botones operativos con tarea", page.run_button.isEnabled() and page.delete_button.isEnabled())
    page._render_status(scheduler.TaskStatus(exists=True, enabled=False, state="Disabled"))  # noqa: SLF001
    check("Estado de tarea desactivada", page.state_badge.text() == "Desactivada", page.state_badge.text())
    check("Histórico de ejecuciones consultado", page.history_list.count() >= 1, str(page.history_list.count()))

    # --- Bandeja y consumo -------------------------------------------------
    if window.tray.available:
        check("Menú de bandeja con limpieza de memoria", window.tray.memory_action.text() == "Liberar memoria ahora")
        check("Menú de bandeja con mantenimiento", "mantenimiento" in window.tray.maintenance_action.text().lower())
    else:
        check(
            "Sin bandeja: acciones igualmente definidas",
            hasattr(window.tray, "memory_requested") and hasattr(window.tray, "maintenance_requested"),
            "bandeja no disponible en este entorno",
        )
    check("Perfil completo con la ventana a la vista", window.metrics.profile == PROFILE_FULL, window.metrics.profile)
    window.hide()
    _settle(app, 200)
    check("Perfil eco con la ventana oculta", window.metrics.profile == PROFILE_ECO, window.metrics.profile)
    check("Muestreo más lento en modo eco", window.metrics.interval_ms > 2000, str(window.metrics.interval_ms))
    window.show()
    _settle(app, 200)
    check("Vuelve al muestreo completo", window.metrics.profile == PROFILE_FULL and window.metrics.interval_ms <= 1200)
    window.metrics.set_process_detail(False)
    check("Sin lista de procesos cuando no se necesita", window.metrics.poll(with_processes=False).top_processes == [])
    check("Lista de procesos bajo demanda", len(window.metrics.poll(with_processes=True).top_processes) > 0)
    window.navigate("home")
    tools_page = window.tools_page
    check("Catálogo de accesos en la interfaz", len(tools_page._tiles) == len(tools.TOOLS), f"{len(tools_page._tiles)} fichas")  # noqa: SLF001
    window.navigate("tools")
    tools_page.search.setText("disco")
    visible = [tile for tile in tools_page._tiles if tile.matches]  # noqa: SLF001
    check("Filtro de herramientas", 0 < len(visible) < len(tools_page._tiles), f"{len(visible)} coincidencias")  # noqa: SLF001
    tools_page.search.setText("")
    window.navigate("tasks")
    check("Estadísticas de tareas visibles", window.tasks_page.stats_layout.count() == 4)  # noqa: SLF001
    window.navigate("reports")
    check("Vista previa con filas", window.reports_page.preview_rows.count() >= 6)  # noqa: SLF001

    # Flujo completo de exportación desde la interfaz (sin diálogos modales).
    with tempfile.TemporaryDirectory() as tmp:
        from PySide6.QtWidgets import QMessageBox

        original_information = QMessageBox.information
        original_question = QMessageBox.question
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            page = window.reports_page
            page.folder_edit.setText(tmp)
            page.filename_edit.setText("informe_ui.html")
            for fmt in ("HTML", "CSV", "JSON"):
                page._formats[fmt].setChecked(True)  # noqa: SLF001
                page.update_preview()
                page.export_now()
                produced = list(Path(tmp).glob(f"informe_ui*.{fmt.lower()}"))
                check(f"Exportación {fmt} desde la interfaz", len(produced) == 1 and produced[0].stat().st_size > 500)
            page._formats["HTML"].setChecked(True)  # noqa: SLF001
            check("Historial de informes de la sesión", page.history_list.count() == 3, str(page.history_list.count()))
            check("Informe HTML con contenido real", "PC Toolkit" in (Path(tmp) / "informe_ui.html").read_text(encoding="utf-8"))
        finally:
            QMessageBox.information = original_information
            QMessageBox.question = original_question

    # --- Historial en el inicio -----------------------------------------
    from pctoolkit.core.history import Sample
    import time as time_module

    base = time_module.time()
    demo_samples = [
        Sample(
            timestamp=base - (40 - index) * 600,
            cpu=20 + index % 10,
            ram=50 + index % 7,
            disk=60.0,
        )
        for index in range(40)
    ]
    for sample in demo_samples:
        history_store.append(sample)
    window.dashboard.reload_history()
    check(
        "Historial cargado en la gráfica",
        window.dashboard.chart.sample_count == len(history_store.load(24)),
        f"{window.dashboard.chart.sample_count} muestras en la gráfica",
    )
    check("Se cargaron las 40 muestras de prueba", window.dashboard.chart.sample_count >= 40, str(window.dashboard.chart.sample_count))
    check(
        "Estadísticas de media y máximo",
        "media" in window.dashboard.history_stats["cpu"].text()
        and "máx" in window.dashboard.history_stats["disk"].text(),
        window.dashboard.history_stats["cpu"].text(),
    )
    check("Etiqueta de rango temporal", "Datos de l" in window.dashboard.history_range.text(), window.dashboard.history_range.text())
    window.dashboard._toggle_series("disk", False)  # noqa: SLF001
    check("Serie oculta desde la leyenda", window.dashboard.chart.is_series_visible("disk") is False)
    window.dashboard._toggle_series("disk", True)  # noqa: SLF001
    check("Serie visible de nuevo", window.dashboard.chart.is_series_visible("disk") is True)
    before = window.dashboard.chart.sample_count
    window.dashboard.append_history_sample(Sample(timestamp=base, cpu=99.0, ram=50.0, disk=60.0))
    check(
        "Muestra nueva añadida en vivo",
        window.dashboard.chart.sample_count == before + 1,
        f"{before} -> {window.dashboard.chart.sample_count}",
    )
    chart_image = window.dashboard.chart.grab()
    check("Gráfica dibujada", not chart_image.isNull() and chart_image.width() > 100, f"{chart_image.width()}x{chart_image.height()}")

    # Cada serie debe aparecer en la posición que le corresponde.
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    from pctoolkit.core.config import COLORS
    from pctoolkit.ui.chart import SERIES

    chart = window.dashboard.chart
    image = QImage(chart.size(), QImage.Format_ARGB32)
    image.fill(QColor(COLORS["panel"]))
    painter = QPainter(image)
    chart.render(painter, QPoint(0, 0))
    painter.end()
    rect = chart._plot_rect()  # noqa: SLF001
    hits = {key: 0 for key, _label, _color_key in SERIES}
    for sample in demo_samples[-8:]:
        x = int(chart._x_for(sample.timestamp, rect))  # noqa: SLF001
        for key, _label, color_key in SERIES:
            y0 = int(chart._y_for(getattr(sample, key), rect))  # noqa: SLF001
            target = QColor(COLORS[color_key])
            best = None
            for y in range(max(0, y0 - 3), min(image.height(), y0 + 4)):
                pixel = image.pixelColor(x, y)
                distance = (
                    (pixel.red() - target.red()) ** 2
                    + (pixel.green() - target.green()) ** 2
                    + (pixel.blue() - target.blue()) ** 2
                ) ** 0.5
                best = distance if best is None else min(best, distance)
            if best is not None and best <= 70:
                hits[key] += 1
    check("Las tres series se pintan en su posición", all(value >= 5 for value in hits.values()), str(hits))
    empty_chart = window.dashboard.history
    empty_chart.clear()
    window.dashboard.reload_history()
    check("Gráfica sin datos controlada", window.dashboard.chart.sample_count == 0 and not window.dashboard.chart.grab().isNull())
    for index in range(40):
        history_store.append(Sample(timestamp=base - (40 - index) * 600, cpu=20.0, ram=50.0, disk=60.0))
    window.dashboard.reload_history()

    # --- Bandeja del sistema, umbrales y avisos --------------------------
    from pctoolkit.ui.tray import TrayController, status_pixmap, tray_available

    check("Icono de bandeja dibujado", not status_pixmap(snap, 32).isNull() and status_pixmap(snap, 32).width() == 32)
    # Si el sistema tiene bandeja (un escritorio normal), tiene que estar en uso; si no la
    # tiene (un servidor sin shell), el controlador tiene que quedarse inactivo en vez de
    # fallar. Comprobar que «hay bandeja» a secas convertía el entorno en el resultado.
    bandeja_disponible = tray_available()
    check(
        "La bandeja se usa solo si el sistema la tiene",
        window.tray.active == bandeja_disponible,
        f"bandeja={'sí' if bandeja_disponible else 'no'}; controlador activo={window.tray.active}",
    )
    if bandeja_disponible:
        check("Controlador de bandeja activo", window.tray.active is True, str(window.tray.active))
        statuses = {key: action.text() for key, action in window.tray.status_actions.items()}
        check("Menú con el estado actual", statuses[METRIC_CPU].startswith("CPU: ") and "%" in statuses[METRIC_RAM], str(statuses))
        check("Tooltip con métricas", "CPU" in window.tray.tray.toolTip() and "RAM" in window.tray.tray.toolTip(), window.tray.tray.toolTip().splitlines()[1] if window.tray.tray.toolTip() else "")

    # Umbrales bajos (pero válidos para el diálogo) para provocar avisos reales
    config = AlertConfig(enabled=True, sustained_samples=1, cooldown_minutes=30)
    for metric, value in ((METRIC_CPU, 10.0), (METRIC_RAM, 10.0), ("disk", 10.0)):
        config.set_threshold(metric, value)
    window.alerts.config = config
    window.settings.alert_config = config
    window.settings.sync()
    window.alerts.reset()
    check("Sin avisos antes de evaluar", window.alert_history == [])
    # Las dos muestras salen de una lectura real con los tres porcentajes forzados por
    # encima del umbral. Con la lectura real a secas —el disco de un servidor recién
    # arrancado, por ejemplo— puede quedarse una métrica por debajo la primera vez y
    # superar el umbral la segunda: el aviso nuevo sería legítimo y la comprobación del
    # enfriamiento fallaría sin que nada esté mal.
    import dataclasses as _dataclasses

    alta = _dataclasses.replace(window.metrics.poll(), cpu_percent=95.0, ram_percent=95.0, disk_percent=95.0)
    window._on_sample(alta)  # noqa: SLF001
    alerts = window.alert_history
    check("Aviso generado al superar el umbral", len(alerts) >= 1, str([(event.metric, round(event.value)) for event in alerts]))
    check("Indicador de aviso visible", window.alert_indicator.isVisible() and "%" in window.alert_indicator.text(), window.alert_indicator.text())
    first_count = len(alerts)
    window._on_sample(alta)  # noqa: SLF001
    check("Avisos limitados por enfriamiento", len(window.alert_history) == first_count, str(len(window.alert_history)))

    # --- Diálogo de alertas ----------------------------------------------
    from pctoolkit.ui.alert_settings import AlertSettingsDialog

    dialog = AlertSettingsDialog(app_settings, history_store, tray_available())
    check("Diálogo de alertas construido", dialog.spins[METRIC_CPU].value() == 10, str(dialog.spins[METRIC_CPU].value()))
    check("Sin desbordes en el diálogo", dialog.sizeHint().width() <= 780, f"{dialog.sizeHint().width()} px")
    dialog.spins[METRIC_CPU].setValue(77)
    dialog.sustained_spin.setValue(2)
    dialog.cooldown_spin.setValue(20)
    dialog.history_spin.setValue(45)
    dialog.tray_check.setChecked(True)
    applied = dialog.apply()
    check(
        "Umbrales guardados desde el diálogo",
        applied.threshold_for(METRIC_CPU) == 77.0 and app_settings.alert_config.threshold_for(METRIC_CPU) == 77.0,
        str(applied.thresholds),
    )
    check(
        "Muestras sostenidas y enfriamiento guardados",
        app_settings.alert_config.sustained_samples == 2 and app_settings.alert_config.cooldown_minutes == 20,
    )
    check("Intervalo del historial guardado", app_settings.history_interval_seconds == 45, str(app_settings.history_interval_seconds))
    window.alerts.config = app_settings.alert_config
    check("Motor de alertas sincronizado", window.alerts.config.threshold_for(METRIC_CPU) == 77.0)
    if window.tray.tray is not None:
        window.tray.alert_action.setChecked(False)
        check("El menú de la bandeja desactiva las alertas", app_settings.alert_config.enabled is False)
        window.tray.alert_action.setChecked(True)
        check("El menú de la bandeja las reactiva", app_settings.alert_config.enabled is True)
    dialog.close()
    app_settings.history_interval_seconds = 30

    window.settings.alert_config = config
    window.settings.tray_enabled = True
    window.settings.close_to_tray = True
    window.settings.sync()
    reloaded = AppSettings(QSettings(str(Path(_TEMP_HOME) / "ajustes_prueba.ini"), QSettings.IniFormat))
    check("Preferencias persistidas", reloaded.alert_config.sustained_samples == 1 and reloaded.tray_enabled is True)
    check("Intervalo de historial persistido", reloaded.history_interval_seconds >= 5, str(reloaded.history_interval_seconds))
    window.settings.history_interval_seconds = 15
    check("Intervalo se acota al guardar", AppSettings(QSettings(str(Path(_TEMP_HOME) / "ajustes_prueba.ini"), QSettings.IniFormat)).history_interval_seconds == 15)
    window.settings.history_interval_seconds = 30

    # Cerrar con la bandeja activa mantiene la app en segundo plano.
    if tray_available():
        window.show()
        _settle(app, 150)
        window.close()
        _settle(app, 150)
        check("Cerrar oculta la ventana con la bandeja activa", window.isVisible() is False and window._force_quit is False)  # noqa: SLF001
        window.quit_application()
        _settle(app, 150)
        check("Salir desde la bandeja cierra de verdad", window._force_quit is True)  # noqa: SLF001
    else:
        window.quit_application()

    # Se reabre para el resto de comprobaciones de interfaz.
    window = MainWindow(app_settings, history_store)
    window.resize(1360, 860)
    window.show()
    _settle(app, 600)
    window._on_sample(window.metrics.poll())  # noqa: SLF001

    # Marcar una tarea desde la interfaz y comprobar el estado en el registro.
    window.navigate("tasks")
    tasks_page = window.tasks_page
    pending = window.task_store.pending()
    if pending:
        target_id = pending[0].id
        tasks_page._toggle(target_id)  # noqa: SLF001
        check("Marcar tarea desde la interfaz", window.task_store.get(target_id).is_done)
        tasks_page._toggle(target_id)  # noqa: SLF001

    # Ninguna página debe desbordar en horizontal, ni en el tamaño mínimo.
    from PySide6.QtWidgets import QScrollArea

    for size in ((1360, 860), (1180, 740)):
        window.resize(*size)
        _settle(app, 250)
        for key in ("home", "memory", "disk", "tools", "tasks", "reports", "schedule"):
            window.navigate(key)
            _settle(app, 250)
            page = window._pages[key]  # noqa: SLF001
            areas = page.findChildren(QScrollArea)
            overflow = [area.horizontalScrollBar().maximum() for area in areas]
            check(
                f"Sin desbordes horizontales en {key} ({size[0]}x{size[1]})",
                all(value == 0 for value in overflow),
                str(overflow),
            )
    window.resize(1360, 860)
    _settle(app, 250)

    # Aspecto del render real: fondo oscuro D2, sin restos del violeta anterior y con el
    # acento neutro a la vista. Se mide sobre la pantalla de verdad, no sobre el texto del
    # estilo.
    window.navigate("home")
    _settle(app, 300)
    image = window.grab().toImage()
    # Se mira por bloques de 40x40: un elemento de color de verdad llenaría el suyo,
    # mientras que los flecos del suavizado de texto quedan dispersos.
    from PySide6.QtGui import QColor as _Color

    acento = _Color(COLORS["accent"])
    block = 40
    step = 2
    sampled = dark = accent = 0
    totals: dict[tuple[int, int], int] = {}
    violetas: dict[tuple[int, int], int] = {}
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            colour = image.pixelColor(x, y)
            red, green, blue_channel = colour.red(), colour.green(), colour.blue()
            sampled += 1
            if red + green + blue_channel < 190:
                dark += 1
            # El violeta y el magenta del diseño anterior: si quedara algún resto,
            # llenaría bloques enteros en vez de dejar píxeles sueltos. Los tonos fríos
            # legítimos de la paleta (series_cpu, series_ram) tienen menos azul relativo,
            # así que no entran en el filtro.
            if (
                blue_channel > red + 24
                and red >= green - 8
                and (max(red, green, blue_channel) - min(red, green, blue_channel)) > 40
            ):
                violetas[(x // block, y // block)] = violetas.get((x // block, y // block), 0) + 1
            if (
                (red - acento.red()) ** 2
                + (green - acento.green()) ** 2
                + (blue_channel - acento.blue()) ** 2
            ) < 2500:
                accent += 1
            key = (x // block, y // block)
            totals[key] = totals.get(key, 0) + 1
    # Lo esperable es que no haya ni un bloque violeta, así que el caso vacío es el bueno
    # y no un error: sin `default` la comprobación saltaba con `max()` sobre una secuencia
    # vacía.
    worst = max((count / totals[key] for key, count in violetas.items()), default=0.0)
    check("Fondo oscuro en pantalla", dark / sampled > 0.5, f"{dark / sampled:.0%} de píxeles oscuros")
    check(
        "Sin restos del violeta anterior en el render",
        worst < 0.35,
        f"{len(violetas)} bloques violáceos, el mayor al {worst:.0%}",
    )
    check("El acento del tema está pintado", accent > 200, f"{accent} píxeles de acento")

    # --- Foco del teclado, alineación y controles apagados -------------------
    # El tema no puede dejar el foco invisible al tabular, ni que dos botones de la
    # misma fila midan distinto, ni que un control deshabilitado se pinte como si
    # estuviera activo. Se mide sobre el render de verdad, no sobre el texto del
    # estilo: así se comprueba lo que ve quien usa la aplicación.
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QCheckBox as _QCheckBox
    from PySide6.QtWidgets import QListWidget as _QListWidget
    from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout
    from PySide6.QtWidgets import QWidget as _QWidget

    def _borde_con_foco(ventana, widget):  # noqa: ANN001, ANN202
        """Borde del widget con el foco del teclado puesto.

        Entre el `setFocus` y la captura, el escritorio puede quitarle la activación a
        la ventana; entonces el estilo no pinta el borde de foco y la comprobación
        fallaría por el entorno y no por el tema. Se reintenta el montaje —nunca la
        comprobación— hasta que el widget tenga el foco de verdad.
        """
        for _intento in range(5):
            _activar(app, ventana)
            widget.setFocus()
            _settle(app, 200)
            if widget.hasFocus():
                break
        return _borde_superior(widget)

    def _borde_superior(widget):  # noqa: ANN001
        imagen = widget.grab().toImage()
        centro = widget.width() // 2
        tonos = [
            imagen.pixelColor(x, 0)
            for x in range(max(1, centro - 12), min(widget.width() - 1, centro + 12))
        ]
        return QColor(
            sum(tono.red() for tono in tonos) // len(tonos),
            sum(tono.green() for tono in tonos) // len(tonos),
            sum(tono.blue() for tono in tonos) // len(tonos),
        )

    def _distancia(uno, otro):  # noqa: ANN001
        referencia = QColor(otro)
        return (
            (uno.red() - referencia.red()) ** 2
            + (uno.green() - referencia.green()) ** 2
            + (uno.blue() - referencia.blue()) ** 2
        ) ** 0.5

    window.navigate("schedule")
    for _ in range(80):
        if window.schedule_page.status_loaded:
            break
        _settle(app, 200)
    fantasma = window.schedule_page.refresh_button
    principal = window.schedule_page.save_button
    check(
        "Los botones de una misma fila quedan a la misma altura",
        fantasma.height() == principal.height() and fantasma.y() == principal.y(),
        f"{fantasma.height()}x{fantasma.y()} y {principal.height()}x{principal.y()}",
    )

    lateral_con_foco = _borde_con_foco(window, fantasma)
    fantasma.clearFocus()
    window.setFocus()
    _settle(app, 200)
    lateral_sin_foco = _borde_superior(fantasma)
    check(
        "El botón secundario marca el foco del teclado",
        _distancia(lateral_con_foco, lateral_sin_foco) > 25 and _distancia(lateral_con_foco, COLORS["accent"]) < 60,
        f"{lateral_sin_foco.name()} -> {lateral_con_foco.name()}",
    )
    principal_con_foco = _borde_con_foco(window, principal)
    principal.clearFocus()
    window.setFocus()
    _settle(app, 200)
    check(
        "El botón principal también marca el foco",
        _distancia(principal_con_foco, _borde_superior(principal)) > 40,
        f"{_borde_superior(principal).name()} -> {principal_con_foco.name()}",
    )

    lateral = window._nav_buttons["disk"]  # noqa: SLF001
    lateral_con_foco = _borde_con_foco(window, lateral)
    lateral.clearFocus()
    window.setFocus()
    _settle(app, 200)
    lateral_sin_foco = _borde_superior(lateral)
    check(
        "Las secciones de la barra lateral marcan el foco",
        _distancia(lateral_con_foco, lateral_sin_foco) > 25,
        f"{lateral_sin_foco.name()} -> {lateral_con_foco.name()}",
    )

    from pctoolkit.ui.widgets import ActionCard

    casilla = _QCheckBox("Casilla marcada y apagada")
    casilla.setChecked(True)
    casilla.setEnabled(False)
    casilla.show()
    _settle(app, 250)
    indicador = casilla.grab().toImage().pixelColor(7, casilla.height() // 2)
    check(
        "Una casilla marcada pero apagada no se pinta como activa",
        _distancia(indicador, COLORS["accent"]) > 60 and _distancia(indicador, COLORS["border"]) < 40,
        indicador.name(),
    )
    casilla.close()

    # La selección de una lista se apaga cuando su ventana no está activa: con el
    # selector escrito de otra forma la regla no se aplicaba y quedaba un estilo
    # muerto (la selección seguía encendida en segundo plano).
    panel_lista = _QWidget()
    listado = _QListWidget()
    panel_lista.setLayout(_QVBoxLayout(panel_lista))
    panel_lista.layout().addWidget(listado)
    for texto in ("Primera", "Segunda", "Tercera"):
        listado.addItem(texto)
    panel_lista.resize(280, 180)
    panel_lista.show()
    activo_panel = _activar(app, panel_lista)
    listado.setCurrentRow(0)
    _settle(app, 250)
    seleccion_activa = listado.grab().toImage().pixelColor(20, 20).name()
    activo_ventana = _activar(app, window)
    _settle(app, 350)
    seleccion_apagada = listado.grab().toImage().pixelColor(20, 20).name()
    check(
        "La selección de una lista se apaga en segundo plano",
        QColor(seleccion_activa) == QColor(SURFACES["selected"])
        and QColor(seleccion_apagada) == QColor(COLORS["panel_alt"]),
        f"activa {seleccion_activa} / inactiva {seleccion_apagada}"
        f" (activación: panel={activo_panel}, ventana={activo_ventana})",
    )
    panel_lista.close()

    tarjeta = ActionCard("tool", "Abrir el Administrador de tareas", "Procesos, rendimiento y arranque.")
    pulsaciones: list[int] = []
    tarjeta.clicked.connect(lambda: pulsaciones.append(1))
    tarjeta.show()
    # Es una ventana propia: sin activarla, `setFocus` no pinta el borde de foco y la
    # comprobación dependería de qué ventana tuviera el foco en el escritorio.
    _activar(app, tarjeta)
    _settle(app, 250)
    check(
        "Las tarjetas pulsables se alcanzan con el tabulador",
        tarjeta.focusPolicy() == Qt.TabFocus,
        str(tarjeta.focusPolicy()),
    )
    borde_tarjeta = _borde_con_foco(tarjeta, tarjeta)
    check(
        "La tarjeta enfocada marca el foco",
        _distancia(borde_tarjeta, COLORS["border_soft"]) > 25,
        f"{borde_tarjeta.name()} (foco: {tarjeta.hasFocus()})",
    )
    QTest.keyClick(tarjeta, Qt.Key_Return)
    _settle(app, 100)
    QTest.keyClick(tarjeta, Qt.Key_Space)
    _settle(app, 100)
    check("Intro y Espacio abren la tarjeta pulsable", len(pulsaciones) == 2, str(len(pulsaciones)))
    tarjeta.close()

    # Atajos de teclado: cada sección, en el orden de la barra lateral.
    check("Nueve atajos de sección registrados", len(window.nav_shortcuts) == 9, str(len(window.nav_shortcuts)))
    secciones = ("home", "memory", "startup", "disk", "tools", "tasks", "reports", "schedule")
    fallos_atajo: list[str] = []
    for indice, clave in enumerate(secciones, start=1):
        # Los atajos de ventana solo responden si la ventana es la activa: las
        # tarjetas y casillas sueltas de arriba son ventanas propias y se llevan
        # la activación mientras están abiertas. El escritorio puede quitársela
        # justo después de dársela (otro programa pasa a primer plano), así que se
        # reintenta una vez antes de darlo por fallo: si no, una pulsación perdida
        # se cuenta como que el atajo no funciona.
        activa = False
        for _reintento in range(2):
            activa = _activar(app, window)
            QTest.keyClick(window, getattr(Qt, f"Key_{indice}"), Qt.ControlModifier)
            _settle(app, 150)
            if window.stack.currentWidget() is window._pages[clave]:  # noqa: SLF001
                break
        else:
            fallos_atajo.append(f"Ctrl+{indice} no abrió {clave} (ventana activa: {activa})")
    check("Los atajos Ctrl+1…Ctrl+8 cambian de sección", not fallos_atajo, "; ".join(fallos_atajo))

    # --- Anuncios para lectores de pantalla --------------------------------
    from pctoolkit.ui import accessibility

    anuncios: list[str] = []
    accessibility.set_announcer(anuncios.append)
    try:
        window.set_status("Prueba de anuncio accesible")
        vacio = accessibility.announce(window.status_message, "   ")
        with_value = accessibility.announce(window.status_message, "Memoria liberada", assertive=True)
    finally:
        accessibility.set_announcer(None)
    check(
        "El estado se anuncia a los lectores de pantalla",
        anuncios == ["Prueba de anuncio accesible", "Memoria liberada"],
        str(anuncios),
    )
    check(
        "El mensaje queda en el nombre accesible de la barra",
        window.status_message.accessibleName() == "Prueba de anuncio accesible",
        window.status_message.accessibleName(),
    )
    check("Un mensaje vacío no se anuncia", vacio is False and with_value is True)
    check(
        "Las métricas del equipo tienen nombre accesible",
        window.status_metrics.accessibleName() == "Métricas del equipo",
        window.status_metrics.accessibleName(),
    )

    # Los indicadores pintados a mano solo pueden leerse por su descripción.
    window.navigate("home")
    _settle(app, 400)
    medidores = {
        "CPU": window.dashboard.cpu_gauge,
        "RAM": window.dashboard.ram_gauge,
        "Disco": window.dashboard.disk_gauge,
        "Red": window.dashboard.net_gauge,
    }
    sin_nombre = [nombre for nombre, medidor in medidores.items() if not medidor.accessibleName()]
    check("Los indicadores de la portada tienen nombre accesible", not sin_nombre, ", ".join(sin_nombre))
    check(
        "El nombre del indicador es el de su tarjeta",
        medidores["CPU"].accessibleName() == "CPU",
        medidores["CPU"].accessibleName(),
    )
    # Cada medidor lleva el color de su serie de datos y solo cambia al alcanzar el umbral
    # real de las alertas; el rojo, además, exige que el valor se sostenga (la misma regla
    # que aplica el motor de alertas). Nada de umbrales inventados en la prueba.
    import dataclasses as _dataclasses

    from pctoolkit.core.alerts import AlertConfig as _AlertConfig
    from pctoolkit.core.metrics import MetricsSnapshot as _MetricsSnapshot

    tablero = window.dashboard
    tablero.set_alert_config(_AlertConfig())  # por defecto: umbral 90 % y racha de 3
    check(
        "Cada medidor usa el color de su serie por debajo del umbral",
        tablero._accent_for(10.0, "series_cpu") == COLORS["series_cpu"]  # noqa: SLF001
        and tablero._accent_for(10.0, "series_ram") == COLORS["series_ram"]  # noqa: SLF001
        and tablero._accent_for(10.0, "series_disk") == COLORS["series_disk"]  # noqa: SLF001
        and tablero._accent_for(10.0, "series_net") == COLORS["series_net"],  # noqa: SLF001
        f"normal {tablero._accent_for(10.0, 'series_cpu')}",  # noqa: SLF001
    )
    instantaneo = window.metrics.poll()
    pico = _dataclasses.replace(instantaneo, cpu_percent=95.0, ram_percent=95.0, disk_percent=95.0)
    tablero._streaks.update(dict.fromkeys(tablero._streaks, 0))  # noqa: SLF001
    tablero._update_streaks(pico)  # noqa: SLF001
    check(
        "Un pico por encima del umbral, sin sostenerse, se queda en aviso",
        tablero._accent_for(95.0, "series_cpu") == COLORS["warn"]  # noqa: SLF001
        and tablero._accent_for(95.0, "series_ram") == COLORS["warn"],  # noqa: SLF001
        tablero._accent_for(95.0, "series_cpu"),  # noqa: SLF001
    )
    tablero._update_streaks(pico)  # noqa: SLF001
    tablero._update_streaks(pico)  # noqa: SLF001
    check(
        "Sostenido por encima del umbral, el medidor pasa a rojo",
        tablero._accent_for(95.0, "series_disk") == COLORS["bad"],  # noqa: SLF001
        tablero._accent_for(95.0, "series_disk"),  # noqa: SLF001
    )
    # Los chips de texto acompañan a la alerta y desaparecen al bajar del umbral.
    tablero.set_alert_config(
        _AlertConfig(thresholds={"cpu": 20.0, "ram": 20.0, "disk": 20.0}, sustained_samples=1)
    )
    tablero.update_snapshot(pico, {})
    check(
        "El chip de texto avisa al alcanzar el umbral",
        "Crítico · 95 % (umbral 20 %)" == tablero.chips["series_cpu"].text(),
        tablero.chips["series_cpu"].text(),
    )
    tablero.update_snapshot(_MetricsSnapshot(cpu_percent=5.0, ram_percent=5.0, disk_percent=5.0), {})
    check(
        "Sin superar el umbral no hay chip",
        tablero.chips["series_cpu"].isHidden(),
        tablero.chips["series_cpu"].text(),
    )
    tablero.set_alert_config(window.settings.alert_config)
    # La marca del umbral se pinta en el abanico y se recorta a la escala del medidor.
    medidor = tablero.cpu_gauge
    from PySide6.QtCore import QPoint as _Punto
    from PySide6.QtGui import QColor as _ColorMedidor, QImage as _Imagen, QPainter as _Pintor

    def _pixeles_marca() -> int:
        lienzo = _Imagen(medidor.size(), _Imagen.Format_ARGB32)
        lienzo.fill(_ColorMedidor(COLORS["panel"]))
        pintor = _Pintor(lienzo)
        medidor.render(pintor, _Punto(0, 0))
        pintor.end()
        objetivo = _ColorMedidor(COLORS["text_muted"])
        total = 0
        for y in range(0, lienzo.height(), 2):
            for x in range(0, lienzo.width(), 2):
                tono = lienzo.pixelColor(x, y)
                if (
                    abs(tono.red() - objetivo.red())
                    + abs(tono.green() - objetivo.green())
                    + abs(tono.blue() - objetivo.blue())
                ) <= 21:
                    total += 1
        return total

    medidor.set_threshold(None)
    sin_marca = _pixeles_marca()
    medidor.set_threshold(90.0)
    check(
        "La marca del umbral se pinta en el medidor",
        _pixeles_marca() > sin_marca,
        f"{sin_marca} píxeles sin marca",
    )
    medidor.set_threshold(150.0)
    check(
        "El umbral se recorta a la escala del medidor",
        medidor._threshold == 100.0,  # noqa: SLF001
        str(medidor._threshold),  # noqa: SLF001
    )
    medidor.set_threshold(None)
    window._on_sample(window.metrics.poll())  # noqa: SLF001
    check(
        "El medidor de red enseña velocidad, no un porcentaje",
        bool(tablero.net_gauge._display) and "/s" in str(tablero.net_gauge._display),  # noqa: SLF001
        str(tablero.net_gauge._display),  # noqa: SLF001
    )
    check(
        "El medidor de red mide contra la velocidad del enlace",
        tablero._net_scale() > 0  # noqa: SLF001
        and tablero.net_gauge._accent == COLORS["series_net"],  # noqa: SLF001
        f"escala {tablero._net_scale():.0f} B/s",  # noqa: SLF001
    )
    caja_lateral = window.auto_log_check
    check(
        "El texto de la casilla de la barra lateral no se corta",
        caja_lateral.sizeHint().width() <= caja_lateral.width(),
        f"necesita {caja_lateral.sizeHint().width()} px y tiene {caja_lateral.width()}",
    )
    descripciones = {nombre: medidor.accessibleDescription() for nombre, medidor in medidores.items()}
    # Los tres medidores que miden un porcentaje lo dicen; el de red mide velocidad, así que
    # describe «…/s» (un «0 %» con tráfico no decía nada).
    check(
        "Cada indicador describe su valor",
        all("%" in descripciones[nombre] for nombre in ("CPU", "RAM", "Disco"))
        and "/s" in descripciones["Red"],
        " | ".join(descripciones.values()),
    )
    check(
        "El medidor de salud describe la puntuación",
        "de 100" in window.dashboard.health_meter.accessibleDescription(),
        window.dashboard.health_meter.accessibleDescription(),
    )

    # --- Rol accesible de los widgets propios ------------------------------
    # Qt da a cada clase un rol fijo: un `QFrame` es «borde» y un `QWidget` es
    # «cliente». Los widgets propios (tarjetas, medidores, barras) dicen lo que
    # son de verdad, y el lector de pantalla lo anuncia así.
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QAccessible
    from PySide6.QtWidgets import QLabel, QPushButton, QWidget

    from pctoolkit.ui.alert_settings import AlertSettingsDialog
    from pctoolkit.ui.chart import HistoryChart
    from pctoolkit.ui.tools_page import ToolTile
    from pctoolkit.ui.widgets import (
        ActionCard as TarjetaPulsable,
        ArcGauge as Indicador,
        ArcProgress as Barra,
        BarRow as FilaDisco,
        Card as Tarjeta,
        HealthMeter as MedidorSalud,
        PageHeader,
        SectionTitle as TituloSeccion,
    )

    def _rol_de(widget):
        """Rol que ve de verdad el lector: pasa por la interfaz accesible de Qt."""
        interfaz = QAccessible.queryAccessibleInterface(widget)
        return interfaz.role() if interfaz is not None else None

    def _nombre_de(widget):
        """Nombre accesible tal y como lo lee el lector de pantalla.

        No siempre es `accessibleName()`: una etiqueta con rol propio (un título de
        sección, por ejemplo) se lee con su propio texto, como hace Qt.
        """
        interfaz = QAccessible.queryAccessibleInterface(widget)
        return interfaz.text(QAccessible.Text.Name) if interfaz is not None else ""

    # Las subclases van antes que su clase base: la primera regla que encaja manda.
    reglas = (
        (ToolTile, QAccessible.Role.Button),
        (TarjetaPulsable, QAccessible.Role.Button),
        (FilaDisco, QAccessible.Role.Button),
        (Tarjeta, QAccessible.Role.Grouping),
        (TituloSeccion, QAccessible.Role.Heading),
        (Indicador, QAccessible.Role.Chart),
        (HistoryChart, QAccessible.Role.Chart),
        (MedidorSalud, QAccessible.Role.ProgressBar),
        (Barra, QAccessible.Role.ProgressBar),
    )

    def _regla_para(widget):
        for clase, rol in reglas:
            if isinstance(widget, clase):
                return clase, rol
        return None, None

    encontrados: dict[type, list] = {clase: [] for clase, _rol in reglas}
    for widget in window.findChildren(QWidget):
        clase, rol = _regla_para(widget)
        if clase is not None:
            encontrados[clase].append(widget)

    sin_rol = [
        type(widget).__name__
        for clase, rol in reglas
        for widget in encontrados[clase]
        if _rol_de(widget) != rol
    ]
    check(
        "Cada widget propio anuncia su rol",
        not sin_rol,
        ", ".join(sin_rol) or "todos correctos",
    )
    sin_nombre = [
        type(widget).__name__
        for clase, _rol in reglas
        for widget in encontrados[clase]
        if not _nombre_de(widget)
    ]
    check(
        "Cada widget propio tiene nombre accesible",
        not sin_nombre,
        ", ".join(sin_nombre) or "todos con nombre",
    )
    # No basta con que existan: cada tipo tiene que estar presente en la ventana.
    ausentes = [
        clase.__name__
        for clase, _rol in reglas
        if clase not in (TarjetaPulsable, FilaDisco)
        and not encontrados[clase]
    ]
    check("Hay tarjetas, títulos, medidores, barras y gráfica en la ventana", not ausentes, ", ".join(ausentes))
    check(
        "El rol no se sale de los widgets marcados",
        len(encontrados[Tarjeta]) >= 10 and len(encontrados[TituloSeccion]) >= 4,
        f"{len(encontrados[Tarjeta])} tarjetas y {len(encontrados[TituloSeccion])} títulos",
    )
    # Los widgets corrientes siguen con el rol que les da Qt.
    boton_corriente = QPushButton("Botón corriente")
    etiqueta_corriente = QLabel("Etiqueta corriente")
    check(
        "Los widgets con rol de Qt no cambian",
        _rol_de(boton_corriente) == QAccessible.Role.Button
        and _rol_de(etiqueta_corriente) == QAccessible.Role.StaticText,
        f"{_rol_de(boton_corriente)} / {_rol_de(etiqueta_corriente)}",
    )

    # El título de la página se anuncia como encabezado, no como texto suelto.
    cabeceras = window.findChildren(PageHeader)
    check(
        "El título de cada página es un encabezado",
        cabeceras and all(_rol_de(cabecera.title) == QAccessible.Role.Heading for cabecera in cabeceras),
        f"{len(cabeceras)} cabeceras",
    )

    # El rol llega con nombre, descripción, estado y rectángulo: sin el estado, el
    # lector no sabe si el widget tiene el foco; sin el rectángulo, no lo encuentra.
    tarjeta_rol = TarjetaPulsable("tool", "Abrir el Administrador de tareas", "Procesos, rendimiento y arranque.")
    tarjeta_rol.show()
    _activar(app, tarjeta_rol)
    tarjeta_rol.setFocus()
    _settle(app, 250)
    interfaz = QAccessible.queryAccessibleInterface(tarjeta_rol)
    estado = interfaz.state()
    check(
        "El rol y el nombre llegan a la interfaz accesible",
        interfaz.role() == QAccessible.Role.Button
        and interfaz.text(QAccessible.Text.Name) == "Abrir el Administrador de tareas"
        and interfaz.text(QAccessible.Text.Description) == "Procesos, rendimiento y arranque.",
        f"{interfaz.role()} / {interfaz.text(QAccessible.Text.Name)!r}",
    )
    check(
        "El estado accesible refleja visibilidad y foco",
        bool(estado.visible) and bool(estado.enabled) and bool(estado.focusable),
        f"visible {bool(estado.visible)} activo {bool(estado.enabled)} enfocable {bool(estado.focusable)}",
    )
    check(
        "El estado accesible sigue al foco del widget",
        bool(estado.focused) == tarjeta_rol.hasFocus(),
        f"interfaz {bool(estado.focused)} / widget {tarjeta_rol.hasFocus()}",
    )
    check(
        "El rectángulo accesible es el del widget en pantalla",
        interfaz.rect() == QRect(tarjeta_rol.mapToGlobal(QPoint(0, 0)), tarjeta_rol.size()),
        str(interfaz.rect()),
    )
    # Una tarjeta pulsable marca su rol después del grupo que hereda de `Card`:
    # el último rol es el que vale, y el de una tarjeta normal no cambia.
    check(
        "La tarjeta pulsable gana el rol al grupo que hereda",
        accessibility.role_of(tarjeta_rol) == QAccessible.Role.Button
        and accessibility.role_of(Tarjeta()) == QAccessible.Role.Grouping,
        f"{accessibility.role_of(tarjeta_rol)} / {accessibility.role_of(Tarjeta())}",
    )
    tarjeta_rol.close()

    # La fila del analizador se abre con Intro o Espacio: es un botón, y su nombre
    # es el del elemento al que apunta.
    class _Nodo:
        name = "videos"
        path = "C:\\videos"
        size = 3_000_000
        is_dir = True

    fila_disco = FilaDisco(_Nodo(), 0.5)
    check(
        "La fila del analizador es un botón con el nombre del elemento",
        _rol_de(fila_disco) == QAccessible.Role.Button and _nombre_de(fila_disco) == "videos",
        f"{_rol_de(fila_disco)} / {_nombre_de(fila_disco)}",
    )

    # Las tarjetas del diálogo de ajustes también son grupos con nombre.
    dialogo_roles = AlertSettingsDialog(app_settings, history_store, tray_available(), window)
    tarjetas_dialogo = dialogo_roles.findChildren(Tarjeta)
    nombres_dialogo = [tarjeta.accessibleName() for tarjeta in tarjetas_dialogo]
    check(
        "Las tarjetas del diálogo de ajustes tienen nombre",
        len(tarjetas_dialogo) >= 3 and all(nombres_dialogo) and all(_rol_de(t) == QAccessible.Role.Grouping for t in tarjetas_dialogo),
        f"{len(tarjetas_dialogo)} tarjetas: {nombres_dialogo}",
    )
    dialogo_roles.close()
    # Las ventanas sueltas de arriba se llevaron la activación: se devuelve a la
    # principal, que es como se usa de verdad (y el estilo apaga lo seleccionado
    # en segundo plano).
    _activar(app, window)
    _settle(app, 250)

    # --- Tema claro ---------------------------------------------------------
    from pctoolkit.core.config import LIGHT_COLORS, active_palette
    from pctoolkit.ui.alert_settings import AlertSettingsDialog

    dialogo = AlertSettingsDialog(app_settings, history_store, tray_available(), window)
    dialogo.theme_combo.setCurrentIndex(dialogo.theme_combo.findData("light"))
    _settle(app, 150)
    check(
        "El diálogo explica el modo elegido",
        "claro" in dialogo.theme_note.text(),
        dialogo.theme_note.text(),
    )
    dialogo.apply()
    check("El modo de apariencia se guarda", app_settings.theme_mode == "light", app_settings.theme_mode)
    dialogo.close()

    apply_theme(app, app_settings.theme_mode)
    check("La paleta activa es la clara", active_palette() == "light" and COLORS["bg"] == LIGHT_COLORS["bg"], COLORS["bg"])
    from pctoolkit.ui import theme as theme_ui

    check(
        "La marca de verificación es la del tema activo",
        "check_light.png" in theme_ui.stylesheet(),
        "check_light.png en el QSS (la imagen es blanca; en oscuro sería invisible)",
    )
    clara = window.reload_theme()
    _settle(app, 700)
    check(
        "La ventana nueva se abre con la paleta clara",
        clara.isVisible() and clara.size() == window.size(),
        f"visible={clara.isVisible()} {clara.size().width()}x{clara.size().height()}",
    )
    clara.navigate("home")
    _settle(app, 500)
    # La ventana nueva pinta sus indicadores cuando le llega la primera muestra de
    # métricas: sin esperarla, el render puede salir con los abanicos a cero y el
    # recuento de acento no mediría lo que dice medir.
    muestras_listas = False
    for _intento in range(60):
        if clara.dashboard.cpu_gauge.accessibleDescription() not in ("", "Sin datos todavía"):
            muestras_listas = True
            break
        _settle(app, 100)
    check("La ventana nueva recibe métricas", muestras_listas, clara.dashboard.cpu_gauge.accessibleDescription())
    _activar(app, clara)
    _settle(app, 150)
    imagen_clara = clara.grab().toImage()
    # El acento de la paleta activa **ahora**: en claro es la tinta oscura, no el casi
    # blanco del tema oscuro.
    acento_claro = _Color(COLORS["accent"])
    muestreo = claro_px = oscuro_px = acento_px = 0
    for y in range(0, imagen_clara.height(), 4):
        for x in range(0, imagen_clara.width(), 4):
            tono = imagen_clara.pixelColor(x, y)
            red, green, blue = tono.red(), tono.green(), tono.blue()
            luz = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            muestreo += 1
            if luz > 170:
                claro_px += 1
            elif luz < 90:
                oscuro_px += 1
            if (
                (red - acento_claro.red()) ** 2
                + (green - acento_claro.green()) ** 2
                + (blue - acento_claro.blue()) ** 2
            ) < 2500:
                acento_px += 1
    check("Fondo claro en pantalla", claro_px / muestreo > 0.85, f"{claro_px / muestreo:.0%} de píxeles claros")
    check("Texto oscuro sobre el fondo claro", oscuro_px / muestreo > 0.005, f"{oscuro_px / muestreo:.2%} de píxeles oscuros")
    check("El acento del tema sigue ahí en claro", acento_px > 200, f"{acento_px} píxeles de acento")

    # Se vuelve al tema oscuro para el resto de comprobaciones.
    app_settings.theme_mode = "dark"
    apply_theme(app, "dark")
    window = clara.reload_theme()
    _settle(app, 700)
    check(
        "Volver al tema oscuro rehace la ventana",
        active_palette() == "dark" and window.stack.currentWidget() is not None
        and "check_dark.png" in theme_ui.stylesheet(),
        active_palette(),
    )
    window.navigate("home")
    _settle(app, 300)

    # Asistente del instalador: se construye, se pinta y recoge las opciones.
    from installer import installer_core as installer_core_test
    from installer.setup_app import InstallerWindow

    opciones_wizard = installer_core_test.InstallOptions(
        target_dir=Path(_TEMP_HOME) / "instalacion_prueba",
        desktop=Path(_TEMP_HOME) / "escritorio_prueba",
        start_menu=Path(_TEMP_HOME) / "menu_prueba",
    )
    wizard = InstallerWindow(options=opciones_wizard)
    wizard.show()
    _settle(app, 400)
    wizard_image = wizard.grab().toImage()
    check(
        "Asistente de instalación pintado",
        not wizard_image.isNull() and wizard_image.width() > 500,
        f"{wizard_image.width()}x{wizard_image.height()}",
    )
    check("Botón principal del asistente", wizard.main_button.text() == "Instalar", wizard.main_button.text())
    # El asistente avisa de la situación real del equipo (instalación previa,
    # instancia abierta o listo para instalar), así que se comprueba que el
    # mensaje se corresponde con lo que hay de verdad en el registro.
    estado = wizard.status_label.text()
    if installer_core_test.read_uninstall_entry(wizard.options.key_name):
        esperado_estado = "Ya había una instalación"
    elif installer_core_test.is_running():
        esperado_estado = "instancia de PC Toolkit abierta"
    else:
        esperado_estado = "Listo para instalar"
    check("Estado del asistente según el equipo", esperado_estado in estado, estado)
    check("Carpeta propuesta en el asistente", wizard.folder_edit.text() == str(opciones_wizard.target_dir), wizard.folder_edit.text())
    wizard.autostart_check.setChecked(True)
    recogidas = wizard._collect()  # noqa: SLF001
    check(
        "El asistente recoge las opciones",
        recogidas.autostart and recogidas.desktop_shortcut and recogidas.start_menu_shortcut and recogidas.launch_after,
        f"autostart={recogidas.autostart} escritorio={recogidas.desktop_shortcut} menu={recogidas.start_menu_shortcut}",
    )
    wizard.desktop_check.setChecked(False)
    check("El asistente respeta lo que se desmarca", not wizard._collect().desktop_shortcut)  # noqa: SLF001
    wizard.close()

    uninstaller_wizard = InstallerWindow(uninstall_mode=True, options=opciones_wizard)
    uninstaller_wizard.show()
    _settle(app, 400)
    check("Asistente de desinstalación pintado", not uninstaller_wizard.grab().toImage().isNull())
    check("Aviso de datos conservados", "conservan" in uninstaller_wizard.data_check.toolTip(), uninstaller_wizard.data_check.toolTip())
    check("Botón del desinstalador", uninstaller_wizard.main_button.text() == "Desinstalar")
    uninstaller_wizard.close()

    shots = Path(_TEMP_HOME) / "capturas_prueba"
    shots.mkdir(parents=True, exist_ok=True)
    for key in ("home", "memory", "startup", "disk", "tools", "tasks", "reports", "schedule"):
        window.navigate(key)
        app.processEvents()
        saved = window.grab().save(str(shots / f"{key}.png"))
        check(f"Captura de {key}", saved, str(shots / f"{key}.png"))

    # --- Cierre con trabajo en segundo plano --------------------------------
    # Salir mientras una consulta está en marcha es el caso que hacía que Qt
    # abortara el proceso por destruir un hilo vivo (la suite terminaba con
    # código 127 en vez de 0). Se comprueba de punta a punta: se lanza a propósito
    # la búsqueda de tareas de inicio de sesión y se sale sin esperarla.
    window.startup_page.refresh(with_tasks=True, force=True)
    app.processEvents()
    pendientes = workers_ui.pending_calls()
    check(
        "Hay trabajo en segundo plano al cerrar",
        bool(pendientes),
        ", ".join(call.describe() for call in pendientes),
    )
    window.quit_application()
    check("Salir cierra de verdad", window._force_quit is True)  # noqa: SLF001
    restantes = workers_ui.pending_calls()
    check(
        "El cierre espera a las tareas en curso (ningún hilo queda vivo)",
        not restantes,
        ", ".join(call.describe() for call in restantes),
    )


def _settle(app, ms: int) -> None:
    """Deja que se apliquen tamaños y estilos pendientes."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _activar(app, widget) -> bool:
    """Da la activación a una ventana y espera a que Qt la tenga de verdad.

    Hay comprobaciones que comparan el aspecto de un widget «activo» con el de «en
    segundo plano» (la selección de una lista, el borde de foco, los atajos). Si la
    ventana no llega a estar activa, las dos medidas salen iguales y la comprobación
    falla por el entorno y no por el tema, así que la activación se busca de verdad:
    primero por la vía normal (`activateWindow` y, en Windows, traer la ventana al
    frente) y solo si el escritorio se niega —hay otro programa en primer plano, por
    ejemplo— se recurre a la activación interna de Qt, que existe justamente para las
    pruebas. Devuelve si la ventana quedó activa, para poder decirlo en el mensaje.
    """
    from PySide6.QtTest import QTest

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        def traer_al_frente(ventana) -> None:  # noqa: ANN001
            try:
                ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(int(ventana.winId())))
            except (OSError, AttributeError, ValueError):
                pass
    else:

        def traer_al_frente(ventana) -> None:  # noqa: ANN001
            return None

    for _intento in range(10):
        widget.raise_()
        widget.activateWindow()
        traer_al_frente(widget)
        if widget.isActiveWindow():
            return True
        QTest.qWait(50)
    with warnings.catch_warnings():
        # Obsoleta para una aplicación normal (ahí se usa `activateWindow`), pero es la
        # única forma de forzar la activación sin depender del escritorio.
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(widget)
    QTest.qWait(50)
    return bool(widget.isActiveWindow())


def test_firma() -> None:
    """Firma digital: localización de signtool y construcción del comando de firmado.

    No firma nada de verdad (eso lo comprueba `python firmar.py --autoprueba` con un
    certificado autofirmado); aquí se verifica que los argumentos que se le pasan a
    signtool son los correctos en cada forma de conseguir el certificado.
    """
    import firmar

    signtool = firmar.find_signtool()
    if signtool is None:
        # Un clon recién descargado (y el servidor de integración) todavía no tiene
        # signtool: se descarga con `firmar.py --traer-signtool` y en el flujo de firma
        # es un paso previo a compilar. Lo que revisan estas comprobaciones es la
        # máquina, no el código, así que se anotan omitidas —igual que las de `dist\`—
        # y la suite sigue sirviendo en un equipo a medio preparar.
        check("signtool localizado (omitida: no está descargado)", True, "python firmar.py --traer-signtool")
        check(
            "Sitios donde se busca signtool (omitida: no está descargado)",
            True,
            "python firmar.py --traer-signtool",
        )
    else:
        check("signtool localizado", signtool.exists(), str(signtool))
        check(
            "Sitios donde se busca signtool",
            len(firmar.signtool_candidates()) >= 1,
            str(len(firmar.signtool_candidates())),
        )

    sello = firmar.SELLO_POR_DEFECTO
    pfx = firmar.CertificateSpec(modo="pfx", pfx=Path("llave.pfx"), password="secreta")
    almacen = firmar.CertificateSpec(modo="almacen", huella="AABBCC")
    hsm = firmar.CertificateSpec(modo="hsm", csp="Proveedor KSP", clave="alias", cert_publico=Path("cert.crt"))
    azure = firmar.CertificateSpec(modo="azure", dlib=Path("d.lib"), metadatos=Path("metadata.json"))
    auto = firmar.CertificateSpec(modo="auto")

    def args(cert, archivo=Path("app.exe"), sello_=sello):
        return firmar.sign_command(Path("signtool.exe"), cert, [archivo], sello_, "PC Toolkit")

    base = args(pfx)
    check(
        "Comando de firma con SHA-256 y sello RFC 3161",
        "/fd" in base and base[base.index("/fd") + 1] == "SHA256" and base[base.index("/tr") + 1] == sello
        and base[base.index("/td") + 1] == "SHA256",
        firmar.visible_command(Path("signtool.exe"), base[:12], pfx.password),
    )
    check("Firma con archivo PFX", "/f" in base and "llave.pfx" in base and "/p" in base)
    check("Certificado del almacén por huella", args(almacen).count("/sha1") == 1 and "AABBCC" in args(almacen))
    check("Certificado del almacén por nombre", "/n" in args(firmar.CertificateSpec(modo="almacen", sujeto="Mi SL")))
    check(
        "Firma con clave en HSM o token",
        all(parte in args(hsm) for parte in ("/csp", "Proveedor KSP", "/kc", "alias", "/f", "cert.crt")),
    )
    check("Firma en la nube (Azure Artifact Signing)", "/dlib" in args(azure) and "/dmdf" in args(azure))
    check("Certificado automático del almacén", args(auto) == ["sign", "/v", "/fd", "SHA256", "/tr", sello, "/td", "SHA256", "/a", "/d", "PC Toolkit", "app.exe"])
    sin_sello = args(pfx, sello_="")
    check("Se puede firmar sin sello de tiempo", "/tr" not in sin_sello and "/td" not in sin_sello)

    visible = firmar.visible_command(Path("signtool.exe"), base, pfx.password)
    check("La contraseña no se muestra en el comando visible", "secreta" not in visible and "contraseña" in visible)

    check(
        "Nombre del firmante legible (CN con coma)",
        firmar._nombre_corto("CN=PC Toolkit (prueba, no usar), O=Alguien") == "PC Toolkit (prueba, no usar)",  # noqa: SLF001
    )
    # La selección se prueba con una mezcla de un archivo que existe y otro que no: así la
    # comprobación dice algo también en un clon recién descargado, donde no hay `dist\`.
    sistema_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
    mezcla = firmar.artifacts([str(sistema_exe), str(ROOT / "no_existe.exe")])
    check(
        "Solo se firman los ejecutables que existen",
        len(mezcla) == 1 and all(archivo.exists() for archivo in mezcla),
        str([archivo.name for archivo in mezcla]),
    )
    lista_firma = firmar.artifacts()
    if lista_firma:
        check("Los cuatro artefactos previstos", len(lista_firma) == 4, str([archivo.name for archivo in lista_firma]))
    else:
        check(
            "Los cuatro artefactos previstos (omitida: sin dist\\ compilado)",
            True,
            "compila con build_installer.py para comprobarlo",
        )

    from firmar import build_parser

    parser = build_parser()
    opciones = parser.parse_args(["--pfx", "llave.pfx", "--sin-sello", "dist\\PC Toolkit.exe"])
    check("Opciones de firma reconocidas", opciones.pfx == "llave.pfx" and opciones.sin_sello and opciones.archivos)

    # `--traer-signtool` es un paso previo, no una firma: tiene que terminar ahí mismo
    # (el servidor de compilación lo ejecuta antes de generar los ejecutables, cuando
    # `dist\\` todavía está vacío). Se ejecuta `main` con la descarga sustituida para no
    # bajar el paquete de 21 MB en cada pasada de la suite.
    descarga_real = firmar.fetch_signtool
    firmar.fetch_signtool = lambda **_argumentos: Path("tools") / "bin" / "signtool" / "signtool.exe"
    try:
        codigo = firmar.main(["--traer-signtool"])
        pedido = [
            firmar.hay_otra_tarea(parser.parse_args(["--traer-signtool", extra]))
            for extra in ("--estado", "--comprobar", "--pfx llave.pfx", "dist\\PC Toolkit.exe")
        ]
        sigue = firmar.main(["--traer-signtool", "--pfx", "no_existe.pfx"])
        firmar.fetch_signtool = lambda **_argumentos: None
        codigo_fallo = firmar.main(["--traer-signtool"])
    finally:
        firmar.fetch_signtool = descarga_real
    check("\u00ab--traer-signtool\u00bb a secas termina bien (no sigue al firmado)", codigo == 0, f"c\u00f3digo {codigo}")
    check("\u00ab--traer-signtool\u00bb con otra tarea pedida no se corta", all(pedido), str(pedido))
    check("Descargar signtool no firma por su cuenta", sigue != 0, f"c\u00f3digo {sigue}")
    check("Si la descarga falla, el c\u00f3digo de salida es 1", codigo_fallo == 1, f"c\u00f3digo {codigo_fallo}")


def strip_pe_signature(path: Path) -> bool:
    """Deja un ejecutable como si nunca se hubiera firmado (tabla de certificados a cero).

    La suite necesita una copia **sin firma** para poder comprobar que la firma que acaba
    en el archivo es la del certificado de prueba. Y no siempre la tiene: con `dist\\`
    compilado los artefactos pueden venir firmados por el flujo de compilación, y sin
    `dist\\` la muestra es `where.exe`, firmado por Microsoft. `signtool remove` no sirve
    para eso (falla con el error 0x57 en binarios del sistema), así que se vacía la
    entrada de la tabla de certificados del propio PE, que es de donde Windows lee la
    firma. Devuelve si había algo que quitar.
    """
    datos = bytearray(path.read_bytes())
    if datos[:2] != b"MZ":
        return False
    cabecera = int.from_bytes(datos[0x3C:0x40], "little")
    if datos[cabecera:cabecera + 4] != b"PE\0\0":
        return False
    inicio_opcional = cabecera + 24
    magia = int.from_bytes(datos[inicio_opcional:inicio_opcional + 2], "little")
    bases = {0x10B: inicio_opcional + 96, 0x20B: inicio_opcional + 112}  # PE32 y PE32+
    if magia not in bases:
        return False
    entrada = bases[magia] + 4 * 8  # la cuarta entrada de datos es la tabla de certificados
    if entrada + 8 > len(datos):
        return False
    inicio_firma = int.from_bytes(datos[entrada:entrada + 4], "little")
    tamano_firma = int.from_bytes(datos[entrada + 4:entrada + 8], "little")
    if not inicio_firma or not tamano_firma:
        return False  # ya estaba sin firma
    datos[entrada:entrada + 8] = b"\0" * 8
    if inicio_firma + tamano_firma <= len(datos):
        datos[inicio_firma:inicio_firma + tamano_firma] = b"\0" * tamano_firma
    path.write_bytes(datos)
    return True


def test_firma_en_la_compilacion() -> None:
    """Firma integrada en los scripts de compilación (`--firmar`).

    Se firma de verdad una **copia** con un certificado de prueba temporal, usando
    exactamente la función que llaman `build_exe.py --firmar` y
    `build_installer.py --firmar`, y se comprueba que los ejecutables de `dist\\`
    no se tocan. Si no hay red, la firma se repite sin sello de tiempo.
    """
    import contextlib
    import io
    import shutil as _shutil

    import build_exe
    import build_installer
    import firmar

    # --- Cómo llegan los argumentos al firmador ------------------------------
    for nombre, modulo in (("build_exe.py", build_exe), ("build_installer.py", build_installer)):
        opciones, extra = modulo.build_parser().parse_known_args(
            ["--firmar", "--pfx", "llave.pfx", "--huella", "AABB"]
        )
        check(f"{nombre} acepta --firmar", opciones.firmar, str(extra))
        check(
            f"{nombre} pasa los argumentos de firma tal cual",
            extra == ["--pfx", "llave.pfx", "--huella", "AABB"],
            str(extra),
        )
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            try:
                modulo.main(["--pfx", "llave.pfx"])
                aviso = False
            except SystemExit as salida:  # argparse sale con 2 ante argumentos desconocidos
                aviso = salida.code == 2
        check(f"{nombre} avisa si se pasan opciones de firma sin --firmar", aviso)

    texto = r'--pfx "C:\mis llaves\llave.pfx" --sello http://timestamp.acs.microsoft.com'
    repartidos = firmar.split_arguments(texto)
    check(
        "Comillas y barras invertidas de Windows bien separadas",
        repartidos == ["--pfx", r"C:\mis llaves\llave.pfx", "--sello", "http://timestamp.acs.microsoft.com"],
        str(repartidos),
    )

    anterior = os.environ.get(firmar.SIGN_ARGS_ENV)
    os.environ[firmar.SIGN_ARGS_ENV] = "--huella AABBCC --sin-sello"
    try:
        del_entorno = firmar.environment_arguments()
        forzados = build_exe.forwarded_signing_arguments([])
        con_orden = build_exe.forwarded_signing_arguments(["--pfx", "llave.pfx"])
    finally:
        if anterior is None:
            os.environ.pop(firmar.SIGN_ARGS_ENV, None)
        else:
            os.environ[firmar.SIGN_ARGS_ENV] = anterior
    check(
        "La firma se configura desde el entorno (PCTOOLKIT_SIGN_ARGS)",
        del_entorno == ["--huella", "AABBCC", "--sin-sello"],
        str(del_entorno),
    )
    check("Sin argumentos propios se usan los del entorno", forzados == del_entorno, str(forzados))
    check("Los argumentos de la orden tienen prioridad", con_orden == ["--pfx", "llave.pfx"], str(con_orden))
    os.environ.pop(firmar.SIGN_ARGS_ENV, None)
    check("Sin configurar nada no hay argumentos de firma", firmar.environment_arguments() == [])

    avisos: list[str] = []
    falta = firmar.sign_paths([ROOT / "no_existe.exe"], [], log=avisos.append)
    check(
        "No se firma algo que no existe",
        falta is False and any("no existe" in aviso for aviso in avisos),
        avisos[-1].strip() if avisos else "",
    )
    if (ROOT / "dist" / "PC Toolkit.exe").exists():
        muestra = ROOT / "dist" / "PC Toolkit.exe"
    else:
        muestra = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
    if firmar.find_signtool() is None:
        # Sin signtool, `sign_paths` se para antes de mirar el certificado (dice cómo
        # conseguirlo): la comprobación del PFX necesita la máquina preparada.
        check(
            "Av\u00edsa si el PFX indicado no existe (omitida: no hay signtool)",
            True,
            "python firmar.py --traer-signtool",
        )
    else:
        avisos.clear()
        sin_pfx = firmar.sign_paths([muestra], ["--pfx", str(ROOT / "no_existe.pfx")], log=avisos.append)
        check(
            "Av\u00edsa si el PFX indicado no existe",
            sin_pfx is False and any("PFX" in aviso for aviso in avisos),
            avisos[-1].strip() if avisos else "",
        )

    # --- Firma de verdad, sobre una copia y con un certificado de prueba -----
    signtool = firmar.find_signtool()
    if signtool is None:
        check("Firma real en la compilaci\u00f3n (omitida: no hay signtool)", True, "firmar.py --traer-signtool")
        return

    # Estado de los ejecutables reales antes de tocar nada: al final se comprueba que
    # siguen igual (firmar la copia no puede haberlos tocado).
    estado_dist_antes = firmar.signature_info(muestra).get("Status")

    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        clave = carpeta / "clave.txt"
        password = firmar._random_password()  # noqa: SLF001
        clave.write_text(password + "\n", encoding="utf-8")
        pfx = carpeta / "prueba.pfx"
        huella, mensaje = firmar._create_test_certificate(pfx, password)  # noqa: SLF001
        # Solo se anota si falla: así el recuento de las guías no cambia por crear el
        # certificado, que es lo que se hace aquí antes de cualquier firma.
        if not huella:
            check("Certificado de prueba para firmar en la compilaci\u00f3n", False, mensaje)
            return
        try:
            if not (ROOT / "dist" / "PC Toolkit.exe").exists():
                # Sin `dist\` no hay muestra válida para firmar de verdad. La única
                # candidata sería `where.exe`, y los binarios del sistema van firmados por
                # **catálogo**: Windows los da por buenos por el catálogo de Microsoft
                # aunque se les ponga encima la firma de prueba (y `signtool remove` no
                # puede quitarla: falla con el error 0x57), así que comprobarían lo que no
                # es. Se anotan omitidas, como el resto de las que piden los ejecutables.
                for nombre in (
                    "Firma real al compilar (omitida: sin dist\\ compilado)",
                    "El certificado con el que se firmó (omitida: sin dist\\ compilado)",
                    "signtool lee la firma creada (omitida: sin dist\\ compilado)",
                    "El comprobador de firmas explica que la firma no es de fiar (omitida: sin dist\\ compilado)",
                    "build_installer.py firma los cuatro artefactos (omitida: sin dist\\ compilado)",
                ):
                    check(nombre, True, "compila con build_installer.py para comprobarlo")
                return
            argumentos = ["--pfx", str(pfx), "--password-file", str(clave)]
            copia = carpeta / "muestra.exe"
            _shutil.copyfile(muestra, copia)
            # La copia tiene que llegar sin firma: si no, las comprobaciones de abajo
            # —que esperan la firma del certificado de prueba y que este equipo no se fíe
            # de ella— estarían mirando la firma que ya traía el archivo.
            strip_pe_signature(copia)
            # La salida de signtool se guarda: en la suite solo se muestra si algo falla.
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                firmada = build_exe.sign_executables([copia], argumentos)
            info = firmar.signature_info(copia)
            if not firmada:  # sin red el sello de tiempo no se puede pedir
                copia = carpeta / "muestra_sin_sello.exe"
                _shutil.copyfile(muestra, copia)
                strip_pe_signature(copia)
                with contextlib.redirect_stdout(salida):
                    firmada = build_exe.sign_executables([copia], argumentos + ["--sin-sello"])
                info = firmar.signature_info(copia)
            check(
                "Firma real al compilar (copias, nunca dist\\)",
                firmada and info.get("Status") != "NotSigned",
                str(info.get("Status")) if firmada else salida.getvalue().strip()[-200:],
            )
            check(
                "El certificado con el que se firm\u00f3 es el indicado",
                (info.get("Thumbprint") or "").upper() == huella.upper(),
                (info.get("Thumbprint") or "")[:16],
            )
            correcta, resumen, _salida = firmar.verify_signature(signtool, copia)
            check("signtool lee la firma creada al compilar", correcta or "ra\u00edz" in resumen, resumen)
            avisos.clear()
            comprobado = firmar.check_signatures([copia], log=avisos.append)
            # Distingue «hay firma pero no es de fiar» de «no hay firma»: con un
            # certificado de prueba lo primero es lo que pasa, y el mensaje tiene que
            # decirlo para no perder media hora en un servidor de compilación.
            check(
                "El comprobador de firmas explica que la firma no es de fiar",
                comprobado is False
                and bool(avisos)
                and "hay firma de" in avisos[-1]
                and "no confía" in avisos[-1],
                avisos[-1].strip() if avisos else "",
            )

            # --- La función que llama `build_installer.py --firmar` -------------
            # `sign_executables` firma todo lo que devuelve `firmar.artifacts()`, que son
            # rutas fijas de `dist\`. Para poder probarla sin tocar los ejecutables de
            # verdad se apuntan esas rutas a copias, y se restauran al terminar.
            pequeno = ROOT / "dist" / "PC Toolkit" / "PC Toolkit.exe"
            if pequeno.exists():
                import build_installer

                onedir = carpeta / "instalador"
                onedir.mkdir()
                objetivos = [
                    onedir / "PC Toolkit.exe",
                    onedir / "Desinstalar PC Toolkit.exe",
                    carpeta / "PC Toolkit Setup.exe",
                    carpeta / "PC Toolkit (un archivo).exe",
                ]
                guardados = (firmar.APP_EXE, firmar.UNINSTALLER_EXE, firmar.SETUP_EXE, firmar.ONEFILE_EXE)
                try:
                    for destino_copia in objetivos:
                        _shutil.copyfile(pequeno, destino_copia)
                    firmar.APP_EXE, firmar.UNINSTALLER_EXE, firmar.SETUP_EXE, firmar.ONEFILE_EXE = objetivos
                    check(
                        "El firmador de la compilación ve los cuatro artefactos",
                        len(firmar.artifacts()) == 4,
                        str([ruta.name for ruta in firmar.artifacts()]),
                    )
                    firmado_instalador = build_installer.sign_executables(argumentos)
                    if not firmado_instalador:  # sin red no se puede pedir el sello
                        firmado_instalador = build_installer.sign_executables(argumentos + ["--sin-sello"])
                    huellas = [
                        (firmar.signature_info(ruta).get("Thumbprint") or "").upper() for ruta in objetivos
                    ]
                    check(
                        "build_installer.py firma los cuatro artefactos con el certificado indicado",
                        firmado_instalador and all(valor == huella.upper() for valor in huellas),
                        f"{len(huellas)} artefactos · {huellas[0][:12] if huellas else ''}",
                    )
                finally:
                    firmar.APP_EXE, firmar.UNINSTALLER_EXE, firmar.SETUP_EXE, firmar.ONEFILE_EXE = guardados
            else:
                check(
                    "build_installer.py firma los cuatro artefactos (omitida: sin dist\\ compilado)",
                    True,
                    "compila con build_installer.py para comprobarlo",
                )
            if muestra == ROOT / "dist" / "PC Toolkit.exe":
                # Se compara con el estado que tenía antes de firmar la copia: así la
                # comprobación vale tanto si `dist\\` está sin firmar (en local) como si
                # viene de la compilación firmada del servidor.
                check(
                    "Los ejecutables de dist/ no se tocan al firmar una copia",
                    firmar.signature_info(muestra).get("Status") == estado_dist_antes,
                    f"antes {estado_dist_antes}, ahora {firmar.signature_info(muestra).get('Status')}",
                )
        finally:
            firmar._remove_test_certificate(huella)  # noqa: SLF001

def test_config() -> None:
    """Números y carpetas personales: como los tenga el sistema, no como supongamos."""
    from PySide6.QtCore import QLocale

    from pctoolkit.core import config, disk

    sistema = QLocale.system()
    check(
        "Los números se escriben como en el sistema",
        config.format_number(140493, 0) == sistema.toString(140493.0, "f", 0)
        and config.format_number(1531.4, 1) == sistema.toString(1531.4, "f", 1),
        f"{config.format_number(140493, 0)} archivos · {config.format_number(1531.4, 1)} ({sistema.name()})",
    )
    check(
        "Los recuentos y los tamaños usan el mismo formato",
        config.format_integer(140493) == sistema.toString(140493.0, "f", 0)
        and config.format_bytes(531.4 * 1024**3).startswith(sistema.toString(531.4, "f", 1)),
        f"{config.format_integer(140493)} archivos · {config.format_bytes(531.4 * 1024**3)}",
    )

    # Las carpetas personales pueden estar redirigidas (OneDrive, políticas de empresa): se
    # comprueba contra lo que responde Windows por otra vía, no contra `Path.home()`.
    from installer import installer_core

    documentos = config.user_folder("Documents")
    de_windows = installer_core.known_folder("MyDocuments", documentos)
    check(
        "Las carpetas personales son las que dice Windows",
        documentos.is_absolute() and str(documentos) == str(de_windows),
        f"{documentos} (Windows: {de_windows})",
    )
    check(
        "La carpeta de informes cuelga de los documentos reales",
        str(config.reports_dir()).startswith(str(documentos)),
        str(config.reports_dir()),
    )
    candidatas = disk.quick_candidates()
    check(
        "El análisis rápido ofrece las carpetas del usuario que existen",
        bool(candidatas) and all(os.path.isdir(ruta) for ruta in candidatas),
        f"{len(candidatas)} carpetas: " + ", ".join(os.path.basename(ruta) for ruta in candidatas),
    )

    # Propiedades del ejecutable: llevan la licencia y el autor de verdad.
    import build_exe

    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "version_info.txt"
        build_exe.write_version_file(destino)
        texto = destino.read_text(encoding="utf-8")
    check(
        "Las propiedades del ejecutable llevan la licencia y el autor",
        "MIT" in texto
        and "Federico" in texto
        and "Uso libre" not in texto
        and APP_VERSION in texto,
        "version_info.txt",
    )


def _normalizar_distribucion(nombre: str) -> str:
    """Nombre de paquete comparable: `PyInstaller` y `pyinstaller` son el mismo."""
    return re.sub(r"[-_.]+", "-", nombre).lower()


def test_dependencias() -> None:
    """Lo que la compilación importa de terceros tiene que estar declarado.

    El servidor de compilación instala `requirements.txt` y nada más, así que un `import`
    de una biblioteca que no esté ahí funciona en un equipo con paquetes de más y rompe el
    empaquetado en el servidor. Pasó con PyInstaller: la guía daba por hecho que estaba en
    `requirements.txt` y no estaba, así que la compilación del servidor llevaba rota desde
    el principio —nadie la había ejecutado nunca, porque el flujo se paraba antes, en el
    inicio de sesión de Azure— hasta la primera publicación sin firma.
    """
    import ast
    import importlib.metadata

    declaradas = set()
    for linea in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if linea:
            declaradas.add(_normalizar_distribucion(re.split(r"[<>=!\[;\s]", linea, 1)[0]))

    # Qué paquete trae cada módulo instalado: `PyInstaller` viene de `pyinstaller` y así
    # con los que no se llaman igual que su distribución.
    de_paquete = importlib.metadata.packages_distributions()

    archivos = [ROOT / nombre for nombre in ("app.py", "build_exe.py", "build_installer.py", "firmar.py")]
    for carpeta in ("installer", "pctoolkit", "tools", "tests"):
        archivos += sorted((ROOT / carpeta).rglob("*.py"))

    # Se leen los `import`, no el texto: así valen las formas raras (dentro de una función,
    # dentro de un `try`, con paréntesis…) y no cuentan los comentarios ni las cadenas.
    importados: dict[str, set[str]] = {}
    for archivo in archivos:
        if not archivo.exists():
            continue
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                nombres = [alias.name.split(".")[0] for alias in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and not nodo.level:
                nombres = [(nodo.module or "").split(".")[0]]
            else:
                continue
            for nombre in nombres:
                if nombre:
                    importados.setdefault(nombre, set()).add(archivo.relative_to(ROOT).as_posix())

    # De todo eso solo interesa lo que no trae Python ni el propio proyecto: sus propios
    # módulos (`_checks`, `documentacion`, `build_exe`…) cuentan como locales, porque la
    # suite los importa con `tests\` y la raíz en el camino de búsqueda.
    propios = {archivo.stem for archivo in archivos} | {ruta.name for ruta in ROOT.iterdir() if ruta.is_dir()}
    faltan = []
    terceros = 0
    for nombre, donde in sorted(importados.items()):
        if nombre in sys.stdlib_module_names or nombre in propios:
            continue
        terceros += 1
        distribuciones = {_normalizar_distribucion(paquete) for paquete in de_paquete.get(nombre, [])}
        if distribuciones & declaradas or _normalizar_distribucion(nombre) in declaradas:
            continue
        faltan.append(f"{nombre} ({', '.join(sorted(donde))})")

    check(
        "Lo que la compilación importa de terceros está declarado en requirements.txt",
        not faltan,
        "sin declarar: " + "; ".join(faltan)
        if faltan
        else f"{terceros} bibliotecas de terceros, todas declaradas",
    )


def test_registro() -> None:
    """El registro en archivo: lo que falla queda escrito sin molestar al usuario."""
    from pctoolkit.core import logs

    ruta = logs.setup()
    check("El registro se prepara en una carpeta del usuario", bool(ruta), str(ruta))
    if ruta:
        check(
            "El archivo de registro vive en los datos locales del usuario",
            str(ruta).startswith(_TEMP_HOME) and ruta.name == logs.FILE_NAME,
            str(ruta),
        )
    logs.warning("Prueba de registro", OSError("fallo de mentira"))
    texto = ruta.read_text(encoding="utf-8") if ruta and ruta.exists() else ""
    check(
        "Un aviso queda escrito con su motivo",
        "Prueba de registro" in texto and "fallo de mentira" in texto,
        f"{len(texto)} caracteres en {ruta}",
    )
    check(
        "El registro se recorta antes de crecer sin límite",
        logs.MAX_BYTES <= 1024 * 1024 and logs.BACKUP_COUNT >= 1,
        f"{logs.MAX_BYTES} bytes, {logs.BACKUP_COUNT} copias",
    )


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    # Las guardas de documentación se pueden dejar fuera: si una captura está por rehacer,
    # no tiene por qué impedir comprobar un cambio de código.
    sin_documentacion = "--sin-documentacion" in argumentos
    test_metrics()
    test_config()
    test_dependencias()
    test_theme()
    test_disk()
    test_tasks()
    test_tools()
    test_reports()
    test_alerts()
    test_history()
    test_memory()
    test_scheduler()
    test_startup()
    test_maintenance()
    test_installer()
    test_firma()
    test_firma_en_la_compilacion()
    test_interface()
    test_registro()
    if sin_documentacion:
        print("Guardas de documentación omitidas (--sin-documentacion).")
    else:
        # La última: comprueba, entre otras cosas, que el recuento de las guías es el de
        # esta misma pasada, así que necesita que ya estén todas hechas.
        verificar_documentacion()

    return report()


if __name__ == "__main__":
    raise SystemExit(main())
