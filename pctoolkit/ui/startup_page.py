"""Página de programas que arrancan con Windows.

Lo importante de esta pantalla es que explique lo que hace: cada fila dice de
dónde sale la entrada, si arranca o no, si el programa sigue instalado y qué
pasa exactamente al desactivarla. Nada se borra —desactivar escribe la misma
marca que usa el Administrador de tareas— y cada cambio queda apuntado abajo
para poder deshacerlo sin tener que recordar nada.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import startup as startup_core
from ..core import undo as undo_core
from ..core.config import COLORS
from . import icons, theme
from .optimize_page import ElidedLabel, relaunch_elevated
from .widgets import Card, Chip, IconBadge, Muted, PageHeader, SectionTitle, hline
from .workers import run_in_background

KIND_ICONS = {
    startup_core.KIND_REGISTRO: "gear",
    startup_core.KIND_CARPETA: "folder",
    startup_core.KIND_TAREA: "calendar",
    startup_core.KIND_STORE: "cloud",
}

# El coste es una estimación del tipo de entrada, no una alerta: solo el «Medio»
# (una tarea programada, que Windows lanza aparte) pide atención. El rojo se dejó de
# usar aquí porque una entrada que ya no arranca no es un estado crítico, y su aviso
# propio («Ya no existe») ya lo dice en ámbar.
COST_COLORS = {
    startup_core.COST_MEDIO: COLORS["warn"],
    startup_core.COST_NULO: COLORS["text_dim"],
    startup_core.COST_LIGERO: COLORS["text_muted"],
}

MAX_UNDO_ROWS = 6


def pill(text: str, color: str, parent: QWidget | None = None) -> Chip:
    """Etiqueta redondeada y coloreada, como las del registro de tareas."""
    return Chip(text, color, parent)


class StartupRow(Card):
    """Una entrada de arranque, con su estado y su botón de activar/desactivar."""

    toggle_requested = Signal(object)

    def __init__(self, entry: startup_core.StartupEntry, parent: QWidget | None = None) -> None:
        # El nombre del grupo es el de la entrada: es lo que anuncia el lector al
        # entrar en la fila, antes de leer sus datos y su botón.
        super().__init__(parent, soft=True, name=entry.name)
        self.entry = entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 14, 12)
        layout.setSpacing(12)

        layout.addWidget(
            IconBadge(KIND_ICONS.get(entry.kind, "power"), 30, 16, COLORS["text_dim"])
        )

        texts = QVBoxLayout()
        texts.setSpacing(4)

        self.name_label = ElidedLabel(entry.name)
        self.name_label.setStyleSheet(
            "font-size: 13px; font-weight: 600;"
            + ("" if entry.enabled else f" color: {COLORS['text_muted']};")
        )
        self.name_label.setToolTip(f"{entry.name}\n{entry.location}")
        texts.addWidget(self.name_label)

        self.command_label = ElidedLabel(entry.display_command or entry.location)
        self.command_label.setObjectName("Dim")
        self.command_label.setToolTip(entry.command or entry.location)
        texts.addWidget(self.command_label)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addWidget(pill(entry.status_label, COLORS["accent"] if entry.enabled else COLORS["text_dim"]))
        cost, reason = entry.cost
        cost_pill = pill(cost, COST_COLORS.get(cost, COLORS["text_muted"]))
        cost_pill.setToolTip(reason)
        meta.addWidget(cost_pill)
        if entry.origin_folder:
            # Se recorta si no cabe: un nombre de carpeta largo no debe ensanchar
            # la fila hasta sacarla de la pantalla.
            self.folder_label = ElidedLabel(f"Programa: {entry.origin_folder}")
            self.folder_label.setObjectName("Dim")
            self.folder_label.setToolTip(entry.target_path or entry.origin_folder)
            meta.addWidget(self.folder_label, 1)
        if entry.problem:
            problem = pill("Ya no existe", COLORS["warn"])
            problem.setToolTip(f"{entry.problem}. Puedes desactivarla para que Windows no la intente abrir.")
            meta.addWidget(problem)
        if entry.needs_admin:
            shield = QLabel()
            theme.pixmap(shield, "shield", 14, "warn")
            shield.setToolTip("Es una entrada de todos los usuarios: cambiarla pide permisos de administrador.")
            meta.addWidget(shield)
        meta.addStretch(1)
        texts.addLayout(meta)
        layout.addLayout(texts, 1)

        self.toggle_button = QPushButton()
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        if entry.can_toggle:
            self.toggle_button.setObjectName("Chip")
            self.toggle_button.setText("Desactivar" if entry.enabled else "Activar")
            self.toggle_button.setToolTip(
                "Deja de abrirse al entrar en Windows. La entrada no se borra: se puede volver a activar."
                if entry.enabled
                else "Volverá a abrirse la próxima vez que entres en Windows."
            )
            self.toggle_button.clicked.connect(lambda: self.toggle_requested.emit(entry))
        else:
            self.toggle_button.setObjectName("Ghost")
            self.toggle_button.setText("Solo lectura")
            self.toggle_button.setEnabled(False)
            self.toggle_button.setToolTip(entry.note or "Windows ejecuta esta entrada sin poder desactivarla.")
        layout.addWidget(self.toggle_button, 0)


class SummaryTile(Card):
    """Cifra resumida de la cabecera."""

    def __init__(
        self,
        icon_name: str,
        title: str,
        value: str,
        color: str,
        note: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, soft=True, name=title)
        if note:
            self.setToolTip(note)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(IconBadge(icon_name, 26, 14, color))
        label = ElidedLabel(title)
        label.setObjectName("Muted")
        top.addWidget(label, 1)
        layout.addLayout(top)
        value_label = QLabel(value)
        value_label.setStyleSheet(f"font-size: 22px; font-weight: 600; color: {color};")
        layout.addWidget(value_label)


class StartupPage(QWidget):
    """Lista lo que arranca con Windows y permite desactivarlo de forma reversible."""

    status = Signal(str)
    # La ventana tiene que cerrarse tras lanzar la copia con permisos; si no, quedarían
    # dos instancias con dos iconos de bandeja y dos guardias de memoria.
    restart_requested = Signal()

    def __init__(self, store: undo_core.UndoStore | None = None, launcher=None, parent=None) -> None:
        super().__init__(parent)
        self.store = store or undo_core.UndoStore()
        self.launcher = launcher
        self._entries: list[startup_core.StartupEntry] = []
        self._task_entries: list[startup_core.StartupEntry] = []
        self._scan_running = False
        self._scan_done = False
        self._toggle_busy = False
        self._build()
        self.refresh(with_tasks=False)

    # ------------------------------------------------------------------
    @property
    def entries(self) -> list[startup_core.StartupEntry]:
        """Todas las entradas conocidas, activadas primero."""
        return startup_core.sort_entries(self._entries + self._task_entries)

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
            "Programas de inicio",
            "Lo que Windows abre solo al encender el equipo; aquí se desactiva sin borrar nada.",
        )
        self.refresh_button = QPushButton("Actualizar lista")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.setIcon(icons.icon("restore", 15))
        self.refresh_button.clicked.connect(lambda: self.refresh(with_tasks=True, force=True))
        header.add_action(self.refresh_button)
        if self.launcher is not None:
            services_button = QPushButton("Servicios")
            services_button.setObjectName("Ghost")
            services_button.setIcon(icons.icon("gear", 15))
            services_button.setToolTip("Los servicios de Windows se administran aparte: ábrelos aquí.")
            services_button.clicked.connect(self._open_services)
            header.add_action(services_button)
        layout.addWidget(header)

        self.tiles_container = QWidget()
        self.tiles_layout = QHBoxLayout(self.tiles_container)
        self.tiles_layout.setContentsMargins(0, 0, 0, 0)
        self.tiles_layout.setSpacing(12)
        layout.addWidget(self.tiles_container)

        layout.addWidget(self._explain_card())

        self.list_card = Card(name="Entradas de arranque")
        list_layout = QVBoxLayout(self.list_card)
        list_layout.setContentsMargins(18, 16, 18, 18)
        list_layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(SectionTitle("Entradas de arranque"))
        self.count_label = QLabel()
        self.count_label.setObjectName("Dim")
        head.addWidget(self.count_label)
        head.addStretch(1)
        self.tasks_note = QLabel()
        self.tasks_note.setObjectName("Dim")
        head.addWidget(self.tasks_note)
        list_layout.addLayout(head)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(10)
        list_layout.addLayout(self.rows_box)
        layout.addWidget(self.list_card)

        layout.addWidget(self._undo_card())
        layout.addStretch(1)

    def _explain_card(self) -> Card:
        card = Card(name="Qué hace exactamente esta pantalla")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(IconBadge("info", 30, 16, COLORS["accent_glow"]))
        title = QLabel("Qué hace exactamente esta pantalla")
        title.setObjectName("CardTitle")
        top.addWidget(title)
        top.addStretch(1)
        layout.addLayout(top)
        for line in (
            "Desactivar no borra nada: se escribe la misma marca que escribe el Administrador de "
            "tareas, así que la entrada se queda donde estaba y puedes volver a activarla cuando quieras.",
            "El «coste» es una estimación nuestra a partir del tipo de entrada (una tarea programada "
            "trabaja más que una línea del registro). Windows no publica la cifra del Administrador "
            "de tareas, así que no se inventa ningún número.",
            "Aquí no aparecen las aplicaciones de la Microsoft Store, porque Windows las arranca por "
            "otra vía, ni los servicios, que se administran en «Servicios».",
        ):
            bullet = QLabel("•  " + line)
            bullet.setObjectName("Muted")
            bullet.setWordWrap(True)
            layout.addWidget(bullet)
        return card

    def _undo_card(self) -> Card:
        card = Card(name="Deshacer")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconBadge("restore", 30, 16, COLORS["good"]))
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = QLabel("Deshacer")
        title.setObjectName("CardTitle")
        texts.addWidget(title)
        subtitle = QLabel("Cada cambio que hagas aquí queda apuntado con su fecha.")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        texts.addWidget(subtitle)
        head.addLayout(texts)
        head.addStretch(1)
        layout.addLayout(head)
        layout.addWidget(hline())
        self.undo_box = QVBoxLayout()
        self.undo_box.setSpacing(8)
        layout.addLayout(self.undo_box)
        return card

    # ------------------------------------------------------------------
    def refresh(self, with_tasks: bool = True, force: bool = False) -> None:
        """Relee el registro y las carpetas, y busca las tareas en segundo plano."""
        try:
            self._entries = startup_core.read_entries()
        except Exception as exc:  # noqa: BLE001 - la página no puede caerse por esto
            self._entries = []
            self.status.emit(f"No se pudo leer la lista de arranque: {exc}")
        # Se busca una sola vez por sesión (tarda unos segundos); «Actualizar
        # lista» vuelve a hacerlo cuando el usuario lo pide.
        if with_tasks and not self._scan_running and (force or not self._scan_done):
            self._scan_running = True
            self.tasks_note.setText("Buscando tareas de inicio de sesión y aplicaciones de la Store…")
            run_in_background(self, startup_core.read_slow_entries, self._on_tasks)
        self._render()

    def _on_tasks(self, result) -> None:
        self._scan_running = False
        self._scan_done = True
        if isinstance(result, Exception):
            self.tasks_note.setText("No se pudieron consultar las tareas de inicio de sesión.")
            return
        self._task_entries = list(result)
        # La lectura trae dos orígenes a la vez (tareas de inicio de sesión y aplicaciones
        # de la Store), así que el aviso dice cuántas hay de cada uno.
        tareas = sum(1 for entry in self._task_entries if entry.source == startup_core.SOURCE_TAREA)
        store = sum(1 for entry in self._task_entries if entry.source == startup_core.SOURCE_STORE)
        partes: list[str] = []
        if tareas:
            partes.append(f"{tareas} tarea{'s' if tareas != 1 else ''} de inicio de sesión")
        if store:
            partes.append(f"{store} aplicación{'es' if store != 1 else ''} de la Store")
        self.tasks_note.setText(" · ".join(partes) if partes else "Sin tareas de inicio de sesión")
        self._render()

    def _render(self) -> None:
        self._render_tiles()
        self._render_list()
        self._render_undo()

    def _render_tiles(self) -> None:
        while self.tiles_layout.count():
            item = self.tiles_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        entries = self.entries
        data = startup_core.summary(entries)
        tiles = [
            (
                "power",
                "Arrancan ahora",
                str(data["activas"]),
                COLORS["accent"],
                "Entradas que Windows ejecuta al iniciar la sesión.",
            ),
            (
                "check",
                "Desactivadas",
                str(data["desactivadas"]),
                COLORS["text_muted"],
                "Siguen en su sitio, pero Windows ya no las abre.",
            ),
            (
                "warn",
                "Ya no existen",
                str(data["sin_archivo"]),
                COLORS["warn"] if data["sin_archivo"] else COLORS["text_muted"],
                "Apuntan a un programa que ya no está: se pueden desactivar sin miedo.",
            ),
            (
                "shield",
                "Del sistema",
                str(data["con_admin"]),
                COLORS["warn"] if data["con_admin"] else COLORS["text_muted"],
                "Entradas de todos los usuarios: cambiarlas pide permisos de administrador.",
            ),
        ]
        for icon_name, title, value, color, note in tiles:
            self.tiles_layout.addWidget(SummaryTile(icon_name, title, value, color, note))

    def _render_list(self) -> None:
        while self.rows_box.count():
            item = self.rows_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        entries = self.entries
        self.count_label.setText(f"{len(entries)} entradas")

        if not entries:
            empty = QLabel(
                "No se ha encontrado nada que arranque con Windows.\n"
                "Puede que este equipo use solo aplicaciones de la Microsoft Store, que no aparecen aquí."
            )
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            self.rows_box.addWidget(empty)
            return

        for source, group in startup_core.by_source(entries).items():
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(SectionTitle(source))
            count = QLabel(f"{len(group)}")
            count.setObjectName("Dim")
            head.addWidget(count)
            head.addStretch(1)
            self.rows_box.addLayout(head)
            for entry in group:
                row = StartupRow(entry)
                row.toggle_requested.connect(self._ask_toggle)
                self.rows_box.addWidget(row)

    def _render_undo(self) -> None:
        while self.undo_box.count():
            item = self.undo_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        actions = self.store.recent(MAX_UNDO_ROWS)
        if not actions:
            empty = Muted("Todavía no has cambiado nada. Lo que hagas aparecerá aquí para poder deshacerlo.")
            empty.setWordWrap(True)
            self.undo_box.addWidget(empty)
            return
        for action in actions:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            row_layout.addWidget(
                IconBadge("check" if action.reverted else "power", 24, 13, COLORS["text_dim"])
            )
            texts = QVBoxLayout()
            texts.setSpacing(1)
            summary = ElidedLabel(action.summary)
            summary.setStyleSheet("font-size: 12.5px; font-weight: 600;")
            summary.setToolTip(f"{action.title}: {action.before} → {action.after}")
            texts.addWidget(summary)
            detail = QLabel(f"{action.date_display} · {action.origin_label}")
            detail.setObjectName("Dim")
            texts.addWidget(detail)
            row_layout.addLayout(texts, 1)
            if action.reverted:
                done = QLabel(f"Deshecho {action.reverted_display}")
                done.setObjectName("Dim")
                row_layout.addWidget(done)
            elif undo_core.can_revert(action.kind):
                undo_button = QPushButton("Deshacer")
                undo_button.setObjectName("Ghost")
                undo_button.setCursor(Qt.PointingHandCursor)
                undo_button.clicked.connect(lambda _=False, item=action: self._undo(item))
                row_layout.addWidget(undo_button)
            self.undo_box.addWidget(row)

    # ------------------------------------------------------------------
    def _open_services(self) -> None:
        if self.launcher is None:
            return
        ok, message = self.launcher("services")
        self.status.emit(message)
        if not ok:
            QMessageBox.warning(self, "No se pudo abrir", message)

    def _ask_toggle(self, entry: startup_core.StartupEntry) -> None:
        if self._toggle_busy or not entry.can_toggle:
            return
        enabling = not entry.enabled
        if enabling:
            question = (
                f"«{entry.name}» volverá a abrirse cada vez que entres en Windows.\n\n"
                "Se quita la marca que lo desactiva; no hay nada que instalar."
            )
        else:
            question = (
                f"«{entry.name}» dejará de abrirse al entrar en Windows.\n\n"
                "No se borra nada: la entrada se queda donde está y podrás volver a activarla desde "
                "aquí, desde el Administrador de tareas de Windows o con el botón «Deshacer»."
            )
        answer = QMessageBox.question(
            self,
            "Activar el arranque" if enabling else "Desactivar el arranque",
            question,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self._toggle_busy = True
        self.refresh_button.setEnabled(False)
        self.status.emit("Aplicando el cambio…")
        run_in_background(
            self,
            lambda: startup_core.move(entry, enabling, self.store),
            self._after_toggle,
        )

    def _after_toggle(self, result) -> None:
        self._toggle_busy = False
        self.refresh_button.setEnabled(True)
        if isinstance(result, Exception):
            self.status.emit(f"No se pudo aplicar el cambio: {result}")
            QMessageBox.warning(self, "No se pudo cambiar", str(result))
            return
        ok, message = result
        self.status.emit(message)
        self.refresh(with_tasks=False)
        if ok:
            return
        if "administrador" not in message:
            QMessageBox.warning(self, "Windows dijo que no", message)
            return
        answer = QMessageBox.question(
            self,
            "Hacen falta permisos",
            message + "\n\n¿Quieres reiniciar PC Toolkit como administrador para cambiarlo?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes and relaunch_elevated():
            self.status.emit("Pidiendo permisos de administrador… la aplicación se reiniciará.")
            self.restart_requested.emit()

    def _undo(self, action: undo_core.UndoAction) -> None:
        ok, message = undo_core.revert_action(self.store, action)
        self.status.emit(message)
        self.refresh(with_tasks=False)
        if not ok:
            QMessageBox.warning(self, "No se pudo deshacer", message)

    @property
    def undo_rows(self) -> int:
        """Cuántos apuntes de deshacer se están mostrando (para las pruebas)."""
        return max(0, self.undo_box.count())
