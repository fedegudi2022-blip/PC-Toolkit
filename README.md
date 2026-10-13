<div align="center">

# PC Toolkit

**Mantenimiento y diagnóstico para Windows, sin ruido.**

Libera RAM, controla lo que arranca con Windows, programa el mantenimiento, mide el equipo en vivo
y exporta informes. Python + PySide6, un solo `.exe`, instalación por usuario y **sin permisos de
administrador**.

[![Última versión](https://img.shields.io/github/v/release/fedegudi2022-blip/PC-Toolkit?label=versi%C3%B3n&color=22c55e)](https://github.com/fedegudi2022-blip/PC-Toolkit/releases/latest)
[![Descargas](https://img.shields.io/github/downloads/fedegudi2022-blip/PC-Toolkit/total?color=22c55e)](https://github.com/fedegudi2022-blip/PC-Toolkit/releases)
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
| [`PC-Toolkit-1.2.0-Setup.exe`](https://github.com/fedegudi2022-blip/PC-Toolkit/releases/download/v1.2.0/PC-Toolkit-1.2.0-Setup.exe) | **Recomendado** (78,9 MB). Asistente con la aplicación dentro: accesos directos, arranque automático opcional y desinstalador. Por usuario, sin avisos de Windows. |
| [`PC-Toolkit-1.2.0-portable.exe`](https://github.com/fedegudi2022-blip/PC-Toolkit/releases/download/v1.2.0/PC-Toolkit-1.2.0-portable.exe) | Versión portátil (46,7 MB). Doble clic y funciona, sin instalar ni dejar rastro. |

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

Dos temas completos —oscuro «negro mate» (fondo negro y grises neutros) y claro «papel»— que se
cambian al instante; por defecto abre en oscuro y en **Ajustes y alertas → Apariencia** se puede
seguir a Windows o pasar al claro. El color tiene significado: **violeta** para la acción principal,
el foco y la selección, **cian** para lo saludable, **ámbar** para los avisos y **rojo** para los
errores; los datos llevan tonos propios (CPU cian, memoria violeta y disco naranja) y el verde no se
usa en ningún sitio. Todo se maneja con el teclado (`Ctrl+1`…`Ctrl+8` para las secciones, `Ctrl+9`
para ajustes; `Tab` e `Intro` alcanzan también tarjetas y filas). Los textos de las dos paletas
cumplen el contraste 4,5:1 de la WCAG y lo que ocurre se anuncia a los lectores de pantalla.

Medido en un Windows 11 x64: liberar RAM bajó del **78,9 % al 63,6 %** (+1.212 MB en 0,84 s), y con
la ventana en la bandeja el consumo baja a **0,52 % de un núcleo** frente al 2,79 % con la ventana a
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
| `dist\PC Toolkit\` (carpeta, 158 archivos) | 64,8 MB | 0,41 s |
| `dist\PC Toolkit.exe` (`--onefile`) | 46,7 MB | 1,94 s |
| `dist\PC Toolkit Setup.exe` | 78,9 MB | instala 159 archivos / 72,0 MB en 8,2 s |

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

## Dónde se guardan los datos

- `%APPDATA%\PCToolkit`: tareas, historial de métricas (24 h), histórico del mantenimiento, cambios
  reversibles y el XML de la tarea programada.
- Registro de Windows, `HKCU\Software\PCToolkit`: preferencias (umbrales, bandeja, tema, modo eco…).
- `Documentos\PCToolkit\Informes`: los informes exportados.

Desinstalar conserva estos datos salvo que se pidan borrar desde el asistente.

## Pruebas

```bat
.venv\Scripts\python.exe tests\smoke_test.py
```

**576 comprobaciones** sin marco externo: métricas y disco reales, persistencia, alertas,
historial, informes en los tres formatos, las ocho páginas construidas y navegadas, programas de
inicio y cambios reversibles sobre claves propias de la prueba, el ciclo del instalador en carpetas
temporales, la firma (argumentos de `signtool` y firma real con certificado de prueba), el contraste
WCAG y la ausencia de verde en las dos paletas, y la accesibilidad (roles, nombres, anuncios y foco
visible).

Se ejecutan con la plataforma gráfica real de Qt; en un equipo sin escritorio, `set
PCTOOLKIT_OFFSCREEN=1` las ejecuta sin pantalla (las comprobaciones de desbordes avisarán de más y
no se ejecutan las que necesitan bandeja).

La última comprobación vigila esta misma documentación: que las guías citen la versión actual y que
cada captura se haya hecho con ella. Para rehacerlas: `tools\capturar_documentacion.py`.

## Estructura

```
app.py                       Punto de entrada y opciones de línea de comandos
build_exe.py / .bat          Empaquetado con PyInstaller (icono, versión y comprobación)
build_installer.py / .bat    Instalador de Windows y comprobación del ciclo completo
firmar.py / .bat             Firma con signtool (SHA-256, sello RFC 3161 y verificación)
installer/                   Asistente, desinstalador y lógica de instalación por usuario
tools/                       Icono, medición de consumo, capturas (y su regeneración) y verificación
capturas/                    Imágenes de esta documentación
pctoolkit/
  core/                      Lógica sin interfaz: config, métricas, memoria, disco, tareas,
                             informes, historial, alertas, arranque, mantenimiento, programador
  ui/                        Interfaz PySide6: tema, accesibilidad, iconos, widgets, gráfica,
                             bandeja, diálogos y una página por sección
tests/smoke_test.py          576 comprobaciones (núcleo, interfaz, instalador, firma y docs)
dist/                        Todo lo compilado; se regenera con los scripts
```

Qué hace cada módulo, con detalle: [DOCUMENTACION.md](DOCUMENTACION.md).

## Limitaciones

- **GPU y temperatura:** psutil no da datos de GPU y en Windows no expone sensores térmicos, así que
  no hay tarjeta de temperatura (en su lugar se muestra el tráfico de red).
- **Programas de inicio:** no aparecen las aplicaciones de la Microsoft Store (Windows las arranca
  por otra vía) ni los servicios, que se administran en «Herramientas». El coste de cada entrada es
  una estimación propia.
- **Permisos:** las herramientas marcadas con escudo piden UAC; si se cancela, la aplicación lo dice
  sin cerrarse.
- **Limpieza de temporales:** solo la carpeta `%TEMP%` del usuario, ignora lo reciente y omite lo
  que está en uso.
- **Bandeja:** si el sistema no la tiene, todo funciona igual salvo el segundo plano, y se avisa.

## Licencia

[MIT](LICENSE): puedes usar, modificar y redistribuir el código, incluso comercialmente, conservando
el aviso de copyright y la licencia.
