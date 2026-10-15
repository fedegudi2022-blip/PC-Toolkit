"""Componentes visuales reutilizables de PC Toolkit."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.config import APP_NAME, COLORS, SURFACES, health_color
from . import accessibility, icons


def make_shadow(widget: QWidget, blur: int = 28, color: str | None = None, alpha: int = 110, dy: int = 8) -> None:
    """Sombra suave bajo un widget.

    El color se resuelve al llamar y no como valor por defecto: los valores por
    defecto se evalúan al importar el módulo, así que la sombra se quedaría con el
    fondo del tema que hubiera en ese momento. Sin color propio se usa el fondo de la
    paleta, que es el tono más neutro que hay en ella.
    """
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    shadow_color = QColor(color or COLORS["bg"])
    shadow_color.setAlpha(alpha)
    effect.setColor(shadow_color)
    widget.setGraphicsEffect(effect)


class Card(QFrame):
    """Tarjeta contenedora con borde redondeado.

    Es un `QFrame`, y para Qt un marco es un «borde»: un lector de pantalla lo
    anunciaría como tal. Aquí es el grupo que envuelve un contenido, así que se
    marca como tal y, si la tarjeta tiene un título visible, se le pasa su nombre
    en `name` (el lector lo lee al entrar en el grupo).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        soft: bool = False,
        hero: bool = False,
        name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HeroCard" if hero else ("CardSoft" if soft else "Card"))
        accessibility.mark(self, QAccessible.Role.Grouping, name)


