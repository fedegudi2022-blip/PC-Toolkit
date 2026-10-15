<div align="center">

# PC Toolkit

**Mantenimiento y diagnóstico para Windows, sin ruido.**

Libera RAM, controla lo que arranca con Windows, programa el mantenimiento, mide el equipo en vivo
y exporta informes. Python + PySide6, un solo `.exe`, instalación por usuario y **sin permisos de
administrador**.

[![Última versión](https://img.shields.io/github/v/release/fedegudi2022-blip/PC-Toolkit?label=versi%C3%B3n&color=22c55e)](https://github.com/fedegudi2022-blip/PC-Toolkit/releases/latest)
[![Descargas](https://img.shields.io/github/downloads/fedegudi2022-blip/PC-Toolkit/total?color=22c55e)](https://github.com/fedegudi2022-blip/PC-Toolkit/releases)
[![Integración](https://github.com/fedegudi2022-blip/PC-Toolkit/actions/workflows/integracion.yml/badge.svg)](https://github.com/fedegudi2022-blip/PC-Toolkit/actions/workflows/integracion.yml)
[![Licencia](https://img.shields.io/badge/licencia-MIT-22c55e)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078d6)](#descargar)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab)](#desde-el-c%C3%B3digo)

[Descargar](#descargar) · [Qué hace](#qué-hace) · [Capturas](#capturas) · [Desde el código](#desde-el-código) · [Documentación](DOCUMENTACION.md)

<img src="capturas/home.png" alt="Pantalla de inicio de PC Toolkit" width="860">

</div>

## Descargar

Windows 10 u 11 (x64). No hace falta instalar Python ni nada más.

| Archivo | Qué es |
| --- | --- |
| [`PC-Toolkit-1.3.1-Setup.exe`](https://github.com/fedegudi2022-blip/PC-Toolkit/releases/download/v1.3.1/PC-Toolkit-1.3.1-Setup.exe) | **Recomendado** (79,3 MB). Asistente con la aplicación dentro: accesos directos, arranque automático opcional y desinstalador. Por usuario, sin avisos de Windows. |
| [`PC-Toolkit-1.3.1-portable.exe`](https://github.com/fedegudi2022-blip/PC-Toolkit/releases/download/v1.3.1/PC-Toolkit-1.3.1-portable.exe) | Versión portátil (47,3 MB). Doble clic y funciona, sin instalar ni dejar rastro. |

Datos y preferencias se guardan en `%APPDATA%\PCToolkit` (véase [Dónde se guardan los datos](#dónde-se-guardan-los-datos)).

> Los ejecutables **no van firmados** con un certificado de confianza pública, así que SmartScreen
> puede pedir confirmación la primera vez («Más información» → «Ejecutar de todas formas») y algún
> antivirus mirarlo de más. El SHA-256 de cada archivo está en la
> [nota de la versión](https://github.com/fedegudi2022-blip/PC-Toolkit/releases/latest).

## Qué hace

| Módulo | Qué hace |
| --- | --- |
| **Inicio** | Medidores de CPU, memoria, disco y red, salud del equipo, gráfica de las últimas 24 h y procesos que más consumen. |
| **Optimizar memoria** | Libera RAM recortando conjuntos de trabajo y cachés, a mano o al pasar un umbral; mide lo liberado de verdad. |
| **Programas de inicio** | Lo que abre Windows al encender: desactiva sin borrar, avisa de entradas muertas y todo se puede deshacer. |
| **Mantenimiento** | Tarea propia en el Programador de Windows (limpieza, memoria e informes) con estado real leído del sistema. |
| **Alertas y bandeja** | Icono en la bandeja, umbrales de CPU, memoria y disco, y aviso por notificación. |
| **Analizar disco** | Escaneo por carpetas con barras proporcionales, uso por categoría y archivos grandes. |
| **Herramientas** | 28 accesos directos a las utilidades de Windows, con buscador y limpieza rápida de temporales. |
| **Tareas** | Registro de mantenimiento con estados, prioridades, fechas límite y estadísticas. |
| **Informes** | Exportación a HTML (visual), CSV y JSON, con selección de contenido y destino. |
| **Actualizaciones** | Al arrancar comprueba si hay una versión nueva (como mucho una vez al día), enseña qué cambia y la instala **verificando antes su SHA-256**. Se puede desactivar o pedir a mano desde Ajustes. |

Dos temas completos —oscuro «grafito» (fondo casi negro y grises fríos, cada nivel de superficie
separado del siguiente) y claro «papel»— que se cambian al instante; por defecto abre en oscuro y en
**Ajustes y alertas → Apariencia** se puede seguir a Windows o pasar al claro. El acento es neutro
—casi blanco en el tema oscuro y tinta en el claro— y manda en los botones principales y el foco.
El color tiene significado: **verde** para lo saludable, **ámbar** para los avisos y **rojo** para
los errores. Los datos llevan cuatro tonos propios (CPU azul, memoria cian, disco naranja y red gris
azulado) que se distinguen entre sí y de los estados también con daltonismo. Todo se maneja con el
teclado (`Ctrl+1`…`Ctrl+8` para las secciones, `Ctrl+9` para ajustes; `Tab` e `Intro` alcanzan
también tarjetas y filas). Los textos de las dos paletas cumplen el contraste 4,5:1 de la WCAG y lo
que ocurre se anuncia a los lectores de pantalla.

Medido en un Windows 11 x64: liberar RAM bajó del **78,9 % al 63,6 %** (+1.212 MB en 0,84 s), y con
la ventana en la bandeja el consumo baja a **0,45 % de un núcleo** frente al 2,25 % con la ventana a
la vista (`.venv\Scripts\python.exe tools\medir_consumo.py`).

## Capturas

<table>
<tr>
<td width="50%"><img src="capturas/memory.png" alt="Optimizar memoria"><br><sub>Optimizar memoria</sub></td>
<td width="50%"><img src="capturas/startup.png" alt="Programas de inicio"><br><sub>Programas de inicio</sub></td>
</tr>
<tr>
<td><img src="capturas/schedule.png" alt="Mantenimiento programado"><br><sub>Mantenimiento programado</sub></td>
<td><img src="capturas/disk.png" alt="Analizar disco"><br><sub>Analizar disco</sub></td>
</tr>
<tr>
<td><img src="capturas/inicio_claro.png" alt="Tema claro"><br><sub>Tema claro</sub></td>
<td><img src="capturas/instalador.png" alt="Asistente de instalación"><br><sub>Instalador</sub></td>
</tr>
</table>

Ejemplo de informe con datos reales: [capturas/informe_muestra.html](capturas/informe_muestra.html).

## Desde el código

Requisitos: Windows 10 u 11 (x64), Python 3.10+ y las dependencias de
[requirements.txt](requirements.txt) (`PySide6`, `psutil`).

```bat
run.bat                              Crea el entorno, instala lo que falta y abre la aplicación
```

```bat
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Opciones útiles de `app.py`:

```bat
python app.py --demo                       Carga tareas de ejemplo
python app.py --analizar C:\Datos           Abre analizando esa carpeta
python app.py --tema claro                  Fuerza el tema en esa ejecución
python app.py --screenshot salida           Guarda una captura de cada página y sale
python app.py --mantenimiento               Mantenimiento desatendido y sale (lo usa la tarea)
```

> Purgar la caché del sistema (la limpieza que más memoria libera) necesita permisos de
> administrador; la aplicación lo avisa y ofrece reiniciarse elevada. El resto funciona sin permisos.

## Compilar

```bat
build_exe.bat                            Programa (carpeta autocontenida) + comprobación real
python build_exe.py --onefile            Un único .exe, más cómodo de compartir
build_installer.bat                      Instalador de Windows (un solo archivo, con la app dentro)
```

| Resultado | Tamaño | Arranque |
| --- | --- | --- |
| `dist\PC Toolkit\` (carpeta, 161 archivos) | 72,4 MB | 0,41 s |
| `dist\PC Toolkit.exe` (`--onefile`) | 47,2 MB | 1,94 s |
| `dist\PC Toolkit Setup.exe` | 79,1 MB | instala 162 archivos / 72,4 MB en 8,9 s |

`build_exe.py` recorta del paquete 53,2 MB de librerías de Qt que la aplicación no usa y comprueba el
resultado **ejecutando el propio `.exe`**; `build_installer.py` instala de verdad en carpetas
temporales —sin tocar el escritorio ni tu instalación—, ejecuta la copia instalada y desinstala
comprobando que no queda rastro. Con la variante de carpeta hay que copiar **toda** `dist\PC Toolkit`.

### Firma digital

[firmar.py](firmar.py) (o [firmar.bat](firmar.bat) con doble clic) localiza `signtool`, firma los
ejecutables de `dist\` con SHA-256 y sello de tiempo RFC 3161, y verifica el resultado. Se puede
firmar en la misma pasada de compilación con `--firmar`.

```bat
firmar.bat --estado           Informe: signtool, certificados y firmas actuales
firmar.bat --traer-signtool   Descarga signtool del paquete oficial de Microsoft y sale
firmar.bat --autoprueba       Prueba el proceso con un certificado autofirmado
firmar.bat --comprobar        Falla (código 1) si algo no está bien firmado
build_installer.py --rebuild --firmar --huella 1A2B3C…
```

Para firmar en un servidor de compilación está [.github/workflows/firmar.yml](.github/workflows/firmar.yml),
que compila en Windows y firma con **Azure Artifact Signing** por identidad federada OIDC (sin
certificados ni claves en GitHub). Qué hay que preparar una vez, y cómo firmar con la clave en un
token o HSM: [DOCUMENTACION.md](DOCUMENTACION.md), apartado 8.10.

## Actualizaciones

La aplicación mira si hay una versión nueva **al arrancar** (como mucho una vez al día, y solo si
está activado) y, cuando la hay, enseña qué cambia y ofrece instalarla. Descargar e instalar es
siempre una decisión tuya: nada se baja ni se ejecuta sin aceptar.

- El instalador se descarga a una carpeta temporal, se **comprueba su SHA-256** contra el que
  publica la versión y solo entonces se ejecuta; si el hash no coincide, el archivo se borra y se
  avisa. Una versión que no publique su hash no se actualiza sola.
- Se instala **encima** de la versión que tengas, conservando tus datos y preferencias, y la
  aplicación se cierra sola y vuelve a abrirse con la versión nueva. Si usas la versión portátil, no
  se instala nada: se abre la página de la versión para que la descargues a mano.
- En **Ajustes y alertas → Actualizaciones** hay un botón «Buscar actualizaciones» y un interruptor
  para desactivar la comprobación automática. «Omitir esta versión» deja de avisar de esa versión en
  concreto (una posterior sí volverá a avisar).

### Publicar una versión nueva

Para quien mantiene el proyecto, publicar una versión son tres pasos:

1. Subir la versión en `pctoolkit/core/config.py` (`APP_VERSION`): es el único sitio donde se
   escribe, y de ahí salen el ejecutable, el instalador, los nombres de los archivos y la
   comprobación de actualizaciones.
2. Rehacer las capturas de la documentación (`tools\capturar_documentacion.py`) y actualizar los
   recuentos de las guías: llevan la versión dentro y la suite lo comprueba.
3. Empujar `main` y la etiqueta con el mismo número:

```bat
git push origin main
git push origin v1.3.1
```

Antes del día del estreno se puede **ensayar la publicación** con una etiqueta de prueba: en
«Actions → Publicar la versión (Windows) → Run workflow» se lanza el mismo trabajo a mano, que
recorre el camino completo (compilar, suite, instalación real, hashes y publicación) y lo deja todo
en una publicación **en borrador y preliminar**, con etiqueta `v1.3.1-ensayo`. No sale en
`/releases/latest` —lo que mira el actualizador— y no gasta ningún número de versión.

La etiqueta dispara [.github/workflows/publicar.yml](.github/workflows/publicar.yml), que compila en
un servidor de Windows, pasa la suite completa (incluida la instalación y desinstalación reales),
calcula los SHA-256 y crea la **publicación** con sus archivos adjuntos y sus notas: el instalador,
la versión portátil, cada `.sha256` y `SHA256SUMS.txt`. Si la etiqueta y `APP_VERSION` no coinciden,
el trabajo se para en el primer paso. Las novedades que se escriban en las notas de la publicación
son las que verá quien tenga una versión anterior al actualizar. Para firmar con Azure sigue estando
[.github/workflows/firmar.yml](.github/workflows/firmar.yml) (a mano, con su configuración; véase
[DOCUMENTACION.md](DOCUMENTACION.md), apartado 8.10).

## Dónde se guardan los datos

- `%APPDATA%\PCToolkit`: tareas, historial de métricas (24 h), histórico del mantenimiento, cambios
  reversibles y el XML de la tarea programada.
- Registro de Windows, `HKCU\Software\PCToolkit`: preferencias (umbrales, bandeja, tema, modo eco,
  actualizaciones…).
- `Documentos\PCToolkit\Informes`: los informes exportados.

Desinstalar conserva estos datos salvo que se pidan borrar desde el asistente.

## Pruebas

```bat
.venv\Scripts\python.exe tests\smoke_test.py
```

**767 comprobaciones** sin marco externo: métricas y disco reales, persistencia, alertas,
historial (con la serie de la GPU, sus huecos y su color neutro), informes en los tres formatos, las
páginas construidas y navegadas, programas de
inicio y cambios reversibles sobre claves propias de la prueba, el ciclo del instalador en carpetas
temporales (incluido **instalar encima de una versión anterior**), que las dependencias que la
compilación importa estén declaradas en `requirements.txt`, la firma (argumentos de `signtool` y
firma real con certificado de prueba), las actualizaciones (comparación de versiones, lectura de una
publicación de GitHub con datos simulados, verificación del SHA-256 —correcta e incorrecta—,
cancelación de la descarga y detección de la versión portátil), el contraste
WCAG, la diferenciación de las series y los estados con daltonismo (CIEDE2000 sobre las matrices de
Machado) y la accesibilidad (roles, nombres, anuncios y foco visible).

Se ejecutan con la plataforma gráfica real de Qt; en un equipo sin escritorio, `set
PCTOOLKIT_OFFSCREEN=1` las ejecuta sin pantalla (las comprobaciones de desbordes avisarán de más y
no se ejecutan las que necesitan bandeja).

La última comprobación vigila esta misma documentación: que las guías citen la versión actual y que
cada captura se haya hecho con ella. Para rehacerlas: `tools\capturar_documentacion.py`. Esas
guardas están en `tests\documentacion.py`, aparte de las pruebas de código, y se pueden dejar fuera
cuando lo que se comprueba es un cambio de código: `tests\smoke_test.py --sin-documentacion`.

En cada envío las pasa [.github/workflows/integracion.yml](.github/workflows/integracion.yml) en un
servidor de Windows, que además **compila el ejecutable y el instalador**: así un fallo de
compilación —una dependencia sin declarar, un cambio que rompe el empaquetado— se ve al enviar y no
al publicar. No firma nada: de la firma se encarga
[.github/workflows/firmar.yml](.github/workflows/firmar.yml) y de las publicaciones
[.github/workflows/publicar.yml](.github/workflows/publicar.yml) (al empujar una etiqueta `v…`).

## Estructura

```
app.py                       Punto de entrada y opciones de línea de comandos
build_exe.py / .bat          Empaquetado con PyInstaller (icono, versión y comprobación)
build_installer.py / .bat    Instalador de Windows y comprobación del ciclo completo
firmar.py / .bat             Firma con signtool (SHA-256, sello RFC 3161 y verificación)
assets/                      Logotipo original, icono del ejecutable y propiedades de versión
installer/                   Asistente, desinstalador y lógica de instalación por usuario
tools/                       Icono, medición de consumo, capturas (y su regeneración) y verificación
capturas/                    Imágenes de esta documentación
pctoolkit/
  core/                      Lógica sin interfaz: config, métricas, tarjeta gráfica, memoria, disco,
                             tareas, informes, historial, alertas, arranque, mantenimiento,
                             programador
  ui/                        Interfaz PySide6: tema, accesibilidad, iconos, widgets, gráfica,
                             vigía de la GPU, bandeja, diálogos y una página por sección
tests/smoke_test.py          767 comprobaciones (núcleo, interfaz, instalador, firma, dependencias,
                             logotipo y actualizaciones)
tests/documentacion.py       Guardas de capturas, informe y recuentos (van aparte)
dist/                        Todo lo compilado; se regenera con los scripts
```

Qué hace cada módulo, con detalle: [DOCUMENTACION.md](DOCUMENTACION.md).

## Limitaciones

- **Temperatura:** `psutil` no expone sensores térmicos en Windows, y ni el WMI del sistema ni las
  librerías que sí los leen funcionan sin permisos de administrador y un controlador con firma (y en
  muchos equipos la clase de temperatura ni existe), así que no hay tarjeta de temperatura. La
  **tarjeta gráfica sí** se muestra (y entra como una serie más en el historial de 24 h): su uso por
  motor viene de los contadores de rendimiento de Windows y su nombre y su controlador, del registro
  (en segundo plano, cada 15 s —un minuto en modo eco—; con contadores se sigue leyendo aunque la
  ventana esté oculta, y donde no los haya, solo con la portada o los informes a la vista). La
  lectura **no arranca ningún proceso**: 3 ms de CPU y nada de memoria por vuelta (medidos), con la
  ventana de un segundo que usa el propio Administrador de tareas. Donde Windows no publique ese
  contador, la aplicación se apaña con un solo proceso de PowerShell que reutiliza mientras la
  tarjeta se mira, y sin él, con uno por lectura.
- **Programas de inicio:** los servicios se administran en «Herramientas»; las aplicaciones de la
  Microsoft Store **sí** aparecen, con su tarea de inicio y el nombre que enseña Windows, y se
  activan y desactivan igual que las demás (regla reversible y deshacer). El coste de cada entrada es
  una estimación propia.
- **Permisos:** las herramientas marcadas con escudo piden UAC; si se cancela, la aplicación lo dice
  sin cerrarse.
- **Limpieza de temporales:** solo la carpeta `%TEMP%` del usuario, ignora lo reciente y omite lo
  que está en uso.
- **Bandeja:** si el sistema no la tiene, todo funciona igual salvo el segundo plano, y se avisa.
- **Actualizaciones:** necesitan conexión con GitHub. La versión portátil no se instala sola (no hay
  instalación que reemplazar): la aplicación ofrece abrir la página de la versión para descargarla.

## Licencia

[MIT](LICENSE): puedes usar, modificar y redistribuir el código, incluso comercialmente, conservando
el aviso de copyright y la licencia.
