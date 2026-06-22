@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM  Nexus Mod Installer - lanzador
REM  Lanza la app SIN consola (pythonw) y cierra esta ventana.
REM  La consola solo se queda si faltan dependencias o hay error.
REM ============================================================

set "PY="
set "PYW="
if exist "%USERPROFILE%\miniconda3\python.exe" (
  set "PY=%USERPROFILE%\miniconda3\python.exe"
  set "PYW=%USERPROFILE%\miniconda3\pythonw.exe"
)
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" (
  set "PY=%USERPROFILE%\anaconda3\python.exe"
  set "PYW=%USERPROFILE%\anaconda3\pythonw.exe"
)
if not defined PY (
  where python >nul 2>nul && ( set "PY=python" & set "PYW=pythonw" )
)

if not defined PY (
  echo [ERROR] No se encontro Python. Instala Python o miniconda.
  pause
  exit /b 1
)

REM Comprobar PySide6; si falta, instalar (aqui SI se ve la consola).
"%PY%" -c "import PySide6" 1>nul 2>nul
if errorlevel 1 (
  echo Faltan dependencias. Instalando con pip ^(puede tardar^)...
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] No se pudieron instalar las dependencias.
    pause
    exit /b 1
  )
)

REM Lanzar SIN consola con pythonw y cerrar esta ventana inmediatamente.
start "" "%PYW%" run.py
exit
