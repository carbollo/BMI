"""Infraestructura de Microsoft Edge WebView2 (motor del sistema) para BMI.

En vez de empaquetar Chromium (QtWebEngine, ~130 MB), usamos el runtime WebView2 que ya trae
Windows 10/11. Así el .exe pesa muchísimo menos. Se accede a WebView2 por su API .NET a
través de ``pythonnet`` (clr).

Este módulo:
  - localiza y carga las DLLs del SDK (Core + Loader), en desarrollo o junto al .exe;
  - instala un ``WindowsFormsSynchronizationContext`` en el hilo de la GUI para que las
    continuaciones async de WebView2 se entreguen sobre el bucle de eventos de Qt;
  - crea UN entorno WebView2 compartido (con carpeta de datos persistente = cookies/sesión
    compartidas entre la vista visible y el escáner de traducciones);
  - expone ``available()`` para saber si el runtime WebView2 está instalado.
"""
from __future__ import annotations

import os
import sys
import traceback

_READY = False          # entorno WebView2 creado
_ENV = None             # CoreWebView2Environment
_PENDING = []           # callbacks esperando al entorno
_INIT_ERROR = ""        # texto de error si falló la carga
_LOADED = False         # DLLs .NET cargadas


_CORE = "Microsoft.Web.WebView2.Core.dll"


def diag(msg: str) -> None:
    """Escribe una línea de diagnóstico en %APPDATA%/BMI/webview2_diag.log (para depurar el
    navegador/escáner en el .exe empaquetado, donde no hay consola)."""
    try:
        from ..config import app_data_dir
        p = os.path.join(str(app_data_dir()), "webview2_diag.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:  # noqa: BLE001
        pass


def _dll_dir() -> str:
    """Carpeta con Microsoft.Web.WebView2.Core.dll y WebView2Loader.dll.

    Se prueban varias ubicaciones y se devuelve la primera que contenga el ensamblado. En un
    onefile de Nuitka el .exe se autoextrae a una carpeta temporal, así que buscar junto a
    ``sys.argv[0]`` (el .exe real) NO sirve: hay que buscar relativo al MÓDULO ya extraído
    (``__file__``), como hace ``vfs.find_usvfs_dir``."""
    here = os.path.dirname(os.path.abspath(__file__))         # .../nexus_mod_installer/gui
    pkg = os.path.dirname(here)                               # .../nexus_mod_installer
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    except Exception:  # noqa: BLE001
        exe_dir = ""
    candidates = [
        os.path.join(pkg, "_webview2"),      # dev y onefile (incluido en nexus_mod_installer/_webview2/)
        os.path.join(here, "_webview2"),
        pkg,
        here,
        exe_dir,                             # por si se distribuyen las DLLs junto al .exe
        os.path.join(exe_dir, "_webview2"),
        os.getcwd(),
    ]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, _CORE)):
            return c
    return os.path.join(pkg, "_webview2")     # por defecto (para el mensaje de error)


def _user_data_dir() -> str:
    from ..config import app_data_dir
    d = os.path.join(str(app_data_dir()), "webview2")
    os.makedirs(d, exist_ok=True)
    return d


def _load_clr() -> bool:
    """Carga pythonnet + el ensamblado .NET de WebView2. Devuelve True si todo bien."""
    global _LOADED, _INIT_ERROR
    if _LOADED:
        return True
    try:
        d = _dll_dir()
        core = os.path.join(d, "Microsoft.Web.WebView2.Core.dll")
        if not os.path.isfile(core):
            _INIT_ERROR = f"No se encuentra el SDK de WebView2 en {d}"
            return False
        try:
            os.add_dll_directory(d)   # para que WebView2Loader.dll se resuelva
        except OSError:
            pass
        import clr  # noqa: F401  (pythonnet)
        clr.AddReference(core)
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        _LOADED = True
        diag(f"[wv2] CLR + WebView2 cargados desde {d}")
        return True
    except Exception:  # noqa: BLE001
        _INIT_ERROR = "No se pudo cargar .NET/WebView2:\n" + traceback.format_exc()
        diag("[wv2] ERROR cargando CLR/WebView2:\n" + _INIT_ERROR)
        return False


def available() -> bool:
    """True si el runtime de WebView2 está instalado en el sistema."""
    if not _load_clr():
        return False
    try:
        from Microsoft.Web.WebView2.Core import CoreWebView2Environment
        v = CoreWebView2Environment.GetAvailableBrowserVersionString()
        return bool(v)
    except Exception:  # noqa: BLE001
        return False


def init_sync_context() -> None:
    """Instala el puente async→Qt en el hilo actual (el de la GUI). Llamar UNA vez tras crear
    la QApplication y antes de crear vistas WebView2."""
    if not _load_clr():
        return
    try:
        from System.Threading import SynchronizationContext
        from System.Windows.Forms import WindowsFormsSynchronizationContext
        if not isinstance(SynchronizationContext.Current, WindowsFormsSynchronizationContext):
            SynchronizationContext.SetSynchronizationContext(WindowsFormsSynchronizationContext())
    except Exception:  # noqa: BLE001
        pass


def _ui_scheduler():
    from System.Threading.Tasks import TaskScheduler
    return TaskScheduler.FromCurrentSynchronizationContext()


def ensure_environment(callback) -> None:
    """Llama a ``callback(env)`` con el ``CoreWebView2Environment`` compartido cuando esté
    listo (inmediato si ya lo está). Si falla, ``callback(None)``."""
    global _READY, _ENV
    if _READY:
        callback(_ENV)
        return
    _PENDING.append(callback)
    if len(_PENDING) > 1:
        return   # ya hay una creación en marcha
    if not _load_clr():
        _flush(None)
        return
    try:
        from Microsoft.Web.WebView2.Core import CoreWebView2Environment
        from System import Action
        task = CoreWebView2Environment.CreateAsync(None, _user_data_dir(), None)

        def _done(t):
            global _READY, _ENV
            try:
                _ENV = t.Result
                _READY = True
                diag("[wv2] entorno WebView2 creado")
                _flush(_ENV)
            except Exception:  # noqa: BLE001
                diag("[wv2] ERROR creando el entorno:\n" + traceback.format_exc())
                _flush(None)

        task.ContinueWith(Action[type(task)](_done), _ui_scheduler())
    except Exception:  # noqa: BLE001
        diag("[wv2] ERROR lanzando CreateAsync:\n" + traceback.format_exc())
        _flush(None)


def _flush(env) -> None:
    cbs, _PENDING[:] = list(_PENDING), []
    for cb in cbs:
        try:
            cb(env)
        except Exception:  # noqa: BLE001
            pass


def init_error() -> str:
    return _INIT_ERROR


def bootstrapper_path() -> str:
    """Ruta al instalador Evergreen de WebView2 empaquetado (o '' si no está)."""
    p = os.path.join(_dll_dir(), "MicrosoftEdgeWebview2Setup.exe")
    return p if os.path.isfile(p) else ""


def install_runtime() -> bool:
    """Lanza el instalador oficial de Microsoft que descarga e instala el runtime WebView2 si
    falta (silencioso). Devuelve True si se pudo lanzar. Requiere conexión a internet."""
    bp = bootstrapper_path()
    if not bp:
        return False
    try:
        import subprocess
        subprocess.Popen([bp, "/silent", "/install"])
        return True
    except Exception:  # noqa: BLE001
        return False
