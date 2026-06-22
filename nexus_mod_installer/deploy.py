"""Despliegue de mods a la carpeta Data de Skyrim y gestión de plugins.txt.

Modelo "gestionado" (como Vortex/MO2):
  - Los mods se extraen en una carpeta propia (mods_dir/<mod>).
  - El "despliegue" enlaza/copia sus archivos dentro de la carpeta Data del juego.
  - Se guarda un manifiesto de archivos desplegados para poder desinstalar limpio.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

# Carpetas que, si aparecen, indican que estamos en la raíz "Data" del mod.
_DATA_MARKERS = {
    "meshes", "textures", "scripts", "sound", "music", "interface",
    "seq", "scripts", "source", "skse", "shadersfx", "lodsettings",
    "grass", "dialogueviews", "facegen", "actors", "calientetools",
    "strings", "video", "materials", "dyndolod", "netscriptframework",
}
# Extensiones de plugin que cargan en el juego.
_PLUGIN_EXTS = {".esp", ".esm", ".esl"}
# Extensiones que típicamente viven directamente en Data.
_LOOSE_DATA_EXTS = {".bsa", ".esp", ".esm", ".esl", ".ini", ".bik", ".seq"}

# ---------------------------------------------------------------------------
# Archivos de "carpeta raíz" del juego (junto al .exe), NO de Data.
# Casos típicos: el preloader de SSE Engine Fixes (d3dx9_42.dll + TBB malloc),
# los wrappers de ENB/ReShade (d3d11.dll, dxgi.dll...) y el runtime del script
# extender (skse64_loader.exe, skse64_1_6_640.dll...). También una carpeta
# 'Root/' (convención de MO2/Vortex) cuyo contenido va tal cual a la raíz.
_ROOT_FILE_NAMES = {
    # SSE Engine Fixes parte 2 (preloader + TBB malloc replacement)
    "d3dx9_42.dll", "tbb.dll", "tbbmalloc.dll", "tbbmalloc_proxy.dll",
    # ENB / ReShade / wrappers cargados junto al ejecutable
    "d3d11.dll", "d3d9.dll", "d3d10.dll", "d3d12.dll", "dxgi.dll",
    "d3dcompiler_46e.dll", "d3dcompiler_47e.dll", "opengl32.dll",
    "dinput8.dll", "winmm.dll", "version.dll", "xinput1_3.dll",
    "binkw64.dll", "binkw64_.dll",
    # Configuración de ENB / ReShade (vive en la raíz)
    "enblocal.ini", "enbseries.ini", "enbadaptation.ini",
    "reshade.ini", "reshadepreset.ini",
}
# Carpetas que van enteras a la raíz del juego. 'root' se "aplana"
# (Root/foo.dll -> <raíz>/foo.dll); el resto conserva su nombre.
_ROOT_DIR_NAMES = {"root", "enbseries", "reshade-shaders", "reshade-presets", "reshade-addons"}
# Patrones de nombre que también son de raíz (runtime del script extender).
_ROOT_NAME_PATTERNS = (
    re.compile(r"^(skse64|skse|f4se|nvse|fose|obse|sfse|mwse)_.*\.(dll|exe)$", re.IGNORECASE),
)


def _is_root_file_name(name: str) -> bool:
    if name.lower() in _ROOT_FILE_NAMES:
        return True
    return any(p.match(name) for p in _ROOT_NAME_PATTERNS)


def find_data_root(extracted_dir: str | os.PathLike) -> Path:
    """Determina qué subcarpeta del archivo extraído corresponde a 'Data'.

    Muchos mods comprimen ya con la estructura correcta (meshes/, textures/, x.esp).
    Otros lo meten dentro de una subcarpeta (p.ej. '00 Core/'). Aquí buscamos la
    primera carpeta que contenga marcadores de Data o plugins.
    """
    root = Path(extracted_dir)

    def looks_like_data(d: Path) -> bool:
        try:
            entries = list(d.iterdir())
        except OSError:
            return False
        for e in entries:
            if e.is_dir() and e.name.lower() in _DATA_MARKERS:
                return True
            if e.is_file() and e.suffix.lower() in _PLUGIN_EXTS:
                return True
            if e.is_file() and e.suffix.lower() == ".bsa":
                return True
        return False

    if looks_like_data(root):
        return root

    # Explora un par de niveles buscando la raíz de datos.
    candidates = [p for p in root.rglob("*") if p.is_dir()]
    # Ordena por profundidad (más superficial primero).
    candidates.sort(key=lambda p: len(p.relative_to(root).parts))
    for c in candidates[:50]:
        if looks_like_data(c):
            return c

    # Si no se reconoce, asumimos la raíz tal cual (mod de solo texturas sueltas, etc.).
    return root


def list_plugins(data_root: str | os.PathLike) -> list[str]:
    """Lista los nombres de plugin (.esp/.esm/.esl) en la raíz de datos."""
    root = Path(data_root)
    out = []
    for p in root.iterdir() if root.is_dir() else []:
        if p.is_file() and p.suffix.lower() in _PLUGIN_EXTS:
            out.append(p.name)
    return sorted(out)


def _link_or_copy(src: Path, dst: Path, method: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            pass
    if method == "hardlink":
        try:
            os.link(src, dst)   # hardlink (mismo volumen)
            return
        except OSError:
            pass  # cae a copia (distinto volumen o sin permiso)
    shutil.copy2(src, dst)


def deploy(
    data_root: str | os.PathLike,
    game_data_path: str | os.PathLike,
    method: str = "hardlink",
    exclude=None,
) -> list[str]:
    """Despliega todos los archivos de ``data_root`` dentro de ``game_data_path``.

    ``exclude`` es una colección de rutas de origen a NO desplegar (p.ej. los
    archivos de carpeta raíz, que van junto al .exe en vez de a Data).
    Devuelve la lista de rutas relativas desplegadas (para el manifiesto).
    """
    src_root = Path(data_root)
    dst_root = Path(game_data_path)
    if not dst_root.is_dir():
        raise FileNotFoundError(f"La carpeta Data del juego no existe: {dst_root}")

    exclude_set = set()
    for p in (exclude or []):
        try:
            exclude_set.add(Path(p).resolve())
        except OSError:
            pass

    deployed: list[str] = []
    for src in src_root.rglob("*"):
        if src.is_dir():
            continue
        if exclude_set:
            try:
                if src.resolve() in exclude_set:
                    continue
            except OSError:
                pass
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        _link_or_copy(src, dst, method)
        deployed.append(str(rel).replace("\\", "/"))
    return deployed


def undeploy(deployed_files: list[str], game_data_path: str | os.PathLike) -> int:
    """Elimina de Data los archivos previamente desplegados. Devuelve cuántos borró."""
    dst_root = Path(game_data_path)
    removed = 0
    for rel in deployed_files:
        target = dst_root / rel
        try:
            if target.is_file():
                target.unlink()
                removed += 1
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Carpeta raíz del juego (junto al .exe): Engine Fixes parte 2, ENB, SKSE…
# ---------------------------------------------------------------------------
def game_root(game_data_path: str | os.PathLike) -> Path:
    """Carpeta raíz del juego (la PADRE de Data / Data Files), donde está el .exe."""
    return Path(game_data_path).parent


def find_root_files(extracted_dir: str | os.PathLike, extra_names=None):
    """Localiza archivos del mod que van en la carpeta RAÍZ del juego (junto al
    .exe) en vez de en Data. Devuelve una lista de ``(ruta_origen, ruta_relativa)``.

    Detecta, en el nivel superior del archivo extraído: archivos sueltos conocidos
    (d3dx9_42.dll, tbb*, wrappers ENB/ReShade, runtime skse64_*), una carpeta
    ``Root/`` (su contenido va tal cual a la raíz) y carpetas tipo ``enbseries/``.
    ``extra_names`` añade nombres exactos extra (p.ej. los loaders del juego activo).
    """
    base = Path(extracted_dir)
    extra = {n.lower() for n in (extra_names or [])}
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def add_tree(folder: Path, prefix: Path | None) -> None:
        for f in folder.rglob("*"):
            if not f.is_file():
                continue
            rel = (prefix / f.relative_to(folder)) if prefix else f.relative_to(folder)
            key = rel.as_posix().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((f, rel.as_posix()))

    try:
        entries = list(base.iterdir())
    except OSError:
        return out

    for e in entries:
        nl = e.name.lower()
        if e.is_dir() and nl == "root":
            add_tree(e, None)                       # Root/foo -> <raíz>/foo
        elif e.is_dir() and nl in _ROOT_DIR_NAMES:
            add_tree(e, Path(e.name))               # enbseries/... -> <raíz>/enbseries/...
        elif e.is_file() and (_is_root_file_name(e.name) or nl in extra):
            if nl not in seen:
                seen.add(nl)
                out.append((e, e.name))
    return out


def deploy_root(root_files, game_root_path: str | os.PathLike, method: str = "hardlink") -> list[str]:
    """Despliega archivos de carpeta raíz. ``root_files``: lista ``(origen, relativa)``.
    Devuelve las rutas relativas desplegadas (para el manifiesto)."""
    dst_root = Path(game_root_path)
    if not dst_root.is_dir():
        raise FileNotFoundError(f"La carpeta del juego no existe: {dst_root}")
    deployed: list[str] = []
    for src, rel in root_files:
        _link_or_copy(Path(src), dst_root / rel, method)
        deployed.append(str(rel).replace("\\", "/"))
    return deployed


def undeploy_root(deployed_root_files: list[str], game_root_path: str | os.PathLike) -> int:
    """Elimina de la raíz del juego los archivos desplegados y limpia las carpetas
    propias (enbseries, reshade-*) si quedan vacías. Devuelve cuántos borró."""
    dst_root = Path(game_root_path)
    removed = 0
    touched: set[Path] = set()
    for rel in deployed_root_files:
        target = dst_root / rel
        try:
            if target.is_file():
                target.unlink()
                removed += 1
            touched.add(target.parent)
        except OSError:
            pass
    # Borra de dentro hacia fuera las subcarpetas vacías que hubiéramos creado,
    # nunca la propia raíz del juego.
    for d in sorted(touched, key=lambda p: len(p.parts), reverse=True):
        try:
            if d != dst_root and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Gestión de plugins.txt (qué plugins se cargan)
# ---------------------------------------------------------------------------
def enable_plugins(plugins_txt_path: str | os.PathLike, plugin_names: list[str]) -> None:
    """Activa plugins en plugins.txt (formato SSE: '*Nombre.esp' = activado).

    No duplica entradas ya presentes.
    """
    if not plugin_names:
        return
    path = Path(plugins_txt_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    existing_names: set[str] = set()
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        for line in existing_lines:
            name = line.strip().lstrip("*").strip()
            if name:
                existing_names.add(name.lower())

    added = []
    for name in plugin_names:
        if name.lower() not in existing_names:
            added.append(f"*{name}")
            existing_names.add(name.lower())

    if added:
        new_content = "\n".join(existing_lines + added).strip() + "\n"
        path.write_text(new_content, encoding="utf-8")


def disable_plugins(plugins_txt_path: str | os.PathLike, plugin_names: list[str]) -> None:
    """Quita plugins de plugins.txt."""
    path = Path(plugins_txt_path)
    if not path.is_file():
        return
    drop = {n.lower() for n in plugin_names}
    kept = []
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        name = line.strip().lstrip("*").strip()
        if name.lower() in drop:
            continue
        kept.append(line)
    path.write_text("\n".join(kept).strip() + "\n", encoding="utf-8")
