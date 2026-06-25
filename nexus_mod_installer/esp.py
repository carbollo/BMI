"""Lectura (solo lectura) de plugins Bethesda .esp/.esm/.esl, sin dependencias.

Para el gestor de plugins: tipo (master/ligero), masters requeridos, autor y nº de
registros — leídos directamente de la cabecera TES4 (rápido, sin recorrer todo el archivo).

Formato (Skyrim LE/SE, Fallout):
  Record header  = 24 B: sig(4) dataSize(uint32) flags(uint32) formID(uint32)
                          timestamp+vcs(uint32) internalVersion(uint16) unknown(uint16)
  Subrecord      = 6 B : sig(4) dataSize(uint16)   (si excede uint16, precedido de 'XXXX'
                          con el tamaño real en uint32)
"""
from __future__ import annotations

import struct
from pathlib import Path

FLAG_ESM = 0x00000001         # plugin maestro (.esm o marcado como master)
FLAG_ESL = 0x00000200         # plugin "ligero" (ESL)

_REC_HDR = struct.Struct("<4sIIIIHH")   # 24 bytes
_SUB_HDR = struct.Struct("<4sH")        # 6 bytes


class PluginError(ValueError):
    pass


def _iter_subrecords(data: bytes):
    i, n, real_size = 0, len(data), None
    while i + 6 <= n:
        sig, size = _SUB_HDR.unpack_from(data, i)
        i += 6
        if sig == b"XXXX":
            real_size = struct.unpack_from("<I", data, i)[0]
            i += size
            continue
        if real_size is not None:
            size, real_size = real_size, None
        yield sig, data[i:i + size]
        i += size


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("cp1252", "replace")


def read_header(path: str) -> dict:
    """Lee la cabecera TES4. Lanza PluginError si no es un plugin válido.

    Devuelve: {file, is_master, is_light, author, masters, num_records}.
    """
    p = Path(path)
    with p.open("rb") as f:
        hdr = f.read(24)
        if len(hdr) < 24:
            raise PluginError("Archivo demasiado pequeño para ser un plugin.")
        sig, data_size, flags, _formid, _ts, _iv, _u = _REC_HDR.unpack(hdr)
        if sig != b"TES4":
            raise PluginError(f"No es un plugin Bethesda (cabecera {sig!r}).")
        body = f.read(data_size)

    author = None
    masters: list[str] = []
    num_records = None
    for s, payload in _iter_subrecords(body):
        if s == b"HEDR" and len(payload) >= 8:
            _ver, num = struct.unpack_from("<fi", payload, 0)
            num_records = num
        elif s == b"MAST":
            masters.append(_cstr(payload))
        elif s == b"CNAM":
            author = _cstr(payload)

    return {
        "file": p.name,
        "is_master": bool(flags & FLAG_ESM),
        "is_light": bool(flags & FLAG_ESL),
        "author": author,
        "masters": masters,
        "num_records": num_records,
    }


def plugin_kind(name: str, header: dict | None) -> str:
    """Etiqueta corta del tipo: 'ESM', 'ESL', 'ESP' (según flags; la extensión como respaldo)."""
    if header:
        if header.get("is_light"):
            return "ESL"
        if header.get("is_master"):
            return "ESM"
    ext = Path(name).suffix.lower()
    if ext == ".esl":
        return "ESL"
    if ext == ".esm":
        return "ESM"
    return "ESP"


def safe_read_header(path: str) -> dict | None:
    """Como read_header pero devuelve None ante cualquier error (uso en la GUI)."""
    try:
        return read_header(path)
    except (OSError, PluginError, struct.error):
        return None
