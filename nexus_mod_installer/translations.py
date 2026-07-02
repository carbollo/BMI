"""Detección de traducciones de un mod al idioma elegido.

En Nexus, las traducciones suelen ser MODS SEPARADOS (subidos por la comunidad).
Detección (vías best-effort, en orden):
  1. Archivo en ese idioma DENTRO del mismo mod (sección Files).
  2. Búsqueda GraphQL v2 por nombre + filtro de idioma (languageName)
     (api.nexusmods.com/v2/graphql; funciona sin API key).

El idioma objetivo es el de la app (config.language: es/en/fr/de/it). Las funciones de
heurística (detección de idioma y solapamiento de nombre) son puras y están cubiertas
por tests. Se conservan envoltorios `*_spanish*` por compatibilidad.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Código de la app -> nombre de idioma tal como lo espera el filtro de Nexus (languageName).
NEXUS_LANGUAGE_NAME = {
    "es": "Spanish",
    "en": "English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}

# Palabras que delatan cada idioma en nombres de archivo/opción de mod.
# (Se evitan fragmentos cortos ambiguos como "ita"/"esp" que chocarían con otras palabras.)
LANGUAGE_KEYWORDS = {
    "es": ["español", "espanol", "castellano", "spanish", "traducción", "traduccion",
           "traducido", "traducida", "al español"],
    "en": ["english", "inglés", "ingles", "english translation"],
    "fr": ["français", "francais", "french", "traduction française",
           "traduction francaise", "traduction"],
    "de": ["deutsch", "german", "übersetzung", "ubersetzung", "deutsche übersetzung"],
    "it": ["italiano", "italian", "traduzione", "traduzione italiana"],
}

# Fragmentos de idioma a IGNORAR al comparar nombres de mods (para que
# "SkyUI - French" solape bien con "SkyUI").
_LANG_FRAGMENTS = (
    "spanish", "español", "espanol", "castellano", "traduc",
    "english", "ingl", "french", "français", "francais",
    "german", "deutsch", "übersetzung", "ubersetzung",
    "italian", "italiano", "traduzione", "traduction",
)

# Palabras irrelevantes al comparar nombres de mods.
_STOP = {
    "the", "of", "and", "a", "an", "for", "with", "to", "in", "on",
    "mod", "se", "sse", "skyrim", "special", "edition", "ae", "le",
}


def looks_language(name: str, lang: str) -> bool:
    """True si el nombre sugiere una traducción al idioma ``lang`` (es/en/fr/de/it)."""
    keywords = LANGUAGE_KEYWORDS.get(lang)
    if not keywords:
        return False
    n = " " + name.lower() + " "
    return any(k in n for k in keywords)


def looks_spanish(name: str) -> bool:
    """Compat: True si el nombre sugiere una traducción al español."""
    return looks_language(name, "es")


def _tokens(name: str) -> set[str]:
    toks = re.split(r"[^a-z0-9áéíóúñ]+", name.lower())
    out = set()
    for t in toks:
        if len(t) > 2 and t not in _STOP and not any(k in t for k in _LANG_FRAGMENTS):
            out.add(t)
    return out


def name_overlap(original: str, candidate: str) -> float:
    """Parecido entre nombres (F1 de palabras significativas).

    Combina recall (cuánto del original aparece) y precisión (que el candidato no
    tenga muchas palabras de más). Así 'SkyUI - Spanish' puntúa más alto que
    'Crafting Categories for SkyUI - Spanish' como traducción de 'SkyUI'.
    """
    o = _tokens(original)
    c = _tokens(candidate)
    if not o or not c:
        return 0.0
    common = len(o & c)
    if common == 0:
        return 0.0
    recall = common / len(o)
    precision = common / len(c)
    return 2 * precision * recall / (precision + recall)


@dataclass
class TranslationRef:
    mod_id: int
    name: str
    game_domain: str
    score: float = 0.0


# ---------------------------------------------------------------------------
def find_translation_file_in_mod(
    graphql_client,
    game_domain: str,
    mod_id: int,
    lang: str,
    exclude_file_id: int = 0,
    log=lambda m: None,
) -> tuple[int, str] | None:
    """Busca un archivo en el idioma ``lang`` DENTRO del mismo mod (sección Files).

    Devuelve (file_id, nombre) del archivo, o None.
    """
    try:
        files = graphql_client.mod_files(game_domain, mod_id)
    except Exception as e:
        log(f"No se pudieron leer los archivos del mod: {e}")
        return None
    for f in files:
        fid = int(f.get("fileId") or 0)
        if not fid or fid == exclude_file_id:
            continue
        text = f"{f.get('name','') or ''} {f.get('description','') or ''}"
        if looks_language(text, lang):
            return fid, (f.get("name", "") or "")
    return None


def find_spanish_file_in_mod(graphql_client, game_domain, mod_id,
                             exclude_file_id=0, log=lambda m: None):
    """Compat: archivo en español dentro del mismo mod."""
    return find_translation_file_in_mod(
        graphql_client, game_domain, mod_id, "es", exclude_file_id, log)


def _search_variants(mod_name: str) -> list[str]:
    """Consultas de búsqueda, de más específica a más corta, para encontrar la traducción
    aunque su nombre NO incluya los sufijos del original (acrónimos tipo 'TNG', versión,
    subtítulos). La búsqueda WILDCARD de Nexus exige que el nombre contenga el texto, así
    que con el nombre completo a veces no encuentra la traducción."""
    name = mod_name.strip()
    out = [name]
    # Parte antes de un separador de subtítulo ( - : | ( [ ).
    head = re.split(r"\s*[-–—:|(\[]", name, 1)[0].strip()
    if head and head.lower() != name.lower():
        out.append(head)
    # Ir quitando palabras del final (deja al menos 2).
    words = name.split()
    for cut in (1, 2, 3):
        if len(words) - cut >= 2:
            q = " ".join(words[: len(words) - cut]).strip()
            if q and q.lower() not in (o.lower() for o in out):
                out.append(q)
    return out


def find_translations(
    graphql_client,
    game_domain: str,
    mod_id: int,
    mod_name: str,
    lang: str,
    min_score: float = 0.7,
    log=lambda m: None,
) -> list[TranslationRef]:
    """Busca traducciones del mod al idioma ``lang`` de forma ESTRICTA.

    El API de Nexus no expone la lista oficial de traducciones, así que se busca por nombre
    entre los mods marcados en ese idioma; pero para NO bajar una traducción equivocada, se
    exige un parecido de nombre ALTO (F1 de palabras significativas ≥ ``min_score``): la
    candidata debe contener casi todo el nombre original Y no tener muchas palabras de más.
    Así «X - Spanish» pasa, pero ni «Super Skyrim Bros» (comparte solo «super» con «Skyrim
    Super Weapons») ni «Crafting Categories for SkyUI» (traducción de OTRO mod) cuelan.
    Prefiere no encontrar nada antes que acertar mal.
    """
    language_name = NEXUS_LANGUAGE_NAME.get(lang)
    if not language_name or not mod_name:
        return []
    orig = _tokens(mod_name)
    if not orig:
        return []
    # Solo el nombre completo y, como mucho, la cabecera antes de un subtítulo. NADA de
    # acortar agresivamente (eso era lo que provocaba falsos positivos).
    queries = [mod_name.strip()]
    head = re.split(r"\s*[-–—:|(\[]", mod_name, 1)[0].strip()
    if head and head.lower() != mod_name.strip().lower() and len(_tokens(head)) >= 2:
        queries.append(head)

    found: dict[int, TranslationRef] = {}
    for query in queries:
        try:
            cands = graphql_client.search_mods(query, game_domain, language=language_name)
        except Exception as e:
            log(f"Búsqueda de traducción no disponible: {e}")
            cands = []
        for c in cands:
            if c.mod_id == mod_id or c.mod_id in found:
                continue
            if not _tokens(c.name):
                continue
            # F1 de palabras significativas: alto = la candidata contiene casi todo el nombre
            # original y no muchas palabras de más (evita falsos positivos en ambos sentidos).
            score = name_overlap(mod_name, c.name)
            if score >= min_score:
                found[c.mod_id] = TranslationRef(c.mod_id, c.name, game_domain, score)
        if found:
            break
    return sorted(found.values(), key=lambda t: t.score, reverse=True)


def find_spanish_translations(graphql_client, game_domain, mod_id, mod_name,
                              min_overlap=0.34, log=lambda m: None):
    """Compat: traducciones al español."""
    return find_translations(graphql_client, game_domain, mod_id, mod_name, "es",
                             min_overlap, log)
