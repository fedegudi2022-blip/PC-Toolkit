# PC Toolkit 1.1.0 — qué es, qué hace y cómo funciona

Aplicación de escritorio para **Windows 10 y 11** que reúne el mantenimiento del equipo en un solo
sitio: libera memoria RAM cuando hace falta, programa el mantenimiento en el propio Windows, vigila
la CPU, la memoria y el disco, analiza el espacio ocupado, reúne los accesos rápidos a las
herramientas del sistema y exporta informes.

Está hecha en **Python + PySide6 + psutil**, con tema oscuro «grafito vivo» (grises cálidos sin azul,
con más contraste entre fondo, tarjetas y paneles) y acento esmeralda, y se instala con un
**instalador propio de Windows** (asistente, accesos directos y desinstalador), sin necesidad de ser
administrador.

> Este documento explica el programa completo: qué hace cada pantalla, cómo funciona por dentro y
> cómo se instala, se compila y se desinstala.

---

## 1. Resumen en una tabla

| Módulo | Qué hace |
| --- | --- |
| **Inicio** | Medidores de CPU, memoria, disco y red, puntuación de salud del equipo, gráfica del historial de las últimas 24 h, detalles del sistema y los procesos que más consumen. |
| **Optimizar memoria** | Libera RAM recortando los conjuntos de trabajo de los procesos y vaciando la caché del sistema (estilo MemReduct), a mano o **automáticamente** al superar un umbral; mide de verdad lo liberado. |
| **Programas de inicio** | Lista lo que Windows abre solo al encender el equipo (registro, carpetas de inicio y tareas al iniciar sesión), avisa de las entradas que apuntan a programas que ya no están y **desactiva sin borrar nada**, con cada cambio apuntado para deshacerlo. |
| **Mantenimiento** | Crea una tarea en el **Programador de tareas de Windows** para limpiar temporales, liberar memoria y generar informes sin abrir la aplicación; muestra próxima ejecución, último resultado e histórico. |
| **Analizar disco** | Escaneo por carpetas con barras proporcionales, uso por categoría (vídeo, imágenes, programas…), archivos grandes y navegación hacia dentro con doble clic. |
| **Herramientas** | 28 accesos directos agrupados por categoría (sistema, mantenimiento, almacenamiento, red y avanzado), buscador y limpieza rápida de archivos temporales. |
| **Tareas** | Registro de mantenimiento con estados, prioridades, fechas límite, plantillas rápidas y estadísticas de progreso. |
| **Informes** | Exportación a **HTML** (informe visual), **CSV** (Excel) y **JSON**, eligiendo contenido y destino. |
| **Alertas y bandeja** | Icono de estado en la bandeja del sistema, avisos por umbrales configurables y opción de mantener la app en segundo plano al cerrar la ventana. |
| **Apariencia** | Tema **oscuro** o **claro**, o el que tenga Windows en cada momento, con contraste WCAG 4,5:1 en las dos paletas. |
| **Accesibilidad** | Manejo completo con el teclado (foco siempre visible y atajos `Ctrl+1`…`Ctrl+9`), anuncios para lectores de pantalla, nombre/valor en los indicadores pintados a mano y rol correcto en cada widget propio (grupo, encabezado, gráfica, barra o botón). |

---

## 2. El instalador de Windows

**Archivo:** `dist\PC Toolkit Setup.exe` (78,9 MB). Es **un solo archivo**: lleva dentro la
aplicación comprimida (`pc_toolkit_app.zip`, 32,6 MB, los 159 archivos y el desinstalador) y la saca
a una carpeta temporal al instalar, borrándola al terminar. No hay que descomprimir nada ni dejar una
carpeta al lado; si el instalador encuentra la carpeta `PC Toolkit\` junto a él (por ejemplo al
probarlo desde `dist\`), la usa directamente y no extrae nada.

### Qué hace al hacer doble clic

Abre un **asistente** con el mismo aspecto que la aplicación:

1. **Carpeta de instalación**: por defecto `%LOCALAPPDATA%\Programs\PC Toolkit` (se puede cambiar).
2. **Opciones**: acceso directo en el escritorio, entrada en el menú Inicio, arranque automático al
   iniciar sesión (en la bandeja) y abrir el programa al terminar.
3. **Progreso** con los pasos reales (copiar archivos, accesos directos, registro, arranque
   automático) y **resultado** con lo que se ha hecho.

**No pide permisos de administrador ni muestra ningún aviso de Windows**: todo se instala en la
carpeta del usuario y en `HKCU`, no en `Archivos de programa` ni en `HKLM`.

### Qué queda instalado exactamente

| Elemento | Dónde |
| --- | --- |
| Programa y sus librerías | `%LOCALAPPDATA%\Programs\PC Toolkit\` (`PC Toolkit.exe` + `_internal\`) |
| Desinstalador | `%LOCALAPPDATA%\Programs\PC Toolkit\Desinstalar PC Toolkit.exe` |
| Acceso directo del escritorio | Escritorio del usuario (`PC Toolkit.lnk`) |
| Acceso directo del menú Inicio | `…\Start Menu\Programs\PC Toolkit.lnk` |
| Entrada en «Aplicaciones instaladas» | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\PCToolkit` |
| Arranque automático (si se marca) | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `"…\PC Toolkit.exe" --bandeja` |

Las carpetas del escritorio y del menú Inicio **se preguntan a Windows**, no se suponen: si el
escritorio está redirigido (por ejemplo a OneDrive), el acceso directo se crea en el que ve el
usuario.

### Arranque automático con el sistema

El valor de `Run` lanza la aplicación con **`--bandeja`**: PC Toolkit arranca **en segundo plano**,
sin mostrar la ventana, con solo el icono de la bandeja y una notificación la primera vez. Desde ahí
controla umbrales y limpieza automática de memoria; al hacer clic en el icono se abre la ventana
completa.

### Actualizar y desinstalar

- **Actualizar**: volver a ejecutar el instalador. Detecta la instalación anterior, avisa de la
  versión que había, **cierra las instancias abiertas** (preguntando antes) y conserva los datos y
  las preferencias.
- **Desinstalar** desde *Configuración → Aplicaciones → Aplicaciones instaladas*, desde el propio
  desinstalador o desde su menú Inicio. Quita accesos directos, la entrada de registro, el arranque
  automático y la **tarea de mantenimiento programado** si existía.
