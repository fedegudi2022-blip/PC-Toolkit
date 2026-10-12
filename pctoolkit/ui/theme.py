"""Tema visual de PC Toolkit: dos paletas y una hoja de estilos QSS.

La oscura «grafito vivo» es un gris casi negro sin tinte azul; la clara «papel
cálido» usa blancos sucios con el mismo acento esmeralda oscurecido. En las dos,
un único acento manda en las acciones y los indicadores activos, y todos los
matices de superficie (hover, pulsado, selección, pistas) salen de
`COLORS`/`SURFACES`, así que el conjunto se reajusta desde un solo sitio.

El QSS es una plantilla con una clave por color: se rellena **al aplicarlo**, no
al importar el módulo, que es lo que permite cambiar de tema en caliente.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette

from ..core.appearance import MODE_AUTO, resolve_mode
from ..core.config import COLORS, SURFACES, active_palette, data_dir, use_palette


def apply_theme(app, mode: str = MODE_AUTO) -> str:
    """Aplica la paleta del modo pedido y devuelve la paleta que quedó activa.

    `mode` admite `auto` (la que tenga Windows), `dark` o `light`.
    """
    palette = use_palette(resolve_mode(mode))
    app.setStyle("Fusion")
    app.setPalette(_palette())
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(font)
    app.setStyleSheet(stylesheet())
    return palette


def stylesheet() -> str:
    """QSS final con la paleta activa y la marca de verificación ya generada."""
    check_icon = _check_indicator()
    return _QSS_TEMPLATE.format_map({**COLORS, **SURFACES}).replace("__CHECK_ICON__", check_icon)


def _check_indicator() -> str:
    """Guarda la marca de verificación del color del fondo del acento.

    La marca se dibuja del color de `on_accent`, que en la paleta oscura es casi
    negro y en la clara es blanco: por eso cada paleta tiene su propio archivo, y
    si se cambiara una sola vez la imagen quedaría invisible al cambiar de tema.
    """
    from PySide6.QtCore import QCoreApplication

    folder = data_dir() / "tema"
    target = folder / f"check_{active_palette()}.png"
    if QCoreApplication.instance() is None:
        return "none"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            from .icons import pixmap_for

            pixmap_for("check", 14, COLORS["on_accent"], 2.6).save(str(target), "PNG")
    except (OSError, RuntimeError):
        return "none"
    return target.as_posix()


def _palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.Base, QColor(COLORS["panel"]))
    palette.setColor(QPalette.AlternateBase, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.Button, QColor(COLORS["panel"]))
    palette.setColor(QPalette.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.Highlight, QColor(COLORS["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(COLORS["on_accent"]))
    palette.setColor(QPalette.ToolTipBase, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.ToolTipText, QColor(COLORS["text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(COLORS["text_dim"]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(COLORS["text_dim"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(COLORS["text_dim"]))
    return palette


_QSS_TEMPLATE = """
* {{
    font-family: "Segoe UI", "Segoe UI Variable", Roboto, Arial;
    outline: none;
}}

QWidget {{
    color: {text};
    background: transparent;
}}

QMainWindow, #Root {{
    background: {bg};
}}

/* --- Tarjetas -------------------------------------------------------- */
/* Cada nivel de superficie tiene su propia luz: el degradado suave de las
   tarjetas las separa del fondo sin necesidad de bordes gruesos. */
#Card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                 stop:0 {panel_alt}, stop:1 {panel});
    border: 1px solid {border_soft};
    border-radius: 18px;
}}
#Card:hover {{ border-color: {border}; }}
#CardSoft {{
    background: {bg_alt};
    border: 1px solid {border_soft};
    border-radius: 16px;
}}
#HeroCard {{
    background: qlineargradient(x1:0, y1:0, x2:0.9, y2:1,
                 stop:0 {panel_alt}, stop:0.45 {panel}, stop:1 {bg_alt});
    border: 1px solid {border};
    border-radius: 20px;
}}

/* --- Texto ----------------------------------------------------------- */
#PageTitle {{ font-size: 26px; font-weight: 600; }}
#PageSubtitle {{ color: {text_muted}; font-size: 13px; }}
#SectionTitle {{ font-size: 15px; font-weight: 600; }}
#CardTitle {{ font-size: 15px; font-weight: 600; }}
#CardSubtitle {{ color: {text_muted}; font-size: 12px; }}
#Muted {{ color: {text_muted}; font-size: 12px; }}
#Dim {{ color: {text_dim}; font-size: 11px; }}
#MetricValue {{ font-size: 20px; font-weight: 600; }}
#Badge {{
    color: {text_muted};
    background: {bg_alt};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
}}
#HealthBadge {{
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}}
#BrandLabel {{ font-size: 19px; font-weight: 700; letter-spacing: 4px; }}
#BrandSub {{ color: {text_dim}; font-size: 10px; letter-spacing: 2px; }}
#StatBig {{ font-size: 24px; font-weight: 700; }}

