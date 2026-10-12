@echo off
rem ---------------------------------------------------------------
rem  PC Toolkit - arranque rapido
rem  Crea el entorno virtual la primera vez y abre la aplicacion.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [!] No se encontro Python. Instala Python 3.10 o superior desde https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [i] Creando entorno virtual...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [!] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo [i] Instalando dependencias: PySide6 y psutil...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] No se pudieron instalar las dependencias.
        pause
        exit /b 1
    )
)

echo [i] Abriendo PC Toolkit...
start "" ".venv\Scripts\pythonw.exe" "app.py" %*
exit /b 0