- **Tus datos se conservan** por defecto (historial, informes y preferencias en
  `%APPDATA%\PCToolkit`); el asistente permite borrarlos marcando una casilla.
- La carpeta del programa se elimina con un pequeño proceso auxiliar que espera a que el
  desinstalador termine (si no, sus propios archivos estarían en uso).

### Instalación y desinstalación sin ventana (para despliegues)

```
PC Toolkit Setup.exe --silent --dir "C:\Ruta" --autostart --sin-abrir
PC Toolkit Setup.exe --silent --desinstalar --borrar-datos
Desinstalar PC Toolkit.exe --silencioso
```

Otras opciones: `--sin-escritorio`, `--sin-menu`, `--origen` (carpeta de la aplicación),
`--clave` y `--valor-arranque` (nombres de registro, útiles para pruebas).

### Cómo se ha comprobado

- **Ciclo completo con rutas temporales** (dentro de las pruebas automáticas): instala en una
  carpeta de prueba con escritorio, menú Inicio y clave de registro propios, comprueba que los
  accesos directos apuntan al programa instalado, que el registro y el arranque automático quedan
  bien, **ejecuta la copia instalada** y desinstala verificando que no queda rastro.
- **Instalación real en este equipo** (medida): `78,9 MB` de instalador (que ya trae la aplicación
  dentro), **159 archivos / 72,0 MB** copiados en **8,2 s**, dos accesos directos correctos, entrada
  de registro y arranque automático
  en la bandeja, la copia instalada ejecutando el mantenimiento, y desinstalación dejando todo
  limpio (accesos directos, registro y carpeta eliminados; datos del usuario intactos). Se puede
  repetir cuando quieras con `tools\verificar_instalacion.py`.

### Compilar el instalador

```bat
build_installer.bat            Doble clic: empaqueta y comprueba
python build_installer.py --rebuild    Reconstruye antes la aplicación
python build_installer.py --no-verify  Solo empaqueta
```

