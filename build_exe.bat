@echo off
rem ---------------------------------------------------------------
rem  PC Toolkit - generar el ejecutable (doble clic)
rem  Deja `dist\PC Toolkit\` y, con --onefile, `dist\PC Toolkit.exe`.
rem
rem    build_exe.bat                                App en carpeta
rem    build_exe.bat --onefile                      App en un solo archivo
rem    build_exe.bat --onefile --firmar --pfx l.pfx Compila y firma al terminar
rem    build_exe.bat --no-verify                    Solo empaqueta, sin comprobar
rem ---------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"

set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo No se encuentra el entorno virtual .venv.
    echo Ejecuta primero run.bat para crearlo e instalar las dependencias.
    pause
    exit /b 1
)

"%PYTHON%" build_exe.py %*
if errorlevel 1 (
    echo.
    echo El programa no se pudo generar. Revisa los mensajes de arriba.
    pause
    exit /b 1
)

echo.
echo Todo listo: revisa la carpeta dist\.
pause
