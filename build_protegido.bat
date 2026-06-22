@echo off
REM ===================================================================
REM  Compila BMI.exe PROTEGIDO con Nuitka (compila el Python a codigo
REM  maquina real: mucho mas dificil de descompilar que PyInstaller).
REM  Requiere: pip install nuitka  (y un compilador; Nuitka baja MinGW).
REM  Resultado: build_nuitka\BMI.exe  ->  copialo a dist\BMI.exe
REM ===================================================================
cd /d "%~dp0"
set PY=%USERPROFILE%\miniconda3\python.exe

"%PY%" -m nuitka run.py ^
  --onefile --enable-plugin=pyside6 --windows-console-mode=disable ^
  --windows-icon-from-ico=icon.ico ^
  --company-name=carbollo ^
  --product-name="BMI - Bethesda Mod Installer" ^
  --file-version=1.0.0.0 --product-version=1.0.0 ^
  --file-description="BMI - Bethesda Mod Installer - gestor de mods de Nexus" ^
  --copyright="(c) 2026 carbollo - BMI" ^
  --include-package=nexus_mod_installer ^
  --include-module=rarfile --include-package=py7zr ^
  --nofollow-import-to=tkinter,matplotlib,pandas,scipy,IPython,notebook,pytest ^
  --lto=no --assume-yes-for-downloads --mingw64 ^
  --output-dir=build_nuitka --output-filename=BMI.exe

echo.
if exist build_nuitka\BMI.exe (
  copy /Y build_nuitka\BMI.exe dist\BMI.exe >nul
  echo LISTO: dist\BMI.exe ^(protegido con Nuitka^)
) else (
  echo ERROR: no se genero el .exe. Revisa los mensajes de arriba.
)
pause
