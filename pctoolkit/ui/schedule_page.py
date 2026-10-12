"""Página «Mantenimiento programado»: alta y control de la tarea de Windows.

Windows guarda la tarea en `\\PCToolkit\\` y la ejecuta como el usuario actual,
sin pedir contraseña y sin privilegios elevados. Aquí se configura qué hace y
cuándo, se muestra el estado real leído del Programador de tareas (próxima
ejecución, último resultado…) y se puede lanzar o eliminar en un clic.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import maintenance as maintenance_core
from ..core import scheduler as scheduler_core
from ..core.config import COLORS, format_bytes
from ..core.settings import AppSettings
from . import icons
from .widgets import Card, IconBadge, Muted, PageHeader, SectionTitle, hline, kv_row
from .workers import run_in_background

STATUS_ROWS = (
    ("registrada", "Registrada"),
    ("estado", "Estado"),
    ("proxima", "Próxima ejecución"),
    ("ultima", "Última ejecución"),
    ("resultado", "Resultado"),
    ("perdidas", "Ejecuciones perdidas"),
)


class MaintenanceSchedulePage(QWidget):
    """Configura el mantenimiento automático de Windows."""

    status = Signal(str)
    schedule_changed = Signal(object)

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.schedule = settings.schedule_config
        self._task_status = scheduler_core.TaskStatus()
        self._busy = False
        # Consultar el Programador de tareas cuesta un proceso de PowerShell de
        # unos segundos: se hace al abrir la página, no al arrancar la aplicación
        # (muchas veces se abre solo para tenerla en la bandeja).
        self._status_loaded = False
        self._status_querying = False
        self._build()
        self._load_schedule()
        self._show_status_pending()

    # --- Construcción ----------------------------------------------------
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
            "Mantenimiento programado",
            "Windows ejecutará las tareas de PC Toolkit aunque la aplicación esté cerrada.",
        )
        self.refresh_button = QPushButton("Actualizar estado")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.setIcon(icons.icon("restore", 15))
        self.refresh_button.clicked.connect(self.refresh_status)
        header.add_action(self.refresh_button)
        self.save_button = QPushButton("Guardar y programar")
        self.save_button.setObjectName("Primary")
        self.save_button.setIcon(icons.icon("calendar", 15, COLORS["on_accent"]))
        self.save_button.clicked.connect(self.apply_schedule)
        header.add_action(self.save_button)
        layout.addWidget(header)

        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(self._form_card())
        top.addWidget(self._status_card())
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(self._history_card(), 3)
        bottom.addWidget(self._explain_card(), 2)
        layout.addLayout(bottom)
        layout.addStretch(1)

    def _form_card(self) -> Card:
        card = Card(name="Cuándo y qué hacer")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        layout.addWidget(SectionTitle("Cuándo y qué hacer"))
        self.enabled_check = QCheckBox("Activar mantenimiento programado")
        self.enabled_check.setChecked(self.schedule.enabled)
        layout.addWidget(self.enabled_check)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)

        self.frequency_combo = QComboBox()
        for frequency in scheduler_core.FREQUENCIES:
            self.frequency_combo.addItem(frequency)
        self.frequency_combo.currentTextChanged.connect(self._on_frequency_changed)
        grid.addWidget(QLabel("Frecuencia"), 0, 0)
        grid.addWidget(self.frequency_combo, 0, 1)

        self.weekday_combo = QComboBox()
        for day in scheduler_core.WEEKDAYS:
            self.weekday_combo.addItem(day)
        grid.addWidget(QLabel("Día de la semana"), 1, 0)
        grid.addWidget(self.weekday_combo, 1, 1)

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(QTime(12, 0))
        grid.addWidget(QLabel("Hora"), 2, 0)
        grid.addWidget(self.time_edit, 2, 1)

        self.age_spin = QDoubleSpinBox()
        self.age_spin.setRange(0, 720)
        self.age_spin.setDecimals(0)
        self.age_spin.setSuffix(" h")
        self.age_spin.setToolTip("Solo se borran temporales con más antigüedad que esta.")
        grid.addWidget(QLabel("Antigüedad mínima"), 3, 0)
        grid.addWidget(self.age_spin, 3, 1)

        self.clean_check = QCheckBox("Limpiar archivos temporales")
        self.memory_check = QCheckBox("Liberar memoria RAM")
        self.report_check = QCheckBox("Generar un informe del equipo")
        self.log_check = QCheckBox("Anotar la ejecución en el registro de tareas")
        for row, widget in enumerate((self.clean_check, self.memory_check, self.report_check, self.log_check)):
            grid.addWidget(widget, 4 + row, 0, 1, 2)
        layout.addLayout(grid)

        self.preview_label = QLabel("")
        self.preview_label.setObjectName("Muted")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.disable_button = QPushButton("Desactivar tarea")
        self.disable_button.setObjectName("Ghost")
        self.disable_button.clicked.connect(lambda: self._toggle_task(False))
        self.enable_button = QPushButton("Activar tarea")
        self.enable_button.setObjectName("Ghost")
        self.enable_button.clicked.connect(lambda: self._toggle_task(True))
        self.delete_button = QPushButton("Eliminar tarea")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self.remove_task)
        actions.addWidget(self.enable_button)
        actions.addWidget(self.disable_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return card

    def _status_card(self) -> Card:
        card = Card(name="Estado en Windows")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconBadge("calendar", 30, 16, COLORS["accent"]))
        head.addWidget(SectionTitle("Estado en Windows"))
        head.addStretch(1)
        self.state_badge = QLabel("—")
        self.state_badge.setObjectName("HealthBadge")
        head.addWidget(self.state_badge)
        layout.addLayout(head)

        self.status_rows: dict[str, QLabel] = {}
        for key, label in STATUS_ROWS:
            row, value = kv_row(label, "—")
            self.status_rows[key] = value
            layout.addWidget(row)

        layout.addWidget(hline())
        layout.addWidget(QLabel("Comando registrado"))
        self.command_label = QLabel("—")
        self.command_label.setObjectName("Dim")
        self.command_label.setWordWrap(True)
        self.command_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.command_label)

        self.notice_label = QLabel("")
        self.notice_label.setObjectName("Muted")
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.run_button = QPushButton("Ejecutar ahora")
        self.run_button.setObjectName("Ghost")
        self.run_button.setIcon(icons.icon("analyze", 15))
        self.run_button.clicked.connect(self.run_now)
        self.open_button = QPushButton("Abrir el Programador de tareas")
        self.open_button.setObjectName("Ghost")
        self.open_button.setIcon(icons.icon("tool", 15))
        self.open_button.clicked.connect(self._open_windows_scheduler)
        actions.addWidget(self.run_button)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return card

    def _history_card(self) -> Card:
        card = Card(name="Últimas ejecuciones")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(SectionTitle("Últimas ejecuciones"))
        head.addStretch(1)
        self.history_hint = QLabel("")
        self.history_hint.setObjectName("Dim")
        head.addWidget(self.history_hint)
        layout.addLayout(head)

        self.history_list = QListWidget()
        self.history_list.setSelectionMode(QListWidget.NoSelection)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_list.setMinimumHeight(220)
        layout.addWidget(self.history_list)
        self.reload_history()
        return card

    def _explain_card(self) -> Card:
        card = Card(name="Cómo funciona")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        layout.addWidget(SectionTitle("Cómo funciona"))
        for text in (
            "La tarea se guarda en la carpeta «PCToolkit» del Programador de tareas de Windows.",
            "Se ejecuta como tu usuario, sin contraseña y sin pedir permisos de administrador.",
            "Si el equipo estaba apagado a la hora prevista, Windows la ejecuta al encenderlo.",
            "Cada ejecución queda anotada en el histórico y, si quieres, en el registro de tareas.",
            "Puedes ver el informe generado en la carpeta «Informes» de tus documentos.",
        ):
            item = QHBoxLayout()
            item.setSpacing(8)
            item.addWidget(IconBadge("check", 20, 12, COLORS["accent"]))
            label = Muted(text)
            label.setWordWrap(True)
            item.addWidget(label, 1)
            layout.addLayout(item)
        layout.addStretch(1)
        return card

    # --- Sincronización con la configuración ------------------------------
    def _load_schedule(self) -> None:
        schedule = self.schedule
        self.enabled_check.setChecked(schedule.enabled)
        index = self.frequency_combo.findText(schedule.frequency)
        self.frequency_combo.setCurrentIndex(max(0, index))
        day_index = self.weekday_combo.findText(schedule.weekday)
        self.weekday_combo.setCurrentIndex(max(0, day_index))
        hour, minute = scheduler_core.normalize_time(schedule.time).split(":")
        self.time_edit.setTime(QTime(int(hour), int(minute)))
        self.age_spin.setValue(float(schedule.temp_min_age_hours))
        self.clean_check.setChecked(schedule.clean_temp)
        self.memory_check.setChecked(schedule.release_memory)
        self.report_check.setChecked(schedule.export_report)
        self.log_check.setChecked(schedule.auto_log_task)
        self._update_enabled_widgets()

    def _collect_schedule(self) -> scheduler_core.MaintenanceSchedule:
        return scheduler_core.MaintenanceSchedule(
            enabled=self.enabled_check.isChecked(),
            frequency=self.frequency_combo.currentText(),
            time=self.time_edit.time().toString("HH:mm"),
            weekday=self.weekday_combo.currentText(),
            clean_temp=self.clean_check.isChecked(),
            temp_min_age_hours=float(self.age_spin.value()),
            release_memory=self.memory_check.isChecked(),
            export_report=self.report_check.isChecked(),
            auto_log_task=self.log_check.isChecked(),
        ).normalized()

    def _on_frequency_changed(self, _text: str) -> None:
        self._update_enabled_widgets()

    def _update_enabled_widgets(self) -> None:
        frequency = self.frequency_combo.currentText()
        weekly = frequency == scheduler_core.FREQ_WEEKLY
        self.weekday_combo.setEnabled(weekly)
        self.time_edit.setEnabled(frequency != scheduler_core.FREQ_LOGON)
        schedule = self._collect_schedule()
        estado = "Activado" if schedule.enabled else "Desactivado"
        self.preview_label.setText(
            f"{estado}: {schedule.describe()}"
            + ("  ·  Al guardar se volverá a programar en Windows." if self._task_status.exists else "")
        )

    # --- Operaciones ------------------------------------------------------
    def apply_schedule(self) -> None:
        """Guarda la configuración y crea (o actualiza) la tarea en Windows."""
        schedule = self._collect_schedule()
        if not schedule.enabled:
            schedule.enabled = True
            self.enabled_check.setChecked(True)
        if not schedule.task_list():
            self.status.emit("Activa al menos un paso del mantenimiento.")
            return
        self.settings.schedule_config = schedule
        self.settings.sync()
        self.schedule = schedule
        self.schedule_changed.emit(schedule)

        self._set_busy(True, "Programando la tarea en Windows…")
        run_in_background(self, lambda: scheduler_core.create_task(schedule), self._after_operation)

    def remove_task(self) -> None:
        self._set_busy(True, "Eliminando la tarea…")
        run_in_background(self, scheduler_core.delete_task, self._after_delete)

    def _toggle_task(self, enabled: bool) -> None:
        self._set_busy(True, "Cambiando el estado de la tarea…")
        run_in_background(self, lambda: scheduler_core.set_task_enabled(enabled), self._after_operation)

    def run_now(self) -> None:
        self._set_busy(True, "Lanzando el mantenimiento…")
        run_in_background(self, scheduler_core.run_task_now, self._after_run_now)

    def ensure_status(self) -> None:
        """Consulta el estado la primera vez que se abre la página.

        Así el arranque no lanza PowerShell por una página que puede que nadie
        mire, y el estado se sigue leyendo de verdad cuando hace falta.
        """
        if self._status_loaded or self._status_querying:
            return
        self.refresh_status()

    def refresh_status(self) -> None:
        """Lee el estado real de la tarea (PowerShell, en segundo plano)."""
        self._status_loaded = True
        self._status_querying = True
        self.state_badge.setText("Consultando…")
        self.state_badge.setStyleSheet(
            f"#HealthBadge {{ background: {COLORS['text_muted']}22; color: {COLORS['text_muted']}; "
            f"border: 1px solid {COLORS['text_muted']}55; }}"
        )
        self.notice_label.setText("Leyendo el Programador de tareas… puede tardar unos segundos.")
        self._set_busy(True, "Consultando el Programador de tareas…")
        run_in_background(self, scheduler_core.task_status, self._after_status)

    @property
    def status_loaded(self) -> bool:
        """Si ya se ha consultado el estado real de la tarea (aunque sea sin crearla)."""
        return self._status_loaded and not self._status_querying

    @property
    def status_querying(self) -> bool:
        """Si hay una consulta del estado en curso."""
        return self._status_querying

    def _show_status_pending(self) -> None:
        """Estado inicial, antes de consultar nada: lo que se ve y por qué.

        Se dice con claridad que todavía no se ha preguntado a Windows, para que
        nadie crea que el mantenimiento no está programado por ver «—».
        """
        for key, _label in STATUS_ROWS:
            self.status_rows[key].setText("—")
        self.state_badge.setText("Sin consultar")
        self.state_badge.setStyleSheet(
            f"#HealthBadge {{ background: {COLORS['text_muted']}22; color: {COLORS['text_muted']}; "
            f"border: 1px solid {COLORS['text_muted']}55; }}"
        )
        self.command_label.setText(scheduler_core.command_line(self.schedule))
        self.notice_label.setText(
            "El estado se lee del Programador de tareas al abrir esta página (tarda unos segundos)."
        )
        self._set_busy(False)

    def _after_operation(self, result) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            self.status.emit(f"Error al programar la tarea: {result}")
            return
        self.status.emit(result.message)
        if result.ok:
            self.schedule.enabled = True
            self.settings.schedule_config = self.schedule
            self.settings.sync()
        self.refresh_status()

    def _after_delete(self, result) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            self.status.emit(f"Error al eliminar la tarea: {result}")
            return
        self.status.emit(result.message)
        if result.ok:
            self.schedule.enabled = False
            self.enabled_check.setChecked(False)
            self.settings.schedule_config = self.schedule
            self.settings.sync()
        self.refresh_status()

    def _after_run_now(self, result) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            self.status.emit(f"No se pudo lanzar el mantenimiento: {result}")
            return
        self.status.emit(
            result.message + " Tarda unos segundos; pulsa «Actualizar estado» para ver el resultado."
        )

    def _after_status(self, result) -> None:
        self._status_querying = False
        self._set_busy(False)
        if isinstance(result, Exception):
            # Se deja marcado como no consultado: al volver a entrar en la página
            # se reintenta, y mientras el botón «Actualizar estado» sigue ahí.
            self._status_loaded = False
            self.state_badge.setText("Sin consultar")
            self.notice_label.setText(f"No se pudo leer el estado: {result}")
            self.status.emit(f"No se pudo consultar la tarea: {result}")
            return
        self._task_status = result
        self._render_status(result)
        self.reload_history()

    def _render_status(self, status: scheduler_core.TaskStatus) -> None:
        rows = self.status_rows
        rows["registrada"].setText("Sí" if status.exists else "No")
        rows["estado"].setText(status.state or "—")
        rows["proxima"].setText(status.next_run_display)
        rows["ultima"].setText(status.last_run_display)
        rows["resultado"].setText(status.last_result_text)
        rows["perdidas"].setText(str(status.missed_runs))
        self.command_label.setText(status.command or scheduler_core.command_line(self.schedule))

        if status.exists and status.enabled:
            text, color = "Programada", COLORS["good"]
        elif status.exists:
            text, color = "Desactivada", COLORS["warn"]
        else:
            text, color = "Sin programar", COLORS["text_muted"]
        self.state_badge.setText(text)
        self.state_badge.setStyleSheet(
            f"#HealthBadge {{ background: {color}22; color: {color}; border: 1px solid {color}55; }}"
        )

        if not status.exists:
            self.notice_label.setText(
                status.message or "Pulsa «Guardar y programar» para crear la tarea en Windows."
            )
        else:
            expected = scheduler_core.command_line(self.schedule)
            registered = status.command or ""
            if registered and registered.strip() != expected.strip() and os.path.sep in registered:
                self.notice_label.setText(
                    "El comando registrado no coincide con la ubicación actual de la aplicación "
                    "(¿la has movido?). Vuelve a guardar para corregirlo."
                )
            else:
                self.notice_label.setText("Todo listo: Windows se encargará del mantenimiento a su hora.")

        self.enable_button.setEnabled(status.exists)
        self.disable_button.setEnabled(status.exists)
        self.delete_button.setEnabled(status.exists)
        self.run_button.setEnabled(status.exists)
        self._update_enabled_widgets()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.save_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        exists = self._task_status.exists
        for button in (self.run_button, self.enable_button, self.disable_button, self.delete_button):
            button.setEnabled(exists and not busy)
        self.save_button.setText("Programando…" if busy else "Guardar y programar")
        if message:
            self.status.emit(message)

    def reload_history(self) -> None:
        """Muestra las últimas ejecuciones registradas por el mantenimiento."""
        runs = maintenance_core.recent_runs(12)
        self.history_list.clear()
        if not runs:
            self.history_list.addItem(
                QListWidgetItem("Todavía no se ha ejecutado el mantenimiento automático.")
            )
            self.history_hint.setText("")
            return
        self.history_hint.setText(f"{len(runs)} ejecuciones registradas")
        for entry in runs:
            ok = bool(entry.get("correcto"))
            freed = int(entry.get("liberado_disco", 0)) + int(entry.get("liberado_memoria", 0))
            detail = entry.get("resumen", "")
            label = f"{entry.get('momento', '')} · {detail}"
            item = QListWidgetItem(
                icons.icon("check" if ok else "warn", 14, COLORS["good"] if ok else COLORS["warn"]),
                label,
            )
            steps = " · ".join(step.get("titulo", "") for step in entry.get("pasos", []))
            item.setToolTip(f"{detail}\n{steps}\n{format_bytes(freed)}")
            self.history_list.addItem(item)

    def _open_windows_scheduler(self) -> None:
        try:
            os.startfile("taskschd.msc")  # type: ignore[attr-defined]
            self.status.emit("Abriendo el Programador de tareas de Windows…")
        except (OSError, AttributeError):
            self.status.emit("No se pudo abrir el Programador de tareas de Windows.")
