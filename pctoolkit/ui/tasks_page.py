"""Página del registro de tareas de mantenimiento."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.config import COLORS
from ..core.tasks import PRIORITIES, STATUS_DONE, TASK_CATEGORIES, TEMPLATES, MaintenanceTask
from . import icons
from .widgets import Card, IconBadge, Muted, PageHeader, SectionTitle, hline

PRIORITY_COLORS = {"Alta": COLORS["bad"], "Normal": COLORS["accent"], "Baja": COLORS["text_muted"]}


class TaskDialog(QDialog):
    """Formulario para crear o editar una tarea de mantenimiento."""

    def __init__(self, parent: QWidget | None = None, task: MaintenanceTask | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar tarea" if task else "Nueva tarea de mantenimiento")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Editar tarea" if task else "Nueva tarea")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        layout.addWidget(Muted("Documenta el mantenimiento para llevar un historial del equipo."))

        form = QFormLayout()
        form.setSpacing(10)

        self.title_edit = QLineEdit(task.title if task else "")
        self.title_edit.setPlaceholderText("Por ejemplo: limpiar el polvo del ventilador")
        form.addRow("Título", self.title_edit)

        self.category_combo = QComboBox()
        self.category_combo.addItems(TASK_CATEGORIES)
        if task:
            self.category_combo.setCurrentText(task.category)
        form.addRow("Categoría", self.category_combo)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(PRIORITIES)
        if task:
            self.priority_combo.setCurrentText(task.priority)
        form.addRow("Prioridad", self.priority_combo)

        date_row = QWidget()
        date_layout = QHBoxLayout(date_row)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.setSpacing(8)
        self.no_date_check = QCheckBox("Sin fecha límite")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setDate(QDate.currentDate().addDays(7))
        if task and task.due_date:
            self.date_edit.setDate(QDate.fromString(task.due_date, Qt.ISODate))
        else:
            self.no_date_check.setChecked(True if not task else not task.due_date)
        self.no_date_check.toggled.connect(lambda checked: self.date_edit.setEnabled(not checked))
        self.date_edit.setEnabled(not self.no_date_check.isChecked())
        date_layout.addWidget(self.date_edit, 1)
        date_layout.addWidget(self.no_date_check)
        form.addRow("Fecha límite", date_row)

        self.notes_edit = QTextEdit(task.notes if task else "")
        self.notes_edit.setPlaceholderText("Notas, pasos realizados, resultado…")
        self.notes_edit.setFixedHeight(90)
        form.addRow("Notas", self.notes_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "Falta el título", "Escribe un título para la tarea.")
            return
        self.accept()

    def values(self) -> dict[str, str]:
        due = "" if self.no_date_check.isChecked() else self.date_edit.date().toString(Qt.ISODate)
        return {
            "title": self.title_edit.text().strip(),
            "category": self.category_combo.currentText(),
            "priority": self.priority_combo.currentText(),
            "notes": self.notes_edit.toPlainText().strip(),
            "due_date": due,
        }


class TaskRow(Card):
    """Fila con casilla de estado, detalles y acciones."""

    toggled = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, task: MaintenanceTask, parent: QWidget | None = None) -> None:
        super().__init__(parent, soft=True, name=task.title)
        self.task = task
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        self.check_button = QPushButton()
        self.check_button.setFixedSize(30, 30)
        self.check_button.setCursor(Qt.PointingHandCursor)
        self.check_button.setObjectName("CheckRound")
        color = COLORS["good"] if task.is_done else COLORS["text_dim"]
        self.check_button.setIcon(icons.icon("check", 16, color))
        self.check_button.setStyleSheet(
            "QPushButton { border: 1px solid %s; border-radius: 15px; background: %s; }"
            "QPushButton:hover { border-color: %s; }"
            % (
                COLORS["good"] if task.is_done else COLORS["border"],
                COLORS["good"] + "22" if task.is_done else "transparent",
                COLORS["good"],
            )
        )
        self.check_button.setToolTip("Marcar como pendiente" if task.is_done else "Marcar como completada")
        self.check_button.clicked.connect(lambda: self.toggled.emit(task.id))
        layout.addWidget(self.check_button, 0, Qt.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        title = QLabel(task.title)
        title.setWordWrap(True)
        style = "font-size: 13px; font-weight: 600;"
        if task.is_done:
            style += f" color: {COLORS['text_dim']}; text-decoration: line-through;"
        title.setStyleSheet(style)
        texts.addWidget(title)

        if task.notes:
            notes = QLabel(task.notes)
            notes.setObjectName("Muted")
            notes.setWordWrap(True)
            texts.addWidget(notes)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addWidget(self._pill(task.category, COLORS["accent"]))
        meta.addWidget(self._pill(task.priority, PRIORITY_COLORS.get(task.priority, COLORS["accent"])))
        if task.is_overdue:
            meta.addWidget(self._pill("Vencida", COLORS["bad"]))
        elif task.due_date:
            meta.addWidget(self._pill(f"Vence {task.due_display}", COLORS["text_muted"]))
        created = QLabel(f"Creada {task.created_display}")
        created.setObjectName("Dim")
        meta.addWidget(created)
        if task.is_done and task.completed_display:
            done = QLabel(f"Completada {task.completed_display}")
            done.setObjectName("Dim")
            meta.addWidget(done)
        meta.addStretch(1)
        texts.addLayout(meta)
        layout.addLayout(texts, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        edit = QPushButton()
        edit.setObjectName("Ghost")
        edit.setIcon(icons.icon("report", 15, COLORS["text_muted"]))
        edit.setToolTip("Editar tarea")
        edit.setFixedSize(32, 32)
        edit.clicked.connect(lambda: self.edit_requested.emit(task.id))
        actions.addWidget(edit)

        remove = QPushButton()
        remove.setObjectName("Danger")
        remove.setIcon(icons.icon("trash", 15, COLORS["bad"]))
        remove.setToolTip("Eliminar tarea")
        remove.setFixedSize(32, 32)
        remove.clicked.connect(lambda: self.delete_requested.emit(task.id))
        actions.addWidget(remove)
        layout.addLayout(actions, 0)

    def _pill(self, text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"background: {color}1F; color: {color}; border-radius: 9px;"
            f"padding: 2px 8px; font-size: 11px;"
        )
        return label


class TaskStat(Card):
    """Métrica resumida del registro de tareas."""

    def __init__(self, icon_name: str, title: str, value: str, color: str, parent=None) -> None:
        super().__init__(parent, soft=True, name=title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(IconBadge(icon_name, 26, 14, color))
        label = QLabel(title)
        label.setObjectName("Muted")
        top.addWidget(label)
        top.addStretch(1)
        layout.addLayout(top)
        value_label = QLabel(value)
        value_label.setStyleSheet(f"font-size: 22px; font-weight: 600; color: {color};")
        layout.addWidget(value_label)


class TasksPage(QWidget):
    """Registro de tareas de mantenimiento del equipo."""

    status = Signal(str)

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.store.changed.connect(self.refresh)
        self._build()
        self.refresh()

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
            "Registro de mantenimiento",
            "Planifica y documenta las tareas de cuidado de tu equipo.",
        )
        templates_button = QPushButton("Añadir plantilla")
        templates_button.setObjectName("Ghost")
        templates_button.setIcon(icons.icon("list", 15))
        templates_button.clicked.connect(self._show_templates)
        header.add_action(templates_button)

        self.clear_button = QPushButton("Limpiar completadas")
        self.clear_button.setObjectName("Ghost")
        self.clear_button.clicked.connect(self._clear_completed)
        header.add_action(self.clear_button)

        new_button = QPushButton("Nueva tarea")
        new_button.setObjectName("Primary")
        new_button.setIcon(icons.icon("calendar", 15, COLORS["on_accent"]))
        new_button.clicked.connect(self._new_task)
        header.add_action(new_button)
        layout.addWidget(header)

        self.stats_container = QWidget()
        self.stats_layout = QHBoxLayout(self.stats_container)
        self.stats_layout.setContentsMargins(0, 0, 0, 0)
        self.stats_layout.setSpacing(12)
        layout.addWidget(self.stats_container)

        filters = Card(name="Filtros")
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(16, 12, 16, 12)
        filters_layout.setSpacing(10)
        filters_layout.addWidget(IconBadge("analyze", 26, 14, COLORS["text_dim"]))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar en el historial…")
        self.search.textChanged.connect(self.refresh)
        filters_layout.addWidget(self.search, 1)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["Todas", "Pendientes", "Completadas", "Vencidas"])
        self.status_combo.currentIndexChanged.connect(self.refresh)
        filters_layout.addWidget(self.status_combo)

        self.category_combo = QComboBox()
        self.category_combo.addItem("Todas las categorías", "")
        for category in TASK_CATEGORIES:
            self.category_combo.addItem(category, category)
        self.category_combo.currentIndexChanged.connect(self.refresh)
        filters_layout.addWidget(self.category_combo)
        layout.addWidget(filters)

        self.list_card = Card(name="Tareas")
        list_layout = QVBoxLayout(self.list_card)
        list_layout.setContentsMargins(18, 16, 18, 18)
        list_layout.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(SectionTitle("Tareas"))
        self.count_label = QLabel()
        self.count_label.setObjectName("Dim")
        head.addWidget(self.count_label)
        head.addStretch(1)
        list_layout.addLayout(head)
        self.tasks_box = QVBoxLayout()
        self.tasks_box.setSpacing(10)
        list_layout.addLayout(self.tasks_box)
        layout.addWidget(self.list_card)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    def _new_task(self) -> None:
        dialog = TaskDialog(self)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            self.store.add(**values)
            self.status.emit(f"Tarea añadida: {values['title']}")

    def _edit_task(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task:
            return
        dialog = TaskDialog(self, task)
        if dialog.exec() == QDialog.Accepted:
            self.store.update(task_id, **dialog.values())
            self.status.emit(f"Tarea actualizada: {dialog.values()['title']}")

    def _delete_task(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task:
            return
        answer = QMessageBox.question(
            self,
            "Eliminar tarea",
            f"¿Eliminar «{task.title}» del registro?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.store.remove(task_id)
            self.status.emit("Tarea eliminada.")

    def _clear_completed(self) -> None:
        pending_count = len(self.store.completed())
        if not pending_count:
            QMessageBox.information(self, "Sin cambios", "No hay tareas completadas que limpiar.")
            return
        answer = QMessageBox.question(
            self,
            "Limpiar completadas",
            f"Se eliminarán {pending_count} tareas completadas del registro. ¿Continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            removed = self.store.clear_completed()
            self.status.emit(f"{removed} tareas completadas eliminadas.")

    def _show_templates(self) -> None:
        menu = QMenu(self)
        for title, category, notes in TEMPLATES:
            action = menu.addAction(f"{title}  ·  {category}")
            action.triggered.connect(
                lambda _=False, t=title, c=category, n=notes: self._add_template(t, c, n)
            )
        menu.exec(self.mapToGlobal(self.rect().topRight()))

    def _add_template(self, title: str, category: str, notes: str) -> None:
        self.store.add_template(title, category, notes)
        self.status.emit(f"Plantilla añadida: {title}")

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_stats()
        self._refresh_list()

    def _refresh_stats(self) -> None:
        while self.stats_layout.count():
            item = self.stats_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        stats = self.store.stats()
        tiles = [
            ("calendar", "Pendientes", str(stats["pendientes"]), COLORS["accent"]),
            ("check", "Completadas", str(stats["completadas"]), COLORS["good"]),
            ("warn", "Vencidas", str(stats["vencidas"]), COLORS["warn"] if stats["vencidas"] else COLORS["text_muted"]),
            ("gauge", "Progreso", f"{stats['porcentaje']:.0f}%", COLORS["magenta"]),
        ]
        for icon_name, title, value, color in tiles:
            self.stats_layout.addWidget(TaskStat(icon_name, title, value, color))

    def _refresh_list(self) -> None:
        while self.tasks_box.count():
            item = self.tasks_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        query = self.search.text().strip().lower()
        status_filter = self.status_combo.currentText()
        category_filter = self.category_combo.currentData()

        tasks = self.store.all()
        if status_filter == "Pendientes":
            tasks = [task for task in tasks if not task.is_done]
        elif status_filter == "Completadas":
            tasks = [task for task in tasks if task.is_done]
        elif status_filter == "Vencidas":
            tasks = [task for task in tasks if task.is_overdue]
        if category_filter:
            tasks = [task for task in tasks if task.category == category_filter]
        if query:
            tasks = [
                task
                for task in tasks
                if query in task.title.lower() or query in task.notes.lower()
            ]

        # Orden: pendientes primero, después las vencidas, por fecha límite y,
        # a igualdad, las creadas más recientemente.
        def sort_key(task: MaintenanceTask) -> tuple:
            try:
                created_ts = -datetime.fromisoformat(task.created_at).timestamp()
            except ValueError:
                created_ts = 0.0
            return (task.is_done, 0 if task.is_overdue else 1, task.due_date or "9999-12-31", created_ts)

        tasks.sort(key=sort_key)

        self.count_label.setText(f"{len(tasks)} de {len(self.store.all())}")

        if not tasks:
            empty = Card(name="Sin tareas")
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(18, 22, 18, 22)
            message = QLabel(
                "No hay tareas que coincidan con el filtro.\n"
                "Usa «Nueva tarea» o «Añadir plantilla» para empezar a registrar el mantenimiento."
            )
            message.setObjectName("Muted")
            message.setAlignment(Qt.AlignCenter)
            empty_layout.addWidget(message)
            self.tasks_box.addWidget(empty)
            return

        for task in tasks:
            row = TaskRow(task)
            row.toggled.connect(self._toggle)
            row.edit_requested.connect(self._edit_task)
            row.delete_requested.connect(self._delete_task)
            self.tasks_box.addWidget(row)

    def _toggle(self, task_id: str) -> None:
        task = self.store.toggle_done(task_id)
        if task:
            state = "completada" if task.is_done else "pendiente"
            self.status.emit(f"«{task.title}» marcada como {state}.")
