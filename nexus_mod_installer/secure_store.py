"""Cifrado en reposo de datos sensibles (tokens OAuth) con DPAPI de Windows.

Usa ``CryptProtectData``/``CryptUnprotectData`` (crypt32.dll) con el ámbito por USUARIO:
el blob resultante solo lo puede descifrar la MISMA cuenta de Windows en el MISMO equipo, sin
que la app tenga que guardar ninguna clave. Es el mecanismo estándar de Windows para secretos
locales (lo usan Chrome, etc.). No hay dependencias externas.

Si DPAPI no está disponible (p. ej. ejecutando en desarrollo fuera de Windows), ``protect``
lanza ``SecureStoreUnavailable`` y el llamador debe NO persistir el secreto (mantenerlo solo en
memoria) en lugar de escribirlo en claro.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class SecureStoreUnavailable(RuntimeError):
    """DPAPI no disponible (no-Windows): no se debe persistir el secreto en claro."""


_ENTROPY = b"BMI-oauth-token-v1"   # entropía secundaria (no secreta): liga el blob a este uso


if sys.platform == "win32":
    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _blob(data: bytes) -> _DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _bytes(blob: _DATA_BLOB) -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)

    def _protect(data: bytes) -> bytes:
        out = _DATA_BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(_blob(data)), "BMI", ctypes.byref(_blob(_ENTROPY)),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise SecureStoreUnavailable("CryptProtectData falló")
        try:
            return _bytes(out)
        finally:
            _kernel32.LocalFree(out.pbData)

    def _unprotect(blob_bytes: bytes) -> bytes:
        out = _DATA_BLOB()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(_blob(blob_bytes)), None, ctypes.byref(_blob(_ENTROPY)),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise SecureStoreUnavailable("CryptUnprotectData falló")
        try:
            return _bytes(out)
        finally:
            _kernel32.LocalFree(out.pbData)
else:  # pragma: no cover - solo dev fuera de Windows
    def _protect(data: bytes) -> bytes:
        raise SecureStoreUnavailable("DPAPI solo disponible en Windows")

    def _unprotect(blob_bytes: bytes) -> bytes:
        raise SecureStoreUnavailable("DPAPI solo disponible en Windows")


def protect(data: bytes) -> bytes:
    """Cifra ``data`` (bytes) ligado a la cuenta de Windows actual. Lanza
    ``SecureStoreUnavailable`` si DPAPI no está disponible."""
    return _protect(data)


def unprotect(blob_bytes: bytes) -> bytes:
    """Descifra un blob de ``protect``. Lanza si no es descifrable por esta cuenta/equipo."""
    return _unprotect(blob_bytes)


def available() -> bool:
    return sys.platform == "win32"
