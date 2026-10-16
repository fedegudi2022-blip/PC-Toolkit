"""Tema visual de PC Toolkit: dos paletas y una hoja de estilos QSS.

La oscura «grafito» es fondo casi negro con superficies en grises fríos; la clara
«papel» usa blancos y grises fríos con el mismo acento tinta. En las dos, el acento es
**neutro** (casi blanco en oscuro, tinta en claro) y manda en los botones principales y
el foco; el verde queda para el estado saludable, el ámbar para los avisos y el rojo
para los errores, y todos los matices de superficie (hover, pulsado, selección, pistas)
salen de `COLORS`/`SURFACES`, así que el conjunto se reajusta desde un solo sitio.

El QSS es una plantilla con una clave por color: se rellena **al aplicarlo**, no
al importar el módulo, que es lo que permite cambiar de tema en caliente.
"""

from __future__ import annotations

import weakref
from typing import Any

from PySide6.QtGui import QColor, QFont, QPalette

from ..core.appearance import MODE_AUTO, resolve_mode
from ..core.config import (
    COLORS,
    DARK_COLORS,
    DARK_SURFACES,
    LIGHT_COLORS,
    LIGHT_SURFACES,
    SURFACES,
    active_palette,
    data_dir,
    use_palette,
)

# Lo que hay que volver a aplicar cuando cambia el tema, por widget. Cada entrada es una
# **receta de datos** —tipo y valores—, nunca una función que se guarde el widget: una función
# así lo mantendría vivo para siempre y el diccionario no soltaría nada. Con datos sueltos,
# la única referencia al widget es débil y lo que ya no existe se olvida en `repaint`.
#
# Va por `id` del widget porque el mismo sitio se repinta muchas veces —un mensaje de estado
# cambia de tono a cada rato—: se queda la última receta y no una lista que crezca.
_POR_REPINTAR: dict[int, tuple[weakref.ref, str, tuple[Any, ...]]] = {}

# Qué clave de la paleta dio cada color. Se llena con **las dos** paletas, no con la activa:
# quien construyó un widget con `COLORS["warn"]` ya tiene el tono resuelto del tema de
# entonces, y ese valor hay que poder reconocer también cuando ya se cambió de tema.
_CLAVES: dict[str, str] = {}


def _mapa_de_claves() -> dict[str, str]:
    mapa: dict[str, str] = {}
    for origen in (DARK_COLORS, LIGHT_COLORS, DARK_SURFACES, LIGHT_SURFACES):
        for clave, color in origen.items():
            if isinstance(color, str) and color.startswith("#"):
                mapa[color.upper()] = clave
    return mapa


_CLAVES = _mapa_de_claves()


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


def follow(color: str | None) -> str | None:
    """El mismo color, pero de la paleta de ahora.

    Un sitio que ya tenía resuelto el color —el tono de una insignia, el de un chip— necesita
    esta traducción para seguir al tema: se mira de qué clave salió y se devuelve la de la
    paleta activa. Si el color no era de la paleta (uno escrito a mano), se devuelve tal cual.
    """
    if not color:
        return color
    clave = _CLAVES.get(str(color).upper())
    return COLORS.get(clave, SURFACES.get(clave, color)) if clave else color


def styled(widget, plantilla: str, **valores) -> None:
    """Pinta un widget con un estilo que sigue a la paleta activa.

    `plantilla` se escribe como la hoja QSS: las claves de la paleta van entre llaves
    (`"color: {warn}; font-size: 12px;"`) y se rellenan **en cada aplicación**, no al llamar.
    Los `valores` extra son para lo que elige quien llama —el tono de un mensaje de estado,
    por ejemplo— y pueden darse de las dos maneras: como clave de la paleta (`tono="warn"`) o
    con el color ya resuelto (`tono=COLORS["good"]`). En los dos casos el valor se vuelve a
    resolver en cada aplicación, que es lo que hace que siga al tema.

    Se apunta el estilo para que `repaint` lo vuelva a aplicar cuando cambie el tema: un
    `setStyleSheet` con el color ya resuelto se queda con los colores del tema viejo, que es
    justo lo que obligaba a rehacer la ventana entera al cambiar entre claro y oscuro.
    """
    _apuntar(widget, "estilo", (plantilla, dict(valores)))


def pixmap(widget, name: str, size: int = 18, color: str | None = None) -> None:
    """Dibuja un icono en una etiqueta y lo deja siguiendo a la paleta.

    Es para lo que no se puede escribir como plantilla: un mapa de píxeles ya dibujado (la
    insignia de una tarjeta, un escudo de aviso). Con el color sin resolver —una clave de
    `COLORS` o directamente nada— el dibujo se rehace con la paleta nueva al cambiar de tema.
    """
    _apuntar(widget, "pixmap", (name, size, color))


def window_icon(widget, name: str, size: int = 64, color: str | None = None) -> None:
    """Pone el icono de la ventana (el de la barra de tareas) siguiendo a la paleta.

    La ventana no se rehace al cambiar de tema, así que su icono es el único que Qt no vuelve a
    pedir: si se dejara el mapa de píxeles del tema anterior, la barra de tareas conservaría el
    glifo viejo hasta cerrar el programa.
    """
    _apuntar(widget, "ventana", (name, size, color))