El resultado queda en `dist\`: `PC Toolkit Setup.exe` (que ya incluye la aplicación) y la carpeta
`PC Toolkit\` (la aplicación compilada, para repartirla sin instalador). Antes de compilar, la
aplicación se comprime en `build\installer\pc_toolkit_app.zip` y esa carga es la que se incrusta en
el ejecutable.

---

## 3. Cómo funciona cada parte

### 3.1 Liberar memoria RAM (estilo MemReduct)

Tres técnicas, de menos a más agresiva, midiendo siempre antes y después:

1. **Recortar los conjuntos de trabajo** de los procesos (`EmptyWorkingSet`): devuelve a Windows la
   memoria reservada que nadie usa. Es la parte segura y **no necesita permisos**.
2. **Vaciar la caché del sistema** (`MemoryEmptyWorkingSets`).
3. **Purgar la lista de espera** (`MemoryPurgeStandbyList`), la que más memoria libera.

Las dos últimas necesitan privilegios: si la aplicación no es administradora, lo avisa y ofrece un
botón para **reiniciarse como administrador** (UAC).

Salvaguardas: los procesos críticos (System, Registry, csrss, lsass, dwm…) **nunca** se tocan, la
aplicación no se recorta a sí misma salvo que se pida, hay un **consumo mínimo por proceso** para no
perder el tiempo con procesos pequeños y la limpieza automática exige **umbral sostenido**, espera
entre limpiezas y no repite avisos.

**La lista de procesos** («Procesos que más memoria usan») muestra los doce procesos que más RAM
gastan con su puesto, el PID, la parte de la RAM total que representan (en ámbar o rojo cuando
aprietan), una barra comparada con el que más consume y la cifra exacta en MB o GB. Cada fila trae su
botón **Liberar**, que recorta solo ese proceso y dice al momento cuánto ha devuelto. La lista es un
área con desplazamiento vertical propio: se ven **siete filas enteras** de un vistazo, ninguna queda
cortada y el botón no queda tapado por la barra (hay comprobaciones automáticas que lo vigilan).

Medición real en este equipo (Windows 11 x64, 32 GB): de **78,9 % a 63,6 % de RAM**, 36 procesos
recortados y **+1 212 MB disponibles** en 0,84 s.

> Es memoria en caché que se devuelve al sistema, no memoria nueva: los programas recargan de disco
> lo que necesiten. Por eso el umbral por defecto es alto (85 %) y la automática espera entre
> pasadas.

### 3.2 Mantenimiento programado de Windows

El botón **Guardar y programar** registra una tarea en el Programador de tareas (carpeta
`\PCToolkit\`) con un XML generado por la propia aplicación:

- Se ejecuta **como tu usuario**, **sin contraseña y sin privilegios elevados**.
- **Diaria**, **semanal** (con día) o **al iniciar sesión**, a la hora que elijas.
- Si el equipo estaba apagado, Windows la recupera al encenderlo (`StartWhenAvailable`).
- Pasos configurables: limpiar temporales (con antigüedad mínima), liberar RAM, generar un informe
  del equipo y anotar la ejecución en el registro de tareas.

La página muestra el estado **real** leído de Windows (próxima ejecución, última, código de
resultado, ejecuciones perdidas) y permite activar, desactivar, ejecutar ahora o eliminar la tarea;
avisa si el comando registrado ya no coincide porque hayas movido la aplicación.

Esa lectura se hace **al abrir la página**, no al arrancar la aplicación: consultar el Programador de
tareas cuesta un proceso de PowerShell de unos segundos, y no tiene sentido gastarlo en un arranque
que quizá solo quería dejar el icono en la bandeja. Mientras tanto el estado aparece como «Sin
consultar», con una nota que lo explica, y el botón **Actualizar estado** lo relee cuando quieras.

El modo desatendido es el mismo que usa la tarea y se puede lanzar a mano:

```bat
python app.py --mantenimiento                    Limpia temporales y genera informe
python app.py --mantenimiento --liberar-memoria  Además libera RAM
python app.py --mantenimiento --simular          Muestra lo que haría sin cambiar nada
python app.py --mantenimiento --antiguedad 72    Solo temporales de más de 72 horas
python app.py --mantenimiento --sin-limpieza --sin-informe --sin-registro
```

### 3.3 Alertas, bandeja e historial

- **Umbrales independientes** de CPU, memoria y disco (90 % por defecto).
- **Muestras sostenidas**: lecturas seguidas necesarias antes de avisar (evita picos puntuales).
- **Enfriamiento**: minutos mínimos entre dos avisos del mismo tipo; al bajar del umbral la cuenta
  se reinicia, así que un episodio nuevo vuelve a avisar.
- Los avisos salen como **notificación de Windows** y en la barra de estado.
- El icono de la bandeja muestra tres barras (CPU, memoria y disco) coloreadas según la carga, y su
  menú permite abrir la ventana, ver las métricas, activar o desactivar alertas y **salir del todo**.
- Con la bandeja activa, cerrar la ventana **no cierra el programa**: sigue vigilando el equipo.
- Al salir de verdad (menú **Salir** de la bandeja o sin bandeja disponible), la aplicación **espera a
  que terminen las consultas en marcha** —la barra de estado lo indica: «Cerrando: esperando a que
  acaben N tarea(s) en curso…»— en lugar de destruir trabajo a medias. Es lo que evita que Windows
  muestre un error del programa justo al cerrarlo.
- El **historial** guarda una muestra cada 30 s (configurable) en JSON Lines y conserva 24 horas; en
  Inicio se dibuja con una línea por métrica, leyenda para ocultar series y cursor con los valores
  exactos de cada instante.

### 3.4 Analizador de disco

Escaneo en un hilo aparte (la ventana sigue respondiendo) con barras proporcionales por carpeta,
clasificación por categorías según la extensión (vídeo, imágenes, audio, programas, documentos,
comprimidos…), lista de archivos grandes y navegación con doble clic; se puede cancelar y se puede
abrir la carpeta en el Explorador.

### 3.5 Herramientas y limpieza de temporales

28 accesos directos agrupados por categoría, con buscador, que abren las herramientas propias de
Windows (Administrador de tareas, Información del sistema, Liberador de espacio, Comprobación de
disco, Opciones de energía, Configuración de red…). Las que necesitan permisos se marcan con un
escudo y piden UAC; si se cancelan, la aplicación lo indica sin cerrarse.

La limpieza rápida actúa **solo sobre `%TEMP%` del usuario**, ignora archivos con menos de una hora,
omite los que están en uso y muestra cuántos archivos y cuántos MB se han liberado.

### 3.6 Tareas e informes

El registro de tareas guarda pendientes con categoría, prioridad, fecha límite y notas, con
plantillas rápidas y estadísticas de progreso; se puede marcar, editar, filtrar y borrar.

Los informes se generan a partir de datos reales del equipo y se exportan en **HTML** (informe visual
con tarjetas y tablas), **CSV** (para Excel) y **JSON**, eligiendo qué incluir y dónde guardarlos,
sin sobrescribir los anteriores.

### 3.7 Consumo de recursos

La aplicación se adapta a lo que estás mirando:

- **Perfil completo** (ventana a la vista): muestreo cada 1,2 s y lista de procesos solo cuando la
  página que la necesita está visible.
- **Perfil eco** (configurable): muestreo cada 3,5 s sin desglose por núcleo.
- **Eco automático** al ocultar la ventana o dejarla en la bandeja: deja de repintar páginas.

Medido con `tools\medir_consumo.py` (16 s por perfil):

| Perfil | CPU (% de un núcleo) | RAM | Actualizaciones |
|---|---|---|---|
| Completo, a la vista | 2,79 % | 103,3 MB | 13 |
| Eco, a la vista | 1,23 % | 102,6 MB | 4 |
| En segundo plano (bandeja) | **0,52 %** | 97,6 MB | 4 |

En segundo plano el consumo baja un **81 %** respecto al muestreo completo.

### 3.8 Programas de inicio y deshacer

**Módulos:** `pctoolkit\core\startup.py` (lectura y cambio) y `pctoolkit\core\undo.py` (registro de
cambios reversibles). **Pantalla:** `pctoolkit\ui\startup_page.py`.

Lee las mismas fuentes que usa Windows, sin escanear nada de más:

| Origen | Claves o carpetas | Se puede cambiar |
|---|---|---|
| Tu usuario | `HKCU\...\CurrentVersion\Run`, `RunOnce` | El `Run`; el `RunOnce` es de solo lectura |
| Todos los usuarios | `HKLM\...\Run` (vistas de 64 y 32 bits), `RunOnce` | Sí, con permisos de administrador |
| Carpetas de inicio | `%APPDATA%` y `%ProgramData%`, ruta `...\Start Menu\Programs\Startup` | Sí (la común, con permisos) |
| Tareas al iniciar sesión | Tareas del Programador con disparador de inicio de sesión, fuera de las de Microsoft | Sí, con `schtasks` |

**Desactivar no borra nada.** Se escribe la misma marca que escribe el Administrador de tareas en
`...\Explorer\StartupApproved\Run` (o `Run32`, o `StartupFolder`): un valor binario cuyo primer byte
es `2` para activada y `3` para desactivada, con la fecha del cambio en los ocho bytes siguientes
exactamente igual que Windows. La entrada sigue donde estaba y se puede volver a activar desde aquí,
desde el Administrador de tareas o con **Deshacer**.

Otras decisiones que se ven en pantalla:

- **Origen y estado real** de cada entrada, y aviso cuando el programa al que apunta ya no existe
  (una entrada muerta que solo añade trabajo al arrancar).
- **Coste estimado** (Ligero / Medio) a partir del tipo de entrada, con su explicación a la vista.
  Es una estimación propia y así se dice: Windows no publica la cifra del Administrador de tareas,
  que la calcula por su cuenta.
- **Dos vistas del registro de sistema**: las entradas de 32 bits viven en `WOW6432Node` y se leen
  aparte, porque los programas antiguos las instalan ahí.
- **Lo que no se ve**: las aplicaciones de la Microsoft Store (Windows las arranca sin una clave
  pública estable) y los servicios, que están en *Herramientas → Servicios*.

El **registro de deshacer** (`undo.py`) apunta cada cambio con su título, cómo estaba antes, cómo
quedó, su fecha y su origen (`a mano` o `automático`) en `%APPDATA%\PCToolkit\deshacer.jsonl`. Quien
sabe deshacer un tipo de cambio es su propio módulo, que se registra al importarse (`startup.py` lo
hace al cargarse), de modo que el mismo panel puede revertir en el futuro la cuarentena de archivos
sin tocar el registro de deshacer.

**Comprobado:** sobre una clave del registro propia de la prueba (creada y borrada por la suite) se
verifica la lectura, la marca binaria escrita byte a byte, el cambio reversible de una entrada del
registro y otra de una carpeta de inicio, el apunte de deshacer con su origen y que un cambio no se
deshaga dos veces. En la pantalla, que haya una fila por entrada, que ninguna se salga del ancho (ni
su botón), que no aparezca desplazamiento horizontal y que el botón **Deshacer** devuelva la entrada a
como estaba.

---

### 3.9 Teclado y accesibilidad

La ventana se maneja entera con el teclado y **el foco siempre se ve**: el borde del control
enfocado se ilumina en esmeralda (en el botón principal, en blanco sobre el propio acento). Antes
del cambio, la hoja de estilos quitaba el contorno de todos los controles y con el tabulador no se
sabía dónde estabas.

| Atajo | Acción |
| --- | --- |
| `Ctrl+1` … `Ctrl+8` | Abre cada sección en el orden de la barra lateral |
| `Ctrl+9` | Ajustes y alertas |
| `Tab` / `Mayús+Tab` | Recorre botones, campos, listas, tarjetas de herramientas y filas del analizador |
| `Intro` o `Espacio` | Abre el elemento enfocado |

Detalles del tema que también se comprueban en las pruebas:

- Los colores de texto pasan el contraste de la WCAG 4,5:1 sobre los fondos en los que se usan.
- Los botones de una misma fila (por ejemplo «Actualizar estado» y «Guardar y programar») miden
  exactamente lo mismo: el botón principal tenía un relleno vertical distinto y bailaba 2 px.
- Al cambiar de sección, el texto de la barra lateral ya no salta de sitio: los dos estados llevan
  los mismos grosores de borde.
- Un control deshabilitado se ve apagado y no reacciona al ratón; una casilla marcada pero
  deshabilitada ya no se queda pintada en esmeralda (parecía activa).
- Con la ventana en segundo plano, la selección de una lista se apaga para no confundirla con la de
  verdad.

#### Lo que se anuncia en voz alta

Qt no lee la barra de estado por su cuenta: los mensajes se envían como **anuncio accesible**
(`QAccessibleAnnouncementEvent`, `accessibility.py`) y además quedan en el nombre accesible del texto,
que es lo que un lector lee al llegar ahí con el teclado. Se anuncian los mensajes que van
apareciendo —«Liberando memoria…», «Mantenimiento completado», «Cerrando: esperando a que acaben N
tareas en curso…»— pero no las cifras de CPU y RAM, que se repintan cada segundo y no harían más que
interrumpir.

Los indicadores **pintados a mano** no tienen texto que leer, así que llevan nombre y descripción
accesibles con su valor y se actualizan al cambiar la medida:

| Elemento | Lo que se lee |
| --- | --- |
| Indicadores del inicio (CPU, RAM, disco, red) | «CPU» · «37 % (8 núcleos · 16 hilos · 3.60 GHz)» |
| Medidor de salud | «Salud del equipo» · «78 de 100 — Buena» |
| Barras de progreso | «Consumo de chrome.exe» · «21 %» |
| Filas del analizador de disco | Nombre y ruta del archivo o carpeta |
| Tarjetas de herramientas | Título y descripción, y se abren con `Intro` o `Espacio` |

#### Qué es cada cosa: el rol accesible

El nombre no basta. Qt da a cada clase un rol fijo, y como nuestros widgets se pintan a mano el rol
es el de su clase base: un `QFrame` llega al lector como «borde» (una tarjeta se anunciaba como un
marco) y un `QWidget` como «cliente». El rol solo se puede cambiar sustituyendo la **interfaz
accesible** del widget, así que `accessibility.py` instala una fábrica que devuelve su propia
interfaz a los widgets marcados y `None` al resto (que siguen con la de Qt, sin cambios).

| Elemento | Rol que anuncia |
| --- | --- |
| Tarjetas (`Card` y sus variantes: unidades, filas de arranque, tareas, cifras) | Grupo (`Grouping`), con el nombre de su título |
| Títulos de página y de sección | Encabezado (`Heading`) |
| Indicadores del inicio y gráfica del historial | Gráfica (`Chart`) |
| Medidor de salud y barras de progreso | Barra de progreso (`ProgressBar`) |
| Tarjetas pulsables, fichas de herramientas y filas del analizador | Botón (`Button`) |

La interfaz propia no solo cambia el rol: informa del **estado** (visible, activo, se puede enfocar
y tiene el foco) y del **rectángulo** del widget en pantalla, que es lo que el lector necesita para
navegar hasta él. Sin eso, cambiar el rol habría dejado los widgets peor de lo que estaban.

Si el lector de pantalla no está activo no se pierde nada: el anuncio simplemente no se oye. Y con
una versión de Qt anterior a la 6.8 (las dependencias permiten 6.6), queda el nombre accesible, que
es el respaldo que Qt sí soporta siempre.

### 3.10 Apariencia: clara, oscura o la de Windows

Hay **dos paletas completas** con las mismas claves y cambia una sola cosa: cuál está activa.
`COLORS` y `SURFACES` son siempre la paleta en uso y se **modifican en su sitio** (nunca se
reasignan), porque toda la interfaz las tiene importadas por referencia; si se reasignaran, media
aplicación seguiría pintando con la paleta vieja.

| Modo | Qué hace |
| --- | --- |
| **Seguir a Windows** (por defecto) | Lee `HKCU\...\Themes\Personalize\AppsUseLightTheme`, la misma marca que usa Windows para el modo claro |
| **Oscuro** | Paleta «grafito vivo», la del diseño original |
| **Claro** | Paleta «papel cálido»: blancos sucios y el esmeralda oscurecido para que el texto se lea |

El cambio se hace en **Ajustes y alertas → Apariencia**, y la propia ventana explica en una línea
qué supone la opción elegida *según cómo esté Windows ahora mismo* («Se usa el mismo modo que
Windows, que ahora mismo está en oscuro»). Al guardar, la paleta se aplica y **la ventana se vuelve
a montar**: los colores se leen al construir cada widget (iconos, gráficos, estilos en línea,
gráficas), así que rehacerla es más fiable que repintar cientos de detalles a mano. La ventana nueva
hereda tamaño, posición y visibilidad, y la anterior se cierra de verdad (no se queda en la bandeja).

Con `--tema claro|oscuro|auto` se fuerza el tema **solo para esa ejecución**, sin tocar lo guardado:
es la forma de probarlo o de generar documentación reproducible.

**Comprobado:** el contraste WCAG 4,5:1 de los pares texto/fondo en **las dos paletas**, que el
acento sea esmeralda en ambas, que cambiar de paleta no reasigne los diccionarios (las referencias
ya tomadas siguen valiendo), que la plantilla del QSS no deje claves sin rellenar, que cada paleta
tenga su marca de verificación (la del tema oscuro es blanca y sobre fondo claro desaparecería), que
el modo `auto` resuelva al del sistema y que un valor ilegible caiga en `auto`, además del render
claro de la portada (fondo claro, texto oscuro y acento presentes).

## 4. Línea de comandos

```bat
PC Toolkit.exe                        Abre la aplicación
PC Toolkit.exe --bandeja              Arranca solo con el icono de la bandeja (inicio automático)
PC Toolkit.exe --analizar C:\Datos     Abre y analiza esa carpeta al arrancar
PC Toolkit.exe --screenshot salida     Guarda una captura de cada página y sale
PC Toolkit.exe --tema claro            Abre con el tema claro (o `oscuro`/`auto`) sin guardarlo
PC Toolkit.exe --demo                  Carga tareas de ejemplo
PC Toolkit.exe --mantenimiento [--simular] [--liberar-memoria] [--antiguedad HORAS]
PC Toolkit.exe --version
```

---

## 5. Estructura del proyecto

```
app.py                          Punto de entrada y opciones de línea de comandos
build_exe.py / build_exe.bat    Empaquetado de la aplicación con PyInstaller
build_installer.py / .bat       Empaquetado del instalador y del desinstalador
firmar.py / firmar.bat          Firma digital con signtool (SHA-256, sello RFC 3161 y verificación)
.github/workflows/firmar.yml   Compila y firma en Windows con Azure Artifact Signing (OIDC)
installer/
  installer_core.py             Lógica compartida: copia, accesos directos, registro, borrado
  setup_app.py                  Asistente de instalación (y modo silencioso)
  uninstall_app.py              Desinstalador mínimo, sin dependencias gráficas
