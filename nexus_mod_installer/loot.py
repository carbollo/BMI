"""Ordenación estilo LOOT y avisos del masterlist de la comunidad.

BMI no empaqueta libloot (C++); en su lugar descarga el MASTERLIST oficial de LOOT (repos
públicos github.com/loot/<juego>, datos abiertos — NO es scraping de Nexus) y lo usa para:

  - AVISOS (solo lectura, sin riesgo): plugins sucios (ITM/UDR), incompatibilidades,
    requisitos que faltan y mensajes de la comunidad.
  - ORDENACIÓN estilo LOOT: masters primero (regla del motor), luego el grafo de GRUPOS del
    masterlist, las reglas ``after`` explícitas y las dependencias de masters de cada plugin.
    Se hace con una ordenación topológica (Kahn) que, ante un CICLO (reglas contradictorias),
    ABORTA y no toca nada — nunca deja un orden a medias/roto.

No es idéntico byte a byte a LOOT (libloot tiene más matices), pero aplica sus mismos
principios y es SEGURO.
"""
from __future__ import annotations

import heapq
import re
import ssl
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import app_data_dir

# Dominio de Nexus (BMI) -> repositorio de masterlist de LOOT.
_DOMAIN_TO_REPO = {
    "skyrimspecialedition": "skyrimse",
    "skyrim": "skyrim",
    "fallout4": "fallout4",
    "newvegas": "falloutnv",
    "fallout3": "fallout3",
    "oblivion": "oblivion",
    "starfield": "starfield",
    "morrowind": "morrowind",
}
# Ramas a probar (libloot versiona el masterlist por su major.minor).
_BRANCHES = ("v0.26", "v0.25", "v0.24", "master")
_RAW = "https://raw.githubusercontent.com/loot/{repo}/{branch}/masterlist.yaml"

# Un nombre de plugin del masterlist se trata como REGEX si trae metacaracteres fuertes.
_REGEXY = re.compile(r"[\\*?|()\[\]^$+{}]")


@dataclass
class PluginMeta:
    group: str = ""
    after: list[str] = field(default_factory=list)   # plugins tras los que cargar
    req: list[str] = field(default_factory=list)      # requisitos (plugins/archivos)
    inc: list[str] = field(default_factory=list)      # incompatibles
    dirty: bool = False                               # tiene entradas 'dirty' (ITM/UDR)
    messages: list[str] = field(default_factory=list) # mensajes de la comunidad


@dataclass
class Masterlist:
    groups: dict[str, list[str]] = field(default_factory=dict)     # grupo -> grupos 'after'
    exact: dict[str, PluginMeta] = field(default_factory=dict)     # nombre.lower() -> meta
    regexes: list[tuple] = field(default_factory=list)             # [(re_compilado, meta)]
    _meta_cache: dict = field(default_factory=dict)                # memo de meta_for (perf)


@dataclass
class PluginInfo:
    name: str
    is_master: bool = False          # flag ESM/ESL (el motor carga masters primero)
    masters: list[str] = field(default_factory=list)  # masters del header
    index: int = 0                   # posición actual (para desempatar de forma estable)


@dataclass
class Warning:
    plugin: str
    kind: str         # 'dirty' | 'inc' | 'req' | 'msg'
    detail: str = ""  # inc/req: el OTRO plugin; msg: el texto del mensaje (del masterlist)


# ---------------------------------------------------------------------------
# Descarga / caché
# ---------------------------------------------------------------------------
def repo_for(game_domain: str) -> str | None:
    return _DOMAIN_TO_REPO.get(game_domain)


def _cache_path(repo: str) -> Path:
    d = Path(app_data_dir()) / "masterlists"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{repo}.yaml"


def download_masterlist(game_domain: str, force: bool = True) -> str | None:
    """Descarga (y cachea) el masterlist del juego. Si falla la descarga, usa la caché si la
    hay. Devuelve el texto YAML o None."""
    repo = repo_for(game_domain)
    if not repo:
        return None
    cache = _cache_path(repo)
    if force or not cache.is_file():
        ctx = ssl.create_default_context()
        for branch in _BRANCHES:
            try:
                url = _RAW.format(repo=repo, branch=branch)
                req = urllib.request.Request(url, headers={"User-Agent": "BMI"})
                with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                    if r.status == 200:
                        txt = r.read().decode("utf-8", "replace")
                        cache.write_text(txt, encoding="utf-8")
                        return txt
            except Exception:  # noqa: BLE001
                continue
    if cache.is_file():
        return cache.read_text(encoding="utf-8", errors="ignore")
    return None


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------
def parse_masterlist(text: str) -> Masterlist:
    import yaml
    data = yaml.safe_load(text) or {}
    ml = Masterlist()
    for g in data.get("groups", []) or []:
        if isinstance(g, dict) and g.get("name"):
            after = g.get("after") or []
            after = [after] if isinstance(after, str) else list(after)
            ml.groups[str(g["name"])] = [str(a) for a in after]
    for p in data.get("plugins", []) or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        meta = PluginMeta(
            group=str(p.get("group") or ""),
            after=_as_name_list(p.get("after")),
            req=_as_name_list(p.get("req")),
            inc=_as_name_list(p.get("inc")),
            dirty=bool(p.get("dirty")),
            messages=_as_msg_list(p.get("msg")),
        )
        name = str(p["name"])
        if _REGEXY.search(name):
            try:
                ml.regexes.append((re.compile(name, re.IGNORECASE), meta))
            except re.error:
                pass
        else:
            ml.exact[name.lower()] = meta
    return ml


