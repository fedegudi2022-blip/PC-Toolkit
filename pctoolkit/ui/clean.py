"""Ventana «Liberar espacio»: qué se puede borrar, cuánto y sin tocar nada hasta decidir.

Es la parte visible del catálogo de `pctoolkit.core.cleaner`. El análisis tarda lo que tarde
en recorrer las carpetas (y las cachés de un navegador con miles de archivos pueden tardar
unos segundos), así que va en segundo plano y la ventana se abre al instante diciendo que
está midiendo. **Nada se borra en el análisis**: los números que se enseñan son los mismos
que después se borran (es el mismo código el que mide y el que borra), y lo que esté en uso
se cuenta aparte en vez de prometerse.

Lo que necesita administrador y lo que no se puede deshacer no se marca solo: aparece con su
motivo y con la casilla libre para quien lo decida a propósito. La papelera, además, avisa en
la confirmación de que lo que se vacía no vuelve.

Dos cosas más, pensadas para no tener que ir a buscarlas:

* **Se puede abrir ya marcada.** Los avisos de la portada («se pueden liberar 2 GB», «quedan
  4 GB libres») traen dentro qué categorías señalan y la ventana se abre con ellas marcadas
  (`prechecked`). Lo que no cambia es lo importante: **abrir no borra nada** y el borrado
  sigue pidiendo su confirmación, con la lista de lo que se va a borrar delante.
* **Se puede programar sola.** `CleanupTaskCard` crea una tarea de Windows aparte
  (`\\PCToolkit\\Limpieza PC Toolkit`) que ejecuta `--limpieza` con las categorías marcadas:
  solo limpia (sin memoria ni informes), así que convive con el mantenimiento completo sin
  pisarlo ni depender de él.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Sequence

from PySide6.QtCore import QTime, QTimer, Qt, Signal
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import cleaner
from ..core import scheduler as scheduler_core
from ..core.config import COLORS, format_bytes, format_integer
from . import accessibility, icons, workers
from .widgets import Card, IconBadge, Muted, SectionTitle, hline

# Color e icono de cada motivo por el que una categoría no se puede limpiar ahora.
BLOCK_COLORS = {
    "admin": COLORS["warn"],
    "otro": COLORS["text_muted"],
}

# «Probar ahora»: cada cuánto y cuántas veces se le pregunta a Windows por el resultado de la
# limpieza recién lanzada (tarda unos segundos en anotarlo).
POLL_MS = 5000
MAX_POLLS = 6


class TargetRow(Card):
    """Una categoría de limpieza: casilla, tamaño, qué se pierde y por qué no (si no)."""

    changed = Signal()

    def __init__(self, item: cleaner.TargetScan, parent: QWidget | None = None) -> None:
        super().__init__(parent, soft=True, name=item.name)
        self.item = item
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.check = QCheckBox()
        self.check.setAccessibleName(f"Limpiar «{item.name}»")
        self.check.setChecked(bool(item.available and item.preselected))
        self.check.setEnabled(bool(item.available))
        self.check.setVisible(bool(item.available))
        self.check.toggled.connect(lambda _=False: self.changed.emit())
        top.addWidget(self.check)

        nombre = QLabel(item.name)
        nombre.setStyleSheet("font-size: 13px; font-weight: 600;")
        nombre.setWordWrap(True)
        top.addWidget(nombre, 1)

        self.size_label = QLabel(self._size_text())
        self.size_label.setStyleSheet("font-size: 12.5px; font-weight: 600;")
        self.size_label.setToolTip("Lo que se liberaría de esta categoría")
        top.addWidget(self.size_label)
        layout.addLayout(top)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        for texto, color in self._chips():
            chip = QLabel(texto)
            chip.setStyleSheet(f"color: {color}; font-size: 11.5px; font-weight: 600;")
            chips.addWidget(chip)
        chips.addStretch(1)
        if chips.count() > 1:
            layout.addLayout(chips)

        descripcion = Muted(item.description)
        descripcion.setWordWrap(True)
        layout.addWidget(descripcion)

        if item.blocked:
            motivo = QLabel(item.blocked)
            motivo.setWordWrap(True)
            motivo.setStyleSheet(f"color: {COLORS['warn']}; font-size: 12px;")
            layout.addWidget(motivo)
        elif not item.present:
            sin = QLabel("No hay nada de este tipo en este equipo.")
            sin.setWordWrap(True)
            sin.setObjectName("Dim")
            layout.addWidget(sin)

        detalle = Muted(item.detail)
        detalle.setWordWrap(True)
        detalle.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11.5px;")
        layout.addWidget(detalle)

        accessibility.mark(self, QAccessible.Role.ListItem, item.name, self._spoken())

    # ------------------------------------------------------------------
    def _size_text(self) -> str:
        if not self.item.present:
            return "—"
        if not self.item.available:
            return "—" if self.item.blocked else "sin datos"
        # La papelera cuenta elementos (archivos y carpetas), no archivos: se dice así.
        unidad = "elemento(s)" if self.item.kind == cleaner.KIND_RECYCLE else "archivo(s)"
        return f"{format_bytes(self.item.size)} · {format_integer(self.item.files)} {unidad}"

    def _chips(self) -> list[tuple[str, str]]:
        chips: list[tuple[str, str]] = []
        if self.item.available and self.item.locked:
            chips.append(
                (
                    f"{format_integer(self.item.locked)} en uso: no se pueden borrar ahora",
                    COLORS["text_muted"],
                )
            )
        if self.item.requires_admin:
            chips.append(("Pide administrador", COLORS["warn"]))
        if self.item.irreversible:
            chips.append(("No se puede deshacer", COLORS["bad"]))
        if (
            self.item.available
            and not self.item.locked
            and not self.item.requires_admin
            and not self.item.irreversible
        ):
            chips.append(("Se puede borrar sin riesgo", COLORS["good"]))
        return chips

    def _spoken(self) -> str:
        partes = [self._size_text()]
        partes.extend(texto for texto, _color in self._chips())
        if self.item.blocked:
            partes.append(self.item.blocked)
        return ". ".join(partes)

    @property
    def marked(self) -> bool:
        return self.check.isVisibleTo(self) and self.check.isChecked()

    def mark(self, value: bool) -> None:
        if self.check.isEnabled():
            self.check.setChecked(value)


class CleanupTaskCard(Card):
    """Programa la limpieza de lo marcado, sin depender del mantenimiento completo.

    Windows guarda esta tarea en `\\PCToolkit\\Limpieza PC Toolkit` y ejecuta la aplicación en
    su modo de **solo limpieza** (`--limpieza`): no libera memoria ni genera informes. Así se
    puede tener una limpieza diaria de temporales y, a la vez, un mantenimiento semanal con
    informe: son dos tareas distintas y quitar una no toca la otra.

    La fecha, la hora y la frecuencia se eligen aquí mismo, con las mismas opciones que la
    página de mantenimiento (para no tener dos maneras de decir lo mismo). Las categorías son
    las que estén marcadas en ese momento, de modo que lo que se programa es exactamente lo
    que se está viendo.
    """

    status = Signal(str)  # mensaje para la ventana (lo que ha pasado)

    def __init__(
        self,
        settings,
        selected: Callable[[], Sequence[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, name="Programar esta limpieza")
        self.settings = settings
        self.selected = selected
        self.schedule = settings.cleanup_schedule
        self._busy = False
        # `None` es «todavía no se sabe»: hasta que responda Windows no se afirma ni que la
        # tarea esté registrada ni que no lo esté.
        self._registered: bool | None = None
        # Lo que contesta el Programador de tareas (próxima ejecución y cómo fue la última).
        self._status: scheduler_core.TaskStatus | None = None
        # Tras lanzarla a mano se pregunta unas cuantas veces por el resultado. El reloj es
        # hijo de la tarjeta: si la ventana se cierra, se va con ella y no queda nada suelto.
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._poll_status)
        self._polls_left = 0
        self._waiting_result = False
        self._build()
        self._sync_controls()
        self.check_status()

    # --- Construcción ----------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(SectionTitle("Programar esta limpieza"))
        explicacion = Muted(
            "Limpia las categorías marcadas arriba en el momento que elijas, sin activar el "
            "mantenimiento completo. Windows la ejecuta aunque la aplicación esté cerrada, con "
            "las mismas garantías que aquí: lo que esté en uso se omite y los documentos no se "
            "tocan."
        )
        explicacion.setWordWrap(True)
        layout.addWidget(explicacion)

        controles = QHBoxLayout()
        controles.setSpacing(8)
        controles.addWidget(QLabel("Frecuencia"))
        self.frequency_combo = QComboBox()
        for frequency in scheduler_core.FREQUENCIES:
            self.frequency_combo.addItem(frequency)
        self.frequency_combo.setAccessibleName("Con qué frecuencia se limpia")
        self.frequency_combo.currentTextChanged.connect(self._on_frequency_changed)
        controles.addWidget(self.frequency_combo)
        self.weekday_label = QLabel("Día")
        controles.addWidget(self.weekday_label)
        self.weekday_combo = QComboBox()
        for day in scheduler_core.WEEKDAYS:
            self.weekday_combo.addItem(day)
        self.weekday_combo.setAccessibleName("Día de la semana en que se limpia")
        controles.addWidget(self.weekday_combo)
        controles.addWidget(QLabel("Hora"))
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setAccessibleName("Hora de la limpieza")
        controles.addWidget(self.time_edit)
        controles.addStretch(1)
        self.apply_button = QPushButton("Programar")
        self.apply_button.setObjectName("Primary")
        self.apply_button.setIcon(icons.icon("calendar", 15, COLORS["on_accent"]))
        self.apply_button.setToolTip("Crea la tarea de Windows que limpia lo marcado")
        self.apply_button.clicked.connect(self.apply)
        controles.addWidget(self.apply_button)
        self.run_button = QPushButton("Probar ahora")
        self.run_button.setObjectName("Ghost")
        self.run_button.setEnabled(False)
        self.run_button.setToolTip(
            "Lanza la limpieza programada en este momento, para comprobar que funciona sin "
            "esperar a su hora"
        )
        self.run_button.clicked.connect(self.run_now)
        controles.addWidget(self.run_button)
        self.remove_button = QPushButton("Quitar")
        self.remove_button.setObjectName("Ghost")
        self.remove_button.setToolTip("Elimina la tarea de limpieza programada")
        self.remove_button.clicked.connect(self.remove)
        controles.addWidget(self.remove_button)
        layout.addLayout(controles)

        self.state = Muted("")
        self.state.setWordWrap(True)
        self.state.setAccessibleName("Estado de la limpieza programada")
        layout.addWidget(self.state)

    # --- Estado ----------------------------------------------------------
    def _sync_controls(self) -> None:
        indice = self.frequency_combo.findText(self.schedule.frequency)
        self.frequency_combo.setCurrentIndex(indice if indice >= 0 else 0)
        hora = QTime.fromString(self.schedule.time, "HH:mm")
        self.time_edit.setTime(hora if hora.isValid() else QTime(21, 0))
        dia = self.weekday_combo.findText(self.schedule.weekday)
        self.weekday_combo.setCurrentIndex(dia if dia >= 0 else 0)
        self._on_frequency_changed()
        self._refresh_state()

    def _on_frequency_changed(self, _valor: str = "") -> None:
        """El día de la semana solo tiene sentido si la limpieza es semanal."""
        semanal = self.frequency_combo.currentText() == scheduler_core.FREQ_WEEKLY
        self.weekday_label.setVisible(semanal)
        self.weekday_combo.setVisible(semanal)

    def _refresh_state(self) -> None:
        if self._busy:
            self.state.setText("Trabajando…")
        elif not self.schedule.targets:
            self.state.setText(
                "Sin programar: marca las categorías que quieras limpiar y elige cuándo."
            )
        else:
            if self._registered is None:
                registro = "Comprobando si la tarea está registrada en Windows…"
            elif self._registered:
                registro = "La tarea está registrada en Windows."
            else:
                registro = "La tarea no está registrada en Windows: se puede volver a programar."
            self.state.setText(
                f"{self.schedule.describe()} {registro}{self._windows_state()}"
            )
        self.apply_button.setEnabled(not self._busy)
        self.remove_button.setEnabled(
            not self._busy and bool(self.schedule.targets or self._registered)
        )
        self.run_button.setEnabled(not self._busy and bool(self._registered))

    def _windows_state(self) -> str:
        """Lo que dice el Programador de tareas: cuándo es la próxima y cómo fue la última.

        Es el mismo dato que enseña la página de Mantenimiento (leído con PowerShell en
        segundo plano), puesto aquí para no tener que ir a otra página a comprobarlo: sin él,
        «programada» sería una promesa sin forma de mirarla.
        """
        status = self._status
        if status is None or not status.exists or not self._registered:
            return ""
        ultima = f"Última: {status.last_run_display}"
        if status.last_result is not None:
            ultima += f" ({status.last_result_text})"
        return f"\nPróxima: {status.next_run_display} · {ultima}"

    def check_status(self) -> None:
        """Pregunta a Windows por la tarea (PowerShell, sin abrir ninguna ventana)."""
        if not self.schedule.targets:
            return  # nada programado: no hay nada que preguntar
        workers.run_in_background(self, scheduler_core.cleanup_task_status, self._status_ready)

    def _status_ready(self, resultado) -> None:
        if isinstance(resultado, Exception):
            return
        self._status = resultado
        self._registered = bool(resultado.exists)
        if self._waiting_result and resultado.last_result is not None:
            self._waiting_result = False
            self._poll.stop()
            self.status.emit(
                f"La limpieza programada terminó: {resultado.last_result_text}. "
                "Queda anotada en el histórico."
            )
        self._refresh_state()

    # --- Probar ahora -----------------------------------------------------
    def run_now(self) -> None:
        """Lanza la tarea con `schtasks` para ver que funciona sin esperar a su hora.

        Es la misma orden que usa el botón «Ejecutar ahora» del mantenimiento, sobre la tarea
        de limpieza: limpia de verdad (es lo que se ha programado), así que solo se ofrece
        cuando la tarea está registrada en Windows.
        """
        if self._busy or not self._registered:
            return
        self._busy = True
        self._refresh_state()
        self.status.emit("Lanzando la limpieza programada…")
        workers.run_in_background(
            self,
            lambda: scheduler_core.run_task_now(scheduler_core.CLEANUP_TASK_PATH),
            self._after_run_now,
        )

    def _after_run_now(self, resultado) -> None:
        self._busy = False
        if isinstance(resultado, Exception):
            self.status.emit(f"No se pudo lanzar la limpieza: {resultado}")
        elif not resultado.ok:
            self.status.emit(resultado.message)
        else:
            self.status.emit(
                f"{resultado.message} Tarda unos segundos: el resultado aparece aquí en cuanto "
                "Windows lo anota."
            )
            # Windows tarda unos segundos en anotar el resultado, así que se pregunta unas
            # cuantas veces y se para en cuanto lo cuenta.
            self._waiting_result = True
            self._polls_left = MAX_POLLS
            self._poll.start()
        self._refresh_state()

    def _poll_status(self) -> None:
        """Una vuelta más del reloj que sigue el resultado de «Probar ahora»."""
        self._polls_left -= 1
        if self._polls_left <= 0:
            self._poll.stop()
            self._waiting_result = False
        self.check_status()

    # --- Programar y quitar -----------------------------------------------
    def _collect(self) -> scheduler_core.CleanupSchedule:
        return replace(
            self.schedule,
            frequency=self.frequency_combo.currentText(),
            time=self.time_edit.time().toString("HH:mm"),
            weekday=self.weekday_combo.currentText(),
            targets=tuple(self.selected()),
        ).normalized()

    def apply(self) -> None:
        """Crea (o actualiza) la tarea con las categorías marcadas en este momento."""
        claves = tuple(self._collect().targets)
        if not claves:
            self.status.emit("Marca al menos una categoría arriba para poder programarla.")
            return
        irreversibles = [
            clave
            for clave in claves
            if (objetivo := cleaner.find_target(clave)) is not None and objetivo.irreversible
        ]
        if irreversibles and not self._confirm_irreversible(irreversibles):
            self.status.emit("No se programó nada.")
            return
        schedule = replace(self._collect(), enabled=True)
        self.settings.cleanup_schedule = schedule
        self.settings.sync()
        self.schedule = schedule
        self._busy = True
        self._refresh_state()
        self.status.emit("Programando la limpieza en Windows…")
        workers.run_in_background(
            self, lambda: scheduler_core.create_cleanup_task(schedule), self._created
        )

    def _confirm_irreversible(self, claves: Sequence[str]) -> bool:
        """Lo que no se puede deshacer se pregunta una vez más antes de programarlo."""
        pregunta = QMessageBox(self)
        pregunta.setWindowTitle("Programar esta limpieza")
        pregunta.setIcon(QMessageBox.Warning)
        pregunta.setText(
            "Entre las categorías marcadas hay alguna que no se puede deshacer "
            f"({cleaner.keys_label(claves)}).\n\nWindows la limpiará sola cada vez que se "
            "ejecute la tarea, sin preguntar y sin que estés delante. ¿Programarla así?"
        )
        pregunta.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        pregunta.button(QMessageBox.Yes).setText("Programar")
        pregunta.button(QMessageBox.No).setText("Cancelar")
        pregunta.setDefaultButton(QMessageBox.No)
        return pregunta.exec() == QMessageBox.Yes

    def _created(self, resultado) -> None:
        self._busy = False
        if isinstance(resultado, Exception):
            self.status.emit(f"No se pudo programar: {resultado}")
        else:
            self.status.emit(resultado.message)
            self._registered = bool(resultado.ok)
        self._refresh_state()

    def remove(self) -> None:
        self._busy = True
        self._refresh_state()
        self.status.emit("Eliminando la limpieza programada…")
        workers.run_in_background(self, scheduler_core.delete_cleanup_task, self._removed)

    def _removed(self, resultado) -> None:
        self._busy = False
        if isinstance(resultado, Exception):
            self.status.emit(f"No se pudo quitar: {resultado}")
        else:
            self.status.emit(resultado.message)
            if resultado.ok:
                self.schedule = replace(self.schedule, enabled=False)
                self.settings.cleanup_schedule = self.schedule
                self.settings.sync()
                self._registered = False
        self._refresh_state()


class CleanDialog(QDialog):
    """Vista previa de la limpieza por categorías, con borrado confirmado."""

    done_changes = Signal(str)  # mensaje para la barra de estado de la página

    def __init__(
        self,
        settings=None,
        scan: cleaner.CleanScan | None = None,
        parent: QWidget | None = None,
        prechecked: Sequence[str] = (),
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.scan = scan or cleaner.CleanScan()
        self._rows: list[TargetRow] = []
        # Categorías que llegan marcadas (las señala un aviso de la portada) y si ya se han
        # marcado: se marca una vez, no en cada repintado (si no, el resultado de una limpieza
        # desaparecería detrás del aviso de «esto viene marcado»).
        self.prechecked = cleaner.normalize_keys(tuple(prechecked))
        self._prechecked_done = False
        self.setWindowTitle("Liberar espacio")
        self.setMinimumSize(840, 700)
        self._build()
        if scan is None:
            self.refresh()
        else:
            self._render()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        textos = QVBoxLayout()
        textos.setSpacing(2)
        title = QLabel("Liberar espacio")
        title.setObjectName("SectionTitle")
        accessibility.set_role(title, QAccessible.Role.Heading)
        textos.addWidget(title)
        subtitulo = Muted(
            "Esto es lo que se puede borrar y cuánto se liberaría. El análisis no borra nada: "
            "solo mira. Lo que esté en uso se queda donde está, y nada de tus documentos se toca."
        )
        subtitulo.setWordWrap(True)
        textos.addWidget(subtitulo)
        head.addLayout(textos, 1)
        self.refresh_button = QPushButton("Volver a analizar")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.setToolTip("Vuelve a medir todas las categorías")
        self.refresh_button.clicked.connect(self.refresh)
        head.addWidget(self.refresh_button)
        layout.addLayout(head)
        layout.addWidget(hline())

        self.summary = Muted("Analizando qué se puede liberar…")
        self.summary.setAccessibleName("Resumen del análisis")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.result_label = Muted("")
        self.result_label.setWordWrap(True)
        self.result_label.setAccessibleName("Resultado de la última limpieza")
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.rows_layout = QVBoxLayout(self.container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)

        # Programar solo esta limpieza (tarea aparte del mantenimiento completo). Solo se
        # construye si hay preferencias donde guardar la programación: sin ellas no hay nada
        # que programar, y un control que no puede guardar nada es un control que engaña.
        self.plan_card: CleanupTaskCard | None = None
        if self.settings is not None:
            self.plan_card = CleanupTaskCard(self.settings, self.selected, self)
            self.plan_card.status.connect(self._resultado)
            layout.addWidget(self.plan_card)

        self.auto_check = QCheckBox("El mantenimiento automático limpia lo que marque aquí")
        self.auto_check.setToolTip(
            "Guarda estas categorías para la tarea programada de Mantenimiento (lo demás se "
            "limpia solo cuando lo pidas desde esta ventana)."
        )
        self.auto_check.toggled.connect(self._save_auto_targets)
        layout.addWidget(self.auto_check)

        pie = QHBoxLayout()
        pie.setSpacing(8)
        self.clean_button = QPushButton("Limpiar lo marcado")
        self.clean_button.setObjectName("Primary")
        self.clean_button.setIcon(icons.icon("broom", 15, COLORS["on_accent"]))
        self.clean_button.setEnabled(False)
        self.clean_button.clicked.connect(self.clean_marked)
        pie.addWidget(self.clean_button)
        pie.addStretch(1)
        self.close_button = QPushButton("Cerrar")
        self.close_button.setObjectName("Ghost")
        self.close_button.clicked.connect(self.accept)
        pie.addWidget(self.close_button)
        layout.addLayout(pie)
        self._sync_auto_check()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Vuelve a medir todas las categorías, en segundo plano."""
        self.refresh_button.setEnabled(False)
        self.summary.setText("Analizando qué se puede liberar…")
        workers.run_in_background(
            self, lambda: cleaner.scan(cleaner.target_keys()), self._scanned
        )

    def _scanned(self, resultado) -> None:
        self.refresh_button.setEnabled(True)
        if isinstance(resultado, Exception):
            self.summary.setText(f"No se pudo analizar: {resultado}")
            return
        self.scan = resultado
        self._render()

    def _render(self) -> None:
        for fila in self._rows:
            fila.setParent(None)
            fila.deleteLater()
        self._rows = []
        for item in self.scan.items:
            fila = TargetRow(item)
            fila.changed.connect(self._update_buttons)
            self._rows.append(fila)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, fila)
        self.summary.setText(self._summary_text())
        self._update_buttons()
        self._apply_prechecked()

    def _apply_prechecked(self) -> None:
        """Marca las categorías que señala el aviso que ha abierto la ventana.

        Solo se marca lo que se puede limpiar ahora: una categoría bloqueada (un navegador
        abierto, por ejemplo) se queda sin marcar con su motivo a la vista, que es más honesto
        que marcarla para luego no poder borrarla. Y marcar es solo marcar: el borrado sigue
        pidiendo su confirmación, aquí igual que si las casillas se hubieran puesto a mano.
        """
        if not self.prechecked or self._prechecked_done:
            return
        self._prechecked_done = True
        anunciadas = [fila for fila in self._rows if fila.item.key in set(self.prechecked)]
        for fila in anunciadas:
            fila.mark(True)
        marcadas = [fila.item.key for fila in anunciadas if fila.marked]
        sin_marcar = [fila.item.name for fila in anunciadas if not fila.marked]
        self._update_buttons()
        mensaje = (
            f"Marcado lo que señala el aviso: {cleaner.keys_label(marcadas)}. "
            "Nada se borra hasta que confirmes el borrado."
        )
        if sin_marcar:
            mensaje += (
                " Sin marcar, porque ahora mismo no se pueden: " + ", ".join(sin_marcar) + "."
            )
        self._resultado(mensaje)

    def _summary_text(self) -> str:
        disponibles = self.scan.available_items
        if not disponibles:
            return (
                "Ahora mismo no hay nada que se pueda liberar de forma segura (mira el motivo "
                "de cada categoría)."
            )
        partes = [
            f"Se pueden liberar {format_bytes(self.scan.total_size)} en "
            f"{format_integer(self.scan.total_files)} elemento(s), en {len(disponibles)} categoría(s)."
        ]
        if self.scan.total_locked:
            partes.append(
                f"Otros {format_integer(self.scan.total_locked)} archivo(s) están en uso ahora "
                "mismo y se omitirán."
            )
        sin_datos = [item.name for item in self.scan.items if not item.present]
        if sin_datos:
            partes.append("Sin nada que mirar: " + ", ".join(sin_datos) + ".")
        return " ".join(partes)

    # ------------------------------------------------------------------
    def selected(self) -> list[str]:
        return [fila.item.key for fila in self._rows if fila.marked]

    def mark_defaults(self) -> None:
        for fila in self._rows:
            fila.mark(bool(fila.item.available and fila.item.preselected))
        self._update_buttons()

    def _update_buttons(self) -> None:
        marcadas = self.selected()
        self.clean_button.setEnabled(bool(marcadas))
        self.clean_button.setText(
            "Limpiar lo marcado" if not marcadas else f"Limpiar lo marcado ({len(marcadas)})"
        )

    def _resultado(self, mensaje: str, *, color: str | None = None) -> None:
        self.result_label.setText(mensaje)
        self.result_label.setStyleSheet(f"color: {color or COLORS['text_muted']}; font-size: 12px;")
        self.result_label.setVisible(bool(mensaje))

    # --- Mantenimiento automático ----------------------------------------
    def _sync_auto_check(self) -> None:
        if self.settings is None:
            self.auto_check.setVisible(False)
            return
        self.auto_check.setVisible(True)
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(bool(self.settings.schedule_config.clean_targets))
        self.auto_check.blockSignals(False)

    def _save_auto_targets(self, activo: bool) -> None:
        """Guarda (o quita) las categorías que limpia el mantenimiento automático."""
        if self.settings is None:
            return
        schedule = self.settings.schedule_config
        claves = self.selected() if activo else ()
        self.settings.schedule_config = replace(schedule, clean_targets=tuple(claves))
        self.settings.sync()
        if activo:
            self._resultado(
                "El mantenimiento automático limpiará: "
                + cleaner.keys_label(claves)
                + ".",
            )

    # --- Limpieza ---------------------------------------------------------
    def clean_marked(self) -> None:
        """Borra las categorías marcadas, con confirmación previa."""
        elegidas = self.selected()
        if not elegidas:
            return
        filas = [fila for fila in self._rows if fila.item.key in set(elegidas)]
        total = sum(fila.item.size for fila in filas)
        archivos = sum(fila.item.files for fila in filas)
        lineas = [
            f"• {fila.item.name}: {format_bytes(fila.item.size)} en "
            + f"{format_integer(fila.item.files)} "
            + ("elemento(s)" if fila.item.kind == cleaner.KIND_RECYCLE else "archivo(s)")
            for fila in filas
        ]
        irreversibles = [fila.item.name for fila in filas if fila.item.irreversible]
        aviso = ""
        if irreversibles:
            aviso = (
                "\n\nAtención: " + ", ".join(irreversibles) + " no se puede deshacer. "
                "Lo que hay ahí fuera no vuelve."
            )
        pregunta = QMessageBox(self)
        pregunta.setWindowTitle("Liberar espacio")
        pregunta.setIcon(QMessageBox.Warning if irreversibles else QMessageBox.Question)
        pregunta.setText(
            f"¿Borrar lo marcado?\n\n" + "\n".join(lineas) + f"\n\nTotal: {format_bytes(total)} "
            f"en {format_integer(archivos)} elemento(s).{aviso}\n\n"
            "Lo que esté en uso se omitirá automáticamente y se dirá al terminar."
        )
        pregunta.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        pregunta.button(QMessageBox.Yes).setText("Borrar")
        pregunta.button(QMessageBox.No).setText("Cancelar")
        pregunta.setDefaultButton(QMessageBox.No)
        if pregunta.exec() != QMessageBox.Yes:
            self._resultado("No se borró nada.")
            return
        self.clean_button.setEnabled(False)
        self._resultado("Borrando lo marcado…")
        workers.run_in_background(
            self, lambda: cleaner.clean(elegidas), self._cleaned
        )

    def _cleaned(self, resultado) -> None:
        if isinstance(resultado, Exception):
            QMessageBox.warning(self, "No se pudo limpiar", str(resultado))
            self._update_buttons()
            return
        fallos = [item for item in resultado.items if item.blocked or not item.ok]
        self._resultado(
            resultado.summary()
            + (
                "  Sin tocar: "
                + "; ".join(f"{item.name} ({item.blocked or 'no se pudo'})" for item in fallos)
                if fallos
                else ""
            ),
            color=COLORS["good"] if not fallos else COLORS["warn"],
        )
        self.done_changes.emit(resultado.summary())
        self.refresh()