pctoolkit/
  core/                         Lógica sin interfaz: métricas, memoria, arranque de Windows,
                                mantenimiento, disco, herramientas, tareas, informes, historial,
                                alertas, preferencias y registro de deshacer
  ui/                           Interfaz PySide6: temas (claro y oscuro), anuncios accesibles y
                                roles de los widgets propios, iconos, tarjetas, gráfica, bandeja,
                                ventana principal y una página por módulo
tests/smoke_test.py             545 comprobaciones automáticas (núcleo, interfaz, instalador y firma)
tools/
  app_icon.py                   Dibuja el icono de la aplicación
  medir_consumo.py              Mide el consumo de CPU/RAM de cada perfil de muestreo
  capturar_instalador.py        Capturas del asistente y del desinstalador
  verificar_instalacion.py      Instala de verdad con tus rutas y desinstala al terminar
capturas/                       Imágenes de la documentación

Al compilar aparecen tres artefactos en `dist\`:

  dist\PC Toolkit\               Programa completo (carpeta autocontenida, 64,7 MB)
  dist\PC Toolkit.exe            Programa en un solo archivo (46,6 MB)
  dist\PC Toolkit Setup.exe      Instalador de Windows con la aplicación dentro (78,9 MB)
```

---

## 6. Dónde se guardan los datos

