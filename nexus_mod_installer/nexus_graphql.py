"""Cliente GraphQL v2 de Nexus (para colecciones y dependencias).

Endpoint: https://api.nexusmods.com/v2/graphql

NOTA: el esquema GraphQL de Nexus no está documentado oficialmente de forma estable
y puede cambiar. Los parsers de este módulo son DEFENSIVOS (navegan los dicts con
``.get`` y toleran campos ausentes) y la resolución de colecciones es "best-effort".
Si Nexus cambia el esquema, sólo hay que ajustar las consultas de abajo.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from typing import Optional

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import requests

from . import __app_name__, __version__

from . import games

GRAPHQL_URL = "https://api.nexusmods.com/v2/graphql"

# dominio de Nexus -> game_id numérico (la API GraphQL usa gameId: ID! en varios sitios).
# Se deriva del registro de juegos (games.py). Se indexa por DOMINIO (no por key), porque
# varias entradas pueden compartir dominio (SE y AE -> skyrimspecialedition).
GAME_IDS = {g.domain: g.game_id for g in games.GAMES.values()}


def parse_download_links(json_str: str) -> list[tuple[str, int, int, str]]:
    """Extrae las dependencias del atributo ``download-links`` que Nexus pone en el elemento
    ``<main-file-requirements>`` de la página de un mod (la sección Requirements / "additional
    files required"). Es la fuente AUTORITATIVA de qué archivos hacen falta para que el mod
    funcione (a veces más completa que el GraphQL ``mod_requirements``).

    Devuelve ``[(game_domain, mod_id, file_id, nombre), ...]`` sin duplicados. El ``file_id``
    se decodifica del ``uid`` (uid = game_id * 2**32 + file_id); si no se puede, queda 0 y el
    flujo normal resolverá el archivo principal por el mod_id.
    """
    import json as _json
    try:
        data = _json.loads(json_str or "")
    except Exception:
        return []
    out: list[tuple[str, int, int, str]] = []
    seen: set[int] = set()
    for dep in (data.get("dependencies") or []):
        for f in (dep.get("files") or []):
            mod = f.get("mod") or {}
            m = re.search(r"nexusmods\.com/([^/?#]+)/mods/(\d+)", mod.get("url", "") or "")
            if not m:
                continue
            domain, mod_id = m.group(1), int(m.group(2))
            if mod_id in seen:
                continue
            seen.add(mod_id)
            gid = GAME_IDS.get(domain, 0)
            try:
                uid = int(f.get("uid") or 0)
            except (TypeError, ValueError):
                uid = 0
            file_id = (uid - gid * (2 ** 32)) if (gid and uid > gid * (2 ** 32)) else 0
            out.append((domain, mod_id, file_id, f.get("name", "") or mod.get("name", "")))
    return out


@dataclass
class TranslationCandidate:
    mod_id: int
    name: str
    is_spanish: bool = False


@dataclass
class CollectionModRef:
    game_domain: str
    mod_id: int
    file_id: int
    name: str = ""
    optional: bool = False
    source: str = "nexus"   # "nexus" o "browse"/"direct" para recursos externos


@dataclass
class CollectionInfo:
    slug: str
    revision: int
    name: str
    mods: list[CollectionModRef]
    external: list[dict]    # recursos no-Nexus (descarga directa / manual)


# Acepta URLs tipo:
#   https://next.nexusmods.com/skyrimspecialedition/collections/abcdef
#   https://www.nexusmods.com/games/skyrimspecialedition/collections/abcdef
#   nxm://skyrimspecialedition/collections/abcdef/revisions/3
_COLLECTION_RE = re.compile(
    r"collections/(?P<slug>[a-z0-9]+)(?:/revisions?/(?P<rev>\d+))?", re.IGNORECASE
)


def parse_collection_url(url: str) -> Optional[tuple[str, Optional[int]]]:
    """Devuelve (slug, revision|None) a partir de una URL/enlace de colección."""
    m = _COLLECTION_RE.search(url or "")
    if not m:
        return None
    rev = m.group("rev")
    return m.group("slug"), (int(rev) if rev else None)


class NexusGraphQLClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._bearer = None          # callable -> access_token OAuth (o None); tiene prioridad
        self._session = requests.Session()

    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key

    def set_bearer_provider(self, provider) -> None:
        """``provider``: función sin args que devuelve un access_token OAuth vigente (o None)."""
        self._bearer = provider

    def _bearer_token(self):
        if not self._bearer:
            return None
        try:
            return self._bearer()
        except Exception:
            return None

    def _headers(self) -> dict:
        h = {
            "Application-Name": __app_name__,
            "Application-Version": __version__,
            "User-Agent": f"{__app_name__}/{__version__}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        tok = self._bearer_token()
        if tok:
            h["Authorization"] = f"Bearer {tok}"     # OAuth (preferente)
        else:
            h["apikey"] = self.api_key
        return h

    def _post(self, query: str, variables: dict) -> dict:
        resp = self._session.post(
            GRAPHQL_URL,
            headers=self._headers(),
            json={"query": query, "variables": variables},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"GraphQL HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if payload.get("errors"):
            msgs = "; ".join(e.get("message", "") for e in payload["errors"])
            raise RuntimeError(f"GraphQL error: {msgs}")
        return payload.get("data", {}) or {}

    # ------------------------------------------------------------------
    def resolve_collection(self, slug: str, revision: Optional[int] = None) -> CollectionInfo:
        """Obtiene la lista de mods de una colección.

        Si ``revision`` es None, se pide la última revisión publicada.
        """
        query = """
        query CollectionRevision($slug: String!, $revision: Int) {
          collectionRevision(slug: $slug, revision: $revision, viewAdultContent: true) {
            revisionNumber
            collection { name }
            modFiles {
              optional
              fileId
              file {
                fileId
                mod {
                  modId
                  name
                  game { domainName }
                }
              }
            }
            externalResources {
              name
              resourceUrl
              resourceType
              optional
            }
          }
        }
        """
        data = self._post(query, {"slug": slug, "revision": revision})
        rev = (data or {}).get("collectionRevision") or {}

        name = ((rev.get("collection") or {}).get("name")) or slug
        revision_number = int(rev.get("revisionNumber") or revision or 0)

        mods: list[CollectionModRef] = []
        for mf in rev.get("modFiles") or []:
            f = mf.get("file") or {}
            mod = f.get("mod") or {}
            game = (mod.get("game") or {}).get("domainName") or "skyrimspecialedition"
            file_id = f.get("fileId")
            mod_id = mod.get("modId")
            if file_id and mod_id:
                mods.append(
                    CollectionModRef(
                        game_domain=game,
                        mod_id=int(mod_id),
                        file_id=int(file_id),
                        name=mod.get("name", "") or "",
                        optional=bool(mf.get("optional")),
                    )
                )

        external = list(rev.get("externalResources") or [])
        return CollectionInfo(
            slug=slug,
            revision=revision_number,
            name=name,
            mods=mods,
            external=external,
        )

    # ------------------------------------------------------------------
    def mod_files(self, game_domain: str, mod_id: int) -> list[dict]:
        """Lista los archivos de un mod (sin API key). Para detectar traducciones
        en español dentro del propio mod."""
        game_id = GAME_IDS.get(game_domain)
        if not game_id:
            return []
        query = """
        query ModFiles($id: ID!, $g: ID!) {
          modFiles(modId: $id, gameId: $g) {
            fileId name description categoryId
          }
        }
        """
        data = self._post(query, {"id": str(mod_id), "g": str(game_id)})
        return (data or {}).get("modFiles") or []

    def mod_requirements(self, game_domain: str, mod_id: int) -> list[CollectionModRef]:
        """Obtiene los mods de Nexus requeridos (dependencias) de un mod.

        Usa el esquema GraphQL v2 actual: mod(modId, gameId).modRequirements
        .nexusRequirements.nodes[]. Ignora requisitos externos (no-Nexus).
        Devuelve [] si no hay o si falla.
        """
        game_id = GAME_IDS.get(game_domain)
        if not game_id:
            return []
        query = """
        query ModRequirements($id: ID!, $g: ID!) {
          mod(modId: $id, gameId: $g) {
            modRequirements {
              nexusRequirements {
                nodes { modId modName gameId externalRequirement }
              }
            }
          }
        }
        """
        data = self._post(query, {"id": str(mod_id), "g": str(game_id)})
        mod = (data or {}).get("mod") or {}
        page = (mod.get("modRequirements") or {}).get("nexusRequirements") or {}
        out: list[CollectionModRef] = []
        for node in page.get("nodes") or []:
            if node.get("externalRequirement"):
                continue  # requisito que no está en Nexus
            rid = node.get("modId")
            if not rid or int(rid) == 0:
                continue
            out.append(
                CollectionModRef(
                    game_domain=game_domain,
                    mod_id=int(rid),
                    file_id=0,  # se resolverá al archivo principal más tarde
                    name=node.get("modName", "") or "",
                )
            )
        return out

    # ------------------------------------------------------------------
    def search_mods(
        self, name: str, game_domain: str, language: str | None = None, count: int = 15
    ) -> list[TranslationCandidate]:
        """Busca mods por nombre (y opcionalmente idioma) con el GraphQL v2 actual.

        Filtro: name WILDCARD + gameDomainName EQUALS [+ languageName EQUALS].
        Funciona sin API key. Devuelve TranslationCandidate(mod_id, name, is_spanish).
        """
        filt = {
            "op": "AND",
            "name": [{"value": name, "op": "WILDCARD"}],
            "gameDomainName": [{"value": game_domain, "op": "EQUALS"}],
        }
        if language:
            filt["languageName"] = [{"value": language, "op": "EQUALS"}]

        query = """
        query SearchMods($f: ModsFilter, $c: Int) {
          mods(filter: $f, count: $c) {
            nodes { modId name }
            totalCount
          }
        }
        """
        data = self._post(query, {"f": filt, "c": count})
        nodes = ((data or {}).get("mods") or {}).get("nodes") or []
        out: list[TranslationCandidate] = []
        for n in nodes:
            mid = n.get("modId")
            if mid:
                out.append(
                    TranslationCandidate(
                        mod_id=int(mid), name=n.get("name", "") or "",
                        is_spanish=bool(language),
                    )
                )
        return out

