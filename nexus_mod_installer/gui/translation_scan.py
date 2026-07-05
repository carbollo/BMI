"""Escáner de páginas de mod: traducciones OFICIALES y requisitos (Requirements).

El API de Nexus no expone la sección «Translations available on the Nexus» y su GraphQL a
veces no trae los requisitos; además la página está protegida por Cloudflare (una petición
directa da 403). Pero el navegador embebido de BMI (QtWebEngine) tiene la sesión/cookies
del usuario y sí la carga. Aquí cargamos la página de cada mod en un ``QWebEnginePage`` de
fondo (mismo perfil que el navegador) y extraemos con JavaScript:

- las traducciones reales de la sección «Translations available on the Nexus», filtrando
  por el idioma pedido, y
- los requisitos de la sección «Nexus requirements» (NUNCA «Mods requiring this file»,
  que son los mods que dependen de ESTE, ni los «Off-site requirements», que no son de
  Nexus).

Los enlaces se acotan por POSICIÓN en el documento (entre el encabezado de su sección y el
siguiente encabezado conocido), no por cercanía de maquetación, para no arrastrar enlaces
de otras partes de la página (mods relacionados, comentarios, etc.).
"""
from __future__ import annotations

import json
from collections import deque

from PySide6.QtCore import QObject, Signal, QUrl, QTimer
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile


# JS que lee la página YA cargada y devuelve JSON {tr:[{dom,modId,name,lang}],
# req:[{dom,modId,name}]}. Tolerante a cambios de maquetación: localiza los encabezados
# de sección por su TEXTO (elementos cuyo texto es solo ese título, no contenedores) y
# recoge los enlaces a /mods/<id> situados entre un encabezado y el siguiente.
EXTRACT_JS = r"""
(function(){
  function norm(s){ return (s||'').replace(/\s+/g,' ').trim().toLowerCase(); }
  var HEADS = ['nexus requirements','mods requiring this file','off-site requirements',
               'credits','translations available on the nexus'];
  // Un "encabezado" es un elemento que contiene la frase y POCO texto más (así nunca
  // se elige un contenedor grande que envuelva media página).
  function findHeadings(){
    var out = {};
    var all = document.querySelectorAll(
      'h1,h2,h3,h4,h5,h6,dt,legend,summary,strong,b,div,span,p,td,th,a');
    for (var i=0;i<all.length;i++){
      var t = norm(all[i].textContent);
      for (var h=0; h<HEADS.length; h++){
        var k = HEADS[h];
        if (t.indexOf(k)>=0 && t.length <= k.length+30){
          var cur = out[k];
          if (!cur || all[i].textContent.length < cur.textContent.length) out[k] = all[i];
        }
      }
    }
    return out;
  }
  // Enlaces a /mods/<id> situados DESPUÉS de startEl y ANTES del siguiente encabezado.
  // Si no hay encabezado posterior que delimite, se limita a la primera tabla/lista.
  function sectionLinks(startEl, heads, firstPerRow){
    var FOLLOWING = 4; // Node.DOCUMENT_POSITION_FOLLOWING
    var end = null;
    for (var k in heads){
      var el = heads[k];
      if (!el || el === startEl) continue;
      if (startEl.compareDocumentPosition(el) & FOLLOWING){
        if (!end || (el.compareDocumentPosition(end) & FOLLOWING)) end = el;
      }
    }
    var out = [], seen = {}, firstContainer = null;
    var links = document.querySelectorAll('a[href*="/mods/"]');
    for (var j=0;j<links.length;j++){
      var a = links[j];
      if (!(startEl.compareDocumentPosition(a) & FOLLOWING)) continue;   // antes de la sección
      if (end && !(a.compareDocumentPosition(end) & FOLLOWING)) continue; // después del final
      if (!end){
        var c = a.closest('table,ul,ol,dl');
        if (!c) continue;
        if (!firstContainer) firstContainer = c;
        if (c !== firstContainer) continue;
      }
      if (firstPerRow){
        // Solo el PRIMER enlace de cada fila (los demás son notas/parche recomendado).
        var row = a.closest('tr,li');
        if (row && row.querySelector('a[href*="/mods/"]') !== a) continue;
      }
      var href = a.getAttribute('href')||'';
      var dom = '', id = 0;
      var m = href.match(/\/([a-z0-9]+)\/mods\/(\d+)/);
      if (m && m[1] !== 'mods' && m[1] !== 'games'){ dom = m[1]; id = parseInt(m[2],10); }
      else {
        var m2 = href.match(/\/mods\/(\d+)/);
        if (!m2) continue;
        id = parseInt(m2[1],10);
      }
      if (!id || seen[id]) continue;
      seen[id] = 1;
      out.push({el:a, dom:dom, id:id, name:(a.textContent||'').trim()});
    }
    return out;
  }
  var heads = findHeadings();
  // --- Traducciones oficiales ---
  var tr = [];
  var th = heads['translations available on the nexus'];
  if (th){
    var ls = sectionLinks(th, heads, false);
    for (var i2=0;i2<ls.length;i2++){
      var row = ls[i2].el.closest('tr,li') || ls[i2].el.parentElement || ls[i2].el;
      var lang = '';
      var img = row.querySelector && row.querySelector('img[title],img[alt]');
      if (img) lang = img.getAttribute('title')||img.getAttribute('alt')||'';
      if (!lang) lang = row.textContent || '';
      tr.push({dom:ls[i2].dom, modId:ls[i2].id, name:ls[i2].name, lang:lang.trim()});
    }
  }
  // --- Requisitos (SOLO «Nexus requirements») ---
  var req = [];
  var rh = heads['nexus requirements'];
  if (rh){
    var ls2 = sectionLinks(rh, heads, true);
    for (var i3=0;i3<ls2.length && i3<60;i3++)
      req.push({dom:ls2[i3].dom, modId:ls2[i3].id, name:ls2[i3].name});
  }
  return JSON.stringify({tr:tr, req:req});
})();
"""