def _as_name_list(v) -> list[str]:
    """Normaliza after/req/inc: pueden ser str, lista de str o lista de dicts {name/file}."""
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    out = []
    for x in v:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            n = x.get("name") or x.get("file") or x.get("display")
            if n:
                out.append(str(n))
    return out


def _as_msg_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    out = []
    for x in v:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            c = x.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list) and c:
                first = c[0]
                out.append(first.get("text", "") if isinstance(first, dict) else str(first))
    return out


def meta_for(name: str, ml: Masterlist) -> PluginMeta | None:
    key = name.lower()
    if key in ml._meta_cache:                 # memoización: se consulta ~2x por plugin al ordenar
        return ml._meta_cache[key]
    m = ml.exact.get(key)
    if m is None:
        for rx, meta in ml.regexes:
            if rx.fullmatch(name) or rx.match(name):
                m = meta
                break
    ml._meta_cache[key] = m
    return m


# ---------------------------------------------------------------------------
# Avisos (solo lectura)
# ---------------------------------------------------------------------------
def warnings_for(plugin_names, ml: Masterlist, present_extra=None) -> list[Warning]:
    present = {n.lower() for n in plugin_names}
    if present_extra:                          # p.ej. masters vanilla implícitos (Dawnguard.esm…)
        present |= {str(n).lower() for n in present_extra}
    out: list[Warning] = []
    for name in plugin_names:
        meta = meta_for(name, ml)
        if not meta:
            continue
        if meta.dirty:
            out.append(Warning(name, "dirty"))
        for inc in meta.inc:
            if inc.lower() in present:
                out.append(Warning(name, "inc", inc))
        for req in meta.req:
            if req.lower().endswith((".esp", ".esm", ".esl")) and req.lower() not in present:
                out.append(Warning(name, "req", req))
        for msg in meta.messages:
            out.append(Warning(name, "msg", msg))
    return out


# ---------------------------------------------------------------------------
# Orden de grupos (topológico, tolerante a ciclos)
# ---------------------------------------------------------------------------
def group_ranks(groups: dict[str, list[str]]) -> dict[str, int]:
    """Rango de cada grupo por orden topológico (un grupo carga DESPUÉS de los de su 'after').
    Si hay un ciclo, degrada a rangos neutros (todos iguales) sin fallar."""
    indeg = {g: 0 for g in groups}
    adj: dict[str, list[str]] = {g: [] for g in groups}
    for g, afters in groups.items():
        for a in afters:
            if a in groups:                # arista a -> g (a antes que g)
                adj[a].append(g)
                indeg[g] += 1
    ready = sorted([g for g, d in indeg.items() if d == 0])
    rank: dict[str, int] = {}
    i = 0
    while ready:
        g = ready.pop(0)
        rank[g] = i
        i += 1
        for nb in adj[g]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                ready.append(nb)
        ready.sort()
    if len(rank) != len(groups):           # ciclo: no ordenar por grupos
        return {g: 0 for g in groups}
    return rank


# ---------------------------------------------------------------------------
# Ordenación de plugins (estilo LOOT, segura)
# ---------------------------------------------------------------------------
def sort_plugins(infos: list[PluginInfo], ml: Masterlist) -> tuple[list[str] | None, str]:
    """Devuelve (orden_nuevo, nota). Si hay un ciclo de dependencias, devuelve (None, motivo)
    y NO se debe escribir nada."""
    names = [p.name for p in infos]
    by_lower = {p.name.lower(): p for p in infos}
    ranks = group_ranks(ml.groups)
    mid_rank = (max(ranks.values()) + 1) if ranks else 0   # grupo desconocido -> en medio-final

    def grank(p: PluginInfo) -> int:
        meta = meta_for(p.name, ml)
        g = meta.group if meta else ""
        return ranks.get(g, mid_rank)

    key = {}
    for p in infos:
        # clave de preferencia (soft): masters primero, luego grupo, luego posición actual.
        key[p.name.lower()] = (0 if p.is_master else 1, grank(p), p.index, p.name.lower())

    # Aristas duras: p carga DESPUÉS de sus masters presentes y de sus 'after' presentes.
    adj: dict[str, set] = {p.name.lower(): set() for p in infos}
    indeg = {p.name.lower(): 0 for p in infos}

    def add_edge(before: str, after: str):
        b, a = before.lower(), after.lower()
        if b in by_lower and a in by_lower and a not in adj[b]:
            # No permitir que un MASTER quede forzado tras un NO-master (rompería la regla del
            # motor): se descarta esa arista.
            if by_lower[a].is_master and not by_lower[b].is_master:
                return
            adj[b].add(a)
            indeg[a] += 1

    for p in infos:
        for mst in p.masters:
            add_edge(mst, p.name)          # el master antes que el plugin
        meta = meta_for(p.name, ml)
        if meta:
            for aft in meta.after:
                add_edge(aft, p.name)      # 'after': carga tras 'aft'

    # Kahn con cola de prioridad (heapq) por la clave de preferencia → O(V log V).
    ready = [(key[n], n) for n, d in indeg.items() if d == 0]
    heapq.heapify(ready)
    out: list[str] = []
    while ready:
        _, n = heapq.heappop(ready)
        out.append(by_lower[n].name)
        for nb in adj[n]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                heapq.heappush(ready, (key[nb], nb))

    if len(out) != len(infos):
        return None, "LOOT detectó un ciclo (reglas contradictorias); el orden NO se cambió."
    return out, "Orden estilo LOOT aplicado."
