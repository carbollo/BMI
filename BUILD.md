# Compilar BMI desde el código

Guía para clonar el repositorio y **compilar BMI en cualquier PC con Windows**, desde cero.

---

## 1. Requisitos previos

| | |
|---|---|
| **Sistema** | Windows 10 u 11, **64 bits** |
| **Python** | 3.10 – 3.12 (recomendado **3.12**) — [python.org](https://www.python.org/downloads/) o Miniconda. Marca *"Add Python to PATH"* al instalar |
| **Compilador C** | **No hay que instalarlo**: Nuitka descarga solo MinGW-w64 en el primer build (el `.bat` pasa `--mingw64 --assume-yes-for-downloads`) |
| **Git** | para clonar (o descarga el ZIP del repo) |

Los binarios de **USVFS** (`usvfs/*.dll` y `*.exe`, para el Modo VFS) ya vienen en el repo, no hay que descargarlos aparte.

---

## 2. Clonar e instalar dependencias

```powershell
git clone https://github.com/carbollo/BMI.git
cd BMI

REM Dependencias de la aplicación (PySide6, requests, py7zr, rarfile)
pip install -r requirements.txt

REM Dependencias solo para compilar (Nuitka)
pip install -r requirements-build.txt
```

### Ejecutar desde el código (sin compilar, para probar/desarrollar)

```powershell
python run.py
```

---

## 3. Compilar el ejecutable portable (`BMI.exe`)

```powershell
build_protegido.bat
```

- Usa **Nuitka** en modo `--onefile` (un solo `.exe` autoejecutable).
- El **primer build tarda** varios minutos (descarga MinGW y compila todo); los siguientes van más rápido (caché).
- Resultado: **`dist\BMI.exe`** — portable, no necesita instalar nada.

> El `.bat` detecta Python automáticamente (Miniconda, Anaconda o el `python` del PATH).

---

## 4. Compilar el instalador (`BMI-Setup.exe`) — opcional

El instalador envuelve el `dist\BMI.exe` ya compilado (paso 3) y añade accesos directos,
registro del protocolo `nxm://` y desinstalador.

1. Instala **[Inno Setup 6](https://jrsoftware.org/isdl.php)** (gratis).
2. Compila el script:

```powershell
REM Ajusta la ruta a tu ISCC.exe si instalaste Inno Setup en otro sitio
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

- Resultado: **`dist\BMI-Setup.exe`** — instalador por-usuario (sin permisos de administrador).

---

## 5. Resumen de archivos de build

| Archivo | Para qué |
|---|---|
| `run.py` | Punto de entrada de la app |
| `requirements.txt` | Dependencias de ejecución |
| `requirements-build.txt` | Dependencias solo de compilación (Nuitka) |
| `build_protegido.bat` | Compila `dist\BMI.exe` (Nuitka, onefile) |
| `installer.iss` | Genera `dist\BMI-Setup.exe` (Inno Setup) |
| `icon.ico` | Icono del ejecutable |
| `usvfs/` | Binarios de USVFS (Modo VFS), incluidos en el repo |

---

## Problemas frecuentes

- **`nuitka` no se reconoce / falla el plugin de PySide6:** asegúrate de haber ejecutado
  `pip install -r requirements-build.txt` **y** `pip install -r requirements.txt` en el mismo Python.
- **El primer build se queda "descargando":** es MinGW/ccache; deja que termine (una sola vez).
- **El antivirus marca el `.exe`:** falso positivo del empaquetado de Python (le pasa a MO2 y
  Vortex). Añade una excepción o compílalo tú mismo con esta guía.
- **Falta `ISCC.exe`:** no tienes Inno Setup instalado (paso 4) o está en otra ruta.
