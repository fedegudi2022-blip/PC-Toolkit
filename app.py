"""PC Toolkit — punto de entrada de la aplicación de escritorio.

Uso:
    python app.py                     Abre la aplicación.
    python app.py --analizar C:\\Ruta  Abre y analiza esa carpeta al arrancar.
    python app.py --screenshot salida Genera capturas de cada página y sale.
    python app.py --tema claro        Abre con el tema claro (o `oscuro`/`auto`)
                                      sin tocar las preferencias guardadas.
    python app.py --mantenimiento     Mantenimiento desatendido (sin ventana);
                                      es lo que ejecuta la tarea programada.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Permite importar el paquete cuando se ejecuta desde otra carpeta.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pctoolkit.core.appearance import MODES  # noqa: E402
from pctoolkit.core.config import APP_NAME, APP_VERSION  # noqa: E402
from pctoolkit.core.settings import AppSettings  # noqa: E402
from pctoolkit.core.single_instance import WAIT_AFTER_RESTART_MS, SingleInstance  # noqa: E402
from pctoolkit.ui.shell import MainWindow  # noqa: E402
from pctoolkit.ui.theme import apply_theme  # noqa: E402


def _wait(ms: int) -> None:
    """Cede el control al bucle de eventos durante `ms` milisegundos."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _seed_demo_tasks(window) -> None:
    """Crea tareas de ejemplo (solo para capturas o demostraciones)."""
    store = window.task_store
    store.add("Eliminar archivos temporales", "Limpieza", "Liberar espacio en el disco del sistema.")
    store.add("Ejecutar Windows Update", "Actualizaciones", "Pendiente de instalar parches.")
    task = store.add(
        "Comprobar el estado SMART del disco",
        "Hardware",
        "Usar la herramienta del fabricante.",
        priority="Alta",
    )
    store.add("Vaciar la papelera de reciclaje", "Limpieza", "Solo archivos de más de 30 días.")
    store.toggle_done(task.id)
    store.add(
        "Crear copia de seguridad de documentos",
        "Copias de seguridad",
        "Guardar en disco externo.",
        due_date="2020-01-01",
    )


def _capture_screens(window, output: Path, analyze_path: str | None = None) -> int:
    """Genera una captura por página (útil para documentación y revisión)."""
    output.mkdir(parents=True, exist_ok=True)
    window.resize(1360, 860)
    window.show()
    # La primera muestra de psutil siempre da 0 % de CPU y cada tarjeta se pinta al
    # recibir la suya: con un par de muestras las capturas de la documentación
    # salen con datos reales en vez de a cero.
    _wait(2600)

    if analyze_path:
        window.navigate("disk")
        window.disk_page.start_scan(analyze_path)
        deadline = 90_000
        step = 200
        waited = 0
        while waited < deadline:
            scanner = window.disk_page._scanner  # noqa: SLF001
            if scanner is None or not scanner.isRunning():
                break
            _wait(step)
            waited += step
        _wait(400)

    pages = [
        ("home", "Inicio"),
        ("memory", "Optimizar memoria"),
        ("startup", "Programas de inicio"),
        ("disk", "Analizador de disco"),
        ("tools", "Herramientas de Windows"),
        ("tasks", "Registro de tareas"),
        ("reports", "Informes"),
        ("schedule", "Mantenimiento programado"),
    ]
    for key, _label in pages:
        window.navigate(key)
        _wait(500)
        if key == "startup":
            # Las tareas de inicio de sesión se buscan en segundo plano (PowerShell
            # tarda unos segundos): la captura espera a que la lista esté completa.
            waited = 0
            while waited < 15_000 and window.startup_page._scan_running:  # noqa: SLF001
                _wait(300)
                waited += 300
            _wait(300)
        if key == "schedule":
            # El estado de la tarea se consulta al abrir la página: la captura
            # espera a tenerlo para no documentar un «Consultando…».
            window.schedule_page.ensure_status()
            waited = 0
            while waited < 15_000 and window.schedule_page.status_querying:
                _wait(300)
                waited += 300
            _wait(300)
        target = output / f"{key}.png"
        window.grab().save(str(target))
        print(f"Captura guardada: {target}")
    # Se cierra por la vía normal (la misma que «Salir»): así el guion de capturas
    # también ejercita el cierre ordenado, que espera a lo que esté en marcha.
    window.quit_application()
    return 0