- Tareas de mantenimiento: `%APPDATA%\PCToolkit\tareas_mantenimiento.json`
- Historial de métricas: `%APPDATA%\PCToolkit\historial.jsonl` (24 h; ~180 KB al día con 30 s)
- Histórico del mantenimiento: `%APPDATA%\PCToolkit\mantenimiento.jsonl` (últimas 200 ejecuciones)
- Cambios reversibles (para el botón Deshacer): `%APPDATA%\PCToolkit\deshacer.jsonl` (los últimos 200)
- XML de la tarea programada: `%APPDATA%\PCToolkit\tarea_mantenimiento.xml`
- Preferencias (umbrales, bandeja, memoria, programación, modo eco, tema): registro de Windows,
  `HKCU\Software\PCToolkit`
- Informes por defecto: `C:\Users\<usuario>\Documents\PCToolkit\Informes`

Todo esto **se conserva al desinstalar** salvo que lo borres desde el asistente o con
`--borrar-datos`.

---

## 7. Requisitos y compilación

- Windows 10 u 11 (x64). Para ejecutar desde el código: Python 3.10+ con `PySide6` y `psutil`.
- Para el ejecutable y el instalador: `PyInstaller` (está en `requirements.txt`).

```bat
build_exe.bat                  Genera dist\PC Toolkit\ y comprueba que arranca y funciona
python build_exe.py --onefile  Genera dist\PC Toolkit.exe (un solo archivo, para compartir)
build_installer.bat            Genera dist\PC Toolkit Setup.exe y comprueba el ciclo completo
firmar.bat                     Firma los cuatro ejecutables de dist\ (véase la sección 8)
```

Si tienes certificado, se puede compilar y firmar en el mismo paso (se le pasa a `--firmar`
cualquier opción de `firmar.py`; véase la sección 8):

```bat
python build_exe.py --onefile --firmar --pfx llave.pfx
python build_installer.py --rebuild --firmar --huella 1A2B3C…
set PCTOOLKIT_SIGN_ARGS=--dlib Azure.CodeSigning.Dlib.dll --metadatos metadata.json
python build_installer.py --firmar
```

El orden importa si vas a firmar a mano: **primero se compila todo y al final se firma**, porque el
instalador copia la carpeta de la aplicación y crea el desinstalador dentro de ella. Con `--firmar`
no hay que preocuparse: cada script firma al terminar, cuando ya está todo creado.

Qué entregar:

- **Para instalar en un equipo**: `dist\PC Toolkit Setup.exe`, un único archivo que ya lleva la
  aplicación dentro. Es la forma recomendada (accesos directos y desinstalador incluidos).