class TranslationScanner(QObject):
    """Cola de mods a los que leerles la página oficial. Carga la página de cada uno en un
    ``QWebEnginePage`` de fondo y emite ``translation_found`` por cada traducción al idioma
    pedido y ``requirement_found`` por cada requisito de «Nexus requirements» (según lo que
    se pidiera al encolar). Sirve para «Traducir mis mods» (muchos, solo traducciones) y
    para las descargas (uno a uno, traducciones + requisitos): una sola cola."""

    scanning = Signal(str, int)                 # (game_domain, mod_id) que se empieza a leer
    translation_found = Signal(str, int, str)   # (game_domain, mod_id_traduccion, nombre)
    requirement_found = Signal(str, int, str)   # (game_domain, mod_id_requisito, nombre)
    idle = Signal(int)                           # cola vacía; nº de páginas leídas en la tanda

    # Margen tras el ÚLTIMO loadFinished para que React pinte las secciones antes de extraer.
    _SETTLE_MS = 1300
    # Si una página no termina de cargar (Cloudflare colgado, red caída), pasar a la siguiente.
    _WATCHDOG_MS = 20000

    def __init__(self, profile: QWebEngineProfile, lang_words, parent=None):
        super().__init__(parent)
        self._page = QWebEnginePage(profile, self)
        self._page.loadFinished.connect(self._on_loaded)
        self._lang_words = tuple(w.lower() for w in lang_words if w)
        self._queue: deque = deque()
        self._seen: set[int] = set()   # no re-escanear el mismo mod
        self._current: tuple | None = None
        self._busy = False
        self._done_count = 0
        # Control de generación: cada carga tiene un id creciente. Un ``loadFinished`` o
        # ``runJavaScript`` de una carga vieja (p.ej. el que dispara Cloudflare al pasar del
        # interstitial a la página real, o el abort al pedir la siguiente) se descarta por
        # generación. ``_pending`` implementa un debounce de cola: solo se extrae cuando NO
        # ha llegado otro loadFinished en los últimos _SETTLE_MS (así se lee la página REAL,
        # no el desafío intermedio).
        self._gen = 0
        self._pending = 0
        self._extracted = False

    def add(self, mods, want_translations: bool = True, want_requirements: bool = False) -> None:
        """``mods``: iterable de (game_domain, mod_id, mod_name). Los nuevos se encolan y,
        si el escáner está parado, arranca. ``want_translations``/``want_requirements``
        indican qué emitir de cada página."""
        for dom, mid, name in mods:
            if not mid or int(mid) in self._seen:
                continue
            self._seen.add(int(mid))
            self._queue.append((dom, int(mid), name or "",
                                bool(want_translations), bool(want_requirements)))
        if not self._busy:
            self._done_count = 0
            self._next()

    @property
    def busy(self) -> bool:
        return self._busy

    def reset(self) -> None:
        """Descarta la cola y la página en curso. Se llama al CAMBIAR DE JUEGO: si no, el
        escáner seguiría leyendo páginas del juego anterior y encolaría sus requisitos/
        traducciones en el juego nuevo (Data equivocada). Invalida la generación en vuelo."""
        self._queue.clear()
        self._seen.clear()
        self._current = None
        self._busy = False
        self._gen += 1          # invalida cualquier extracción/loadFinished pendiente
        self._extracted = True
        try:
            self._page.stop()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def _next(self) -> None:
        if not self._queue:
            self._busy = False
            self._current = None
            self.idle.emit(self._done_count)
            return
        self._busy = True
        self._current = self._queue.popleft()
        self._gen += 1
        self._extracted = False
        gen = self._gen
        dom, mid = self._current[0], self._current[1]
        self.scanning.emit(dom, mid)
        self._page.load(QUrl(f"https://www.nexusmods.com/{dom}/mods/{mid}?tab=description"))
        # Watchdog: si esta generación nunca llega a extraer, saltar a la siguiente.
        QTimer.singleShot(self._WATCHDOG_MS, lambda g=gen: self._watchdog(g))

    def _on_loaded(self, ok: bool) -> None:
        # Puede dispararse VARIAS veces por página (interstitial de Cloudflare -> página real,
        # o abort al pedir la siguiente). Debounce por cola: extraer _SETTLE_MS después del
        # ÚLTIMO loadFinished de ESTA generación.
        if self._current is None:
            return
        self._pending += 1
        gen, pend = self._gen, self._pending
        QTimer.singleShot(self._SETTLE_MS, lambda g=gen, p=pend: self._extract(g, p))

    def _extract(self, gen: int, pend: int) -> None:
        # Solo si sigue siendo la generación activa y no llegó otro loadFinished después.
        if gen != self._gen or pend != self._pending or self._extracted or self._current is None:
            return
        self._page.runJavaScript(EXTRACT_JS, lambda r, g=gen: self._on_extracted(g, r))

    def _watchdog(self, gen: int) -> None:
        if gen == self._gen and not self._extracted and self._current is not None:
            self._extracted = True
            self._done_count += 1
            self._next()

    def _on_extracted(self, gen: int, result) -> None:
        if gen != self._gen or self._extracted or self._current is None:
            return
        self._extracted = True
        dom, mid, _name, want_tr, want_req = self._current
        try:
            data = json.loads(result or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if want_tr:
            for it in data.get("tr", []):
                tmid = it.get("modId")
                lang = (it.get("lang") or "").lower()
                if tmid and int(tmid) != mid and any(w in lang for w in self._lang_words):
                    self.translation_found.emit(it.get("dom") or dom, int(tmid),
                                                it.get("name", "") or "")
        if want_req:
            for it in data.get("req", []):
                rmid = it.get("modId")
                if rmid and int(rmid) != mid:
                    self.requirement_found.emit(it.get("dom") or dom, int(rmid),
                                                it.get("name", "") or "")
        self._done_count += 1
        self._next()
