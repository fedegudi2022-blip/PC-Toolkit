@echo off
rem Genera el instalador de PC Toolkit (doble clic).
rem Deja `dist\PC Toolkit Setup.exe` junto a la carpeta `dist\PC Toolkit`.
rem
rem   build_installer.bat --rebuild          Reconstruye antes la aplicación
rem   build_installer.bat --firmar --pfx l.pfx  Compila y firma al terminar
rem   build_installer.bat --no-verify        Solo empaqueta, sin comprobar
chcp 65001 >nul
cd /d "%~dp0"

set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo No se encuentra el entorno virtual .venv.
    echo Ejecuta primero run.bat para crearlo e instalar las dependencias.
    pause
    exit /b 1
)

"%PYTHON%" build_installer.py %*
if errorlevel 1 (
    echo.
    echo El instalador no se pudo generar. Revisa los mensajes de arriba.
    pause
    exit /b 1
)

echo.
echo Todo listo: reparte `dist\PC Toolkit Setup.exe` junto a la carpeta `dist\PC Toolkit`.
pause
