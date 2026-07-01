"""Detección de la plataforma del juego (Steam/GOG) y de variantes de mod incorrectas.

Muchos mods con plugins de SKSE publican archivos separados por plataforma: la versión
normal (Steam), una versión **GOG** y una versión **VR** (Skyrim VR). Un DLL de SKSE de
GOG o de VR NO funciona en un Skyrim Special Edition de Steam: SKSE lo detecta incompatible
y lo desactiva. Aquí detectamos esos casos por el nombre del mod/archivo para avisar y no
descargar la variante equivocada.
"""
from __future__ import annotations

import re


def game_platform(config) -> str:
    """Plataforma del juego según su ruta: 'steam', 'gog' o 'unknown'."""
    p = (getattr(config, "game_data_path", "") or "").lower().replace("/", "\\")
    if "steamapps" in p or "steamlibrary" in p:
        return "steam"
    if "\\gog galaxy\\" in p or "\\gog\\" in p or "gog.com" in p or "goggames" in p:
        return "gog"
    return "unknown"


# 'VR' como palabra suelta (SkyUI VR, ... VR Edition) o pegado en nombres conocidos.
_VR = re.compile(r"(?:^|[\s\-_(\[])vr(?:$|[\s\-_)\]])|sksevr|skyrimvr|fallout4vr|fo4vr|skyrimvrtools",
                 re.IGNORECASE)
# 'GOG' como palabra suelta (PapyrusUtil GOG, (GOG Edition), GOG-13048...).
_GOG = re.compile(r"(?:^|[\s\-_(\[])gog(?:$|[\s\-_)\]])", re.IGNORECASE)


def wrong_variant_reason(name: str, platform: str) -> str | None:
    """Devuelve 'VR' o 'GOG' si ``name`` (nombre de mod/archivo) parece una variante que NO
    corresponde a la plataforma/juego, o None si parece correcta.

    BMI solo gestiona juegos NO-VR (Skyrim SE/AE, Fallout 4…), así que cualquier variante VR
    es incorrecta. Una variante GOG es incorrecta si el juego está instalado en Steam.
    """
    if not name:
        return None
    if _VR.search(name):
        return "VR"
    if platform == "steam" and _GOG.search(name):
        return "GOG"
    return None


def wrong_variant_mods(config, store) -> list[tuple]:
    """Lista los mods instalados cuya variante no corresponde a la plataforma del juego.
    Devuelve [(mod, motivo)]."""
    plat = game_platform(config)
    out = []
    for m in store.all():
        reason = wrong_variant_reason(getattr(m, "name", "") or "", plat)
        if reason:
            out.append((m, reason))
    return out
