"""Ventana «Aplicaciones preinstaladas»: ver lo que trae Windows y quitar lo que sobra.

Es la parte visible del gestor de debloat: la lista llega del núcleo (`pctoolkit.core.apps`),
que es quien decide qué se puede quitar y qué no, y aquí solo se enseña, se marca y se
confirma. Consultar la lista tarda —arranca PowerShell y mide carpetas—, así que la consulta
va en segundo plano y la ventana se abre al instante con un aviso de «consultando…».

La decisión de qué es intocable **no** se toma aquí: si un paquete está en la lista blanca, la
ventana lo enseña con su motivo y sin casilla, y el núcleo lo volvería a rechazar aunque se le
pidiera. Dos sitios que deciden lo mismo es la forma de que uno se olvide.

Idea inspirada en CrapFixer (builtbybel, licencia MIT); ver el README.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import apps
from ..core.config import COLORS, format_bytes, format_integer
from ..core.undo import UndoStore
from . import accessibility, icons, workers
from .widgets import Card, IconBadge, Muted, hline

RISK_COLORS = {
    apps.RISK_LOW: COLORS["text_muted"],
    apps.RISK_MEDIUM: COLORS["warn"],
    apps.RISK_HIGH: COLORS["bad"],
}


class AppRow(Card):
    """Una aplicación de la lista: casilla, nombre, datos, riesgo y qué se pierde."""

    changed = Signal()

    def __init__(self, entry: apps.AppEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent, soft=True, name=entry.display_name)
        self.entry = entry
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.check = QCheckBox()
        self.check.setAccessibleName(f"Quitar «{entry.display_name}»")
        self.check.toggled.connect(lambda _=False: self.changed.emit())
        # Sin casilla no hay nada que marcar: una pieza del sistema no se quita desde aquí.
        self.check.setVisible(entry.removable)
        top.addWidget(self.check)

        self.name_label = QLabel(entry.display_name)
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.name_label.setWordWrap(True)
        top.addWidget(self.name_label, 1)

        self.badge = QLabel(entry.risk_label)
        self.badge.setStyleSheet(
            f"color: {RISK_COLORS.get(entry.risk, COLORS['text_muted'])}; font-size: 11.5px; font-weight: 600;"
        )
        top.addWidget(self.badge)

        self.shield = QLabel()
        self.shield.setPixmap(icons.pixmap_for("shield", 14, COLORS["warn"]))
        self.shield.setToolTip("Es una pieza del sistema: no se puede quitar")
        self.shield.setVisible(not entry.removable)
        top.addWidget(self.shield)
        layout.addLayout(top)

        detalles = " · ".join(
            parte
            for parte in (
                entry.publisher or "editor desconocido",
                f"versión {entry.version}" if entry.version else "",
                entry.size_label,
            )
            if parte
        )
        datos = QLabel(f"{entry.name} · {detalles}")
        datos.setObjectName("Muted")
        datos.setWordWrap(True)
        layout.addWidget(datos)

        nota = Muted(entry.note)
        nota.setWordWrap(True)
        layout.addWidget(nota)

        accessibility.mark(
            self,
            QAccessible.Role.ListItem,
            entry.display_name,
            f"{entry.risk_label}. {entry.note}",
        )

    @property
    def marked(self) -> bool:
        return self.check.isVisibleTo(self) and self.check.isChecked()

    def mark(self, value: bool) -> None:
        if self.check.isVisibleTo(self):
            self.check.setChecked(value)


class DebloatDialog(QDialog):
    """Lista de aplicaciones preinstaladas, con la lista blanca y el quitado confirmado."""

    done_changes = Signal(str)  # mensaje para la barra de estado de la página

    def __init__(self, store: UndoStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.entries: list[apps.AppEntry] = []
        self._rows: list[AppRow] = []
        self.setWindowTitle("Aplicaciones preinstaladas")
        self.setMinimumSize(820, 640)
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        textos = QVBoxLayout()
        textos.setSpacing(2)
        title = QLabel("Aplicaciones preinstaladas")
        title.setObjectName("SectionTitle")
        accessibility.set_role(title, QAccessible.Role.Heading)
        textos.addWidget(title)
        subtitulo = Muted(
            "Lo que Windows trae puesto de fábrica. Se quita solo de tu cuenta (los demás "
            "usuarios no se tocan) y sin permisos de administrador; desde «Deshacer» vuelven a "
            "instalarse. La lista y los tamaños se consultan en segundo plano: puede tardar unos "
            "segundos en aparecer."
        )
        subtitulo.setWordWrap(True)
        textos.addWidget(subtitulo)
        head.addLayout(textos, 1)
        self.refresh_button = QPushButton("Volver a consultar")
        self.refresh_button.setObjectName("Ghost")
        self.refresh_button.setToolTip("Vuelve a preguntar a Windows la lista de aplicaciones")
        self.refresh_button.clicked.connect(self.refresh)
        head.addWidget(self.refresh_button)
        layout.addLayout(head)
        layout.addWidget(hline())

        filtros = QHBoxLayout()
        filtros.setSpacing(10)
        filtros.addWidget(IconBadge("analyze", 28, 15, COLORS["text_dim"]))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar (por ejemplo: bing, xbox, solitario, mapas)…")
        self.search.setAccessibleName("Buscar aplicaciones")
        self.search.textChanged.connect(self._apply_filter)
        filtros.addWidget(self.search, 1)
        self.only_recommended = QCheckBox("Solo las recomendadas")
        self.only_recommended.setToolTip("Enseña únicamente las que PC Toolkit considera molestas")
        self.only_recommended.toggled.connect(self._apply_filter)
        filtros.addWidget(self.only_recommended)
        self.mark_button = QPushButton("Marcar las recomendadas")
        self.mark_button.setObjectName("Ghost")
        self.mark_button.clicked.connect(lambda: self.mark_recommended(True))
        filtros.addWidget(self.mark_button)
        layout.addLayout(filtros)

        self.summary = Muted("Consultando las aplicaciones instaladas…")
        self.summary.setAccessibleName("Resumen de la lista")
        layout.addWidget(self.summary)
        # El resultado de quitar va aparte del recuento: al volver a consultar la lista, el
        # recuento se recalcula y el mensaje de «quiénes se quitaron» tiene que seguir ahí.
        self.result_label = Muted("")
        self.result_label.setWordWrap(True)
        self.result_label.setAccessibleName("Resultado de la última operación")
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

        pie = QHBoxLayout()
        pie.setSpacing(8)
        self.remove_button = QPushButton("Quitar las marcadas")
        self.remove_button.setObjectName("Danger")
        self.remove_button.setEnabled(False)
        self.remove_button.setToolTip("Quita del usuario actual las aplicaciones marcadas, con confirmación")
        self.remove_button.clicked.connect(self.remove_marked)
        pie.addWidget(self.remove_button)
        self.copy_button = QPushButton("Copiar la lista")
        self.copy_button.setObjectName("Ghost")
        self.copy_button.setToolTip("Copia la lista con tamaños, para pedir ayuda en un foro")
        self.copy_button.clicked.connect(self.copy_list)
        pie.addWidget(self.copy_button)
        pie.addStretch(1)
        self.close_button = QPushButton("Cerrar")
        self.close_button.setObjectName("Ghost")
        self.close_button.clicked.connect(self.accept)
        pie.addWidget(self.close_button)
        layout.addLayout(pie)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Pide la lista al núcleo en segundo plano (tarda unos segundos)."""
        self.refresh_button.setEnabled(False)
        self.summary.setText("Consultando las aplicaciones instaladas…")
        workers.run_in_background(self, apps.list_apps, self._listed)

    def _listed(self, resultado) -> None:
        self.refresh_button.setEnabled(True)
        if isinstance(resultado, Exception):
            self.summary.setText(f"No se pudo consultar la lista: {resultado}")
            return
        self.entries = list(resultado or [])
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        for fila in self._rows:
            fila.setParent(None)
            fila.deleteLater()
        self._rows = []
        for entry in self.entries:
            fila = AppRow(entry)
            fila.changed.connect(self._update_buttons)
            self._rows.append(fila)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, fila)
        cifras = apps.summary(self.entries)
        self.summary.setText(
            f"{format_integer(cifras['total'])} aplicación(es): "
            f"{format_integer(cifras['recomendadas'])} que conviene quitar · "
            f"{format_integer(cifras['protegidas'])} piezas del sistema (no se tocan) · "
            f"{format_bytes(max(0, cifras['tamano']))} en las recomendadas"
        )
        self._apply_filter()
        self._update_buttons()

    def _apply_filter(self) -> None:
        texto = self.search.text().strip().lower()
        solo_recomendadas = self.only_recommended.isChecked()
        for fila in self._rows:
            entry = fila.entry
            coincide = not texto or texto in entry.display_name.lower() or texto in entry.name.lower() or texto in entry.publisher.lower()
            if solo_recomendadas and not apps.is_recommended(entry):
                coincide = False
            fila.setVisible(coincide)

    # ------------------------------------------------------------------
    def selected(self) -> list[apps.AppEntry]:
        return [fila.entry for fila in self._rows if fila.marked]

    def mark_recommended(self, value: bool = True) -> None:
        for fila in self._rows:
            if apps.is_recommended(fila.entry):
                fila.mark(value)
        self._update_buttons()

    def _update_buttons(self) -> None:
        marcadas = len(self.selected())
        self.remove_button.setEnabled(marcadas > 0)
        self.remove_button.setText(
            "Quitar las marcadas" if marcadas == 0 else f"Quitar las marcadas ({marcadas})"
        )

    def _resultado(self, mensaje: str, *, color: str | None = None) -> None:
        """Enseña el resultado de la última operación (y lo deja visible)."""
        self.result_label.setText(mensaje)
        self.result_label.setStyleSheet(f"color: {color or COLORS['text_muted']}; font-size: 12px;")
        self.result_label.setVisible(bool(mensaje))

    def copy_list(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(apps.format_entries_report(self.entries))
        self._resultado("Lista copiada al portapapeles.")

    def remove_marked(self) -> None:
        """Quita las aplicaciones marcadas, avisando de qué se pierde con cada una."""
        elegidas = self.selected()
        if not elegidas:
            return
        lineas = [f"• {entry.display_name} ({entry.risk_label.lower()})" for entry in elegidas]
        perdidas = [f"  – {entry.display_name}: {entry.note}" for entry in elegidas]
        pregunta = QMessageBox(self)
        pregunta.setWindowTitle("Quitar aplicaciones preinstaladas")
        pregunta.setIcon(QMessageBox.Warning)
        pregunta.setText(
            f"¿Quitar {len(elegidas)} aplicación(es) de tu cuenta?\n\n"
            + "\n".join(lineas)
            + "\n\nQué se pierde:\n"
            + "\n".join(perdidas)
            + "\n\nSe quitan solo de tu usuario (no hace falta administrador) y quedan apuntadas "
            "en «Deshacer» para volver a instalarlas."
        )
        pregunta.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        pregunta.button(QMessageBox.Yes).setText("Quitar")
        pregunta.button(QMessageBox.No).setText("Cancelar")
        pregunta.setDefaultButton(QMessageBox.No)
        if pregunta.exec() != QMessageBox.Yes:
            self._resultado("No se quitó nada.")
            return
        self.remove_button.setEnabled(False)
        self._resultado("Quitando las aplicaciones marcadas…")
        workers.run_in_background(
            self,
            lambda: apps.remove_apps(elegidas, store=self.store),
            self._removed,
        )

    def _removed(self, resultado) -> None:
        self.remove_button.setEnabled(True)
        if isinstance(resultado, Exception):
            QMessageBox.warning(self, "No se pudo quitar", str(resultado))
            return
        mensaje = resultado.message()
        self._resultado(
            mensaje,
            color=COLORS["bad"] if resultado.failed else COLORS["good"],
        )
        self.done_changes.emit(mensaje)
        if resultado.failed:
            QMessageBox.warning(
                self,
                "Algunas aplicaciones no se pudieron quitar",
                "\n".join(f"• {nombre}: {motivo}" for nombre, motivo in resultado.failed),
            )
        self.refresh()