class IconBadge(QLabel):
    """Cuadrado redondeado con un icono vectorial."""

    def __init__(
        self,
        name: str,
        size: int = 34,
        icon_size: int = 18,
        color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setPixmap(icons.pixmap_for(name, icon_size, color or COLORS["text_muted"]))


class BrandLogo(QLabel):
    """Logotipo de la aplicación, con nombre accesible.

    Una imagen no se puede leer: el nombre accesible es lo único que le llega al lector de
    pantalla, así que va aquí y no en cada sitio que enseña la marca. Si falta el archivo
    del logotipo (una copia sin `assets\\`), se dibuja el glifo de siempre: la marca no
    desaparece, pero tampoco se disfraza de logotipo algo que no lo es.
    """

    def __init__(self, size: int = 36, name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        logotipo = icons.logo_pixmap(size)
        if logotipo is None:
            self.setPixmap(icons.pixmap_for("cpu", max(16, size // 2), COLORS["accent"]))
        else:
            self.setPixmap(logotipo)
        accessibility.mark(self, QAccessible.Role.Graphic, name or APP_NAME)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("SectionTitle")
        # Un título de sección se anuncia como encabezado (y el lector lee el
        # texto de la etiqueta, que es su nombre accesible).
        accessibility.set_role(self, QAccessible.Role.Heading)


class Muted(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Muted")


class PageHeader(QWidget):
    """Encabezado de página con título, subtítulo y acciones a la derecha."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        accessibility.set_role(self.title, QAccessible.Role.Heading)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        # El subtítulo se ajusta en varias líneas: sin esto, un texto largo fija
        # el ancho mínimo de la página entera y aparece un desplazamiento
        # horizontal en ventanas no muy anchas.
        self.subtitle.setWordWrap(True)
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        layout.addLayout(texts)
        layout.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class ArcGauge(QWidget):
    """Indicador semicircular con abanico de marcas, al estilo del panel."""

    def __init__(
        self,
        title: str = "",
        unit: str = "%",
        subtitle: str = "",
        ticks: int = 39,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._title = title
        self._unit = unit
        self._subtitle = subtitle
        self._ticks = ticks
        self._accent = COLORS["accent"]
        # Texto propio para el centro, cuando el dato no es un porcentaje.
        self._display: str | None = None
        # Marca del umbral de aviso (en porcentaje): el abanico la pinta como una raya
        # aparte, para que se vea dónde empieza el aviso configurado.
        self._threshold: float | None = None
        self.setMinimumSize(170, 148)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(152)
        # Un indicador pintado a mano no tiene texto que leer: el lector de
        # pantalla solo puede anunciarlo por su nombre y su descripción, que se
        # actualizan con cada valor nuevo. Y como no se puede pulsar ni ajustar,
        # su rol es el de una gráfica, no el de un control.
        accessibility.mark(
            self, QAccessible.Role.Chart, title or "Indicador", "Sin datos todavía"
        )

    # --- API -------------------------------------------------------------
    def set_value(self, value: float, accent: str | None = None, subtitle: str | None = None) -> None:
        changed = (
            abs(self._value - float(value)) > 0.05
            or accent != self._accent
            or (subtitle is not None and subtitle != self._subtitle)
        )
        self._value = max(0.0, min(100.0, float(value)))
        if accent:
            self._accent = accent
        if subtitle is not None:
            self._subtitle = subtitle
        if changed:
            self.setAccessibleDescription(self._spoken_value())
            self.update()

    def _spoken_value(self) -> str:
        """Valor del indicador en palabras, para lectores de pantalla."""
        texto = f"{self._value:.0f}{self._unit}" if self._unit == "%" else f"{self._value:.0f} {self._unit}"
        return f"{texto} ({self._subtitle})" if self._subtitle else texto

    def set_display(self, main: str | None, subtitle: str | None = None) -> None:
        """Muestra un texto propio en el centro (p. ej. «1,2 MB/s»).

        Lo usan los medidores que no miden un porcentaje: la red se enseña en velocidad,
        porque un «0 %» con tráfico no dice nada. Con `None` vuelve al valor numérico.
        """
        nuevo = None if main is None else str(main)
        if nuevo == self._display and (subtitle is None or subtitle == self._subtitle):
            return
        self._display = nuevo
        if subtitle is not None:
            self._subtitle = subtitle
        self.setAccessibleDescription(
            f"{nuevo} ({self._subtitle})" if nuevo else self._spoken_value()
        )
        self.update()

    def set_threshold(self, percent: float | None) -> None:
        """Marca del umbral de aviso (el de las alertas configuradas).

        `None` la quita. No cambia el color del medidor: solo dibuja dónde queda el
        umbral, para que el cambio de color no llegue por sorpresa.
        """
        nuevo = None if percent is None else max(0.0, min(100.0, float(percent)))
        if nuevo == self._threshold:
            return
        self._threshold = nuevo
        self.update()

    # --- Pintura ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = self.width()
        height = self.height()
        center = QPointF(width / 2.0, height * 0.92)
        radius = min(width * 0.45, height * 0.86)
        inner = radius * 0.55
        # En coordenadas de pantalla la «Y» crece hacia abajo: 90° apunta arriba.
        start, end = 14.0, 166.0

        active_ticks = int(round(self._ticks * self._value / 100.0))
        accent = QColor(self._accent)
        dim = QColor(SURFACES["track"])

        # Sin halo: el abanico se sostiene solo con sus marcas, el color de su serie y la
        # marca del umbral. El resplandor radial que había antes era un resto del diseño
        # anterior y velaba el fondo de la tarjeta sin aportar información (D2).

        for index in range(self._ticks):
            angle = math.radians(start + (end - start) * index / max(1, self._ticks - 1))
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            outer = QPointF(center.x() + cos_a * radius, center.y() - sin_a * radius)
            inner_point = QPointF(center.x() + cos_a * inner, center.y() - sin_a * inner)

            if index < active_ticks:
                pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 60))
                pen.setWidthF(5.4)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawLine(inner_point, outer)
                pen = QPen(accent.lighter(118))
                pen.setWidthF(2.1)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
            else:
                pen = QPen(dim)
                pen.setWidthF(2.1)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
            painter.drawLine(inner_point, outer)

        # Marca del umbral: una raya que cruza el abanico justo por donde empieza el
        # aviso configurado, un poco más larga que las marcas normales.
        if self._threshold is not None:
            angle = math.radians(start + (end - start) * self._threshold / 100.0)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            marca = QPen(QColor(COLORS["text_muted"]))
            marca.setWidthF(2.2)
            marca.setCapStyle(Qt.FlatCap)
            painter.setPen(marca)
            painter.drawLine(
                QPointF(center.x() + cos_a * inner * 0.94, center.y() - sin_a * inner * 0.94),
                QPointF(center.x() + cos_a * radius * 1.07, center.y() - sin_a * radius * 1.07),
            )

        # Valor central
        value_text = self._display if self._display else f"{self._value:.0f}"
        value_font = QFont(self.font())
        # Un dato corto («42») va grande; uno con unidad («12,4 MB/s») necesita menos
        # cuerpo para no salirse del abanico.
        value_font.setPointSizeF(26.0 if len(value_text) <= 4 else (19.0 if len(value_text) <= 7 else 15.0))
        value_font.setWeight(QFont.DemiBold)
        painter.setFont(value_font)
        painter.setPen(QColor(COLORS["text"]))
        metrics = painter.fontMetrics()
        baseline = center.y() - 34.0
        value_rect = QRectF(center.x() - radius, baseline - 34.0, radius * 2, 34.0)
        painter.drawText(value_rect, Qt.AlignHCenter | Qt.AlignBottom, value_text)

        # Unidad (pequeña, a la derecha del número). Si el centro lleva un texto propio,
        # la unidad ya va dentro de él, así que no se repite.
        if not self._display:
            unit_font = QFont(self.font())
            unit_font.setPointSizeF(10.0)
            painter.setFont(unit_font)
            painter.setPen(QColor(COLORS["text_muted"]))
            unit_width = painter.fontMetrics().horizontalAdvance(self._unit)
            painter.drawText(
                QRectF(center.x() + math.floor(metrics.horizontalAdvance(value_text) / 2) + 2,
                       baseline - 24.0, unit_width + 8, 20),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._unit,
            )

        if self._subtitle:
            sub_font = QFont(self.font())
            sub_font.setPointSizeF(8.6)
            painter.setFont(sub_font)
            painter.setPen(QColor(COLORS["text_muted"]))
            # El detalle se dibuja bajo el valor, dentro del hueco del abanico.
            painter.drawText(
                QRectF(center.x() - radius, center.y() - 27.0, radius * 2, 18),
                Qt.AlignHCenter | Qt.AlignTop,
                self._subtitle,
            )


class HealthMeter(QWidget):
    """Barra vertical con degradado y marcador de la puntuación de salud."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._score = 100.0
        self._label = "Good"
        self.setFixedWidth(74)
        self.setMinimumHeight(220)
        # Una barra llena hasta la puntuación: el lector la trata como tal y lee
        # el valor («72 de 100») de la descripción.
        accessibility.mark(
            self, QAccessible.Role.ProgressBar, "Salud del equipo", "100 de 100"
        )

    def set_score(self, score: float, label: str | None = None) -> None:
        self._score = max(0.0, min(100.0, float(score)))
        if label:
            self._label = label
        self.setAccessibleDescription(f"{self._score:.0f} de 100 — {self._label}")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = self.width()
        height = self.height()
        top = 34.0
        bottom = height - 12.0
        bar_height = bottom - top
        bar_width = 14.0
        bar_x = 12.0

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS["bg_alt"]))
        painter.drawRoundedRect(QRectF(bar_x, top, bar_width, bar_height), 7, 7)

        # El relleno lleva el color del **estado** (cian, ámbar o rojo) y el degradado
        # solo le da volumen. Antes pintaba la escala entera —rojo abajo, verde arriba—,
        # así que una puntuación «Aceptable» enseñaba medio rojo y parecía una alarma
        # cuando el equipo estaba bien.
        base = QColor(health_color(self._score))
        fill_height = bar_height * self._score / 100.0
        gradient = QLinearGradient(0, bottom, 0, top)
        gradient.setColorAt(0.0, base.darker(125))
        gradient.setColorAt(1.0, base)
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(QRectF(bar_x, bottom - fill_height, bar_width, fill_height), 7, 7)

        # Marcas de escala
        pen = QPen(QColor(SURFACES["track"]))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        for step in range(0, 21):
            y = bottom - bar_height * step / 20.0
            length = 7.0 if step % 5 == 0 else 4.0
            painter.drawLine(QPointF(bar_x + bar_width + 4, y), QPointF(bar_x + bar_width + 4 + length, y))

        # Marcador de la puntuación
        marker_y = bottom - fill_height
        painter.setPen(QPen(QColor(COLORS["text"]), 1.6))
        painter.drawLine(QPointF(bar_x - 6, marker_y), QPointF(bar_x + bar_width + 2, marker_y))

        # Etiquetas
        label_font = QFont(self.font())
        label_font.setPointSizeF(8.0)
        painter.setFont(label_font)
        painter.setPen(QColor(COLORS["text_dim"]))
        painter.drawText(QRectF(bar_x + bar_width + 14, top - 8, 30, 16), Qt.AlignLeft | Qt.AlignVCenter, "100%")
        painter.drawText(
            QRectF(bar_x + bar_width + 14, bottom - 8, 30, 16), Qt.AlignLeft | Qt.AlignVCenter, "0%"
        )
        title_font = QFont(self.font())
        title_font.setPointSizeF(8.5)
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(COLORS["text_muted"]))
        painter.drawText(QRectF(0, 2, width, 16), Qt.AlignLeft | Qt.AlignVCenter, "SALUD")


class ArcProgress(QWidget):
    """Barra horizontal con porcentaje (para listas y desglose)."""

    def __init__(self, color: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ratio = 0.0
        # El color por defecto se resuelve ahora y no como argumento por defecto: los
        # valores por defecto se evalúan al importar el módulo, y entonces la barra se
        # quedaría con el acento del tema que hubiera en ese momento.
        self._color = color or COLORS["accent"]
        self._label = "Progreso"
        self.setFixedHeight(7)
        accessibility.mark(self, QAccessible.Role.ProgressBar, self._label, "0 %")

    def set_label(self, label: str) -> None:
        """Nombre de lo que mide la barra (para lectores de pantalla)."""
        self._label = label or "Progreso"
        self.setAccessibleName(self._label)
        self.setAccessibleDescription(f"{self._ratio * 100:.0f} %")

    def set_ratio(self, ratio: float, color: str | None = None) -> None:
        self._ratio = max(0.0, min(1.0, ratio))
        if color:
            self._color = color
        self.setAccessibleDescription(f"{self._ratio * 100:.0f} %")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        radius = self.height() / 2.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS["bg_alt"]))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)
        width = max(0.0, self.width() * self._ratio)
        if width > 0:
            color = QColor(self._color)
            gradient = QLinearGradient(0, 0, self.width(), 0)
            gradient.setColorAt(0.0, color.darker(135))
            gradient.setColorAt(1.0, color)
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(QRectF(0, 0, max(width, self.height()), self.height()), radius, radius)


class BarRow(QWidget):
    """Fila del analizador: nombre, ruta, tamaño y barra proporcional."""

    activated = Signal(object)

    def __init__(self, node, ratio: float, color: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.node = node
        color = color or COLORS["accent"]
        self.setCursor(Qt.PointingHandCursor)
        # Se puede llegar con el tabulador y abrir con Intro o Espacio (el estilo
        # `#DiskRow` le pinta el anillo de foco cuando lo tiene).
        self.setObjectName("DiskRow")
        self.setFocusPolicy(Qt.TabFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        badge = IconBadge("folder" if getattr(node, "is_dir", False) else "list", 22, 13, COLORS["text_dim"])
        header.addWidget(badge)
        name = QLabel(getattr(node, "name", ""))
        name.setStyleSheet("font-size: 12.5px;")
        header.addWidget(name)
        header.addStretch(1)
        from ..core.config import format_bytes

        size = QLabel(format_bytes(getattr(node, "size", 0)))
        size.setStyleSheet(f"font-size: 12.5px; font-weight: 600; color: {COLORS['text']};")
        header.addWidget(size)
        layout.addLayout(header)

        bar = ArcProgress(color)
        bar.set_ratio(ratio)
        bar.set_label(f"Espacio de {getattr(node, 'name', 'la carpeta')}")
        layout.addWidget(bar)

        path = QLabel(getattr(node, "path", ""))
        path.setObjectName("Dim")
        layout.addWidget(path)
        # La fila se abre con el teclado (Intro o Espacio): su nombre, su detalle y
        # el rol de botón son lo que anuncia el lector al llegar a ella.
        accessibility.mark(
            self,
            QAccessible.Role.Button,
            str(getattr(node, "name", "")),
            f"{format_bytes(getattr(node, 'size', 0))} — {getattr(node, 'path', '')}",
        )

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt)
        self.activated.emit(self.node)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.node)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self.node)
            event.accept()
            return
        super().keyPressEvent(event)


