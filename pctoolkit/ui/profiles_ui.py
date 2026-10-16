"""Ventanas de los perfiles: guardar lo elegido y revisar un perfil antes de marcarlo.

Son dos diálogos y los dos hacen lo mismo con la vista puesta en no tocar nada por sorpresa:

* **Guardar perfil**: enseña qué se va a guardar (cuántos ajustes y de qué riesgo, qué
  limpieza y qué mantenimiento), deja elegir dónde y admite una nota. Un perfil con un ajuste
  de riesgo alto se ve antes de compartirlo, que es cuando importa saber que lo lleva.
* **Abrir perfil**: no aplica nada. Enseña de dónde salió, qué trae, qué ya está puesto en
  este equipo, qué no se puede aplicar aquí y qué no conoce esta versión, y marca las casillas
  solo cuando quien lo abre lo acepta. La limpieza y el mantenimiento se ofrecen aparte, con
  su propia casilla, porque se aplican en otras páginas.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import profiles
from ..core.config import COLORS
from ..core.scheduler import MaintenanceSchedule
from ..core.windows_settings import RISK_HIGH, RISK_MEDIUM, Setting
from .widgets import Card, IconBadge, Muted, hline


class SaveProfileDialog(QDialog):
    """Guardar el perfil: qué se guarda, dónde y con qué nota."""

    def __init__(
        self,
        *,
        ajustes: list[Setting],
        limpieza: tuple[str, ...] = (),
        mantenimiento: MaintenanceSchedule | None = None,
        sugerida: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ajustes = ajustes
        self.limpieza = limpieza
        self.mantenimiento = mantenimiento
        self.setWindowTitle("Guardar el perfil")
        self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("disk", 36, 19, COLORS["accent_glow"]))
        textos = QVBoxLayout()
        textos.setSpacing(2)
        titulo = QLabel("Guardar el perfil")
        titulo.setObjectName("SectionTitle")
        textos.addWidget(titulo)
        textos.addWidget(
            Muted(
                "Se guarda en un archivo de texto (JSON) con los ajustes marcados, la limpieza "
                "elegida y el mantenimiento programado. Guardar no cambia nada del equipo."
            )
        )
        head.addLayout(textos, 1)
        layout.addLayout(head)
        layout.addWidget(hline())

        contenido = Card(name="Lo que se va a guardar")
        dentro = QVBoxLayout(contenido)
        dentro.setContentsMargins(16, 14, 16, 14)
        dentro.setSpacing(6)
        for linea in self._detalle():
            etiqueta = QLabel(linea)
            etiqueta.setWordWrap(True)
            dentro.addWidget(etiqueta)
        layout.addWidget(contenido)

        aviso = [a for a in ajustes if a.risk in (RISK_MEDIUM, RISK_HIGH)]
        if aviso:
            # Un perfil se comparte con facilidad; quien lo reciba tiene que poder ver que
            # dentro hay cambios de riesgo medio o alto sin abrir el archivo.
            riesgo = Muted(
                f"Ojo al compartirlo: {len(aviso)} de los ajustes son de riesgo medio o alto."
            )
            riesgo.setStyleSheet(f"color: {COLORS['warn']};")
            riesgo.setWordWrap(True)
            layout.addWidget(riesgo)

        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addWidget(QLabel("Archivo:"))
        self.path_edit = QLineEdit(str(sugerida or profiles.ruta_sugerida()))
        self.path_edit.setAccessibleName("Dónde guardar el perfil")
        fila.addWidget(self.path_edit, 1)
        examinar = QPushButton("Examinar…")
        examinar.setObjectName("Ghost")
        examinar.clicked.connect(self._examinar)
        fila.addWidget(examinar)
        layout.addLayout(fila)

        fila_nota = QHBoxLayout()
        fila_nota.setSpacing(8)
        fila_nota.addWidget(QLabel("Nota (opcional):"))
        self.nota_edit = QLineEdit()
        self.nota_edit.setPlaceholderText("Para qué es este perfil: «equipo de la oficina», «portátil»…")
        self.nota_edit.setMaxLength(profiles.MAXIMO_NOTA)
        self.nota_edit.setAccessibleName("Nota del perfil")
        fila_nota.addWidget(self.nota_edit, 1)
        layout.addLayout(fila_nota)

        botones = QDialogButtonBox()
        cancelar = QPushButton("Cancelar")
        cancelar.setObjectName("Ghost")
        cancelar.clicked.connect(self.reject)
        guardar = QPushButton("Guardar")
        guardar.setObjectName("Primary")
        guardar.clicked.connect(self._guardar)
        botones.addButton(cancelar, QDialogButtonBox.RejectRole)
        botones.addButton(guardar, QDialogButtonBox.AcceptRole)
        layout.addWidget(botones)

    # ------------------------------------------------------------------
    def _detalle(self) -> list[str]:
        lineas = [f"Ajustes de Windows: {len(self.ajustes)} marcados"]
        for riesgo, etiqueta in ((RISK_MEDIUM, "medio"), (RISK_HIGH, "alto")):
            cuantos = sum(1 for a in self.ajustes if a.risk == riesgo)
            if cuantos:
                lineas[-1] += f" · {cuantos} de riesgo {etiqueta}"
        if self.limpieza:
            from ..core.cleaner import find_target

            nombres = [find_target(clave).name for clave in self.limpieza if find_target(clave)]
            lineas.append("Limpieza: " + " · ".join(nombres))
        if self.mantenimiento is not None:
            plan = self.mantenimiento.normalized()
            partes = []
            if plan.clean_temp:
                from ..core.cleaner import keys_label

                partes.append(f"limpiar {keys_label(plan.clean_targets)}")
            if plan.release_memory:
                partes.append("liberar memoria")
            if plan.export_report:
                partes.append("generar informe")
            lineas.append("Mantenimiento: " + (" · ".join(partes) or "ningún paso activo"))
        return lineas

    def _examinar(self) -> None:
        elegido, _filtro = QFileDialog.getSaveFileName(
            self, "Guardar el perfil", self.path_edit.text() or str(profiles.ruta_sugerida()), profiles.FILTRO
        )
        if elegido:
            self.path_edit.setText(elegido)

    def _guardar(self) -> None:
        if not self.path_edit.text().strip():
            QMessageBox.warning(self, "Falta el archivo", "Escribe dónde guardar el perfil.")
            return
        destino = Path(self.path_edit.text().strip())
        if destino.suffix.lower() != profiles.EXTENSION:
            destino = destino.with_suffix(profiles.EXTENSION)
        if destino.exists():
            pregunta = QMessageBox(self)
            pregunta.setWindowTitle("Ya existe ese archivo")
            pregunta.setIcon(QMessageBox.Question)
            pregunta.setText(f"¿Sobrescribir {destino.name}?")
            pregunta.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            pregunta.button(QMessageBox.Yes).setText("Sobrescribir")
            pregunta.button(QMessageBox.No).setText("Cancelar")
            if pregunta.exec() != QMessageBox.Yes:
                return
        self.destino = destino
        self.accept()

    def resultado(self) -> tuple[Path, str]:
        """El archivo elegido y la nota (solo tiene sentido tras aceptar)."""
        return self.destino, self.nota_edit.text().strip()


class OpenProfileDialog(QDialog):
    """Revisar un perfil antes de marcar nada."""

    def __init__(
        self,
        perfil: profiles.Profile,
        revision: dict,
        *,
        archivo: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.perfil = perfil
        self.revision = revision
        self.setWindowTitle("Abrir un perfil")
        self.setMinimumSize(660, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("analyze", 36, 19, COLORS["accent_glow"]))
        textos = QVBoxLayout()
        textos.setSpacing(2)
        titulo = QLabel(archivo.name if archivo else "Perfil")
        titulo.setObjectName("SectionTitle")
        textos.addWidget(titulo)
        textos.addWidget(
            Muted(
                "Abrir un perfil no cambia nada: solo marca las casillas de los ajustes que "
                "propone. Se aplican después, con la misma confirmación y el mismo respaldo que "
                "si se hubieran marcado a mano."
            )
        )
        head.addLayout(textos, 1)
        layout.addLayout(head)
        layout.addWidget(hline())

        resumen = Card(name="Qué trae")
        dentro = QVBoxLayout(resumen)
        dentro.setContentsMargins(16, 14, 16, 14)
        dentro.setSpacing(4)
        for linea in profiles.resumen(perfil, revision):
            etiqueta = QLabel(linea)
            etiqueta.setWordWrap(True)
            # Las líneas con «·» al principio son el detalle de la anterior.
            etiqueta.setObjectName("Muted" if linea.startswith("  ·") else "")
            dentro.addWidget(etiqueta)
        layout.addWidget(resumen)

        self.limpieza_check = QCheckBox("Abrir también la limpieza con esas categorías marcadas")
        self.limpieza_check.setChecked(bool(revision["limpieza"]))
        self.limpieza_check.setEnabled(bool(revision["limpieza"]))
        self.limpieza_check.setToolTip(
            "Abre la ventana de limpieza con esas categorías marcadas. No borra nada: allí se "
            "ve cuánto se liberaría y se confirma aparte."
        )
        layout.addWidget(self.limpieza_check)

        self.mantenimiento_check = QCheckBox(
            "Guardar las opciones de mantenimiento y de limpieza programada"
        )
        self.mantenimiento_check.setChecked(bool(perfil.mantenimiento))
        self.mantenimiento_check.setEnabled(bool(perfil.mantenimiento))
        self.mantenimiento_check.setToolTip(
            "Escribe esas opciones en la página de mantenimiento (qué limpia, si libera memoria "
            "y si genera informe). Programar la tarea de Windows se hace allí, con su botón."
        )
        layout.addWidget(self.mantenimiento_check)
        layout.addStretch(1)

        marcables = len(revision["marcar"])
        botones = QDialogButtonBox()
        cancelar = QPushButton("Cancelar")
        cancelar.setObjectName("Ghost")
        cancelar.clicked.connect(self.reject)
        self.marcar_button = QPushButton(
            "Marcar en la lista" if marcables == 0 else f"Marcar {marcables} en la lista"
        )
        self.marcar_button.setObjectName("Primary")
        self.marcar_button.setEnabled(marcables > 0 or bool(revision["limpieza"]))
        self.marcar_button.setToolTip("Marca esos ajustes en la página para que los revises y los apliques tú")
        self.marcar_button.clicked.connect(self.accept)
        botones.addButton(cancelar, QDialogButtonBox.RejectRole)
        botones.addButton(self.marcar_button, QDialogButtonBox.AcceptRole)
        layout.addWidget(botones)

    @property
    def abrir_limpieza(self) -> bool:
        return self.limpieza_check.isEnabled() and self.limpieza_check.isChecked()

    @property
    def aplicar_mantenimiento(self) -> bool:
        return self.mantenimiento_check.isEnabled() and self.mantenimiento_check.isChecked()
