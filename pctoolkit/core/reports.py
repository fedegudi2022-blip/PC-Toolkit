"""Exportación de informes del sistema en HTML, CSV y JSON."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import (
    APP_NAME,
    APP_VERSION,
    COLORS,
    DARK_COLORS,
    LIGHT_COLORS,
    LIGHT_SURFACES,
    format_bytes,
    format_integer,
)
from .disk import DriveInfo, ScanResult
from .metrics import MetricsSnapshot

FORMAT_HTML = "HTML"
FORMAT_CSV = "CSV"
FORMAT_JSON = "JSON"

EXTENSIONS = {FORMAT_HTML: ".html", FORMAT_CSV: ".csv", FORMAT_JSON: ".json"}


def build_report(
    snapshot: MetricsSnapshot,
    system_info: dict[str, Any],
    drives: list[DriveInfo],
    tasks: list[dict[str, Any]],
    task_stats: dict[str, Any] | None = None,
    disk_result: ScanResult | None = None,
    drive_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Reúne toda la información disponible en un diccionario serializable."""
    generated = datetime.now()
    report: dict[str, Any] = {
        "app": {"nombre": APP_NAME, "version": APP_VERSION},
        "generado": generated.isoformat(timespec="seconds"),
        "generado_display": generated.strftime("%d/%m/%Y %H:%M:%S"),
        "equipo": system_info,
        "resumen": {
            "salud": round(snapshot.health_score, 1),
            "salud_etiqueta": snapshot.health_label,
            "cpu_porcentaje": round(snapshot.cpu_percent, 1),
            "cpu_frecuencia_mhz": snapshot.cpu_freq_mhz,
            "cpu_temperatura": snapshot.cpu_temp,
            "ram_porcentaje": round(snapshot.ram_percent, 1),
            "ram_usada": snapshot.ram_used,
            "ram_usada_display": format_bytes(snapshot.ram_used),
            "ram_total": snapshot.ram_total,
            "ram_total_display": format_bytes(snapshot.ram_total),
            "swap_porcentaje": round(snapshot.swap_percent, 1),
            "disco_porcentaje": round(snapshot.disk_percent, 1),
            "disco_unidad": snapshot.disk_mount,
            "disco_usado": snapshot.disk_used,
            "disco_usado_display": format_bytes(snapshot.disk_used),
            "disco_total": snapshot.disk_total,
            "disco_total_display": format_bytes(snapshot.disk_total),
            "disco_libre_display": format_bytes(snapshot.disk_free),
            "procesos": snapshot.process_count,
            "red_subida_display": f"{format_bytes(snapshot.net_up_rate)}/s",
            "red_bajada_display": f"{format_bytes(snapshot.net_down_rate)}/s",
            "red_total_enviado": format_bytes(snapshot.net_sent),
            "red_total_recibido": format_bytes(snapshot.net_recv),
            "encendido_desde": _uptime_display(snapshot.uptime_seconds),
            "bateria": snapshot.battery_percent,
        },
        "unidades": [
            {
                "unidad": drive.mount,
                "etiqueta": drive.label,
                "sistema_archivos": drive.fstype,
                "total": drive.total,
                "total_display": format_bytes(drive.total),
                "usado": drive.used,
                "usado_display": format_bytes(drive.used),
                "libre": drive.free,
                "libre_display": format_bytes(drive.free),
                "porcentaje": round(drive.percent, 1),
            }
            for drive in drives
        ],
        "procesos_principales": [
            {"nombre": name, "cpu_porcentaje": round(cpu, 1), "memoria_mb": round(mem, 1)}
            for name, cpu, mem in snapshot.top_processes
        ],
        "tareas": {
            "estadisticas": task_stats or {},
            "lista": tasks,
        },
    }
    if disk_result is not None:
        report["analisis_disco"] = {
            "ruta": disk_result.root,
            "tamano_total": disk_result.total_size,
            "tamano_total_display": format_bytes(disk_result.total_size),
            "archivos": disk_result.total_files,
            "carpetas": disk_result.dirs_scanned,
            "errores": disk_result.errors,
            "duracion_segundos": round(disk_result.duration, 2),
            "incompleto": disk_result.cancelled,
            "carpetas_principales": [
                {
                    "nombre": node.name,
                    "ruta": node.path,
                    "tipo": "Carpeta" if node.is_dir else "Archivo",
                    "tamano": node.size,
                    "tamano_display": format_bytes(node.size),
                    "elementos": node.file_count,
                }
                for node in disk_result.children[:15]
            ],
            "categorias": [
                {"categoria": name, "tamano": size, "tamano_display": format_bytes(size)}
                for name, size in disk_result.categories
            ],
            "archivos_mas_grandes": [
                {
                    "nombre": node.name,
                    "ruta": node.path,
                    "tamano": node.size,
                    "tamano_display": format_bytes(node.size),
                }
                for node in disk_result.largest_files[:15]
            ],
            "unidad_etiqueta": (drive_labels or {}).get(disk_result.root, ""),
        }
    return report


