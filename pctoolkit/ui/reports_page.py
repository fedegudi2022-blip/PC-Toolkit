"""Página de exportación de informes (HTML, CSV y JSON)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import reports as report_core
from ..core import windows_settings as tweaks_core
from ..core.config import COLORS, format_bytes, reports_dir
from ..core.disk import list_drives
from . import icons, theme
from .widgets import Card, IconBadge, Muted, PageHeader, SectionTitle, hline, kv_row


class FormatButton(QPushButton):
    """Botón de selección de formato."""

    def __init__(self, fmt: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fmt = fmt
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setText(f"{fmt}\n{description}")
        self.setMinimumHeight(62)
        self.setMinimumWidth(150)
        # El estilo va por `theme.styled` y no con los colores ya resueltos: estos botones se
        # construyen una vez, al abrir la página, y con el color escrito dentro se quedaban con
        # la paleta del tema que hubiera al arrancar (en claro, un botón oscuro sobre blanco).
        theme.styled(
            self,
            """
            QPushButton {{
                text-align: left; padding: 10px 14px; border-radius: 12px;
                background: {bg_alt}; border: 1px solid {border};
                color: {text_muted}; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {accent_soft}; color: {text}; }}
            QPushButton:checked {{
                background: {acento};
                border: 1px solid {accent};
                color: {text}; font-weight: 600;
            }}
            """,
            acento=theme.alpha_de("accent", 34),
        )


class ReportsPage(QWidget):
    """Genera y guarda informes del estado del equipo."""

    status = Signal(str)

    def __init__(self, metrics, task_store, disk_page, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.metrics = metrics
        self.task_store = task_store
        self.disk_page = disk_page
        self._formats: dict[str, FormatButton] = {}
        self._build()
        self.update_preview()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 22, 28, 26)
        layout.setSpacing(18)

        header = PageHeader(
            "Exportar informes",
            "Guarda un resumen del estado del equipo en el formato que necesites.",
        )
        self.open_folder_button = QPushButton("Abrir carpeta")
        self.open_folder_button.setObjectName("Ghost")
        self.open_folder_button.setIcon(icons.icon("folder", 15))
        self.open_folder_button.clicked.connect(self._open_reports_folder)
        header.add_action(self.open_folder_button)

        self.export_button = QPushButton("Generar informe")
        self.export_button.setObjectName("Primary")
        self.export_button.setIcon(icons.icon("download", 15, COLORS["on_accent"]))
        self.export_button.clicked.connect(self.export_now)
        header.add_action(self.export_button)
        layout.addWidget(header)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        middle.addWidget(self._options_card(), 3)
        middle.addWidget(self._preview_card(), 2)
        layout.addLayout(middle)

        layout.addWidget(self._history_card())
        layout.addStretch(1)

    def _options_card(self) -> Card:
        card = Card(name="Formato del informe")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(SectionTitle("Formato del informe"))

        formats = QHBoxLayout()
        formats.setSpacing(10)
        group = QButtonGroup(self)
        for fmt, description in (
            (report_core.FORMAT_HTML, "Informe visual"),
            (report_core.FORMAT_CSV, "Hoja de cálculo"),
            (report_core.FORMAT_JSON, "Datos estructurados"),
        ):
            button = FormatButton(fmt, description)
            group.addButton(button)
            formats.addWidget(button)
            self._formats[fmt] = button
        self._formats[report_core.FORMAT_HTML].setChecked(True)
        group.buttonClicked.connect(lambda _: self.update_preview())
        layout.addLayout(formats)

        layout.addWidget(hline())
        layout.addWidget(SectionTitle("Contenido incluido"))
        self.include_metrics = QCheckBox("Rendimiento del sistema (CPU, memoria, disco, red)")
        self.include_metrics.setChecked(True)
        self.include_drives = QCheckBox("Unidades de almacenamiento")
        self.include_drives.setChecked(True)
        self.include_disk = QCheckBox("Resultado del análisis de disco")
        self.include_disk.setChecked(True)
        self.include_tasks = QCheckBox("Registro de tareas de mantenimiento")
        self.include_tasks.setChecked(True)
        self.include_processes = QCheckBox("Procesos con más consumo")
        self.include_processes.setChecked(True)
        # Los cambios en los ajustes de Windows son del registro de la propia aplicación (qué
        # se analizó y qué se aplicó), así que van en su propia casilla y se pueden dejar fuera.
        self.include_tweaks = QCheckBox("Cambios en los ajustes de Windows")
        self.include_tweaks.setChecked(bool(tweaks_core.recent_changes(1)))
        self.include_tweaks.setToolTip(
            "El registro de análisis y cambios de la página «Ajustes de Windows» (con su fecha)."
        )
        for check in (
            self.include_metrics,
            self.include_drives,
            self.include_disk,
            self.include_tasks,
            self.include_processes,
            self.include_tweaks,
        ):
            check.toggled.connect(self.update_preview)
            layout.addWidget(check)

        layout.addWidget(hline())
        layout.addWidget(SectionTitle("Destino"))
        destination = QHBoxLayout()
        destination.setSpacing(8)
        self.folder_edit = QLineEdit(str(reports_dir()))
        self.folder_edit.textChanged.connect(self.update_preview)
        destination.addWidget(self.folder_edit, 1)
        browse = QPushButton("Cambiar…")
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._choose_folder)
        destination.addWidget(browse)
        layout.addLayout(destination)

        self.filename_edit = QLineEdit(report_core.default_filename(report_core.FORMAT_HTML))
        self.filename_edit.textChanged.connect(self.update_preview)
        layout.addWidget(self.filename_edit)
        layout.addWidget(Muted("Si el archivo ya existe se añadirá un número para no sobrescribirlo."))
        return card

    def _preview_card(self) -> Card:
        card = Card(name="Vista previa del contenido")
        card.setMinimumWidth(300)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(SectionTitle("Vista previa del contenido"))

        self.preview_rows = QVBoxLayout()
        self.preview_rows.setSpacing(6)
        layout.addLayout(self.preview_rows)

        layout.addWidget(hline())
        title = QLabel("Resumen que se incluirá")
        title.setObjectName("Muted")
        layout.addWidget(title)
        self.summary_list = QListWidget()
        self.summary_list.setSelectionMode(QListWidget.NoSelection)
        self.summary_list.setMinimumHeight(190)
        self.summary_list.setWordWrap(True)
        self.summary_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.summary_list)
        layout.addStretch(1)
        return card

    def _history_card(self) -> Card:
        card = Card(name="Informes generados en esta sesión")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(SectionTitle("Informes generados en esta sesión"))
        head.addStretch(1)
        layout.addLayout(head)
        self.history_list = QListWidget()
        self.history_list.setFixedHeight(130)
        self.history_list.itemDoubleClicked.connect(self._open_history_item)
        layout.addWidget(self.history_list)
        layout.addWidget(Muted("Doble clic en un elemento para abrirlo."))
        return card

    # ------------------------------------------------------------------
    def _choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Carpeta destino", self.folder_edit.text())
        if selected:
            self.folder_edit.setText(selected)

    def selected_format(self) -> str:
        for fmt, button in self._formats.items():
            if button.isChecked():
                return fmt
        return report_core.FORMAT_HTML

    def _open_reports_folder(self) -> None:
        path = self.folder_edit.text().strip() or str(reports_dir())
        if os.path.isdir(path):
            self._open_path(path)
        else:
            QMessageBox.information(self, "Carpeta no disponible", f"La carpeta no existe:\n{path}")

    def _open_path(self, path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            QMessageBox.warning(self, "No se pudo abrir", str(exc))

    def _open_history_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path and os.path.exists(path):
            self._open_path(path)

    # ------------------------------------------------------------------
    def update_preview(self) -> None:
        while self.preview_rows.count():
            layout_item = self.preview_rows.takeAt(0)
            widget = layout_item.widget()
            if widget:
                widget.deleteLater()

        snapshot = self.metrics.snapshot
        drives = list_drives() if self.include_drives.isChecked() else []
        analysis = self.disk_page.last_result() if self.include_disk.isChecked() else None
        tasks = self.task_store.all() if self.include_tasks.isChecked() else []

        # Los valores se mantienen cortos: las rutas largas desbordan la tarjeta.
        analysis_value = "No incluido"
        if self.include_disk.isChecked():
            analysis_value = (
                f"{Path(analysis.root).name or analysis.root} · {format_bytes(analysis.total_size)}"
                if analysis
                else "Sin análisis reciente"
            )
        rows = [
            ("Salud del sistema", f"{snapshot.health_score:.0f}/100 · {snapshot.health_label}"),
            ("CPU y memoria", f"{snapshot.cpu_percent:.0f}% · {snapshot.ram_percent:.0f}%"),
            ("Disco del sistema", f"{snapshot.disk_percent:.0f}% · {format_bytes(snapshot.disk_free)} libres"),
            ("Unidades incluidas", str(len(drives)) if self.include_drives.isChecked() else "No incluidas"),
            ("Análisis de disco", analysis_value),
            ("Tareas registradas", str(len(tasks)) if self.include_tasks.isChecked() else "No incluidas"),
            (
                "Cambios en los ajustes",
                str(len(tweaks_core.recent_changes(40))) if self.include_tweaks.isChecked() else "No incluidos",
            ),
            ("Generado", datetime.now().strftime("%d/%m/%Y %H:%M")),
        ]
        for label, value in rows:
            row, value_label = kv_row(label, value)
            self.preview_rows.addWidget(row)

        fmt = self.selected_format()
        suggested = report_core.default_filename(fmt)
        if self.filename_edit.text().strip().endswith((".html", ".csv", ".json")):
            current = Path(self.filename_edit.text().strip())
            if current.suffix.lower() != report_core.EXTENSIONS[fmt]:
                self.filename_edit.setText(f"{current.stem}{report_core.EXTENSIONS[fmt]}")
        elif not self.filename_edit.text().strip():
            self.filename_edit.setText(suggested)

        report = report_core.build_report(
            snapshot=snapshot,
            system_info=self.metrics.system_info(),
            drives=drives,
            tasks=tasks,
            task_stats=self.task_store.stats(),
            disk_result=analysis,
        )
        self.summary_list.clear()
        for line in report_core.summary_lines(report):
            item = QListWidgetItem(icons.icon("check", 13, COLORS["good"]), line)
            item.setToolTip(line)
            self.summary_list.addItem(item)

    # ------------------------------------------------------------------
    def export_now(self) -> None:
        fmt = self.selected_format()
        folder = self.folder_edit.text().strip() or str(reports_dir())
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                QMessageBox.warning(self, "Carpeta no válida", str(exc))
                return

        filename = self.filename_edit.text().strip() or report_core.default_filename(fmt)
        if not Path(filename).suffix:
            filename += report_core.EXTENSIONS[fmt]
        filename = Path(filename).name
        try:
            target = report_core.unique_path(folder, filename)
        except OSError as exc:
            QMessageBox.warning(self, "Ruta no válida", str(exc))
            return

        snapshot = self.metrics.poll()
        report = report_core.build_report(
            snapshot=snapshot,
            system_info=self.metrics.system_info(),
            drives=list_drives(),
            tasks=self.task_store.to_dicts(),
            task_stats=self.task_store.stats(),
            disk_result=self.disk_page.last_result() if self.include_disk.isChecked() else None,
            drive_labels=self.disk_page.drive_labels(),
            windows_changes=tweaks_core.changes_for_report(40) if self.include_tweaks.isChecked() else None,
        )
        if not self.include_metrics.isChecked():
            report.pop("resumen", None)
        if not self.include_drives.isChecked():
            report.pop("unidades", None)
            if report.get("analisis_disco"):
                report["analisis_disco"].pop("unidades", None)
        if not self.include_tasks.isChecked():
            report.pop("tareas", None)
        if not self.include_processes.isChecked():
            report.pop("procesos_principales", None)

        try:
            written = report_core.export(report, target, fmt)
        except OSError as exc:
            QMessageBox.critical(self, "Error al guardar", f"No se pudo escribir el informe:\n{exc}")
            return

        size = written.stat().st_size
        item = QListWidgetItem(
            icons.icon("report", 13, COLORS["accent"]),
            f"{written.name} · {format_bytes(size)} · {datetime.now().strftime('%H:%M:%S')}",
        )
        item.setData(Qt.UserRole, str(written))
        self.history_list.insertItem(0, item)

        self.status.emit(f"Informe guardado en {written}")
        answer = QMessageBox.information(
            self,
            "Informe generado",
            f"El informe se guardó correctamente:\n\n{written}\n\nTamaño: {format_bytes(size)}",
            QMessageBox.Open | QMessageBox.Ok,
        )
        if answer == QMessageBox.Open:
            self._open_path(str(written))
