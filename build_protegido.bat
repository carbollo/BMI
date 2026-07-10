@echo off
REM ===================================================================
REM  Compila BMI.exe (Nuitka --onefile): un unico .exe portable que se
REM  autoextrae en temp al arrancar. Compila el Python a codigo maquina
REM  real (mas dificil de descompilar que PyInstaller).
REM  Resultado: build_nuitka\BMI.exe  ->  dist\BMI.exe
REM  Requiere: pip install nuitka  (y un compilador; Nuitka baja MinGW).
REM ===================================================================
cd /d "%~dp0"
REM Detecta Python: Miniconda, Anaconda o el 'python' del PATH (para compilar en cualquier PC).
set "PY=%USERPROFILE%\miniconda3\python.exe"
if not exist "%PY%" if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -m nuitka run.py ^
  --onefile --enable-plugin=pyside6 --windows-console-mode=disable ^
  --windows-icon-from-ico=icon.ico ^
  --company-name=carbollo ^
  --product-name="BMI - Bethesda Mod Installer" ^
  --file-version=1.3.2.0 --product-version=1.3.2 ^
  --file-description="BMI - Bethesda Mod Installer - gestor de mods de Nexus" ^
  --copyright="(c) 2026 carbollo - BMI" ^
  --include-package=nexus_mod_installer ^
  --include-module=rarfile --include-package=py7zr ^
  --nofollow-import-to=tkinter,matplotlib,pandas,scipy,IPython,notebook,pytest ^
  --include-data-files="usvfs/usvfs_x64.dll=usvfs/usvfs_x64.dll" ^
  --include-data-files="usvfs/usvfs_x86.dll=usvfs/usvfs_x86.dll" ^
  --include-data-files="usvfs/usvfs_proxy_x64.exe=usvfs/usvfs_proxy_x64.exe" ^
  --include-data-files="usvfs/usvfs_proxy_x86.exe=usvfs/usvfs_proxy_x86.exe" ^
  --lto=no --assume-yes-for-downloads --mingw64 ^
  --output-dir=build_nuitka --output-filename=BMI.exe

echo.
if exist build_nuitka\BMI.exe (
  if not exist dist mkdir dist
  copy /Y build_nuitka\BMI.exe dist\BMI.exe >nul
  echo LISTO: dist\BMI.exe ^(onefile, portable^)
) else (
  echo ERROR: no se genero el .exe. Revisa los mensajes de arriba.
)
pause
