"""Descarga de archivos con progreso (sin dependencias de GUI)."""
from __future__ import annotations

import time
import warnings
from pathlib import Path
from urllib.parse import urlparse, unquote

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import requests


def filename_from_url(url: str, fallback: str = "download.bin") -> str:
    path = urlparse(url).path
    name = unquote(Path(path).name)
    return name or fallback


def download(
    url: str,
    dest_dir: str,
    file_name: str | None = None,
    progress_cb=None,
    chunk_size: int = 1024 * 256,
) -> Path:
    """Descarga ``url`` a ``dest_dir``.

    progress_cb(downloaded_bytes, total_bytes, speed_bps) se llama periódicamente.
    Devuelve la ruta del archivo descargado.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    name = file_name or filename_from_url(url)
    target = dest / name
    tmp = dest / (name + ".part")

    headers = {"User-Agent": "NexusModInstaller/0.1"}
    with requests.get(url, stream=True, headers=headers, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        start = time.monotonic()
        last_emit = 0.0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if progress_cb and (now - last_emit) > 0.15:
                    elapsed = max(now - start, 1e-6)
                    speed = downloaded / elapsed
                    progress_cb(downloaded, total, speed)
                    last_emit = now
        if progress_cb:
            elapsed = max(time.monotonic() - start, 1e-6)
            progress_cb(downloaded, total or downloaded, downloaded / elapsed)

    # Renombrar .part -> definitivo
    if target.exists():
        target.unlink()
    tmp.rename(target)
    return target
