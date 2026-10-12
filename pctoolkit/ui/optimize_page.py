"""Página «Optimizar memoria»: liberación de RAM manual y automática.

La página no libera memoria por su cuenta: pide la operación (`release_requested`)
y la ventana principal la ejecuta en segundo plano, de modo que solo hay un
sitio que limpia, ya sea a mano o por umbral. El recorte de un proceso suelto sí
se hace aquí, porque tarda milisegundos.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtGui import QColor, QPainter

from ..core import memory as memory_core
from ..core.config import COLORS, format_bytes
from ..core.settings import AppSettings
from . import icons
from .widgets import ArcProgress, Card, IconBadge, Muted, PageHeader, SectionTitle, hline, kv_row

MAX_LISTED_PROCESSES = 12

# A partir de qué parte de la RAM total se considera que un proceso consume mucho.
UMBRAL_ALTO = 0.15
UMBRAL_MEDIO = 0.07


def relaunch_elevated() -> bool:
    """Vuelve a abrir PC Toolkit pidiendo permisos de administrador (UAC)."""
    if not memory_core.IS_WINDOWS:
        return False
    if getattr(sys, "frozen", False):
        program, arguments = str(Path(sys.executable)), ""
    else:
        script = Path(__file__).resolve().parents[2] / "app.py"
        program, arguments = sys.executable, f'"{script}"'
    try:
        result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", program, arguments or None, None, 1
        )
    except (OSError, AttributeError):
        return False
    return result > 32


def memory_level(share: float) -> str:
    """Color con el que se pinta un proceso según la parte de RAM que ocupa."""
    if share >= UMBRAL_ALTO:
        return COLORS["bad"]
    if share >= UMBRAL_MEDIO:
        return COLORS["warn"]
    return COLORS["accent"]


def tint(color: str, alpha: float = 0.18) -> str:
    """Mismo color con transparencia, para los fondos de las etiquetas."""
    base = QColor(color)
    return f"rgba({base.red()}, {base.green()}, {base.blue()}, {alpha})"


def _list_header(text: str) -> QLabel:
    """Rótulo de columna de la lista de procesos."""
    label = QLabel(text)
    label.setObjectName("ListHeader")
    return label


class ElidedLabel(QLabel):
    """Etiqueta que recorta su texto con puntos suspensivos si no cabe.

    Sin esto, un nombre de proceso o un detalle largos fijan el ancho mínimo de
    la fila por encima del hueco disponible y aparece una barra horizontal (o el
    botón «Liberar» se sale). Con el recorte, la fila siempre cabe y el texto
    largo se lee completo en el consejo emergente.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        color = self.palette().color(self.foregroundRole())
        painter.setPen(color)
        metrics = painter.fontMetrics()
        texto = metrics.elidedText(self.text(), Qt.ElideRight, self.width())
        painter.drawText(self.rect(), int(self.alignment()), texto)
        painter.end()


