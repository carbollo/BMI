"""Extracción de archivos comprimidos (.zip, .7z, .rar)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Evita que Windows abra una ventana de consola al invocar 7-Zip desde una app sin consola.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class ArchiveError(RuntimeError):
    pass


def find_7zip() -> str | None:
    """Localiza un desempaquetador de la familia 7-Zip (7-Zip, NanaZip, 7za…) que use la
    CLI estándar ``x archivo -odest -y`` y cubra zip/7z/rar."""
    # Por nombre (incluye alias de la Microsoft Store, p.ej. 7z.EXE o NanaZipC.exe).
    for name in ("7z", "7za", "7zr", "NanaZipC", "nanazip", "NanaZip"):
        found = shutil.which(name)
        if found:
            return found
    # Rutas de instalación habituales.
    for candidate in (
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
        r"C:\Program Files\NanaZip\NanaZipC.exe",
        r"C:\Program Files\NanaZip\7z.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _extract_with_7zip(seven_zip: str, archive: Path, dest: Path) -> None:
    # x = extraer con rutas, -y = sí a todo, -o = directorio de salida
    result = subprocess.run(
        [seven_zip, "x", str(archive), f"-o{dest}", "-y"],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,   # sin ventana de consola
    )
    if result.returncode != 0:
        raise ArchiveError(
            f"7-Zip falló ({result.returncode}): {result.stderr or result.stdout}"
        )


def _extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def _extract_7z_py(archive: Path, dest: Path) -> None:
    try:
        import py7zr
    except ImportError as e:
        raise ArchiveError(
            "Para extraer .7z instala 'py7zr' (pip install py7zr) o 7-Zip."
        ) from e
    with py7zr.SevenZipFile(archive, mode="r") as z:
        z.extractall(path=dest)


def _extract_rar_py(archive: Path, dest: Path) -> None:
    try:
        import rarfile
    except ImportError as e:
        raise ArchiveError(
            "Para extraer .rar instala 7-Zip, o 'pip install rarfile' + unrar."
        ) from e
    with rarfile.RarFile(archive) as rf:
        rf.extractall(dest)


def extract(archive_path: str | os.PathLike, dest_dir: str | os.PathLike) -> Path:
    """Extrae ``archive_path`` dentro de ``dest_dir``. Devuelve la ruta de destino.

    Estrategia: si hay 7-Zip instalado, se usa para todo (lo más robusto).
    En su defecto, se usa la librería de Python adecuada por extensión.
    """
    archive = Path(archive_path)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    if not archive.is_file():
        raise ArchiveError(f"No existe el archivo: {archive}")

    ext = archive.suffix.lower()
    seven_zip = find_7zip()

    # 7-Zip cubre zip/7z/rar y formatos raros: úsalo si está.
    if seven_zip and ext in (".7z", ".rar", ".zip"):
        _extract_with_7zip(seven_zip, archive, dest)
        return dest

    if ext == ".zip":
        _extract_zip(archive, dest)
    elif ext == ".7z":
        _extract_7z_py(archive, dest)
    elif ext == ".rar":
        if seven_zip:
            _extract_with_7zip(seven_zip, archive, dest)
        else:
            _extract_rar_py(archive, dest)
    else:
        # Intento genérico con 7-Zip si está, si no error.
        if seven_zip:
            _extract_with_7zip(seven_zip, archive, dest)
        else:
            raise ArchiveError(f"Formato no soportado: {ext}. Instala 7-Zip.")
    return dest
