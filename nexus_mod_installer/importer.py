"""Detección e importación de mods ya presentes en la carpeta de mods (estilo Mod Organizer 2).

Cuando el usuario apunta la «Carpeta de mods» a un directorio con mods ya organizados —cada
mod en su propia subcarpeta, con el contenido a nivel de Data (meshes/, textures/, *.esp…),
exactamente como los guarda MO2— BMI escanea esas subcarpetas y las añade a la lista SIN
descargar nada:

  - lee el ``meta.ini`` de MO2 (coge ``modid`` -> id real de Nexus, y ``version``) si existe;
  - detecta los plugins (.esp/.esm/.esl);
  - respeta la estructura (usa deploy.find_data_root por si el mod trae una subcarpeta
    envoltorio o una carpeta Data interna).

No despliega nada: los mods aparecen activados y se virtualizan solos en Modo VFS (o se
despliegan a mano desde la lista en modo normal). Re-escanear no duplica.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import deploy
from .models import InstalledMod

# Subcarpetas de un layout tipo MO2 que NO son mods.
_SKIP_NAMES = {
    "overwrite", "profiles", "categories", "downloads", "logs", "webcache",
    "crashdumps", "__installer", "edit scripts",
}


def _external_id(rel_name: str) -> int:
    """Id sintético ESTABLE y NEGATIVO para un mod sin modid de Nexus. Los ids de Nexus son
    positivos, así que un negativo nunca colisiona; deriva del nombre de la carpeta, de modo
    que re-escanear la misma carpeta da el mismo id (no duplica) y lo marca como mod externo."""
    h = int(hashlib.sha1(rel_name.strip().lower().encode("utf-8", "ignore")).hexdigest()[:12], 16)
    return -(h % 2_000_000_000 + 1)


def read_mo2_meta(mod_folder: str | Path) -> dict:
    """Lee ``modid`` y ``version`` de la sección [General] del ``meta.ini`` de MO2.
    Devuelve {} si no hay archivo o no trae esos datos."""
    p = Path(mod_folder) / "meta.ini"
    if not p.is_file():
        return {}
    out: dict = {}
    section = ""
    try:
        lines = p.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    except OSError:
        return {}
    for line in lines:
        s = line.strip()
        low = s.lower()
        if low.startswith("[") and low.endswith("]"):
            section = low.strip("[]").strip()
            continue
        if section != "general" or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip().lower()
        val = val.strip().strip('"')
        if key == "modid":
            try:
                mid = int(val)
            except ValueError:
                continue
            if mid > 0:
                out["modid"] = mid
        elif key == "version" and val:
            out["version"] = val
    return out


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _is_bmi_managed(sub: Path) -> bool:
    """Las carpetas que gestiona el propio BMI tienen _extracted/_data dentro; ya están en el
    store por su id real, así que no se re-importan (evita duplicados con id sintético)."""
    return (sub / "_extracted").exists() or (sub / "_data").exists()


def scan_mods_folder(mods_dir, game_domain: str, known_ids, known_dirs) -> list[InstalledMod]:
    """Escanea las subcarpetas de ``mods_dir`` y devuelve los mods NUEVOS detectados (los que
    no están ya en el store, ni por id ni por carpeta desplegada). No despliega nada."""
    base = Path(mods_dir) if mods_dir else None
    if not base or not base.is_dir():
        return []
    known_dirs_set = {str(Path(d).resolve()).lower() for d in known_dirs if d}
    used_ids = set(known_ids)
    found: list[InstalledMod] = []
    try:
        subs = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
    except OSError:
        return []
    for sub in subs:
        try:
            name = sub.name
            if name.startswith(".") or name.lower() in _SKIP_NAMES:
                continue
            if _is_bmi_managed(sub):
                # Carpeta con estructura de BMI (_extracted/_data): sáltala SOLO si de verdad
                # sigue en la lista (algún install_dir del almacén vive dentro de ella). Si se
                # quitó de la lista y el usuario la vuelve a meter en la carpeta, debe poder
                # importarse como un mod más (antes se saltaba siempre y nunca reaparecía).
                root_l = str(sub.resolve()).lower()
                if any(k == root_l or k.startswith(root_l + "\\") or k.startswith(root_l + "/")
                       for k in known_dirs_set):
                    continue
            if not deploy.looks_like_mod(sub):
                continue
            data_root = deploy.find_data_root(str(sub))
            if str(Path(data_root).resolve()).lower() in known_dirs_set:
                continue
            meta = read_mo2_meta(sub)
            mod_id = int(meta.get("modid", 0)) or _external_id(name)
            if mod_id in used_ids:
                continue
            used_ids.add(mod_id)
            try:
                mtime = sub.stat().st_mtime
            except OSError:
                mtime = 0.0
            found.append(InstalledMod(
                mod_id=mod_id,
                name=name,
                version=meta.get("version", ""),
                game_domain=game_domain,
                install_dir=str(data_root),
                plugins=deploy.list_plugins(data_root),
                enabled=True,
                installed_at=mtime,
                size_bytes=_dir_size(sub),
                category="Importados",
                imported=True,
            ))
        except Exception:  # noqa: BLE001
            # Una carpeta problemática (enlace roto, sin permisos, placeholder de la nube…)
            # NO debe anular el escaneo entero: antes tumbaba todas las altas en silencio.
            continue
    return found