def chip(
    widget,
    color: str,
    *,
    alpha: int = 31,
    borde: int = 0,
    radio: int = 9,
    fuente: float = 11,
    relleno: str = "2px 8px",
) -> None:
    """Etiqueta redondeada del color de un estado (una «píldora»), siguiendo a la paleta.

    El relleno y el borde salen del mismo color con transparencia, y se calculan **en cada
    aplicación**: con el tono ya resuelto al construir la etiqueta, al cambiar de tema quedaba
    un fondo tintado con el tono del tema viejo sobre la tarjeta nueva (en claro, un ámbar
    oscuro sobre blanco).
    """
    _apuntar(widget, "chip", (color, alpha, borde, radio, fuente, relleno))


def dot(widget, color: str, size: int = 8) -> None:
    """Pinta el punto de color de una serie en una etiqueta, siguiendo a la paleta."""
    _apuntar(widget, "punto", (color, size))


def dot_icon(widget, color: str, size: int = 9) -> None:
    """Pone el punto de color de una leyenda como icono, siguiendo a la paleta.

    El punto es del color de su serie (acento, verde, ámbar o rojo), así que el mapa de
    píxeles se vuelve a dibujar al cambiar de tema, igual que los demás dibujos a mano.
    """
    _apuntar(widget, "punto_icono", (color, size))


def alpha_de(color: str, alpha: int) -> tuple[str, str, int]:
    """Valor para `styled`: el color pedido con transparencia, resuelto en cada tema.

    Se usa donde el fondo sale del mismo color con alfa (una píldora, un botón marcado) y hay
    que recalcularlo al cambiar de paleta. Es un dato —una tupla— y no una función, para que
    la receta siga sin poder quedarse con ningún widget.
    """
    return ("alpha", color, alpha)


def _resolver(valor):
    """Deja un color de la paleta como el tono que le toca en el tema de ahora.

    Acepta las dos formas en que se pide un color por aquí: la clave (`"warn"`) y el valor ya
    resuelto (`COLORS["warn"]`, el que había al construir el widget). Lo que no sea un color
    de la paleta se devuelve tal cual, así que un tamaño de letra o un grosor pasan de largo.
    """
    if isinstance(valor, tuple) and len(valor) == 3 and valor[0] == "alpha":
        return with_alpha(_tono(valor[1]), int(valor[2]))
    if not isinstance(valor, str):
        return valor
    return follow(COLORS.get(valor, SURFACES.get(valor, valor)))


def _tono(color, por_defecto: str = "text_muted") -> str:
    """Un tono de la paleta, venga como clave (`"warn"`) o ya resuelto (`COLORS["warn"]`).

    Lo que no sea un color válido cae al tono por defecto: es lo que evita que una clave escrita
    a mano (`"warn"`) llegue a `QColor` y deje el dibujo en blanco —un `QPen` con un color
    inválido no pinta nada, y eso no se ve como un error, se ve como un hueco.
    """
    resuelto = _resolver(color) if color else COLORS.get(por_defecto, COLORS["text_muted"])
    if not isinstance(resuelto, str) or not QColor(resuelto).isValid():
        return COLORS.get(por_defecto, COLORS["text_muted"])
    return resuelto


def _apuntar(widget, tipo: str, datos: tuple[Any, ...]) -> None:
    """Deja la receta apuntada y la aplica ya (la primera vez es la del constructor)."""
    try:
        referencia = weakref.ref(widget)
    except TypeError:  # pragma: no cover - un objeto de Qt que no admite referencias débiles
        return
    _POR_REPINTAR[id(widget)] = (referencia, tipo, datos)
    _aplicar(widget, tipo, datos)


def _aplicar(widget, tipo: str, datos: tuple[Any, ...]) -> None:
    """Pinta una receta sobre el widget vivo. Nunca deja escapar el error de un widget muerto."""
    try:
        if tipo == "estilo":
            plantilla, valores = datos
            completos: dict[str, Any] = {**COLORS, **SURFACES, **valores}
            for clave, valor in valores.items():
                completos[clave] = _resolver(valor)
            widget.setStyleSheet(plantilla.format_map(completos))
        elif tipo == "pixmap":
            from .icons import pixmap_for

            name, size, color = datos
            # Sin color propio se deja en `None`: el icono elige entonces el tono por defecto
            # al dibujarse, que es lo que hace que el mismo dibujo valga para los dos temas.
            widget.setPixmap(pixmap_for(name, size, _tono(color) if color else None))
        elif tipo == "ventana":
            from .icons import icon_from_pixmap, pixmap_for

            name, size, color = datos
            widget.setWindowIcon(icon_from_pixmap(pixmap_for(name, size, _tono(color, "accent"))))
        elif tipo == "chip":
            color, alpha, borde, radio, fuente, relleno = datos
            tono = _tono(color)
            partes = [
                f"background: {with_alpha(tono, alpha)};",
                f"color: {tono};",
                f"border-radius: {radio}px;",
                f"padding: {relleno};",
                f"font-size: {fuente}px;",
            ]
            if borde:
                partes.append(f"border: 1px solid {with_alpha(tono, borde)};")
            widget.setStyleSheet(" ".join(partes))
        elif tipo == "punto":
            from .icons import dot_pixmap

            color, size = datos
            widget.setPixmap(dot_pixmap(_tono(color, "accent"), size))
        elif tipo == "punto_icono":
            from .icons import dot_pixmap, icon_from_pixmap

            color, size = datos
            widget.setIcon(icon_from_pixmap(dot_pixmap(_tono(color, "accent"), size)))
    except RuntimeError:
        return  # el widget ya no existe: Qt destruyó su parte de C++