def _run_maintenance(args) -> int:
    """Mantenimiento desatendido: sin ventana y con salida por consola."""
    from pctoolkit.core import maintenance, memory as memory_core, tools as tools_core
    from pctoolkit.core.scheduler import MaintenanceSchedule

    # QCoreApplication basta: no hay interfaz, pero las clases del núcleo usan Qt.
    app = QCoreApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("PCToolkit")
    settings = AppSettings()
    schedule = MaintenanceSchedule.from_dict(settings.schedule_config.to_dict())
    schedule.enabled = True
    options = memory_core.MemoryOptions.from_dict(settings.memory_options.to_dict())
    # Perder esto borraría archivos temporales de hace segundos (por ejemplo, los de
    # una instalación en curso), así que se aplica el mismo suelo que en la interfaz.
    antiguedad = tools_core.min_temp_age_hours(args.antiguedad)
    if antiguedad != float(args.antiguedad):
        print(
            f"Aviso: la antigüedad mínima es de {tools_core.MIN_TEMP_AGE_HOURS:g} hora(s); "
            f"se usan {antiguedad:g} en vez de {args.antiguedad:g}."
        )
    report = maintenance.run_maintenance(
        schedule,
        memory_options=options,
        dry_run=args.simular,
        trigger="programado",
        clean=False if args.sin_limpieza else None,
        export=False if args.sin_informe else None,
        release_memory=True if args.liberar_memoria else None,
        min_age_hours=antiguedad,
        register=not args.sin_registro,
    )
    print(report.summary())
    for item in report.items:
        estado = "OK " if item.ok else "AVISO"
        print(f"  [{estado}] {item.title}: {item.detail}")
    print(f"Registro: {report.log_path}")
    if report.report_path:
        print(f"Informe: {report.report_path}")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--analizar", metavar="RUTA", help="Analiza esa carpeta al abrir")
    parser.add_argument(
        "--screenshot",
        metavar="DIR",
        help="Guarda capturas de todas las páginas en DIR y sale",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Carga tareas de ejemplo (pensado para demostraciones y capturas)",
    )
    parser.add_argument(
        "--tema",
        # Se admiten los nombres en español, que son los que usa la interfaz.
        choices=(*MODES, "claro", "oscuro"),
        help="Fuerza la apariencia de esta ejecución (claro, oscuro o auto) sin guardarla",
    )
    parser.add_argument(
        "--bandeja",
        action="store_true",
        help="Arranca en segundo plano, solo con el icono de la bandeja (para el inicio automático)",
    )
    parser.add_argument(
        "--esperar",
        action="store_true",
        help=(
            "Espera a que la copia anterior se cierre antes de arrancar (lo usa el "
            "reinicio con permisos de administrador)"
        ),
    )
    parser.add_argument(
        "--mantenimiento",
        action="store_true",
        help="Ejecuta el mantenimiento desatendido (limpieza, memoria e informe) y sale",
    )
    parser.add_argument("--sin-limpieza", action="store_true", help="Omite la limpieza de temporales")
    parser.add_argument("--sin-informe", action="store_true", help="Omite la generación del informe")
    parser.add_argument("--liberar-memoria", action="store_true", help="Libera memoria RAM en esta ejecución")
    parser.add_argument("--sin-registro", action="store_true", help="No anota la ejecución en el histórico")
    parser.add_argument(
        "--antiguedad",
        type=float,
        default=24.0,
        metavar="HORAS",
        help="Solo borra temporales con más de esas horas (mínimo 1; por debajo se aplica 1)",
    )
    parser.add_argument(
        "--simular",
        action="store_true",
        help="Muestra lo que haría el mantenimiento sin cambiar nada",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = parser.parse_args(argv)

    if args.mantenimiento:
        return _run_maintenance(args)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("PCToolkit")
    # El tema se resuelve antes de construir nada: las preferencias dicen si hay
    # que seguir a Windows o quedarse en claro u oscuro, y `--tema` manda sobre
    # ellas solo para esta ejecución.
    settings = AppSettings()
    apply_theme(app, args.tema or settings.theme_mode)

    # Una sola copia a la vez. Sin esto, el arranque automático más el acceso directo
    # (o el reinicio con permisos) dejan dos iconos de bandeja, dos guardias de memoria
    # automática y dos procesos anotando en el historial. Las capturas quedan fuera de
    # la guarda porque su objetivo es justamente abrir una ventana y salir.
    guarda: SingleInstance | None = None
    if not args.screenshot:
        guarda = SingleInstance(app)
        if not guarda.acquire(WAIT_AFTER_RESTART_MS if args.esperar else 0):
            # Ya había una copia abierta: se le pide que saque su ventana y esta se va.
            guarda.notify_existing()
            print(f"{APP_NAME} ya está abierto; se ha traído su ventana a delante.")
            return 0

    window = MainWindow(settings)
    if guarda is not None:
        guarda.listen(window.show_from_another_instance)
    if args.demo:
        _seed_demo_tasks(window)

    if args.screenshot:
        return _capture_screens(window, Path(args.screenshot), args.analizar)

    if args.bandeja and window.tray.active:
        # Arranque automático: la ventana no aparece, solo el icono de la bandeja.
        window.set_status("PC Toolkit iniciado en segundo plano. Ábrelo desde el icono de la bandeja.")
    else:
        if args.bandeja:
            window.set_status("Este sistema no tiene bandeja: se abre la ventana.")
        window.show()
    if args.analizar:
        window.show()
        window.navigate("disk")
        window.disk_page.start_scan(args.analizar)

    if args.bandeja and window.tray.active:
        window.tray.notify(
            "PC Toolkit está vigilando tu equipo",
            "Se ha iniciado en segundo plano. Haz clic en el icono para abrir la ventana.",
            warning=False,
        )
    try:
        return app.exec()
    finally:
        if guarda is not None:
            guarda.stop_listening()
            guarda.release()


if __name__ == "__main__":
    raise SystemExit(main())