- **Para llevar y ejecutar sin instalar nada**: `dist\PC Toolkit.exe`, un único archivo (tarda un
  par de segundos más en abrir porque se descomprime en memoria cada vez).
- **Firmados** (opcional, véase la sección 8): después de compilar, `firmar.bat` firma los cuatro
  ejecutables con SHA-256 y sello de tiempo, y verifica el resultado.

Los tres artefactos se comprueban solos al generarlos: el ejecutable se **ejecuta** (capturas de las
siete páginas, historial y mantenimiento desatendido en simulación) y el instalador completa un ciclo
de instalar, ejecutar y desinstalar.

Pruebas:

```bat
.venv\Scripts\python.exe tests\smoke_test.py           545 comprobaciones
.venv\Scripts\python.exe tools\verificar_instalacion.py  Instala de verdad y desinstala al terminar
set PCTOOLKIT_OFFSCREEN=1                               si no hay pantalla disponible
```

---

## 8. Firma digital de los ejecutables

### 8.1 Qué aporta firmar (y qué no)

Firmar un ejecutable con un **certificado de firma de código** (Authenticode) hace tres cosas:

- **Quita el «editor desconocido»**: Windows muestra el nombre verificado de quien publica.
- **Reduce los avisos de SmartScreen y de los antivirus**, que desconfían de los binarios nuevos sin
  firmar.
- **Garantiza integridad**: si alguien modifica el archivo después de firmarlo, la firma deja de ser
  válida y Windows lo dice.

Lo que **no** hace: la reputación de SmartScreen se gana con el tiempo y las descargas de los
usuarios, así que un certificado nuevo no elimina el aviso el primer día; y un certificado firmado con
una CA que el equipo no conoce (por ejemplo, uno autofirmado) sigue sin dar confianza pública.

### 8.2 La herramienta del proyecto

[firmar.py](firmar.py) (con [firmar.bat](firmar.bat) para doble clic) hace todo el trabajo:
localiza `signtool`, firma los cuatro ejecutables de `dist\` con **SHA-256** y **sello de tiempo
RFC 3161**, y después **verifica** cada firma con `signtool verify /pa /v`:

```bat
firmar.bat                     Firma con el certificado disponible en el almacén
firmar.bat --estado            Informe: signtool, certificados y firmas actuales
firmar.bat --traer-signtool    Descarga signtool del paquete oficial de Microsoft
firmar.bat --huella 1A2B3C…    Certificado del almacén de Windows por huella
firmar.bat --sujeto "Mi SL"    Certificado del almacén por nombre
firmar.bat --pfx llave.pfx     Archivo PFX
firmar.bat --autoprueba        Prueba completa con un certificado autofirmado
firmar.bat --sin-sello         Sin sello de tiempo (no recomendado)
firmar.bat --comprobar         Comprueba las firmas y falla (código 1) si alguna no vale
firmar.bat ruta\otro.exe       Solo los archivos que le indiques
```

La contraseña del PFX **no se escribe en la línea de comandos**: se lee de la variable de entorno
`PCTOOLKIT_CERT_PASSWORD` o del archivo indicado con `--password-file`. Si aun así se usa
`--password`, la herramienta avisa de que queda visible en la lista de procesos.

Las mismas opciones valen para firmar al compilar, con `--firmar`:

```bat
python build_exe.py --onefile --firmar --pfx llave.pfx --password-file clave.txt
python build_installer.py --firmar --huella 1A2B3C…
```

`--firmar` acepta cualquier argumento de `firmar.py` tal cual, así que sirve para las cuatro formas
de conseguir el certificado. Y si no quieres repetirlas en cada orden (o no puedes escribirlas,
como en un servidor de compilación), se dejan en la variable de entorno `PCTOOLKIT_SIGN_ARGS`:

```bat
set PCTOOLKIT_SIGN_ARGS=--dlib C:\tools\Azure.CodeSigning.Dlib.dll --metadatos metadata.json
python build_installer.py --rebuild --firmar
```

Lo que se firma en cada caso: `build_exe.py --firmar` firma el ejecutable que acaba de crear (la
carpeta o el archivo único); `build_installer.py --firmar` firma **todo** lo que haya en `dist\`
(programa, desinstalador, instalador y la variante de un solo archivo si existe). Si no hay ningún
certificado configurado, la compilación termina con error en vez de dejar los ejecutables sin firmar
en silencio. Para comprobar después el resultado, también desde un servidor de compilación:

```bat
python firmar.py --comprobar     :: código de salida 1 si algo no está bien firmado
```

### 8.3 Conseguir signtool

`signtool.exe` viene con el **Windows SDK** («Signing Tools for Desktop Apps»), en
`C:\Program Files (x86)\Windows Kits\10\bin\<versión>\x64\signtool.exe`. También se puede descargar
sin instalar nada desde el paquete oficial de Microsoft
([Microsoft.Windows.SDK.BuildTools](https://www.nuget.org/packages/Microsoft.Windows.SDK.BuildTools)):

```bat
python firmar.py --traer-signtool
```

Eso descarga el paquete (unos 21 MB) y deja `signtool.exe` en `tools\bin\signtool\` (esa carpeta no se
versiona). La herramienta busca signtool, por este orden: `--signtool`, el `PATH`, `tools\bin`,
el Windows SDK y la caché de NuGet.

### 8.4 Qué certificado se puede usar hoy

Desde el **1 de junio de 2023**, los requisitos del CA/Browser Forum exigen que la clave privada de
los certificados de firma de código de confianza pública se genere y se use **en hardware**
(token USB o HSM), así que las CA ya no entregan un `.pfx` suelto. Desde el **1 de marzo de 2026** la
validez máxima de estos certificados es de **460 días**.

| Opción | Para qué sirve | Qué implica |
| --- | --- | --- |
| **Certificado autofirmado** | Probar el proceso en tus equipos | Gratis; Windows no lo confía. Sirve para la autoprueba, **no para repartir** |
| **IV / OV / EV** de una CA (Sectigo, DigiCert, SSL.com…) | Distribuir el programa | Validación de identidad (particular o empresa) y **token USB o HSM**: validez de 460 días y renovación |
| **Azure Artifact Signing** (antes Trusted Signing) | Firmar desde cualquier equipo o CI sin llevar el token | Servicio en la nube: validación de identidad, suscripción de Azure de pago, desde ~9,99 US$/mes (Basic, 5.000 firmas). Public Trust para particulares solo en EE. UU. o Canadá; los certificados duran 72 horas, de ahí la importancia del sello |
| **DigiCert KeyLocker / SSL.com eSigner** | Clave en HSM remoto con partners ya conocidos | Se firma con la misma sintaxis de signtool usando su proveedor (KSP/CNG) |

Fuentes: [requisitos del CA/Browser Forum](https://cabforum.org/working-groups/code-signing/requirements/),
[Azure Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) y
[precios](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/).

> Firmar un programa para uso propio o interno no necesita ningún certificado de pago: basta con
> repartirlo tal cual (SmartScreen pedirá «Ejecutar de todas formas») o firmarlo con uno autofirmado y
> confiar en él **solo en tus equipos**.

### 8.5 Cómo se firma en cada caso

Comandos que se pueden copiar (los genera también `firmar.py`, que además los verifica):

```bat
:: Certificado del almacén de Windows (recomendado si la clave está en un token/HSM)
signtool sign /v /fd SHA256 /sha1 HUELLA /tr http://timestamp.digicert.com /td SHA256 /d "PC Toolkit" "dist\PC Toolkit\PC Toolkit.exe"