/* --- Botones --------------------------------------------------------- */
QPushButton {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 11px;
    padding: 7px 14px;
    color: {text};
    font-size: 12px;
}}
QPushButton:hover {{ background: {hover}; border-color: {accent_soft}; }}
QPushButton:pressed {{ background: {pressed}; }}
/* En Qt no hay «outline»: el anillo de foco del teclado es el borde del propio
   control. Sin esto, con el tabulador no se ve dónde estás. */
QPushButton:focus {{ border-color: {accent}; }}
/* Los estados deshabilitados van **después** de los de hover: si no, un control
   apagado se ilumina al pasar por encima y parece que se puede pulsar. */
QPushButton:disabled {{
    color: {text_dim};
    border-color: {border_soft};
    background: {bg_alt};
}}

QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                 stop:0 {accent_glow}, stop:1 {accent});
    border: 1px solid {accent_glow};
    color: {on_accent};
    font-weight: 700;
    /* Mismo relleno vertical que el resto de botones: así los de una misma fila
       (por ejemplo «Actualizar estado» y «Guardar y programar») quedan a la
       misma altura en vez de bailar un par de píxeles. */
    padding: 7px 18px;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                 stop:0 {primary_hover}, stop:1 {accent_glow});
    border-color: {primary_hover};
}}
QPushButton#Primary:pressed {{ background: {accent_soft}; }}
/* Su borde normal ya es el acento: para el foco se aclara a blanco. */
QPushButton#Primary:focus {{ border-color: #FFFFFF; }}
QPushButton#Primary:disabled {{
    background: {primary_disabled_bg};
    border-color: {primary_disabled_border};
    color: {primary_disabled_text};
}}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {border};
    color: {text_muted};
}}
QPushButton#Ghost:hover {{ color: {text}; border-color: {accent_soft}; }}
QPushButton#Ghost:focus {{ color: {text}; border-color: {accent}; }}
QPushButton#Ghost:disabled {{
    color: {text_dim};
    border-color: {border_soft};
    background: transparent;
}}

QPushButton#Danger {{
    background: transparent;
    border: 1px solid {danger_border};
    color: {bad};
}}
QPushButton#Danger:hover {{ background: {danger_hover}; }}
QPushButton#Danger:focus {{ border-color: {bad}; color: {bad}; }}
QPushButton#Danger:disabled {{ color: {text_dim}; border-color: {border_soft}; background: transparent; }}

QPushButton#Legend {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 5px 10px;
    color: {text_dim};
    font-size: 11.5px;
}}
QPushButton#Legend:hover {{ color: {text}; }}
QPushButton#Legend:focus {{ border-color: {accent}; color: {text}; }}
QPushButton#Legend:checked {{
    background: {hover};
    border: 1px solid {border};
    color: {text};
}}

QPushButton#Chip {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 14px;
    padding: 5px 12px;
    color: {text};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#Chip:hover {{
    color: {on_accent};
    background: {accent};
    border-color: {accent_glow};
}}
QPushButton#Chip:focus {{ border-color: {accent_glow}; }}
/* Un «chip» deshabilitado no puede quedarse con el acento del hover. */
QPushButton#Chip:disabled {{
    color: {text_dim};
    background: {bg_alt};
    border-color: {border_soft};
}}

QPushButton#SidebarItem {{
    text-align: left;
    padding: 9px 14px;
    /* Los dos estados llevan los mismos grosores de borde (el izquierdo es la
       barra de la página activa): así el texto y el icono no saltan de sitio al
       cambiar de sección. */
    border: 1px solid transparent;
    border-left: 3px solid transparent;
    border-radius: 11px;
    color: {text_muted};
    font-size: 13px;
    background: transparent;
}}
QPushButton#SidebarItem:hover {{ background: {hover}; color: {text}; }}
QPushButton#SidebarItem:focus {{
    background: {hover};
    color: {text};
    border-color: {accent_soft};
}}
QPushButton#SidebarItem:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                 stop:0 {selected}, stop:1 {panel_alt});
    color: {on_selected};
    border: 1px solid {accent_soft};
    border-left: 3px solid {accent};
    font-weight: 700;
}}

/* --- Campos ---------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QTextEdit, QPlainTextEdit {{
    background: {bg_alt};
    border: 1px solid {border};
    border-radius: 11px;
    padding: 7px 10px;
    selection-background-color: {accent};
    selection-color: {on_accent};
    color: {text};
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus {{
    border-color: {accent};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 10px;
    selection-background-color: {selected};
    padding: 4px;
}}
QDateEdit::drop-down {{ border: none; width: 18px; }}

QCheckBox {{ color: {text_muted}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border-radius: 5px;
    border: 1px solid {border};
    background: {bg_alt};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
    image: url(__CHECK_ICON__);
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox:focus {{ color: {text}; }}
QCheckBox:focus::indicator {{ border-color: {accent}; }}
/* Una casilla marcada pero apagada no puede seguir pintada en esmeralda: se ve
   como si estuviera activa. */
