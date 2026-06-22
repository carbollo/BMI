# BMI — Bethesda Mod Installer

Descargador e instalador de mods de **Nexus Mods** para los juegos de **Bethesda**
(Skyrim, Fallout, Oblivion, Starfield, Morrowind), pensado para que instalar mods sea
fácil — también con **cuentas gratuitas** y **en tu idioma**.

Hace lo esencial igual que Vortex / Mod Organizer 2: navegas Nexus dentro del programa
(o pegas una URL), pulsas **un** botón de descarga, y la app se encarga del resto —
descargar, extraer, instalar (incluido FOMOD), desplegar a `Data`, activar el plugin en
`plugins.txt`, y resolver **colecciones**, **dependencias** y **traducciones** en cadena.

> **Respeta las normas de Nexus.** BMI usa la **API oficial de Nexus** y el protocolo
> estándar **`nxm://`** — el mismo mecanismo que Vortex y Mod Organizer 2. **No automatiza
> clics en la web, no salta el contador de descarga lenta ni el captcha, y no elude Nexus
> Premium.** En cuentas gratuitas el clic de descarga lo das tú en la web de Nexus; BMI
> solo recibe el enlace `nxm://`.

---

## Cuentas gratis vs Premium

Nexus entrega el enlace de descarga directo **solo a cuentas Premium** (es su modelo de
negocio). Para cuentas gratis, pulsas **"Mod Manager Download"** en la web (1 clic por
mod, igual que en Vortex/MO2), que genera un enlace `nxm://` con una clave temporal; BMI
lo captura y hace todo lo demás automáticamente. Con **Premium**, pegas la URL y descarga
sin ningún clic.

## Multi-juego

Soporta **Skyrim Special Edition (+ Anniversary)**, **Skyrim** clásico, **Fallout 4**,
**Fallout 3**, **Fallout: New Vegas**, **Oblivion**, **Starfield** y **Morrowind**. Al
cambiar de juego todo se adapta: el navegador abre su Nexus, las descargas/dependencias
usan su dominio, el escáner usa **sus** masters vanilla, **▶ Jugar** lanza **su** script
extender (SKSE64/F4SE/NVSE/FOSE/OBSE/SFSE…), y los mods se guardan por separado por juego.

## Características

- **Pega una URL** de mod o **colección** y se descarga e instala.
- **Dependencias automáticas**: detecta los mods requeridos y los descarga antes.
- **Cola múltiple**: añade muchas URLs a la vez (una por línea), se instalan en orden.
- **Traducción a tu idioma**: busca e instala la traducción del mod (español, inglés,
  francés, alemán, italiano), y en FOMOD elige automáticamente la opción de tu idioma.
- **Interfaz en 5 idiomas** (se aplica al reiniciar).
- **Instaladores FOMOD**: asistente interactivo, o automático con las opciones obligatorias
  y recomendadas.
- **Gestor de mods**: orden de carga, conflictos, perfiles, activar/desactivar (repliega
  sin borrar) y detección de los mods que ya tenías instalados.
- **Archivos de carpeta raíz**: los que van junto al `.exe` (Engine Fixes parte 2, wrappers
  de ENB/ReShade, runtime de SKSE) se despliegan en la raíz del juego, no en `Data`.
- **▶ Jugar** con un clic (con su Script Extender si lo tienes instalado).

## Versión portable (.exe)

`dist\BMI.exe` es **portable**: en Windows 10/11 lo ejecutas con doble clic y funciona,
**sin instalar nada ni permisos de administrador**. La configuración se guarda en
`%APPDATA%\BMI`. Pesa ~128 MB (incluye el navegador Chromium de QtWebEngine); el **primer
arranque** tarda unos segundos en descomprimirse. Compilado con **Nuitka**
(`build_protegido.bat`).

## Ejecutar desde el código (desarrollo)

Requiere **Python 3.10+**.

```powershell
cd "C:\Users\eziog\Desktop\Google Antigravity proyectos\programas\sin bot"
pip install -r requirements.txt
python run.py
```

> `PySide6` incluye el navegador embebido (QtWebEngine). Para extraer `.rar`/`.7z` conviene
> tener **7-Zip** instalado (https://www.7-zip.org/).

## Uso (resumen)

1. **Primer arranque**: un asistente te guía → elige idioma y juego, pega tu **API Key** de
   Nexus (https://www.nexusmods.com/users/myaccount?tab=api, sección *Personal API Key*),
   indica la carpeta **`Data`** del juego y **Registra el protocolo nxm://**.
2. Pega la URL de un mod o colección (o navega por Nexus dentro del programa) y pulsa
   **"Mod Manager Download"** (cuenta gratis) — con Premium se descarga solo.
3. Mira la pestaña **Descargas** para el progreso y **Mods** para gestionarlos. Pulsa
   **▶ Jugar** para lanzar el juego con su Script Extender.

## Arquitectura

| Módulo | Función |
|--------|---------|
| `nxm.py` | Registra el protocolo `nxm://` en Windows y parsea los enlaces. |
| `ipc.py` | Instancia única: reenvía `nxm://` del navegador externo a la ventana abierta. |
| `nexus_api.py` | Cliente REST v1 (validar key, info de mods/archivos, enlace de descarga). |
| `nexus_graphql.py` | Cliente GraphQL v2 (colecciones y dependencias). |
| `downloader.py` | Descarga con progreso. |
| `archive.py` | Extracción `.zip` / `.7z` / `.rar` (usa 7-Zip si está). |
| `fomod.py` | Instalador FOMOD (asistente o automático). |
| `deploy.py` | Despliega a `Data` (hardlink/copia), a la carpeta raíz del juego, y gestiona `plugins.txt`. |
| `installer.py` | Orquesta extraer → FOMOD → desplegar → registrar mod. |
| `manager.py` | Cola en segundo plano: resolver → descargar → instalar → dependencias → traducción. |
| `scanner.py` / `conflicts.py` / `profiles.py` | Escaneo de plugins, conflictos y perfiles de orden de carga. |
| `launcher.py` | Lanza el juego con su script extender. |
| `gui/` | Interfaz PySide6: navegador, descargas, mods, registro, ajustes. |

El **despliegue por hardlink** (por defecto) no duplica espacio en disco y permite
**desinstalar limpio**: cada mod guarda la lista de archivos que colocó.

## Aviso legal

BMI es un proyecto **independiente y no oficial**. No está afiliado ni respaldado por
Nexus Mods ni Bethesda Softworks. Respeta los Términos de Servicio de Nexus: usa la **API
oficial** y el protocolo `nxm://`, y **no** automatiza la web ni elude la limitación de
las cuentas gratuitas. Usa siempre tu propia cuenta de Nexus.