:: Archivo PFX (solo si tienes uno; la contraseña queda visible en la lista de procesos)
signtool sign /v /fd SHA256 /f llave.pfx /p CONTRASENA /tr http://timestamp.digicert.com /td SHA256 "dist\PC Toolkit Setup.exe"

:: Clave en HSM remoto (ejemplo de DigiCert KeyLocker)
signtool sign /v /fd SHA256 /csp "DigiCert Signing Manager KSP" /kc MI_ALIAS /f cert.crt /tr http://timestamp.digicert.com /td SHA256 "dist\PC Toolkit.exe"

:: Azure Artifact Signing (necesita sus herramientas cliente y el JSON de metadatos)
signtool sign /v /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib Azure.CodeSigning.Dlib.dll /dmdf metadata.json "dist\PC Toolkit\PC Toolkit.exe"
```

Hay que firmar los **cuatro** ejecutables de `dist\`:

1. `dist\PC Toolkit\PC Toolkit.exe` — el programa (y el que copia el instalador).
2. `dist\PC Toolkit\Desinstalar PC Toolkit.exe` — el desinstalador.
3. `dist\PC Toolkit Setup.exe` — el instalador.
4. `dist\PC Toolkit.exe` — la variante de un solo archivo, si la generas.

### 8.6 Sello de tiempo (por qué es imprescindible)

El sello de tiempo RFC 3161 deja constancia de **cuándo** se firmó. Gracias a él, la firma sigue
siendo válida después de que el certificado caduque; sin sello, todos los archivos habrían que
volverse a firmar cada vez que se renueve el certificado. `firmar.py` usa `/tr` y `/td SHA256` con
estos servidores (`--sello` permite cambiarlo):

| Proveedor | URL |
| --- | --- |
| DigiCert | `http://timestamp.digicert.com` |
| Sectigo | `http://timestamp.sectigo.com` |
| GlobalSign | `http://timestamp.globalsign.com/tsa/r45standard` |
| Azure Artifact Signing | `http://timestamp.acs.microsoft.com` |

### 8.7 Verificar la firma

```bat
signtool verify /pa /v "dist\PC Toolkit Setup.exe"
firmar.bat --estado        :: informe de los cuatro, sin firmar nada
```

`/pa` aplica la política Authenticode e `/v` muestra el detalle. Si la firma se hizo con un
certificado autofirmado, el resultado es algo como *«la cadena termina en un certificado raíz que el
proveedor de confianza no reconoce»*: **eso no significa que el archivo esté mal**, sino que ese
equipo no confía en esa CA (por ejemplo, en la autoprueba del apartado siguiente). Lo que sí importa
es que signtool muestre el hash del archivo y el certificado firmante, y que no avise de que la firma
no coincide con el contenido.

### 8.8 Buenas costumbres

- **No versiones el certificado ni su contraseña**: `*.pfx` fuera del repositorio y la contraseña en la
  variable de entorno `PCTOOLKIT_CERT_PASSWORD` o en un archivo que no se comparta.
- **Firma al final del proceso**: `build_exe.bat` → `build_installer.bat` → `firmar.bat`, o mejor con
  `--firmar` en cada script, que firma en cuanto termina (nunca antes de comprobar que funciona).
- **En un servidor de compilación**, un token USB no está enchufado: usa un servicio en la nube
  (Azure Artifact Signing con identidad OIDC, véase 8.10) o el KSP del proveedor; evita firmar en
  trabajos que ejecuten código de terceros.
- **Renueva con antelación**: los certificados de firma de código duran como máximo 460 días.

### 8.9 Qué se ha probado aquí

`firmar.bat --autoprueba` crea un certificado autofirmado temporal, lo exporta a PFX con una
contraseña aleatoria, **firma copias** del programa y del instalador con SHA-256 y sello RFC 3161,
comprueba las firmas, confirma que los ejecutables buenos no se han tocado y borra el certificado
temporal y las copias. Así se valida toda la cadena (firma, sello y verificación) sin comprar nada.

> Con la autoprueba se ha comprobado que la firma se crea, que el sello de tiempo de DigiCert valida
> su propia cadena y que signtool lee el hash del archivo y el certificado firmante. Lo único que no
> se puede probar sin un certificado de pago es la confianza pública (que SmartScreen deje de avisar).