class ProcessList(QScrollArea):
    """Lista de procesos con desplazamiento vertical.

    Es un área con un diseño vertical en lugar de una `QListWidget` con widgets
    dentro porque así cada fila ocupa **exactamente** el ancho disponible: con la
    lista, el elemento se dimensionaba por el ancho natural de la fila, que es
    mayor que el hueco visible, y el botón «Liberar» quedaba recortado y tapado
    por la barra de desplazamiento. Aquí la barra vive fuera de las filas y ninguna
    se corta.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setObjectName("ProcessList")
        self.contenedor = QWidget()
        self.setWidget(self.contenedor)
        self._layout = QVBoxLayout(self.contenedor)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)

    def clear(self) -> None:  # noqa: A003 (mismo nombre que el de la lista)
        """Quita todas las filas y mensajes."""
        while self._layout.count() > 1:
            elemento = self._layout.takeAt(0)
            widget = elemento.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def add_row(self, row: QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, row)

    def add_message(self, text: str) -> None:
        label = Muted(text)
        label.setWordWrap(True)
        self._layout.insertWidget(self._layout.count() - 1, label)

    def count(self) -> int:
        """Número de filas visibles (sin contar mensajes ni el hueco final)."""
        return sum(
            1 for indice in range(self._layout.count())
            if isinstance(self._layout.itemAt(indice).widget(), ProcessMemoryRow)
        )


class ProcessMemoryRow(QWidget):
    """Fila de la lista de procesos, con su botón para recortar la memoria.

    Muestra, de izquierda a derecha: el puesto que ocupa en el consumo, el icono,
    el nombre y el PID, una barra proporcional al proceso que más gasta, la
    memoria usada en MB/GB y el botón para liberarla.
    """

    trim_requested = Signal(int)

    def __init__(
        self,
        target: memory_core.ProcessTarget,
        rank: int = 0,
        share: float = 0.0,
        ratio: float = 0.0,
        alternate: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.target = target
        self.share = share
        self.setObjectName("ProcRowAlt" if alternate else "ProcRow")
        color = memory_level(share)

        layout = QHBoxLayout(self)
        # El margen derecho deja sitio a la barra de desplazamiento de la lista.
        # Los laterales son ajustados a propósito: cuantas menos filas se coman el
        # alto, más procesos se ven de un vistazo sin desplazar la lista.
        layout.setContentsMargins(10, 5, 16, 5)
        layout.setSpacing(10)

        self.rank_label = QLabel(str(rank) if rank else "–")
        self.rank_label.setFixedSize(24, 24)
        self.rank_label.setAlignment(Qt.AlignCenter)
        self.rank_label.setStyleSheet(
            f"background: {tint(color)}; color: {color}; font-size: 11px;"
            " font-weight: 700; border-radius: 9px;"
        )
        layout.addWidget(self.rank_label)

        layout.addWidget(IconBadge("memory", 28, 15, color))

        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.name_label = ElidedLabel(target.name)
        self.name_label.setObjectName("ProcName")
        self.name_label.setMinimumWidth(96)
        self.detail_label = ElidedLabel(self._detail_text())
        self.detail_label.setObjectName("Dim")
        texts.addWidget(self.name_label)
        texts.addWidget(self.detail_label)
        layout.addLayout(texts, 1)

        self.bar = ArcProgress(color)
        self.bar.set_ratio(ratio)
        self.bar.set_label(f"Consumo de {target.name}")
        self.bar.setMinimumWidth(40)
        layout.addWidget(self.bar, 1)

        self.value_label = QLabel(format_bytes(target.rss))
        self.value_label.setObjectName("ProcValue")
        self.value_label.setStyleSheet(f"color: {color};")
        self.value_label.setFixedWidth(80)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.value_label)

        self.trim_button = QPushButton("Liberar")
        self.trim_button.setObjectName("Chip")
        self.trim_button.setCursor(Qt.PointingHandCursor)
        self.trim_button.setToolTip("Recorta el conjunto de trabajo (memoria no usada) de este proceso.")
        self.trim_button.clicked.connect(lambda: self.trim_requested.emit(self.target.pid))
        layout.addWidget(self.trim_button)

        self.setToolTip(
            f"{target.name} · PID {target.pid}\n"
            f"Memoria en uso: {format_bytes(target.rss)}" + (f" ({share * 100:.1f} % de la RAM)" if share else "")
        )

    def _detail_text(self) -> str:
        parts = [f"PID {self.target.pid}"]
        if self.share:
            parts.append(f"{self.share * 100:.1f} % de la RAM")
            if self.share >= UMBRAL_ALTO:
                parts.append("alto")
            elif self.share >= UMBRAL_MEDIO:
                parts.append("medio")
        else:
            parts.append(format_bytes(self.target.rss))
        return " · ".join(parts)

    def set_memory(self, rss: int) -> None:
        """Actualiza la memoria mostrada (tras liberar parte de ella)."""
        self.target = memory_core.ProcessTarget(pid=self.target.pid, name=self.target.name, rss=rss)
        self.value_label.setText(format_bytes(rss))
        self.detail_label.setText(self._detail_text())

    def set_result(self, text: str, ok: bool = True) -> None:
        self.trim_button.setEnabled(False)
        self.trim_button.setText(text)
        self.trim_button.setStyleSheet(f"color: {COLORS['good' if ok else 'bad']};")


class OptimizeMemoryPage(QWidget):
    """Vista de memoria RAM con limpieza manual, automática y por proceso."""

    status = Signal(str)
    release_requested = Signal()
    settings_changed = Signal(object)

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.options = settings.memory_options
        self._snapshot = None
        self._results: list[memory_core.MemoryCleanResult] = []
        self._rows: dict[int, ProcessMemoryRow] = {}
        self._busy = False
        self._build()
        self.refresh_privileges()

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
            "Optimizar memoria",
            "Recorta la memoria que los programas tienen reservada y no están usando.",
        )
        self.refresh_button = QPushButton("Actualizar procesos")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.setIcon(icons.icon("restore", 15))
        self.refresh_button.clicked.connect(self.refresh_processes)
        header.add_action(self.refresh_button)
        self.release_button = QPushButton("Liberar memoria ahora")
        self.release_button.setObjectName("Primary")
        self.release_button.setIcon(icons.icon("broom", 15, COLORS["on_accent"]))
        self.release_button.clicked.connect(self.release_requested.emit)
        header.add_action(self.release_button)
        layout.addWidget(header)

        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(self._overview_card())
        top.addWidget(self._auto_card())
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(self._processes_card(), 3)
        bottom.addWidget(self._history_card(), 2)
        layout.addLayout(bottom)
        layout.addStretch(1)

    def _overview_card(self) -> Card:
        card = Card(hero=True, name="Memoria RAM")
        card.setFixedWidth(320)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconBadge("memory", 30, 16, COLORS["accent"]))
        head.addWidget(SectionTitle("Memoria RAM"))
        head.addStretch(1)
        layout.addLayout(head)

        self.ram_value = QLabel("—")
        self.ram_value.setObjectName("StatBig")
        layout.addWidget(self.ram_value)
        self.ram_detail = QLabel("Esperando la primera muestra…")
        self.ram_detail.setObjectName("Muted")
        self.ram_detail.setWordWrap(True)
        layout.addWidget(self.ram_detail)

        layout.addWidget(hline())
        self.last_freed = QLabel("Sin liberaciones todavía")
        self.last_freed.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {COLORS['accent_glow']};")
        self.last_freed.setWordWrap(True)
        layout.addWidget(self.last_freed)
        self.last_detail = QLabel("")
        self.last_detail.setObjectName("Dim")
        self.last_detail.setWordWrap(True)
        layout.addWidget(self.last_detail)

        self.busy_label = QLabel("")
        self.busy_label.setObjectName("Muted")
        layout.addWidget(self.busy_label)
        layout.addStretch(1)
        return card

    def _auto_card(self) -> Card:
        card = Card(name="Limpieza automática")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        layout.addWidget(SectionTitle("Limpieza automática"))
        layout.addWidget(
            Muted("Libera memoria sola cuando la RAM se mantiene por encima del umbral, sin molestar.")
        )

        self.auto_check = QCheckBox("Liberar memoria automáticamente")
        self.auto_check.setChecked(self.options.auto_enabled)
        self.auto_check.toggled.connect(self._save_options)
        layout.addWidget(self.auto_check)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(30, 99)
        self.threshold_spin.setSuffix(" %")
        self.threshold_spin.setValue(int(round(self.options.threshold_percent)))
        self.threshold_spin.valueChanged.connect(self._save_options)
        grid.addWidget(QLabel("Umbral de RAM"), 0, 0)
        grid.addWidget(self.threshold_spin, 0, 1)

        self.samples_spin = QSpinBox()
        self.samples_spin.setRange(1, 60)
        self.samples_spin.setSuffix(" muestras")
        self.samples_spin.setValue(int(self.options.sustained_samples))
        self.samples_spin.setToolTip("Cuántas muestras seguidas deben superar el umbral antes de limpiar.")
        self.samples_spin.valueChanged.connect(self._save_options)
        grid.addWidget(QLabel("Sostenido"), 1, 0)
        grid.addWidget(self.samples_spin, 1, 1)

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(1, 720)
        self.cooldown_spin.setSuffix(" min")
        self.cooldown_spin.setValue(int(self.options.cooldown_minutes))
        self.cooldown_spin.setToolTip("Tiempo mínimo entre dos limpiezas automáticas.")
        self.cooldown_spin.valueChanged.connect(self._save_options)
        grid.addWidget(QLabel("Espera entre limpiezas"), 2, 0)
        grid.addWidget(self.cooldown_spin, 2, 1)

        self.min_mb_spin = QDoubleSpinBox()
        self.min_mb_spin.setRange(0, 2048)
        self.min_mb_spin.setDecimals(0)
        self.min_mb_spin.setSuffix(" MB")
        self.min_mb_spin.setValue(float(self.options.min_process_mb))
        self.min_mb_spin.setToolTip("No se tocan procesos por debajo de este consumo: no aportan nada.")
        self.min_mb_spin.valueChanged.connect(self._save_options)
        grid.addWidget(QLabel("Mínimo por proceso"), 3, 0)
        grid.addWidget(self.min_mb_spin, 3, 1)

        self.processes_check = QCheckBox("Recortar procesos")
        self.processes_check.setChecked(self.options.trim_processes)
        self.processes_check.toggled.connect(self._save_options)
        grid.addWidget(self.processes_check, 4, 0, 1, 2)

        self.system_check = QCheckBox("Vaciar la caché del sistema")
        self.system_check.setChecked(self.options.empty_system_cache)
        self.system_check.toggled.connect(self._save_options)
        grid.addWidget(self.system_check, 5, 0, 1, 2)

        self.standby_check = QCheckBox("Purgar la caché en espera")
        self.standby_check.setChecked(self.options.purge_standby)
        self.standby_check.setToolTip(
            "Es lo que más memoria libera, pero Windows exige permisos de administrador."
        )
        self.standby_check.toggled.connect(self._save_options)
        grid.addWidget(self.standby_check, 6, 0, 1, 2)

        # Campos compactos: con el ancho del panel, un campo que se estira hasta el
        # borde se lee peor y deja el número perdido al fondo.
        for control in (self.threshold_spin, self.samples_spin, self.cooldown_spin, self.min_mb_spin):
            control.setMaximumWidth(150)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)

        self.privilege_label = QLabel("")
        self.privilege_label.setObjectName("Muted")
        self.privilege_label.setWordWrap(True)
        layout.addWidget(self.privilege_label)

        self.elevate_button = QPushButton("Reiniciar como administrador")
        self.elevate_button.setObjectName("Ghost")
        self.elevate_button.setIcon(icons.icon("shield", 15))
        self.elevate_button.clicked.connect(self._elevate)
        layout.addWidget(self.elevate_button)
        layout.addStretch(1)
        return card

    def _processes_card(self) -> Card:
        card = Card(name="Procesos que más memoria usan")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(SectionTitle("Procesos que más memoria usan"))
        head.addStretch(1)
        self.process_count_label = QLabel("")
        self.process_count_label.setObjectName("Muted")
        head.addWidget(self.process_count_label)
        layout.addLayout(head)
        layout.addWidget(
            Muted("«Liberar» devuelve la memoria reservada y no usada de ese programa. No cierra nada.")
        )

        columns = QHBoxLayout()
        columns.setContentsMargins(10, 0, 14, 0)
        columns.setSpacing(10)
        columns.addWidget(_list_header("PROCESO"))
        columns.addStretch(1)
        columns.addWidget(_list_header("MEMORIA RAM"))
        columns.addSpacing(166)
        layout.addLayout(columns)

        self.process_list = ProcessList()
        # Alto para que se vean **siete filas enteras** de una vez (43 px de fila más
        # 4 px de separación): es la parte útil de la página, y con menos hay que
        # desplazar la lista para ver quién está tirando de la memoria. El último
        # hueco no se deja a medias para que ninguna fila quede cortada.
        self.process_list.setMinimumHeight(325)
        layout.addWidget(self.process_list)
        return card

    def _history_card(self) -> Card:
        card = Card(name="Liberaciones de esta sesión")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        layout.addWidget(SectionTitle("Liberaciones de esta sesión"))

        self.history_list = QListWidget()
        self.history_list.setSelectionMode(QListWidget.NoSelection)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_list.setSpacing(2)
        self.history_list.setMinimumHeight(190)
        layout.addWidget(self.history_list)

        self.history_empty = Muted("Todavía no has liberado memoria en esta sesión.")
        layout.addWidget(self.history_empty)

        note = Muted(
            "Recortar conjuntos de trabajo devuelve a Windows la memoria reservada que nadie usa. "
            "Los programas recargan de disco lo que necesiten, así que conviene no abusar."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return card

    # --- Datos -----------------------------------------------------------
    def update_snapshot(self, snap) -> None:
        """Refresca la tarjeta de memoria con la última muestra."""
        self._snapshot = snap
        self.ram_value.setText(f"{snap.ram_percent:.0f}%")
        self.ram_detail.setText(
            f"{format_bytes(snap.ram_used)} en uso de {format_bytes(snap.ram_total)} · "
            f"{format_bytes(max(0, snap.ram_total - snap.ram_used))} libres"
        )
        color = COLORS["bad"] if snap.ram_percent >= 85 else (
            COLORS["warn"] if snap.ram_percent >= 65 else COLORS["accent_glow"]
        )
        self.ram_value.setStyleSheet(f"color: {color};")

    def refresh_privileges(self) -> None:
        """Indica si se pueden hacer las operaciones que necesitan administrador."""
        allowed = memory_core.has_standby_privilege()
        if allowed:
            self.privilege_label.setText(
                "Permisos de administrador disponibles: se vaciará también la caché del sistema."
            )
            self.privilege_label.setStyleSheet(f"color: {COLORS['good']}; font-size: 11.5px;")
            self.elevate_button.setVisible(False)
        else:
            self.privilege_label.setText(
                "Sin permisos de administrador, la caché del sistema y la lista de espera no se "
                "pueden tocar. El resto de la limpieza funciona igual."
            )
            self.privilege_label.setStyleSheet(f"color: {COLORS['warn']}; font-size: 11.5px;")
            self.elevate_button.setVisible(True)

    def refresh_processes(self) -> None:
        """Vuelve a leer los procesos que más memoria consumen.

        Cada fila se pinta con su puesto, la parte de RAM que ocupa y una barra
        comparada con el que más consume, para que se vea de un vistazo quién
        está tirando de la memoria.
        """
        targets = memory_core.select_targets(memory_core.collect_processes(), min_mb=1.0)
        self.process_list.clear()
        self._rows.clear()
        total = len(targets)
        mostrados = targets[:MAX_LISTED_PROCESSES]
        tope = mostrados[0].rss if mostrados else 0
        ram_total = float(getattr(self._snapshot, "ram_total", 0) or 0)
        for puesto, target in enumerate(mostrados, start=1):
            row = ProcessMemoryRow(
                target,
                rank=puesto,
                share=(target.rss / ram_total) if ram_total else 0.0,
                ratio=(target.rss / tope) if tope else 0.0,
                alternate=(puesto % 2 == 0),
            )
            row.trim_requested.connect(self._trim_one)
            self.process_list.add_row(row)
            self._rows[target.pid] = row
        if total:
            en_uso = sum(target.rss for target in mostrados)
            self.process_count_label.setText(
                f"{len(mostrados)} de {total} procesos · {format_bytes(en_uso)} en total"
            )
        else:
            self.process_count_label.setText("Sin procesos")
            self.process_list.add_message("No se pudo leer la lista de procesos.")

    def _trim_one(self, pid: int) -> None:
        row = self._rows.get(pid)
        if row is None:
            return
        before = row.target.rss
        import psutil

        try:
            before = psutil.Process(pid).memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        ok = memory_core.trim_process(pid)
        after = before
        try:
            after = psutil.Process(pid).memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        freed = max(0, before - after)
        if ok:
            row.set_memory(after)
            row.set_result(f"−{format_bytes(freed)}", True)
            self.status.emit(f"{row.target.name}: liberados {format_bytes(freed)}.")
        else:
            row.set_result("Sin acceso", False)
            self.status.emit(f"No se pudo liberar la memoria de {row.target.name}.")

    def _save_options(self, *_args) -> None:
        self.options = memory_core.MemoryOptions(
            auto_enabled=self.auto_check.isChecked(),
            threshold_percent=float(self.threshold_spin.value()),
            cooldown_minutes=int(self.cooldown_spin.value()),
            sustained_samples=int(self.samples_spin.value()),
            min_process_mb=float(self.min_mb_spin.value()),
            trim_processes=self.processes_check.isChecked(),
            empty_system_cache=self.system_check.isChecked(),
            purge_standby=self.standby_check.isChecked(),
            include_own_process=False,
        )
        self.settings.memory_options = self.options
        self.settings.sync()
        self.settings_changed.emit(self.options)

    def _elevate(self) -> None:
        if relaunch_elevated():
            self.status.emit("Pidiendo permisos de administrador… la aplicación se reiniciará.")
        else:
            self.status.emit("No se pudieron solicitar los permisos de administrador.")

    # --- Estado de la limpieza ------------------------------------------
    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.release_button.setEnabled(not busy)
        self.release_button.setText("Liberando…" if busy else "Liberar memoria ahora")
        self.busy_label.setText("Trabajando, no cierres la ventana…" if busy else "")

    def show_result(self, result: memory_core.MemoryCleanResult) -> None:
        """Muestra el resultado de una limpieza (manual o automática)."""
        self.set_busy(False)
        self._results.insert(0, result)
        self._results = self._results[:12]

        headline = f"{result.freed_mb:.0f} MB liberados"
        if not result.ok:
            headline = "No se pudo liberar memoria"
        self.last_freed.setText(headline)
        parts = [
            f"{result.before_percent:.0f}% → {result.after_percent:.0f}% de RAM",
            f"{result.processes_trimmed} procesos ({format_bytes(result.trimmed_bytes)})",
        ]
        if result.standby_purged:
            parts.append("caché en espera purgada")
        if result.system_cache_empty:
            parts.append("caché del sistema vaciada")
        if result.trigger == "automatico":
            parts.append("automática")
        parts.append(f"{result.duration:.1f} s")
        self.last_detail.setText(" · ".join(parts))

        self.history_empty.setVisible(False)
        self.history_list.clear()
        for entry in self._results:
            label = f"{entry.freed_mb:.0f} MB · {entry.trigger}"
            item = QListWidgetItem(
                icons.icon("memory", 14, COLORS["accent"] if entry.ok else COLORS["bad"]),
                f"{label}   {entry.before_percent:.0f}%→{entry.after_percent:.0f}% · "
                f"{entry.processes_trimmed} proc.",
            )
            item.setToolTip(entry.summary() + "\n" + "\n".join(entry.errors[:2]))
            self.history_list.addItem(item)

    @property
    def last_result(self) -> memory_core.MemoryCleanResult | None:
        return self._results[0] if self._results else None