class ActionCard(Card):
    """Tarjeta pulsable con icono, título, descripción y flecha."""

    clicked = Signal()

    def __init__(
        self,
        icon_name: str,
        title: str,
        subtitle: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # Antes heredaba el estilo «Card» a secas: por eso una tarjeta pulsable y
        # una informativa se veían igual. Con su propio nombre (y su borde al
        # pasar por encima o al enfocarla) se entiende que se puede abrir.
        self.setObjectName("ActionCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)
        # Solo con el tabulador: quien usa el ratón no arrastra un anillo de foco.
        self.setFocusPolicy(Qt.TabFocus)
        # Se abre con Intro o Espacio, así que para el lector es un botón.
        accessibility.mark(self, QAccessible.Role.Button, title, subtitle)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 14, 12)
        layout.setSpacing(12)

        layout.addWidget(IconBadge(icon_name, 32, 17, COLORS["text_muted"]))

        texts = QVBoxLayout()
        texts.setSpacing(1)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 13.5px; font-weight: 600;")
        # El ajuste de línea permite que la tarjeta se estreche en ventanas pequeñas.
        title_label.setWordWrap(True)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("Muted")
        subtitle_label.setWordWrap(True)
        texts.addWidget(title_label)
        texts.addWidget(subtitle_label)
        layout.addLayout(texts)
        layout.addStretch(1)
        layout.addWidget(IconBadge("arrow", 22, 14, COLORS["text_dim"]))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def hline() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {COLORS['border_soft']}; border: none;")
    return line


def kv_row(label: str, value: str, parent: QWidget | None = None) -> tuple[QWidget, QLabel]:
    """Fila «etiqueta — valor» para paneles laterales; devuelve (fila, etiqueta de valor)."""
    widget = QWidget(parent)
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    left = QLabel(label)
    left.setObjectName("Muted")
    right = QLabel(value)
    right.setStyleSheet("font-size: 12.5px; font-weight: 600;")
    right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    layout.addWidget(left)
    layout.addStretch(1)
    layout.addWidget(right)
    return widget, right