QCheckBox::indicator:disabled {{ background: {bg_alt}; border-color: {border_soft}; }}
QCheckBox::indicator:checked:disabled {{
    background: {border};
    border-color: {border};
    image: none;
}}

QRadioButton {{ color: {text_muted}; font-size: 12px; spacing: 8px; }}
QRadioButton::indicator {{
    width: 15px; height: 15px;
    border-radius: 8px;
    border: 1px solid {border};
    background: {bg_alt};
}}
QRadioButton::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QRadioButton:focus {{ color: {text}; }}
QRadioButton:focus::indicator {{ border-color: {accent}; }}
QRadioButton::indicator:disabled {{ background: {bg_alt}; border-color: {border_soft}; }}
QRadioButton::indicator:checked:disabled {{ background: {border}; border-color: {border}; }}

/* --- Listas y tablas ------------------------------------------------- */
QListWidget, QTableWidget, QTreeWidget {{
    background: {bg_alt};
    border: 1px solid {border_soft};
    border-radius: 13px;
    padding: 6px;
    alternate-background-color: {row};
    gridline-color: {border_soft};
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 9px; color: {text}; }}
QListWidget::item:selected {{
    background: {selected};
    color: {text};
    border: 1px solid {accent_soft};
}}
QListWidget::item:hover {{ background: {hover}; }}
QListWidget::item:alternate {{ background: {row}; }}
/* Con la ventana en segundo plano la selección se apaga, para no confundirla
   con la de verdad. */
QListWidget::item:selected:!active {{
    background: {panel_alt};
    color: {text_muted};
    border: 1px solid {border_soft};
}}
QListWidget:focus {{ border: 1px solid {accent_soft}; }}

/* --- Filas y tarjetas pulsables -------------------------------------- */
/* Se pueden recorrer con el tabulador y abrir con Intro o Espacio: el foco tiene
   que verse igual que en un botón. */
#ActionCard {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                 stop:0 {panel_alt}, stop:1 {panel});
    border: 1px solid {border_soft};
    border-radius: 18px;
}}
#ActionCard:hover {{ border-color: {accent_soft}; }}
#ActionCard:focus {{ border: 1px solid {accent}; }}
#DiskRow {{ border: 1px solid transparent; border-radius: 12px; }}
#DiskRow:hover {{ background: {hover}; }}
#DiskRow:focus {{ border: 1px solid {accent}; background: {hover}; }}

/* --- Filas de proceso (Optimizar memoria) ---------------------------- */
#ListHeader {{
    color: {text_dim};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
#ProcRow {{ background: transparent; border: 1px solid transparent; border-radius: 12px; }}
#ProcRow:hover {{ background: {hover}; border-color: {border_soft}; }}
#ProcRowAlt {{
    background: {row};
    border: 1px solid {border_soft};
    border-radius: 12px;
}}
#ProcRowAlt:hover {{ background: {hover}; }}
#ProcName {{ font-size: 13px; font-weight: 600; color: {text}; }}
#ProcValue {{ font-size: 13.5px; font-weight: 700; }}
/* El puesto de la fila se pinta desde el código: su color depende del consumo de
   cada proceso, algo que la hoja de estilos no puede decidir. */

QTableWidget::item {{ padding: 6px 8px; }}
QTableWidget::item:selected {{ background: {selected}; }}
QHeaderView::section {{
    background: {panel};
    color: {text_muted};
    border: none;
    border-bottom: 1px solid {border};
    padding: 8px;
    font-size: 11px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {panel}; border: none; }}

/* --- Progreso y scroll ---------------------------------------------- */
QProgressBar {{
    background: {bg_alt};
    border: 1px solid {border_soft};
    border-radius: 8px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                 stop:0 {accent_soft}, stop:1 {accent});
}}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px 4px 0; }}
QScrollBar::handle:vertical {{ background: {track}; border-radius: 6px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {track_hover}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 4px 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {track}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; height: 0; width: 0;
}}

/* --- Controles deslizantes ------------------------------------------ */
QSlider::groove:horizontal {{
    height: 6px;
    border-radius: 3px;
    background: {border_soft};
}}
QSlider::sub-page:horizontal {{
    height: 6px;
    border-radius: 3px;
    background: {accent};
}}
QSlider::handle:horizontal {{
    width: 16px; height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {text};
    border: 2px solid {accent};
}}
QSlider::handle:horizontal:hover {{ border-color: {accent_glow}; }}

/* --- Otros ----------------------------------------------------------- */
QToolTip {{
    background: {panel_alt};
    color: {text};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 8px;
}}
QSplitter::handle {{ background: {border_soft}; width: 1px; }}
QMenu {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 11px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 18px; border-radius: 8px; }}
QMenu::item:selected {{ background: {hover}; }}
QMenu::separator {{ height: 1px; background: {border_soft}; margin: 5px 8px; }}
QStatusBar {{
    background: {panel};
    color: {text_muted};
    border-top: 1px solid {border_soft};
    font-size: 12px;
}}
QStatusBar::item {{ border: none; }}
QDialog {{ background: {bg}; }}
"""
