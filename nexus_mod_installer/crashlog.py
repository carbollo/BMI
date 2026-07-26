"""Analizador de crash logs (Crash Logger SSE / .NET Script Framework).

Localiza el crash log más reciente, extrae la pila de llamadas (los DLL implicados) y los
cruza con los mods instalados por BMI para señalar el CULPABLE PROBABLE de un CTD. Todo es
LOCAL: no usa la API de Nexus ni internet. Los resultados son PISTAS probables, no certezas.

Ventaja de hacerlo dentro de BMI: conoce el mapa DLL→mod de tu instalación, cosa que las webs
de análisis (Phostwood, Crash Decoder) no tienen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# DLL del motor/sistema/overlays que aparecen en casi todos los crashes y NO son sospechosas
# por sí solas (no queremos culpar a Windows o a la GPU).
_ENGINE_DLLS = {
    "skyrimse.exe", "skyrimvr.exe", "fallout4.exe", "fallout4vr.exe", "falloutnv.exe",
    "fallout3.exe", "oblivion.exe", "tesv.exe", "starfield.exe", "morrowind.exe",
    "kernel32.dll", "kernelbase.dll", "ntdll.dll", "user32.dll", "win32u.dll",
    "gdi32.dll", "gdi32full.dll", "d3d11.dll", "d3d9.dll", "dxgi.dll", "d3dcompiler_46e.dll",
    "d3dx9_42.dll", "msvcr120.dll", "msvcr110.dll", "msvcp140.dll", "msvcp110.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "ucrtbase.dll", "combase.dll", "ole32.dll",
    "steam_api64.dll", "steamclient64.dll", "binkw64.dll", "flexrelease_x64.dll",
    "nvwgf2umx.dll", "atidxx64.dll", "amdxx64.dll", "igd10iumd64.dll", "opengl32.dll",
    "usvfs_x64.dll", "hook.dll", "bink2w64.dll", "concrt140.dll", "msvcp140_1.dll",
    "wow64.dll", "wow64cpu.dll", "wow64win.dll", "advapi32.dll", "rpcrt4.dll",
}

_DLL_RE = re.compile(r"\b([A-Za-z0-9_\-\.]+\.(?:dll|exe))\b", re.IGNORECASE)
# Línea de plugin en la sección PLUGINS: '  [00] Skyrim.esm' o '  [FE:001] Algo.esp'
_PLUGIN_LINE = re.compile(r"^\s*\[[0-9A-Fa-f]{2,3}(?::[0-9A-Fa-f]{3})?\]\s*(.+?\.es[pml])\s*$",
                          re.IGNORECASE)


@dataclass
class CrashReport:
    path: str
    exception: str = ""
    suspect_dlls: list[str] = field(default_factory=list)     # DLL de la pila que no son del motor
    culprit_mods: list[tuple[str, str]] = field(default_factory=list)  # (dll, nombre_del_mod)
    plugins: list[str] = field(default_factory=list)          # orden de carga del log


def _candidate_dirs(config) -> list[Path]:
    """Carpetas donde los crash loggers dejan sus .txt."""
    dirs: list[Path] = []
    g = config.game()
    home = Path.home()
    for docs in (home / "Documents", home / "OneDrive" / "Documents"):
        if g.appdata_folder:
            dirs.append(docs / "My Games" / g.appdata_folder / "SKSE" / "Crashlogs")
    if getattr(config, "game_data_path", ""):
        dirs.append(Path(config.game_data_path) / "NetScriptFramework" / "Crash")
    return dirs


def find_latest_crashlog(config) -> Path | None:
    """Devuelve el crash log .txt más reciente, o None si no hay ninguno."""
    newest, newest_t = None, 0.0
    for d in _candidate_dirs(config):
        try:
            if not d.is_dir():
                continue
            for f in d.glob("*.txt"):
                t = f.stat().st_mtime
                if t > newest_t:
                    newest, newest_t = f, t
        except OSError:
            continue
    return newest


def parse_and_analyze(config, store, text: str, log_path: str) -> CrashReport:
    """Parsea el crash log y lo cruza con los mods instalados."""
    lines = text.splitlines()
    rep = CrashReport(path=log_path)

    # 1) Línea de excepción (suele ser de las primeras).
    for ln in lines[:60]:
        low = ln.lower()
        if "unhandled" in low or "exception" in low or "access violation" in low:
            rep.exception = ln.strip()[:400]
            break

    # 2) DLL de la sección de pila de llamadas (PROBABLE CALLSTACK / STACK / relevant objects).
    stack_dlls: list[str] = []
    seen = set()
    in_stack = False
    for ln in lines:
        u = ln.strip().upper()
        if ("CALL STACK" in u or u.startswith("STACK") or "CALLSTACK" in u
                or u.startswith("POSSIBLE RELEVANT")):
            in_stack = True
            continue
        if in_stack and (u.startswith("MODULES") or u.startswith("REGISTERS")
                         or u.startswith("PLUGINS") or u.startswith("F ") or not u):
            if u.startswith("MODULES") or u.startswith("REGISTERS") or u.startswith("PLUGINS"):
                in_stack = False
        if in_stack:
            for m in _DLL_RE.findall(ln):
                low = m.lower()
                if low in _ENGINE_DLLS or low in seen:
                    continue
                seen.add(low)
                stack_dlls.append(m)
    rep.suspect_dlls = stack_dlls[:15]

    # 3) Orden de plugins del log.
    for ln in lines:
        mo = _PLUGIN_LINE.match(ln)
        if mo:
            rep.plugins.append(mo.group(1).strip())

    # 4) Cruzar los DLL sospechosos con las carpetas de los mods instalados.
    dll_to_mod = _map_dlls_to_mods(store, {d.lower() for d in stack_dlls})
    rep.culprit_mods = [(d, dll_to_mod[d.lower()]) for d in stack_dlls if d.lower() in dll_to_mod]
    return rep


def _map_dlls_to_mods(store, dll_names: set[str]) -> dict[str, str]:
    """Asocia cada DLL a la carpeta del mod que la contiene (si BMI la gestiona)."""
    out: dict[str, str] = {}
    if not dll_names:
        return out
    for m in store.all():
        if not str(getattr(m, "install_dir", "")).strip():
            continue                         # install_dir vacío: Path('') = cwd → NO escanear
        try:
            base = Path(m.install_dir)
            if not base.is_dir():
                continue
            for f in base.rglob("*.dll"):
                low = f.name.lower()
                if low in dll_names and low not in out:
                    out[low] = m.name
            if len(out) == len(dll_names):
                break
        except OSError:
            continue
    return out
