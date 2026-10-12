@echo off
rem ---------------------------------------------------------------
rem  PC Toolkit - firmar los ejecutables de dist\ (doble clic)
rem  Usa signtool con SHA-256 y sello de tiempo RFC 3161.
rem
rem    firmar.bat --estado          Informe: signtool, certificados y firmas
rem    firmar.bat --comprobar       Falla (codigo 1) si algo no esta bien firmado
rem    firmar.bat --huella 1A2B..   Certificado del almacen de Windows
rem    firmar.bat --pfx llave.pfx   Certificado en un archivo PFX
rem    firmar.bat --autoprueba      Proceso completo con un certificado temporal
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

"%PYTHON%" firmar.py %*
if errorlevel 1 (
    echo.
    echo La firma no termino bien. Revisa los mensajes de arriba.
    pause
    exit /b 1
)

echo.
echo Firma terminada. Comprueba el resultado con firmar.bat --comprobar.
pause