La firma **integrada en la compilación** (`--firmar`) también se comprueba de verdad: la suite crea
un certificado de prueba temporal y firma una **copia** con la misma función que llaman los scripts
de compilación. Además se ha recorrido el camino completo de la línea de comandos
(`build_exe.py --onefile --firmar` y `build_installer.py --rebuild --firmar` con un certificado de
prueba): los cuatro ejecutables de `dist\` salen firmados, con su sello de tiempo RFC 3161, y
`firmar.py --comprobar` los ve (informa de que la raíz no es de confianza, que es lo esperado con un
certificado de prueba; con uno de pago la comprobación termina con código 0).

### 8.10 Compilar y firmar en un servidor (GitHub Actions)

El proyecto incluye [`.github/workflows/firmar.yml`](.github/workflows/firmar.yml), que en un servidor
de Windows compila el programa y el instalador, los firma con **Azure Artifact Signing** (antes
Trusted Signing) y guarda los ejecutables firmados como artefacto de la ejecución. La autenticación
es **OIDC**: el flujo pide el token a Azure con la identidad federada del repositorio, así que **no
hay ningún certificado, contraseña ni clave guardados en GitHub**.

Se ejecuta a mano (Actions → *Compilar y firmar (Windows)* → Run workflow) y al publicar una etiqueta
`v…`. Para que corra además en cada envío a `main`, añade `branches: [main]` al `push`.

Qué hay que preparar una sola vez:

1. **En Azure**: una cuenta de Artifact Signing y un **perfil de certificado** (*Public Trust* o
   *Private Trust*, según para quién firmes). La cuenta vive en una región concreta y el `Endpoint`
   tiene que ser el de esa región (`https://weu.codesigning.azure.net` para Europa Occidental,
   `https://eus.codesigning.azure.net` para Este de EE. UU., etc.; la tabla completa está en la
   [documentación oficial](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)).
2. **En Entra ID**: una aplicación con una **credencial federada** para este repositorio, con sujeto
   `repo:PROPIETARIO/REPO:ref:refs/heads/main` y audiencia `api://AzureADTokenExchange`, a la que se
   asigna el rol **Artifact Signing Certificate Profile Signer** en el perfil. No hace falta darle
   permisos de propietario sobre la suscripción.
3. **En GitHub** (Settings → Secrets and variables → Actions):
   - Secretos: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
   - Variables: `ARTIFACT_SIGNING_ENDPOINT`, `ARTIFACT_SIGNING_ACCOUNT`, `ARTIFACT_SIGNING_PROFILE`.

El flujo, paso a paso: prepara Python y .NET (la biblioteca de firma en la nube lo necesita), instala
`requirements.txt`, ejecuta la suite de comprobaciones, descarga `signtool` con
`firmar.py --traer-signtool` (con la versión del SDK fijada en el propio flujo) y la biblioteca
`Microsoft.ArtifactSigning.Client`, que trae `bin\x64\Azure.CodeSigning.Dlib.dll`; inicia sesión en
Azure con `azure/login@v3`; escribe el `metadata.json` con la cuenta, el perfil y
`ExcludeCredentials` (conserva `AzureCliCredential`, que es la sesión que deja el inicio de sesión, y
descarta las que no existen en un servidor); y compila y firma de una vez:

```pwsh
$env:PCTOOLKIT_SIGN_ARGS = "--signtool $signtool --dlib $dlib --metadatos $metadata --sello http://timestamp.acs.microsoft.com"
python build_exe.py --onefile --firmar
python build_installer.py --rebuild --firmar
python firmar.py --comprobar        # falla el trabajo si algo quedó sin firmar
```

Dos detalles que suelen dar problemas: **el signtool del servidor puede ser antiguo** (la biblioteca
en la nube exige una versión reciente y no admite el SDK 20348), por eso el flujo le pasa la ruta
exacta de `tools\bin\signtool`; y **el sello de tiempo debe ser el de Microsoft**
(`http://timestamp.acs.microsoft.com`), porque los certificados de este servicio duran 72 horas.

Alternativa: la acción oficial
[Azure/artifact-signing-action](https://github.com/Azure/artifact-signing-action) hace lo mismo en
un solo paso. El flujo del proyecto usa las herramientas locales a propósito, porque así prueba el
mismo camino (`--firmar` con las opciones en `PCTOOLKIT_SIGN_ARGS`) que usarías en tu equipo.

---

## 9. Notas y limitaciones

- **Temperatura y GPU**: `psutil` solo expone sensores térmicos en Linux y no da datos de GPU, así
  que la cuarta tarjeta de Inicio muestra el **tráfico de red**.
- **Permisos**: sin administrador funcionan el recorte de conjuntos de trabajo, el analizador, las
  alertas, el historial, las tareas, los informes y el mantenimiento programado; el vaciado de
  cachés del sistema y las herramientas marcadas con escudo piden UAC.
- **Firma digital**: el instalador y la aplicación no están firmados, así que SmartScreen puede
  pedir confirmación la primera vez en otro equipo («Más información → Ejecutar de todas formas»).
- **Antivirus de terceros** pueden analizar el ejecutable la primera vez (tarda unos segundos).
- **Bandeja**: si el sistema no tiene bandeja disponible, la aplicación funciona igual sin segundo
  plano y lo avisa en su diálogo de configuración.
- **Programas de inicio**: las aplicaciones de la Microsoft Store no aparecen en la lista (Windows las
  arranca sin una clave del registro estable) y los servicios se administran aparte. El «coste» de
  cada entrada es una estimación propia a partir de su tipo, no la cifra del Administrador de tareas.

---

## 10. Preguntas frecuentes

**¿Hace falta ser administrador para instalar?**
No. Se instala en tu carpeta de usuario y escribe solo en `HKCU`; nada de UAC.

**¿Puedo desinstalarlo sin perder mi historial?**
Sí: por defecto se conservan el historial, los informes y las preferencias. Solo se borran si marcas
la casilla (o usas `--borrar-datos`).

**¿Se queda algo abierto cuando cierro la ventana?**
Solo si tienes activa la bandeja; el menú del icono tiene **Salir** para cerrarlo del todo y el
diálogo de **Alertas y bandeja** permite desactivar ese comportamiento.

**¿La limpieza de memoria puede hacer daño?**
No toca procesos críticos ni provoca pérdidas de datos: devuelve al sistema memoria en caché, que se
recarga desde el disco cuando se necesita. Aun así, el umbral por defecto es alto para no hacerlo
constantemente.

**¿Qué pasa si muevo la carpeta de la aplicación?**
La página de mantenimiento programado avisa de que el comando registrado ya no coincide y permite
volver a programar la tarea con la ruta nueva.

**¿Tengo que firmar el instalador para usarlo yo?**
No. Si es para tus equipos, funciona igual (SmartScreen pedirá «Ejecutar de todas formas» la primera
vez). La firma de confianza pública solo es necesaria si vas a distribuirlo a otras personas.

**¿Puedo probar que la firma funciona sin comprar un certificado?**
Sí: `firmar.bat --autoprueba` comprueba el proceso entero con un certificado autofirmado temporal y
deja el equipo como estaba.
