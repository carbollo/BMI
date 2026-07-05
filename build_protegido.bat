@echo off
REM ===================================================================
REM  Compila BMI en modo ONEDIR (Nuitka --standalone): una CARPETA con
REM  BMI.exe + sus dependencias, SIN autoextraccion en temp. Reduce
REM  mucho los falsos positivos de antivirus frente a --onefile.
REM  Resultado: dist\BMI\  (carpeta portable)  +  dist\BMI.zip (distribuir)
REM  Requiere: pip install nuitka  (y un compilador; Nuitka baja MinGW).
REM ===================================================================
cd /d "%~dp0"
set PY=%USERPROFILE%\miniconda3\python.exe

"%PY%" -m nuitka run.py ^
  --standalone --enable-plugin=pyside6 --windows-console-mode=disable ^
  --windows-icon-from-ico=icon.ico ^
  --company-name=carbollo ^
  --product-name="BMI - Bethesda Mod Installer" ^
  --file-version=1.2.0.0 --product-version=1.2.0 ^
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
if exist build_nuitka\run.dist\BMI.exe (
  if exist dist\BMI rmdir /s /q dist\BMI
  if not exist dist mkdir dist
  robocopy build_nuitka\run.dist dist\BMI /E /NFL /NDL /NJH /NJS /NC /NS >nul
  powershell -NoProfile -Command "if(Test-Path dist\BMI.zip){Remove-Item dist\BMI.zip}; Compress-Archive -Path dist\BMI -DestinationPath dist\BMI.zip"
  echo LISTO: dist\BMI\ ^(carpeta portable^) y dist\BMI.zip ^(para distribuir^)
) else (
  echo ERROR: no se genero el .exe. Revisa los mensajes de arriba.
)
pause
