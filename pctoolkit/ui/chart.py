"""Gráfica del historial de CPU, memoria y disco."""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QAccessible,
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.config import COLORS, SURFACES
from . import accessibility, theme

# Series de la gráfica: (clave de la muestra, etiqueta, clave de color de la paleta).
# Se guarda la **clave** y no el color: la paleta cambia al cambiar de tema, y un
# color copiado aquí al importar el módulo se quedaría con el del tema anterior.
# Los colores de serie son de datos, no de estado: azul, cian, naranja y gris azulado
# se distinguen entre sí (también con daltonismo) sin parecer una alerta cuando el valor
# es normal (el ámbar y el rojo quedan reservados para avisos y errores).
SERIES = (
    ("cpu", "CPU", "series_cpu"),
    ("ram", "Memoria", "series_ram"),
    ("disk", "Disco", "series_disk"),
)

# Radio del halo translúcido que acompaña a cada línea, en píxeles, y hasta dónde
# puede engordar en un tramo muy inclinado (antes era un trazo de 6 px de ancho).
HALO_HALF = 3.0
HALO_MAX = 9.0


class HistoryChart(QWidget):
    """Dibuja las muestras de las últimas horas con rejilla, área y cursor."""

    def __init__(self, hours: int = 24, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hours = hours
        self._samples: list = []
        self._visible: dict[str, bool] = {key: True for key, _, _ in SERIES}
        self._hover_index: int | None = None
        self._window_start = time.time() - hours * 3600
        self._window_end = time.time()
        # Datos ya pintados: `_revision` cambia cuando llegan muestras o se
        # oculta una serie, y el mapa de bits guardado se reutiliza mientras la
        # gráfica siga igual (véase `_chart_pixmap`).
        self._revision = 0
        self._cache: QPixmap | None = None
        self._cache_token: tuple | None = None
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(206)
        self.setMouseTracking(True)
        # La gráfica se pinta a mano: no hay texto que leer, así que se anuncia
        # como gráfica y se explica qué series dibuja.
        accessibility.mark(
            self,
            QAccessible.Role.Chart,
            f"Historial de las últimas {hours} horas",
            "Series de CPU, memoria y disco en porcentaje.",
        )

    # --- API -------------------------------------------------------------
    def set_samples(self, samples) -> None:
        self._samples = list(samples or [])
        self._window_end = time.time()
        self._window_start = self._window_end - self.hours * 3600
        if self._hover_index is not None and self._hover_index >= len(self._samples):
            self._hover_index = None
        self._revision += 1
        self.update()

    def set_series_visible(self, visible: dict[str, bool]) -> None:
        self._visible.update(visible)
        self._revision += 1
        self.update()

    def is_series_visible(self, key: str) -> bool:
        return bool(self._visible.get(key, True))

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    # --- Geometría -------------------------------------------------------
    def _plot_rect(self) -> QRectF:
        return QRectF(38.0, 10.0, max(10.0, self.width() - 52.0), max(10.0, self.height() - 34.0))

    def _x_for(self, timestamp: float, rect: QRectF) -> float:
        span = max(1.0, self._window_end - self._window_start)
        ratio = (timestamp - self._window_start) / span
        return rect.left() + max(0.0, min(1.0, ratio)) * rect.width()

    def _y_for(self, percent: float, rect: QRectF) -> float:
        ratio = max(0.0, min(100.0, float(percent))) / 100.0
        return rect.bottom() - ratio * rect.height()

    # --- Pintura ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.drawPixmap(0, 0, self._chart_pixmap())
        if self._samples:
            self._paint_hover(painter, self._plot_rect())

    def _cache_token_now(self) -> tuple:
        """Huella de lo que depende el dibujo: tamaño, datos, series y paleta.

        La paleta se lee en cada llamada porque cambiar de tema reescribe los
        colores en el sitio (`config.set_palette`), sin recrear los widgets.
        """
        return (
            self.width(),
            self.height(),
            round(self.devicePixelRatioF(), 3),
            self._revision,
            tuple(sorted(self._visible.items())),
            tuple(COLORS.values()),
            tuple(SURFACES.values()),
        )

    def _chart_pixmap(self) -> QPixmap:
        """Devuelve la gráfica ya pintada y la reaprovecha si nada ha cambiado.

        Dibujar las tres series cuesta decenas de milisegundos y el dibujo solo
        cambia cuando llega una muestra nueva o cambia el tamaño; guardarlo evita
        repetir ese trabajo en cada repintado (el cursor, los cambios de tema o
        cualquier otro aviso de la ventana se resuelven volcando el mapa de bits).
        """
        token = self._cache_token_now()
        if self._cache is not None and self._cache_token == token:
            return self._cache

        ratio = max(1.0, self.devicePixelRatioF())
        pixmap = QPixmap(
            max(1, round(self.width() * ratio)),
            max(1, round(self.height() * ratio)),
        )
        pixmap.setDevicePixelRatio(ratio)
        # Sin fondo propio: la tarjeta que contiene la gráfica ya trae el suyo.
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self._plot_rect()
        self._paint_background(painter, rect)
        if self._samples:
            painter.save()
            painter.setClipRect(rect)
            for key, _label, color_key in SERIES:
                if self._visible.get(key, True):
                    self._paint_series(painter, rect, key, COLORS[color_key])
            painter.restore()
        else:
            self._paint_empty(painter, rect)
        painter.end()

        self._cache = pixmap
        self._cache_token = token
        return pixmap

    def _paint_background(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(self.font())
        font.setPointSizeF(8.0)
        painter.setFont(font)

        # Rejilla horizontal con etiquetas de porcentaje
        for percent in (0, 25, 50, 75, 100):
            y = self._y_for(percent, rect)
            pen = QPen(QColor(SURFACES["row"] if percent % 50 else COLORS["border_soft"]))
            pen.setWidthF(1.0)
            if percent % 50:
                pen.setStyle(Qt.DotLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            if percent % 50 == 0:
                painter.setPen(QColor(COLORS["text_dim"]))
                painter.drawText(
                    QRectF(0, y - 8, rect.left() - 6, 16),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{percent}%",
                )

        # Rejilla vertical con horas
        ticks = 5
        for index in range(ticks):
            ratio = index / (ticks - 1)
            timestamp = self._window_start + ratio * (self._window_end - self._window_start)
            x = rect.left() + ratio * rect.width()
            pen = QPen(QColor(SURFACES["row"]))
            pen.setStyle(Qt.DotLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setPen(QColor(COLORS["text_dim"]))
            label = datetime.fromtimestamp(timestamp).strftime("%H:%M")
            align = Qt.AlignCenter
            if index == 0:
                align = Qt.AlignLeft | Qt.AlignVCenter
            elif index == ticks - 1:
                align = Qt.AlignRight | Qt.AlignVCenter
            painter.drawText(
                QRectF(x - 26, rect.bottom() + 3, 52, 16), align, label
            )

    def _paint_series(self, painter: QPainter, rect: QRectF, key: str, color: str) -> None:
        points = [
            QPointF(self._x_for(sample.timestamp, rect), self._y_for(getattr(sample, key), rect))
            for sample in self._samples
        ]
        if not points:
            return

        base = QColor(color)
        if len(points) == 1:
            painter.setBrush(base)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(points[0], 3.0, 3.0)
            return

        # El halo y la línea van por tramos sueltos, y el relleno como un polígono
        # corriente. Trazar la polilínea entera con una pluma ancha (que es lo que
        # hacía antes) obliga a Qt a construir el contorno de la línea completa
        # —miles de vértices, con el redondeo de cada unión— y a rellenarlo después:
        # eso cuesta decenas de milisegundos por serie, y con él se van el cursor y
        # los repintados. Un segmento suelto, un rectángulo o un polígono cuestan
        # milisegundos.
        halo: list[QRectF] = []
        segments: list[QLineF] = []
        for start, end in zip(points, points[1:]):
            top = min(start.y(), end.y())
            bottom = max(start.y(), end.y())
            # El halo era un trazo de 6 px de ancho: en un tramo inclinado eso ocupa
            # más altura vertical (3 px / cos θ), y por eso se ensancha en los picos.
            # Aquí el tramo se rellena con esa misma altura, sin trazar nada ancho.
            ancho, alto = end.x() - start.x(), end.y() - start.y()
            coseno = abs(ancho) / max(1e-6, (ancho * ancho + alto * alto) ** 0.5)
            mitad = min(HALO_MAX, HALO_HALF / max(coseno, 0.05))
            halo.append(QRectF(start.x(), top - mitad, max(0.0, ancho), bottom - top + mitad * 2))
            segments.append(QLineF(start, end))

        area = QPolygonF(points + [QPointF(points[-1].x(), rect.bottom()), QPointF(points[0].x(), rect.bottom())])
        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        # `theme.with_alpha` escribe el alfa delante (#AARRGGBB), que es como lo
        # lee Qt; pegarlo al final del hexadecimal daría un color casi opaco.
        gradient.setColorAt(0.0, QColor(theme.with_alpha(color, 60)))
        gradient.setColorAt(1.0, QColor(theme.with_alpha(color, 0)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPolygon(area)

        painter.setBrush(QColor(theme.with_alpha(color, 55)))
        painter.drawRects(halo)

        pen = QPen(base.lighter(115))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawLines(segments)

    def _paint_empty(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(self.font())
        font.setPointSizeF(10.0)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["text_dim"]))
        painter.drawText(rect, Qt.AlignCenter, "Recopilando datos…")

    def _paint_hover(self, painter: QPainter, rect: QRectF) -> None:
        if self._hover_index is None or not self._samples:
            return
        sample = self._samples[self._hover_index]
        x = self._x_for(sample.timestamp, rect)
        pen = QPen(QColor(SURFACES["track_hover"]))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        lines = [datetime.fromtimestamp(sample.timestamp).strftime("%d/%m %H:%M")]
        for key, label, color_key in SERIES:
            if not self._visible.get(key, True):
                continue
            y = self._y_for(getattr(sample, key), rect)
            painter.setBrush(QColor(COLORS[color_key]))
            painter.setPen(QPen(QColor(COLORS["bg"]), 1.5))
            painter.drawEllipse(QPointF(x, y), 3.2, 3.2)
            lines.append(f"{label}  {getattr(sample, key):.0f}%")

        font = QFont(self.font())
        font.setPointSizeF(8.4)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = max(metrics.horizontalAdvance(line) for line in lines) + 18
        height = len(lines) * (metrics.height() + 1) + 10
        box_x = min(max(rect.left() + 4, x + 10), rect.right() - width - 2)
        box_y = rect.top() + 6
        box = QRectF(box_x, box_y, width, height)
        painter.setPen(QPen(QColor(COLORS["border"]), 1.0))
        painter.setBrush(QColor(COLORS["panel_alt"]))
        painter.drawRoundedRect(box, 8, 8)
        text_y = box_y + 6
        for index, line in enumerate(lines):
            painter.setPen(QColor(COLORS["text"] if index else COLORS["text_muted"]))
            painter.drawText(
                QRectF(box_x + 9, text_y, width - 18, metrics.height() + 1),
                Qt.AlignLeft | Qt.AlignVCenter,
                line,
            )
            text_y += metrics.height() + 1

    # --- Interacción -----------------------------------------------------
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt)
        if not self._samples:
            return
        rect = self._plot_rect()
        position = event.position()
        if not rect.contains(position):
            if self._hover_index is not None:
                self._hover_index = None
                self.update()
            return
        closest = None
        best = float("inf")
        for index, sample in enumerate(self._samples):
            distance = abs(self._x_for(sample.timestamp, rect) - position.x())
            if distance < best:
                best = distance
                closest = index
        if closest != self._hover_index:
            self._hover_index = closest
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt)
        if self._hover_index is not None:
            self._hover_index = None
            self.update()