def _uptime_display(seconds: float) -> str:
    total = int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days} d")
    if hours or days:
        parts.append(f"{hours} h")
    parts.append(f"{minutes} min")
    return " ".join(parts)


# --- Exportadores ---------------------------------------------------------
def export(report: dict[str, Any], path: str | Path, fmt: str) -> Path:
    target = Path(path)
    if fmt == FORMAT_HTML:
        return export_html(report, target)
    if fmt == FORMAT_CSV:
        return export_csv(report, target)
    return export_json(report, target)


def export_json(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def export_csv(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    rows: list[list[Any]] = []
    resumen = report.get("resumen", {})
    equipo = report.get("equipo", {})

    rows.append(["PC Toolkit - Informe del sistema"])
    rows.append(["Generado", report.get("generado_display", "")])
    rows.append(["Versión", report.get("app", {}).get("version", "")])
    rows.append([])

    rows.append(["EQUIPO"])
    rows.append(["Campo", "Valor"])
    for key, value in equipo.items():
        rows.append([key.replace("_", " ").capitalize(), value])
    rows.append([])

    rows.append(["RESUMEN DE RENDIMIENTO"])
    rows.append(["Campo", "Valor"])
    for key, value in resumen.items():
        if key.endswith("_display") or key.islower():
            rows.append([key.replace("_", " ").capitalize(), value])
    rows.append([])

    rows.append(["UNIDADES"])
    rows.append(["Unidad", "Etiqueta", "Sistema de archivos", "Total", "Usado", "Libre", "% usado"])
    for drive in report.get("unidades", []):
        rows.append([
            drive["unidad"],
            drive["etiqueta"],
            drive["sistema_archivos"],
            drive["total_display"],
            drive["usado_display"],
            drive["libre_display"],
            drive["porcentaje"],
        ])
    rows.append([])

    analysis = report.get("analisis_disco")
    if analysis:
        rows.append(["ANÁLISIS DE DISCO"])
        rows.append(["Ruta", analysis["ruta"]])
        rows.append(["Tamaño analizado", analysis["tamano_total_display"]])
        rows.append(["Archivos", analysis["archivos"]])
        rows.append(["Carpetas", analysis["carpetas"]])
        rows.append(["Duración (s)", analysis["duracion_segundos"]])
        rows.append([])
        rows.append(["Carpetas más pesadas"])
        rows.append(["Carpeta", "Tipo", "Tamaño", "Elementos", "Ruta"])
        for node in analysis["carpetas_principales"]:
            rows.append([node["nombre"], node["tipo"], node["tamano_display"], node["elementos"], node["ruta"]])
        rows.append([])
        rows.append(["Uso por categoría"])
        rows.append(["Categoría", "Tamaño"])
        for item in analysis["categorias"]:
            rows.append([item["categoria"], item["tamano_display"]])
        rows.append([])
        rows.append(["Archivos más grandes"])
        rows.append(["Archivo", "Tamaño", "Ruta"])
        for item in analysis["archivos_mas_grandes"]:
            rows.append([item["nombre"], item["tamano_display"], item["ruta"]])
        rows.append([])

    rows.append(["TAREAS DE MANTENIMIENTO"])
    stats = report.get("tareas", {}).get("estadisticas", {})
    rows.append(["Total", stats.get("total", 0)])
    rows.append(["Pendientes", stats.get("pendientes", 0)])
    rows.append(["Completadas", stats.get("completadas", 0)])
    rows.append(["Vencidas", stats.get("vencidas", 0)])
    rows.append([])
    rows.append(["Título", "Categoría", "Prioridad", "Estado", "Creada", "Vence", "Completada", "Notas"])
    for task in report.get("tareas", {}).get("lista", []):
        rows.append([
            task.get("title", ""),
            task.get("category", ""),
            task.get("priority", ""),
            task.get("status", ""),
            task.get("created_at", ""),
            task.get("due_date", ""),
            task.get("completed_at", ""),
            task.get("notes", ""),
        ])
    rows.append([])

    rows.append(["PROCESOS CON MÁS CONSUMO"])
    rows.append(["Proceso", "CPU %", "Memoria MB"])
    for proc in report.get("procesos_principales", []):
        rows.append([proc["nombre"], proc["cpu_porcentaje"], proc["memoria_mb"]])

    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerows(rows)
    return target


def export_html(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.write_text(_html(report), encoding="utf-8")
    return target


def _html(report: dict[str, Any]) -> str:
    resumen = report.get("resumen", {})
    equipo = report.get("equipo", {})
    stats = report.get("tareas", {}).get("estadisticas", {})
    tasks = report.get("tareas", {}).get("lista", [])
    analysis = report.get("analisis_disco")
    # El informe se abre en el navegador y siempre va en claro, así que sus colores
    # salen de la paleta **clara** aunque la aplicación esté en oscuro: los azules y
    # cianes claros de la oscura no se leen sobre blanco.
    claro = LIGHT_COLORS

    def bar(percent: float, color: str | None = None) -> str:
        value = max(0.0, min(100.0, float(percent or 0)))
        return (
            f'<div class="bar"><span style="width:{value:.1f}%;background:{color or claro["accent"]}">'
            "</span></div>"
        )

    def color_for(percent: float) -> str:
        if percent >= 85:
            return claro["bad"]
        if percent >= 65:
            return claro["warn"]
        return claro["accent"]

    health = float(resumen.get("salud", 0) or 0)
    health_color = (
        claro["good"] if health >= 70 else (claro["warn"] if health >= 45 else claro["bad"])
    )

    drive_rows = "".join(
        f"<tr><td><strong>{d['unidad']}</strong><div class='muted'>{d['etiqueta'] or d['sistema_archivos']}</div></td>"
        f"<td>{d['total_display']}</td><td>{d['usado_display']}</td><td>{d['libre_display']}</td>"
        f"<td class='w-140'>{bar(d['porcentaje'], color_for(d['porcentaje']))}<div class='muted'>{d['porcentaje']}%</div></td></tr>"
        for d in report.get("unidades", [])
    ) or "<tr><td colspan='5' class='muted'>Sin datos</td></tr>"

    proc_rows = "".join(
        f"<tr><td>{p['nombre']}</td><td>{p['cpu_porcentaje']:.1f}%</td><td>{p['memoria_mb']:.0f} MB</td></tr>"
        for p in report.get("procesos_principales", [])
    ) or "<tr><td colspan='3' class='muted'>Sin datos</td></tr>"

    task_rows = "".join(
        f"<tr><td>{_escape(t.get('title', ''))}<div class='muted'>{_escape(t.get('notes', ''))}</div></td>"
        f"<td>{_escape(t.get('category', ''))}</td><td>{_escape(t.get('priority', ''))}</td>"
        f"<td><span class='tag {'done' if t.get('status') == 'Completada' else 'pending'}'>"
        f"{_escape(t.get('status', ''))}</span></td>"
        f"<td class='muted'>{_escape(t.get('created_at', ''))[:16].replace('T', ' ')}</td>"
        f"<td class='muted'>{_escape(t.get('due_date', '')) or '—'}</td></tr>"
        for t in tasks
    ) or "<tr><td colspan='6' class='muted'>No hay tareas registradas</td></tr>"

    analysis_html = ""
    if analysis:
        folder_rows = "".join(
            f"<tr><td>{_escape(n['nombre'])}<div class='muted'>{_escape(n['ruta'])}</div></td>"
            f"<td>{n['tipo']}</td><td>{n['tamano_display']}</td><td>{n['elementos']}</td></tr>"
            for n in analysis["carpetas_principales"]
        )
        category_rows = "".join(
            f"<tr><td>{_escape(c['categoria'])}</td><td>{c['tamano_display']}</td></tr>"
            for c in analysis["categorias"]
        )
        file_rows = "".join(
            f"<tr><td>{_escape(f['nombre'])}<div class='muted'>{_escape(f['ruta'])}</div></td>"
            f"<td>{f['tamano_display']}</td></tr>"
            for f in analysis["archivos_mas_grandes"]
        )
        analysis_html = f"""
        <section>
          <h2>Análisis de espacio en disco</h2>
          <p class="muted">Ruta analizada: <strong>{_escape(analysis['ruta'])}</strong> ·
             {analysis['tamano_total_display']} en {format_integer(analysis['archivos'])} archivos ·
             {analysis['duracion_segundos']:.1f} s</p>
          <div class="grid">
            <div class="card"><h3>Carpetas y archivos más pesados</h3>
              <table><thead><tr><th>Elemento</th><th>Tipo</th><th>Tamaño</th><th>Elementos</th></tr></thead>
              <tbody>{folder_rows}</tbody></table>
            </div>
            <div class="card"><h3>Uso por categoría</h3>
              <table><thead><tr><th>Categoría</th><th>Tamaño</th></tr></thead>
              <tbody>{category_rows}</tbody></table>
            </div>
          </div>
          <div class="card"><h3>Archivos más grandes</h3>
            <table><thead><tr><th>Archivo</th><th>Tamaño</th></tr></thead><tbody>{file_rows}</tbody></table>
          </div>
        </section>"""

    cpu_temp = resumen.get("cpu_temperatura")
    temp_row = f"<li><span>Temperatura</span><strong>{cpu_temp:.0f} °C</strong></li>" if cpu_temp else ""

    # La cabecera sí lleva la paleta oscura (la identidad de la aplicación) y las barras
    # de categorías, los tres tonos de dato.
    grafito = DARK_COLORS
    claro_sup = LIGHT_SURFACES

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Informe {APP_NAME} · {report.get('generado_display', '')}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:32px; font-family:"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         background:{claro['bg']}; color:{claro['text']}; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:24px;
            background:{grafito['bg_alt']}; color:{grafito['text']}; padding:24px 28px;
            border-radius:18px; border:1px solid {grafito['border']}; }}
  header h1 {{ margin:0 0 6px; font-size:24px; letter-spacing:.2px; }}
  header p {{ margin:0; color:{grafito['text_muted']}; font-size:13px; }}
  .score {{ text-align:center; min-width:150px; }}
  .score b {{ display:block; font-size:42px; color:{health_color}; line-height:1; }}
  .score span {{ font-size:12px; color:{grafito['text_muted']}; text-transform:uppercase; letter-spacing:1px; }}
  section {{ margin-top:24px; }}
  h2 {{ font-size:16px; margin:0 0 12px; }}
  h3 {{ font-size:14px; margin:0 0 10px; }}
  .muted {{ color:{claro['text_dim']}; font-size:12px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  .card {{ background:{claro['panel']}; border:1px solid {claro['border_soft']};
           border-radius:14px; padding:18px; }}
  ul.kv {{ list-style:none; margin:0; padding:0; display:grid;
           grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:12px; }}
  ul.kv li {{ background:{claro['panel']}; border:1px solid {claro['border_soft']};
              border-radius:12px; padding:12px 14px;
              display:flex; justify-content:space-between; align-items:baseline; gap:8px; }}
  ul.kv span {{ color:{claro['text_dim']}; font-size:12px; }}
  ul.kv strong {{ font-size:15px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid {claro['border_soft']};
            vertical-align:top; }}
  th {{ color:{claro['text_dim']}; font-weight:600; font-size:11px; text-transform:uppercase;
        letter-spacing:.6px; }}
  tr:last-child td {{ border-bottom:none; }}
  .bar {{ height:8px; border-radius:6px; background:{claro['bg_alt']}; overflow:hidden;
          margin-bottom:4px; }}
  .bar span {{ display:block; height:100%; border-radius:6px; }}
  .w-140 {{ width:160px; }}
  .tag {{ font-size:11px; padding:2px 8px; border-radius:20px; }}
  .tag.done {{ background:{claro_sup['selected']}; color:{claro['good']}; }}
  .tag.pending {{ background:{claro['bg_alt']}; color:{claro['warn']}; }}
  footer {{ margin-top:28px; text-align:center; color:{claro['text_dim']}; font-size:12px; }}
  @media (max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Informe de mantenimiento · {APP_NAME}</h1>
      <p>Generado el {report.get('generado_display', '')} · {_escape(equipo.get('nombre_equipo', ''))} ·
         {_escape(equipo.get('sistema', ''))} · v{report.get('app', {}).get('version', '')}</p>
    </div>
    <div class="score"><b>{health:.0f}</b><span>{_escape(resumen.get('salud_etiqueta', ''))}</span></div>
  </header>

  <section>
    <h2>Resumen de rendimiento</h2>
    <ul class="kv">
      <li><span>CPU</span><strong>{resumen.get('cpu_porcentaje', 0):.1f} %</strong></li>
      {temp_row}
      <li><span>Memoria</span><strong>{resumen.get('ram_usada_display', '')} / {resumen.get('ram_total_display', '')}</strong></li>
      <li><span>Disco del sistema</span><strong>{resumen.get('disco_porcentaje', 0):.1f} %</strong></li>
      <li><span>Espacio libre</span><strong>{resumen.get('disco_libre_display', '')}</strong></li>
      <li><span>Procesos activos</span><strong>{resumen.get('procesos', 0)}</strong></li>
      <li><span>Red (bajada)</span><strong>{resumen.get('red_bajada_display', '')}</strong></li>
      <li><span>Encendido desde</span><strong>{resumen.get('encendido_desde', '')}</strong></li>
    </ul>
  </section>

  <section class="card">
    <h2>Unidades de almacenamiento</h2>
    <table><thead><tr><th>Unidad</th><th>Total</th><th>Usado</th><th>Libre</th><th>Ocupación</th></tr></thead>
    <tbody>{drive_rows}</tbody></table>
  </section>

  {analysis_html}

  <section class="grid">
    <div class="card">
      <h2>Tareas de mantenimiento</h2>
      <ul class="kv">
        <li><span>Total</span><strong>{stats.get('total', 0)}</strong></li>
        <li><span>Pendientes</span><strong>{stats.get('pendientes', 0)}</strong></li>
        <li><span>Completadas</span><strong>{stats.get('completadas', 0)}</strong></li>
        <li><span>Vencidas</span><strong>{stats.get('vencidas', 0)}</strong></li>
      </ul>
    </div>
    <div class="card">
      <h2>Equipo</h2>
      <ul class="kv">
        <li><span>Procesador</span><strong>{_escape(equipo.get('procesador', ''))}</strong></li>
        <li><span>Núcleos / hilos</span><strong>{equipo.get('nucleos_fisicos', 0)} / {equipo.get('procesadores_logicos', 0)}</strong></li>
        <li><span>Arquitectura</span><strong>{_escape(equipo.get('arquitectura', ''))}</strong></li>
        <li><span>Versión de Windows</span><strong>{_escape(equipo.get('version', ''))}</strong></li>
      </ul>
    </div>
  </section>

  <section class="card">
    <h2>Registro de tareas</h2>
    <table><thead><tr><th>Tarea</th><th>Categoría</th><th>Prioridad</th><th>Estado</th><th>Creada</th><th>Vence</th></tr></thead>
    <tbody>{task_rows}</tbody></table>
  </section>

  <section class="card">
    <h2>Procesos con más consumo</h2>
    <table><thead><tr><th>Proceso</th><th>CPU</th><th>Memoria</th></tr></thead><tbody>{proc_rows}</tbody></table>
  </section>

  <footer>Informe generado automáticamente por {APP_NAME} {report.get('app', {}).get('version', '')}</footer>
</div>
</body>
</html>"""


def _escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def default_filename(fmt: str, prefix: str = "informe_pc") -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return f"{prefix}_{stamp}{EXTENSIONS.get(fmt, '.txt')}"


def unique_path(directory: str | Path, filename: str) -> Path:
    """Evita sobrescribir: añade _1, _2… si el archivo ya existe."""
    target = Path(directory) / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    index = 1
    while True:
        candidate = target.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def summary_lines(report: dict[str, Any]) -> Iterable[str]:
    """Resumen corto en texto, útil para la vista previa y la consola."""
    resumen = report.get("resumen", {})
    yield f"Informe {APP_NAME} · {report.get('generado_display', '')}"
    yield f"Equipo: {report.get('equipo', {}).get('nombre_equipo', '')} ({report.get('equipo', {}).get('sistema', '')})"
    yield f"Salud del sistema: {resumen.get('salud', 0)}/100 ({resumen.get('salud_etiqueta', '')})"
    yield f"CPU: {resumen.get('cpu_porcentaje', 0)}% · RAM: {resumen.get('ram_porcentaje', 0)}% · Disco: {resumen.get('disco_porcentaje', 0)}%"
    yield f"Unidades incluidas: {len(report.get('unidades', []))}"
    yield f"Tareas registradas: {report.get('tareas', {}).get('estadisticas', {}).get('total', 0)}"
    if report.get("analisis_disco"):
        yield f"Análisis de disco: {report['analisis_disco']['ruta']} → {report['analisis_disco']['tamano_total_display']}"