def repaint() -> int:
    """Vuelve a pintar la interfaz que ya está abierta con la paleta activa.

    Devuelve cuántos widgets se repintaron. Se llama justo después de `apply_theme` (que ya
    rehace la paleta de la aplicación y la hoja QSS entera) y es lo que deja el tema nuevo
    aplicado **sin rehacer la ventana**: los iconos se dibujan al pedirlos, lo que llevaba el
    color ya resuelto se vuelve a resolver aquí y lo que se pinta solo —arcos, medidores,
    gráficas— lee la paleta al pintarse. De paso se olvidan los widgets que ya no existen.
    """
    hechos = 0
    for clave, (referencia, tipo, datos) in list(_POR_REPINTAR.items()):
        destino = referencia()
        if destino is None:
            del _POR_REPINTAR[clave]
            continue
        _aplicar(destino, tipo, datos)
        hechos += 1
    return hechos


def stylesheet() -> str:
    """QSS final con la paleta activa y la marca de verificación ya generada."""
    check_icon = _check_indicator()
    return _QSS_TEMPLATE.format_map({**COLORS, **SURFACES}).replace("__CHECK_ICON__", check_icon)


def with_alpha(color: str, alpha: int) -> str:
    """Color de la paleta con transparencia, para las insignias de estado.

    Ojo con el orden: Qt lee un hexadecimal de ocho dígitos como `#AARRGGBB`, no como
    el `#RRGGBBAA` del CSS. Pegar el alfa al final —el color + `22`— daba un tono casi
    opaco en vez de uno al 13 %, así que aquí se escribe el alfa delante.
    """
    tono = QColor(color)
    valor = max(0, min(255, int(alpha)))
    return f"#{valor:02X}{tono.red():02X}{tono.green():02X}{tono.blue():02X}"


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
/* Su borde normal ya es el acento: para el foco se cambia al color que contrasta
   con él (`on_accent`: casi negro en la paleta oscura y blanco en la clara). */
QPushButton#Primary:focus {{ border-color: {on_accent}; }}
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
QPushButton#Ghost:hover {{ color: {text}; border-color: {accent_soft}; background: {hover}; }}
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
    /* D2: la sección activa se marca con el gris de selección y un borde de 1 px en
       el gris atenuado, no con el acento (que en el tema oscuro es casi blanco y
       brillaría más que el propio contenido). */
    background: {selected};
    color: {on_selected};
    border: 1px solid {text_muted};
    border-left: 3px solid {text_muted};
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
/* Un campo apagado tiene que verse apagado: sin esto hereda el aspecto del activo y
   da la impresión de que se puede escribir en él. */
QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled, QTimeEdit:disabled {{
    color: {text_dim};
    background: {bg_alt};
    border-color: {border_soft};
}}
/* Las flechas de los campos numéricos se colocan a mano: al redondear el borde del campo, Qt
   dibuja los botones pegados al borde exterior y las flechas asoman fuera del campo (se veía
   en los diálogos de umbrales). Con `subcontrol-origin: border` quedan dentro y con el mismo
   radio del campo. */
QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{ padding-right: 26px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button, QTimeEdit::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border: none;
    border-top-right-radius: 10px;
    background: transparent;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateEdit::down-button, QTimeEdit::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border: none;
    border-bottom-right-radius: 10px;
    background: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QDateEdit::up-button:hover,
QTimeEdit::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover,
QDateEdit::down-button:hover, QTimeEdit::down-button:hover {{ background: {hover}; }}
QComboBox:disabled::drop-down {{ border: none; }}
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
/* Una casilla marcada pero apagada no puede seguir pintada con el acento: se ve
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
    border: 1px solid {text_muted};
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

QTableWidget::item {{ padding: 6px 8px; border: 1px solid transparent; }}
QTableWidget::item:hover {{ background: {hover}; }}
QTableWidget::item:selected {{
    background: {selected};
    color: {text};
    border: 1px solid {text_muted};
}}
/* Igual que en las listas: con la ventana en segundo plano la selección se apaga. */
QTableWidget::item:selected:!active {{ background: {panel_alt}; color: {text_muted}; }}
QTreeWidget::item {{ padding: 6px 8px; }}
QTreeWidget::item:hover {{ background: {hover}; }}
QTreeWidget::item:selected {{ background: {selected}; color: {text}; }}
QTableWidget::item:alternate, QTreeWidget::item:alternate {{ background: {row}; }}
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
