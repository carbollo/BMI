"""Modo VFS (experimental): sistema de archivos virtual estilo MO2 vía USVFS.

USVFS (de Mod Organizer 2, GPL-3.0) virtualiza los archivos de los mods dentro del proceso
del juego mediante hooking de las APIs de Windows: el juego "ve" un Data combinado SIN que
se copie nada a la carpeta Data real (que queda limpia). Aquí se llama a su API C por ctypes.

Flujo (como MO2):
  init_logging -> create_vfs(instancia) -> por cada mod en orden de prioridad:
  link_directory(carpeta_del_mod, Data_real)  (overlay; el último gana) -> launch(juego).

Requiere los binarios de USVFS (usvfs_x64.dll + usvfs_proxy_x64.exe + dependencias),
extraídos de un release de MO2. Es OPCIONAL: si no están, BMI sigue con el despliegue normal.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

# Flags de enlace (de usvfs.h)
LINKFLAG_FAILIFEXISTS = 0x00000001
LINKFLAG_MONITORCHANGES = 0x00000002
LINKFLAG_CREATETARGET = 0x00000004
LINKFLAG_RECURSIVE = 0x00000008
LINKFLAG_FAILIFSKIPPED = 0x00000010

# Overlay de la carpeta de un mod sobre Data: crear destino + recursivo.
_DIR_FLAGS = LINKFLAG_CREATETARGET | LINKFLAG_RECURSIVE


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


class VfsError(RuntimeError):
    pass


def find_usvfs_dir(extra: str | None = None) -> Path | None:
    """Localiza la carpeta con usvfs_x64.dll (junto al paquete, en data, o ruta dada)."""
    candidates = []
    if extra:
        candidates.append(Path(extra))
    here = Path(__file__).resolve().parent
    candidates += [here / "usvfs", here.parent / "usvfs"]
    try:
        from .config import app_data_dir
        candidates.append(app_data_dir() / "usvfs")
    except Exception:
        pass
    for c in candidates:
        if (c / "usvfs_x64.dll").is_file():
            return c
    return None


class Vfs:
    """Envoltorio ctypes de la API C de USVFS (x64)."""

    def __init__(self, usvfs_dir: str | Path):
        d = Path(usvfs_dir)
        dll = d / "usvfs_x64.dll"
        if not dll.is_file():
            raise VfsError(f"No se encontró usvfs_x64.dll en {d}")
        # El proxy debe estar junto al dll (usvfs lo lanza para hooks cross-arch).
        try:
            import os
            os.add_dll_directory(str(d))   # para resolver dependencias del dll
        except (OSError, AttributeError):
            pass
        try:
            self.lib = ctypes.WinDLL(str(dll))
        except OSError as e:
            raise VfsError(f"No se pudo cargar usvfs_x64.dll (¿faltan dependencias?): {e}")
        self._params = None
        self._bind()

    def _fn(self, name, restype, argtypes):
        f = getattr(self.lib, name, None)
        if f is not None:
            f.restype = restype
            f.argtypes = argtypes
        return f

    def _bind(self) -> None:
        P = ctypes.c_void_p
        W = wintypes.LPCWSTR
        self._InitLogging = self._fn("usvfsInitLogging", None, [wintypes.BOOL])
        self._CreateParameters = self._fn("usvfsCreateParameters", P, [])
        self._SetInstanceName = self._fn("usvfsSetInstanceName", None, [P, wintypes.LPCSTR])
        self._SetDebugMode = self._fn("usvfsSetDebugMode", None, [P, wintypes.BOOL])
        self._SetLogLevel = self._fn("usvfsSetLogLevel", None, [P, ctypes.c_int])
        self._SetCrashDumpType = self._fn("usvfsSetCrashDumpType", None, [P, ctypes.c_int])
        self._SetProcessDelay = self._fn("usvfsSetProcessDelay", None, [P, ctypes.c_int])
        self._FreeParameters = self._fn("usvfsFreeParameters", None, [P])
        self._CreateVFS = self._fn("usvfsCreateVFS", wintypes.BOOL, [P])
        self._DisconnectVFS = self._fn("usvfsDisconnectVFS", None, [])
        self._ClearMappings = self._fn("usvfsClearVirtualMappings", None, [])
        self._LinkDir = self._fn("usvfsVirtualLinkDirectoryStatic", wintypes.BOOL,
                                 [W, W, ctypes.c_uint])
        self._LinkFile = self._fn("usvfsVirtualLinkFile", wintypes.BOOL, [W, W, ctypes.c_uint])
        self._CreateProcessHooked = self._fn(
            "usvfsCreateProcessHooked", wintypes.BOOL,
            [W, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
             wintypes.DWORD, ctypes.c_void_p, W,
             ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)])
        if not (self._CreateParameters and self._CreateVFS and self._LinkDir
                and self._CreateProcessHooked):
            raise VfsError("La DLL de usvfs no exporta la API esperada (versión incompatible).")

    # ------------------------------------------------------------------
    def create(self, instance: str = "bmi_instance", debug: bool = False) -> None:
        if self._InitLogging:
            self._InitLogging(False)
        p = self._CreateParameters()
        if not p:
            raise VfsError("usvfsCreateParameters devolvió NULL.")
        if self._SetInstanceName:
            self._SetInstanceName(p, instance.encode("utf-8"))
        if self._SetDebugMode:
            self._SetDebugMode(p, bool(debug))
        if self._SetLogLevel:
            self._SetLogLevel(p, 2)          # Warning
        if self._SetCrashDumpType:
            self._SetCrashDumpType(p, 0)     # None
        if self._SetProcessDelay:
            self._SetProcessDelay(p, 0)
        self._params = p
        if not self._CreateVFS(p):
            raise VfsError("usvfsCreateVFS falló.")

    def link_directory(self, source: str | Path, dest: str | Path) -> bool:
        """Superpone (overlay) la carpeta de un mod sobre la carpeta Data virtual."""
        return bool(self._LinkDir(str(source), str(dest), _DIR_FLAGS))

    def clear_mappings(self) -> None:
        if self._ClearMappings:
            self._ClearMappings()

    def launch(self, exe: str | Path, cwd: str | Path | None = None) -> _PROCESS_INFORMATION:
        """Lanza el juego enganchado al VFS. Devuelve PROCESS_INFORMATION (hProcess)."""
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(_STARTUPINFOW)
        pi = _PROCESS_INFORMATION()
        ok = self._CreateProcessHooked(
            str(exe), None, None, None, False, 0, None,
            str(cwd) if cwd else None, ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            raise VfsError(f"usvfsCreateProcessHooked falló (err {ctypes.get_last_error()}).")
        return pi

    def disconnect(self) -> None:
        try:
            if self._DisconnectVFS:
                self._DisconnectVFS()
        finally:
            if self._params and self._FreeParameters:
                self._FreeParameters(self._params)
            self._params = None
