"""Diálogo de preferencias: alertas por umbral, bandeja e historial."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.alerts import METRIC_CPU, METRIC_DISK, METRIC_RAM, AlertConfig
from ..core.appearance import MODE_LABELS, MODES, describe_mode
from ..core.config import APP_VERSION, COLORS
from ..core.history import HistoryStore
from ..core.settings import AppSettings
from .widgets import Card, IconBadge, Muted, SectionTitle, hline


class AlertSettingsDialog(QDialog):
    """Permite ajustar umbrales, avisos de bandeja, historial y actualizaciones."""

    def __init__(
        self,
        settings: AppSettings,
        store: HistoryStore,
        tray_available: bool = True,
        parent: QWidget | None = None,
        updates=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.config = settings.alert_config
        #: Controlador de actualizaciones de la ventana (puede no haberlo, en las pruebas).
        self.updates = updates
        self.setWindowTitle("Alertas, bandeja y actualizaciones")
        self.setMinimumWidth(520)
        self._build(tray_available)

    # ------------------------------------------------------------------
    def _build(self, tray_available: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("shield", 36, 19, COLORS["accent_glow"]))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel("Alertas y bandeja del sistema")
        title.setObjectName("SectionTitle")
        subtitle = QLabel("Avisa cuando el equipo se acerque a sus límites.")
        subtitle.setObjectName("Muted")
        texts.addWidget(title)
        texts.addWidget(subtitle)
        head.addLayout(texts, 1)
        layout.addLayout(head)

        # --- Umbrales -----------------------------------------------------
        thresholds = Card(soft=True, name="Avisos por umbral")
        form = QFormLayout(thresholds)
        form.setContentsMargins(16, 14, 16, 16)
        form.setSpacing(10)

        self.enabled_check = QCheckBox("Avisar cuando se supere un umbral")
        self.enabled_check.setChecked(self.config.enabled)
        form.addRow("", self.enabled_check)

        self.spins: dict[str, QSpinBox] = {}
        for metric, label in ((METRIC_CPU, "CPU"), (METRIC_RAM, "Memoria RAM"), (METRIC_DISK, "Disco del sistema")):
            spin = QSpinBox()
            spin.setRange(10, 100)
            spin.setSuffix(" %")
            spin.setValue(int(round(self.config.threshold_for(metric))))
            spin.setFixedWidth(110)
            self.spins[metric] = spin
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(spin)
            row_layout.addStretch(1)
            form.addRow(f"Umbral de {label}", row)

        self.sustained_spin = QSpinBox()
        self.sustained_spin.setRange(1, 30)
        self.sustained_spin.setValue(self.config.sustained_samples)
        self.sustained_spin.setSuffix(" muestras")
        self.sustained_spin.setFixedWidth(130)
        form.addRow("Sostenido durante", self.sustained_spin)

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(1, 240)
        self.cooldown_spin.setValue(self.config.cooldown_minutes)
        self.cooldown_spin.setSuffix(" min")
        self.cooldown_spin.setFixedWidth(110)
        form.addRow("No repetir el aviso en", self.cooldown_spin)
        layout.addWidget(thresholds)

        # --- Apariencia ----------------------------------------------------
        appearance = Card(soft=True, name="Apariencia")
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(16, 14, 16, 16)
        appearance_layout.setSpacing(8)
        appearance_layout.addWidget(SectionTitle("Apariencia"))
        appearance_layout.addWidget(hline())

        appearance_row = QWidget()
        appearance_row_layout = QHBoxLayout(appearance_row)
        appearance_row_layout.setContentsMargins(0, 0, 0, 0)
        appearance_row_layout.setSpacing(10)
        appearance_row_layout.addWidget(QLabel("Tema de la aplicación"))
        self.theme_combo = QComboBox()
        for mode in MODES:
            self.theme_combo.addItem(MODE_LABELS[mode], mode)
        # Si se sigue a Windows, el aviso de abajo dice en qué modo está ahora
        # mismo: así se entiende por qué se ve claro u oscuro sin tocar nada.
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.settings.theme_mode)))
        self.theme_combo.setMinimumWidth(180)
        self.theme_combo.setToolTip(
            "«Seguir a Windows» usa el mismo modo que el sistema y cambia con él. "
            "El resto de opciones lo fijan a mano."
        )
        self.theme_combo.currentIndexChanged.connect(self._update_theme_note)
        appearance_row_layout.addWidget(self.theme_combo)
        appearance_row_layout.addStretch(1)
        appearance_layout.addWidget(appearance_row)

        self.theme_note = Muted(describe_mode(self.settings.theme_mode))
        self.theme_note.setWordWrap(True)
        appearance_layout.addWidget(self.theme_note)
        layout.addWidget(appearance)

        # --- Bandeja e historial -------------------------------------------
        extras = Card(soft=True, name="Ventana, bandeja e historial")
        extras_layout = QVBoxLayout(extras)
        extras_layout.setContentsMargins(16, 14, 16, 16)
        extras_layout.setSpacing(8)
        extras_layout.addWidget(SectionTitle("Ventana, bandeja e historial"))
        extras_layout.addWidget(hline())

        self.tray_check = QCheckBox("Mostrar el icono de estado en la bandeja del sistema")
        self.tray_check.setChecked(self.settings.tray_enabled and tray_available)
        self.tray_check.setEnabled(tray_available)
        extras_layout.addWidget(self.tray_check)

        self.notify_check = QCheckBox("Mostrar una notificación al superar un umbral")
        self.notify_check.setChecked(self.settings.notify_on_alert)
        extras_layout.addWidget(self.notify_check)

        self.close_check = QCheckBox("Al cerrar la ventana, mantener la app en la bandeja")
        self.close_check.setChecked(self.settings.close_to_tray)
        self.close_check.setEnabled(tray_available)
        extras_layout.addWidget(self.close_check)

        extras_layout.addWidget(hline())
        extras_layout.addWidget(SectionTitle("Consumo de recursos"))
        self.eco_check = QCheckBox("Modo eco: muestrear más despacio y gastar menos CPU y RAM")
        self.eco_check.setChecked(self.settings.eco_mode)
        self.eco_check.setToolTip(
            "Con el modo eco, PC Toolkit mide cada 3,5 s en vez de cada 1,2 s y no recorre la "
            "lista de procesos. Las mediciones siguen siendo válidas, pero se actualizan menos."
        )
        extras_layout.addWidget(self.eco_check)

        self.eco_hidden_check = QCheckBox("Aplicar el modo eco mientras la ventana está oculta")
        self.eco_hidden_check.setChecked(self.settings.eco_when_hidden)
        self.eco_hidden_check.setToolTip(
            "Cuando la aplicación solo vive en la bandeja, nadie mira las gráficas: se baja el "
            "ritmo automáticamente."
        )
        extras_layout.addWidget(self.eco_hidden_check)

        history_row = QWidget()
        history_layout = QHBoxLayout(history_row)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(8)
        self.history_check = QCheckBox("Guardar historial de métricas cada")
        self.history_check.setChecked(self.settings.history_enabled)
        self.history_spin = QSpinBox()
        self.history_spin.setRange(5, 3600)
        self.history_spin.setSuffix(" s")
        self.history_spin.setValue(self.settings.history_interval_seconds)
        self.history_spin.setFixedWidth(110)
        history_layout.addWidget(self.history_check)
        history_layout.addWidget(self.history_spin)
        history_layout.addStretch(1)
        extras_layout.addWidget(history_row)

        muestras = self.store.count()
        size = self.store.size_bytes()
        from ..core.config import format_bytes, format_integer

        extras_layout.addWidget(
            Muted(
                f"Historial actual: {format_integer(muestras)} muestras ({format_bytes(size)}) en "
                f"{self.store.path.name}. Se conservan las últimas 24 horas."
            )
        )
        if not tray_available:
            extras_layout.addWidget(Muted("Este sistema no tiene bandeja disponible."))
        layout.addWidget(extras)

        # --- Actualizaciones ------------------------------------------------
        # La comprobación automática solo avisa: descargar e instalar siempre pasa por
        # el diálogo de la versión nueva. Aquí se decide si se comprueba al arrancar y
        # se puede pedir una comprobación en el momento.
        updates_card = Card(soft=True, name="Actualizaciones")
        updates_layout = QVBoxLayout(updates_card)
        updates_layout.setContentsMargins(16, 14, 16, 16)
        updates_layout.setSpacing(8)
        updates_layout.addWidget(SectionTitle("Actualizaciones"))
        updates_layout.addWidget(hline())

        self.updates_check = QCheckBox("Buscar actualizaciones al arrancar")
        self.updates_check.setChecked(self.settings.check_updates)
        self.updates_check.setToolTip(
            "Como mucho una vez al día. Solo avisa: descargar e instalar es siempre una "
            "decisión tuya."
        )
        updates_layout.addWidget(self.updates_check)

        updates_row = QWidget()
        updates_row_layout = QHBoxLayout(updates_row)
        updates_row_layout.setContentsMargins(0, 0, 0, 0)
        updates_row_layout.setSpacing(10)
        self.updates_button = QPushButton("Buscar actualizaciones")
        self.updates_button.setObjectName("Ghost")
        self.updates_button.clicked.connect(self._search_updates)
        updates_row_layout.addWidget(self.updates_button)
        updates_row_layout.addStretch(1)
        updates_layout.addWidget(updates_row)

        self.updates_status = Muted(f"Versión instalada: {APP_VERSION}")
        self.updates_status.setWordWrap(True)
        self.updates_status.setAccessibleName("Estado de las actualizaciones")
        updates_layout.addWidget(self.updates_status)
        if self.updates is None:
            self.updates_button.setEnabled(False)
        else:
            # El diálogo escucha al controlador: así el botón puede contar qué pasó
            # (al día, versión nueva o error) sin bloquear la ventana mientras se consulta.
            self.updates.status.connect(self.updates_status.setText)
            self.updates.checked.connect(self._on_checked)
            self.updates.failed.connect(self._on_failed)
        layout.addWidget(updates_card)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.button(QDialogButtonBox.Save).setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    @property
    def theme_mode(self) -> str:
        """Modo de apariencia elegido en el diálogo («auto», «dark» o «light»)."""
        return str(self.theme_combo.currentData() or "auto")

    def _update_theme_note(self) -> None:
        """Explica qué supone el modo elegido, mirando cómo está Windows."""
        self.theme_note.setText(describe_mode(self.theme_mode))

    def apply(self) -> AlertConfig:
        """Guarda los valores en las preferencias y devuelve la configuración."""
        config = AlertConfig(
            enabled=self.enabled_check.isChecked(),
            sustained_samples=self.sustained_spin.value(),
            cooldown_minutes=self.cooldown_spin.value(),
        )
        for metric, spin in self.spins.items():
            config.set_threshold(metric, spin.value())
        self.config = config

        self.settings.alert_config = config
        self.settings.theme_mode = self.theme_mode
        self.settings.tray_enabled = self.tray_check.isChecked()
        self.settings.notify_on_alert = self.notify_check.isChecked()
        self.settings.close_to_tray = self.close_check.isChecked() and self.tray_check.isChecked()
        self.settings.eco_mode = self.eco_check.isChecked()
        self.settings.eco_when_hidden = self.eco_hidden_check.isChecked()
        self.settings.history_enabled = self.history_check.isChecked()
        self.settings.history_interval_seconds = self.history_spin.value()
        self.settings.check_updates = self.updates_check.isChecked()
        self.settings.sync()
        return config

    # ------------------------------------------------------------------
    def _search_updates(self) -> None:
        """Pide una comprobación de actualizaciones (sin bloquear la ventana)."""
        if self.updates is None:
            return
        self.updates_status.setText("Buscando actualizaciones…")
        self.updates.check_now()

    def _on_checked(self, release) -> None:
        """Resultado de una comprobación pedida a mano."""
        if release is None:
            self.updates_status.setText(
                f"Ya tienes la última versión ({APP_VERSION}). Se vuelve a comprobar dentro de "
                "un día."
            )

    def _on_failed(self, message: str) -> None:
        self.updates_status.setText(message)

    def accept(self) -> None:  # noqa: D102 (Qt)
        self.apply()
        super().accept()
