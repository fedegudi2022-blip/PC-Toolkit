"""Ventana «Plugins»: revisar qué trae cada archivo antes de dejar que toque el equipo.

Un plugin es un JSON que el usuario deja en `%APPDATA%\\PCToolkit\\plugins`. Sus ajustes
aparecen en la página «Ajustes de Windows», pero **no se pueden aplicar** hasta que se revisan
aquí: esta ventana enseña qué claves del registro toca cada uno, con qué riesgo y si necesita
administrador, y solo entonces se acepta. La aceptación guarda la huella del archivo: si el
JSON cambia después, vuelve a pedir revisión (un archivo editado no es el que se aprobó).

Nada de esta ventana ejecuta nada del plugin: los ajustes entran por el mismo camino que los
del catálogo, con respaldo `.reg`, historial de «Deshacer» y registro de cambios.

Idea inspirada en CrapFixer (builtbybel, licencia MIT); ver el README.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QAccessible, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import plugins, windows_settings as tweaks
from ..core.config import COLORS
from . import accessibility, icons
from .widgets import Card, IconBadge, Muted, SectionTitle, hline

RISK_COLORS = {
    tweaks.RISK_LOW: COLORS["text_muted"],
    tweaks.RISK_MEDIUM: COLORS["warn"],
    tweaks.RISK_HIGH: COLORS["bad"],
}


def _estado_color(trusted: bool) -> str:
    return COLORS["good"] if trusted else COLORS["warn"]


class PluginCard(Card):
    """Un plugin con lo que trae, su estado y el botón para aceptarlo o retirarlo."""

    accepted = Signal(object)  # PluginInfo
    revoked = Signal(object)  # PluginInfo

    def __init__(self, info: plugins.PluginInfo, settings: list[tweaks.Setting], parent: QWidget | None = None) -> None:
        super().__init__(parent, name=info.name)
        self.info = info
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(IconBadge("tool" if info.trusted else "shield", 32, 17, _estado_color(info.trusted)))
        textos = QVBoxLayout()
        textos.setSpacing(2)
        titulo = QLabel(f"{info.name} · {info.id}")
        titulo.setObjectName("CardTitle")
        titulo.setWordWrap(True)
        textos.addWidget(titulo)
        autor = " · ".join(
            parte for parte in (info.author or "autor sin indicar", f"versión {info.version}" if info.version else "", f"{info.count} ajuste(s)") if parte
        )
        etiqueta = Muted(autor)
        etiqueta.setWordWrap(True)
        textos.addWidget(etiqueta)
        top.addLayout(textos, 1)
        self.estado = QLabel("Aceptado" if info.trusted else "Sin confirmar")
        self.estado.setStyleSheet(f"color: {_estado_color(info.trusted)}; font-size: 12px; font-weight: 600;")
        top.addWidget(self.estado)
        layout.addLayout(top)

        if info.description:
            descripcion = Muted(info.description)
            descripcion.setWordWrap(True)
            layout.addWidget(descripcion)

        if info.dangerous:
            aviso = QLabel(
                "Aviso: "
                + " y ".join(
                    parte
                    for parte in (
                        f"{info.needs_admin} ajuste(s) tocan HKEY_LOCAL_MACHINE (necesitan administrador)"
                        if info.needs_admin
                        else "",
                        f"{info.high_risk} ajuste(s) son de riesgo alto" if info.high_risk else "",
                    )
                    if parte
                )
                + ". Revísalos uno a uno antes de aceptar."
            )
            aviso.setWordWrap(True)
            aviso.setStyleSheet(f"color: {COLORS['bad']}; font-size: 12px;")
            layout.addWidget(aviso)

        if info.path is not None:
            ruta = QLabel(str(info.path))
            ruta.setObjectName("Dim")
            ruta.setWordWrap(True)
            layout.addWidget(ruta)

        for setting in settings:
            layout.addWidget(hline())
            linea = QHBoxLayout()
            linea.setSpacing(8)
            nombre = QLabel(setting.name)
            nombre.setWordWrap(True)
            linea.addWidget(nombre, 1)
            riesgo = QLabel(f"{setting.risk_label}" + (" · administrador" if setting.needs_admin else ""))
            riesgo.setStyleSheet(f"color: {RISK_COLORS.get(setting.risk, COLORS['text_muted'])}; font-size: 11.5px;")
            linea.addWidget(riesgo)
            layout.addLayout(linea)
            claves = QLabel("\n".join(f"• {linea_clave}" for linea_clave in setting.registry_lines) or "sin claves")
            claves.setObjectName("Dim")
            claves.setWordWrap(True)
            layout.addWidget(claves)

        pie = QHBoxLayout()
        pie.setSpacing(8)
        pie.addStretch(1)
        if info.trusted:
            self.action = QPushButton("Retirar la confianza")
            self.action.setObjectName("Ghost")
            self.action.setToolTip("Sus ajustes vuelven a quedar bloqueados hasta que los revises otra vez")
            self.action.clicked.connect(lambda: self.revoked.emit(self.info))
        else:
            self.action = QPushButton("Aceptar este plugin")
            self.action.setObjectName("Primary")
            self.action.setToolTip("Sus ajustes se podrán aplicar desde la página «Ajustes de Windows»")
            self.action.clicked.connect(self._confirm_accept)
        pie.addWidget(self.action)
        layout.addLayout(pie)
        accessibility.mark(self, QAccessible.Role.Grouping, info.name, f"{info.count} ajuste(s)")

    def _confirm_accept(self) -> None:
        pregunta = QMessageBox(self)
        pregunta.setWindowTitle("Aceptar el plugin")
        pregunta.setIcon(QMessageBox.Warning if self.info.dangerous else QMessageBox.Question)
        pregunta.setText(
            f"¿Confiar en «{self.info.name}»?\n\n"
            f"Sus {self.info.count} ajuste(s) aparecerán como aplicables en «Ajustes de Windows», "
            "con su análisis y su vuelta atrás. Nada se aplica solo: hay que marcarlos y confirmarlos.\n\n"
            "Se guarda la huella del archivo: si lo editas después, habrá que revisarlo otra vez."
        )
        pregunta.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        pregunta.button(QMessageBox.Yes).setText("Aceptar")
        pregunta.button(QMessageBox.No).setText("Cancelar")
        if pregunta.exec() == QMessageBox.Yes:
            self.accepted.emit(self.info)


class PluginsDialog(QDialog):
    """Ventana para revisar, aceptar y retirar plugins, y para ver los que dieron error."""

    changed = Signal(str)  # mensaje para la barra de estado

    def __init__(self, load_result: plugins.PluginLoad | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.load_result = load_result or plugins.loaded()
        self.setWindowTitle("Plugins")
        self.setMinimumSize(760, 620)
        self._build()
        self._fill()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        textos = QVBoxLayout()
        textos.setSpacing(2)
        titulo = QLabel("Plugins de ajustes")
        titulo.setObjectName("SectionTitle")
        accessibility.set_role(titulo, QAccessible.Role.Heading)
        textos.addWidget(titulo)
        subtitulo = Muted(
            "Ajustes que aporta un archivo JSON de la carpeta de plugins. Nada se aplica hasta "
            "que aceptes el plugin aquí, y cada ajuste sigue pasando por el mismo análisis y la "
            "misma confirmación que los del catálogo."
        )
        subtitulo.setWordWrap(True)
        textos.addWidget(subtitulo)
        head.addLayout(textos, 1)
        self.reload_button = QPushButton("Volver a leer")
        self.reload_button.setObjectName("Ghost")
        self.reload_button.setToolTip("Vuelve a leer la carpeta de plugins (por si has añadido alguno)")
        self.reload_button.clicked.connect(self.reload)
        head.addWidget(self.reload_button)
        layout.addLayout(head)
        layout.addWidget(hline())

        self.summary = Muted("")
        layout.addWidget(self.summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.rows_layout = QVBoxLayout(self.container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(10)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)

        pie = QHBoxLayout()
        pie.setSpacing(8)
        self.folder_button = QPushButton("Abrir la carpeta")
        self.folder_button.setObjectName("Ghost")
        self.folder_button.setToolTip(str(plugins.plugins_dir()))
        self.folder_button.clicked.connect(self.open_folder)
        pie.addWidget(self.folder_button)
        self.example_button = QPushButton("Crear un plugin de ejemplo")
        self.example_button.setObjectName("Ghost")
        self.example_button.setToolTip("Escribe la plantilla para copiar y editar")
        self.example_button.clicked.connect(self.create_example)
        pie.addWidget(self.example_button)
        self.copy_button = QPushButton("Copiar resumen")
        self.copy_button.setObjectName("Ghost")
        self.copy_button.clicked.connect(self.copy_summary)
        pie.addWidget(self.copy_button)
        pie.addStretch(1)
        cerrar = QPushButton("Cerrar")
        cerrar.setObjectName("Primary")
        cerrar.clicked.connect(self.accept)
        pie.addWidget(cerrar)
        layout.addLayout(pie)

    # ------------------------------------------------------------------
    def _clear(self) -> None:
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _fill(self) -> None:
        self._clear()
        resultado = self.load_result
        self.summary.setText(f"{resultado.summary()} · carpeta: {plugins.plugins_dir()}")
        if not resultado.plugins:
            vacio = Card(name="Sin plugins")
            vacio_layout = QVBoxLayout(vacio)
            vacio_layout.setContentsMargins(16, 14, 16, 14)
            vacio_layout.addWidget(SectionTitle("Todavía no hay plugins"))
            texto = Muted(
                "Pulsa «Crear un plugin de ejemplo» y edita el archivo con tus propios ajustes: "
                "se cargan al abrir esta ventana, sin tocar el programa."
            )
            texto.setWordWrap(True)
            vacio_layout.addWidget(texto)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, vacio)
        for info in resultado.plugins:
            ajustes = [setting for setting in resultado.settings if setting.plugin == info.id]
            tarjeta = PluginCard(info, ajustes)
            tarjeta.accepted.connect(self._accept)
            tarjeta.revoked.connect(self._revoke)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, tarjeta)

        if resultado.errors:
            errores = Card(name="Plugins con errores")
            errores_layout = QVBoxLayout(errores)
            errores_layout.setContentsMargins(16, 14, 16, 14)
            errores_layout.setSpacing(6)
            errores_layout.addWidget(SectionTitle("Archivos con errores"))
            aviso = Muted("No se cargan: corrige el archivo y vuelve a leer. Los demás siguen funcionando.")
            aviso.setWordWrap(True)
            errores_layout.addWidget(aviso)
            for archivo, motivo in resultado.errors:
                linea = QLabel(f"• {archivo}: {motivo}")
                linea.setWordWrap(True)
                linea.setStyleSheet(f"color: {COLORS['bad']}; font-size: 12px;")
                errores_layout.addWidget(linea)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, errores)

    # ------------------------------------------------------------------
    def reload(self) -> None:
        """Vuelve a leer la carpeta y rehace el catálogo (los ajustes viejos se van)."""
        self.load_result = plugins.reload_into_catalog()
        self._fill()
        self.changed.emit(
            f"Plugins leídos: {len(self.load_result.plugins)} plugin(s), "
            f"{len(self.load_result.errors)} con errores."
        )

    def _accept(self, info: plugins.PluginInfo) -> None:
        plugins.accept(info)
        info.trusted = True
        self._fill()
        self.changed.emit(f"Plugin «{info.name}» aceptado: sus ajustes ya se pueden aplicar.")

    def _revoke(self, info: plugins.PluginInfo) -> None:
        plugins.revoke(info.id)
        info.trusted = False
        self._fill()
        self.changed.emit(f"Plugin «{info.name}» retirado: sus ajustes quedan bloqueados.")

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(plugins.ensure_dir())))

    def create_example(self) -> None:
        destino = plugins.write_example()
        if destino is None:
            QMessageBox.warning(self, "No se pudo escribir", "No se pudo crear el archivo de ejemplo.")
            return
        self.reload()
        self.changed.emit(f"Plantilla de plugin en {destino}")

    def copy_summary(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(plugins.summary_text(self.load_result))
        self.summary.setText("Resumen de plugins copiado al portapapeles.")
