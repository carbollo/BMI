"""Escáner de traducciones OFICIALES desde la página del mod.

El API de Nexus no expone la sección «Translations available on the Nexus», y la página
está protegida por Cloudflare (una petición directa da 403). Pero el navegador embebido de
BMI (QtWebEngine) tiene la sesión/cookies del usuario y sí carga la página. Aquí cargamos la
página de cada mod en un ``QWebEnginePage`` de fondo (mismo perfil que el navegador) y
extraemos, con JavaScript, las traducciones reales que aparecen en esa sección, filtrando por
el idioma pedido. Así se baja SOLO la traducción oficial del mod, no una parecida por nombre.
"""
from __future__ import annotations

import json
from collections import deque

from PySide6.QtCore import QObject, Signal, QUrl, QTimer
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile


# JS que lee la sección de traducciones de la página YA cargada y devuelve un JSON con
# [{modId, name, lang}]. Es tolerante a cambios de maquetación: localiza el encabezado
# «Translations available on the Nexus» y recoge los enlaces a /mods/<id> de su tabla,
# con el idioma que indique la bandera (alt/title) o el texto de la fila.
EXTRACT_JS = r"""
(function(){
  function norm(s){ return (s||'').replace(/\s+/g,' ').trim().toLowerCase(); }
  function findSection(){
    var all = document.querySelectorAll('h1,h2,h3,h4,h5,dt,legend,summary,strong,b,div,span,p');
    for (var i=0;i<all.length;i++){
      if (norm(all[i].textContent).indexOf('translations available on the nexus')>=0) return all[i];
    }
    return null;
  }
  var sec = findSection();
  if(!sec) return "[]";
  var scope = sec;
  for (var up=0; up<6; up++){
    if (scope && scope.querySelectorAll && scope.querySelectorAll('a[href*="/mods/"]').length>0) break;
    scope = scope.parentElement || scope;
  }
  var out = [], seen = {};
  var links = (scope||document).querySelectorAll('a[href*="/mods/"]');
  for (var j=0;j<links.length;j++){
    var a=links[j];
    var m=(a.getAttribute('href')||'').match(/\/mods\/(\d+)/);
    if(!m) continue;
    var id=parseInt(m[1],10);
    if(seen[id]) continue;
    var row = a.closest('tr,li') || a.parentElement || a;
    var lang='';
    var img = row.querySelector && row.querySelector('img[title],img[alt]');
    if(img) lang = img.getAttribute('title')||img.getAttribute('alt')||'';
    if(!lang) lang = row.textContent || '';
    seen[id]=1;
    out.push({modId:id, name:(a.textContent||'').trim(), lang:lang.trim()});
  }
  return JSON.stringify(out);
})();
"""


class TranslationScanner(QObject):
    """Cola de mods a los que leerles su lista OFICIAL de traducciones. Carga la página de
    cada uno en un ``QWebEnginePage`` de fondo y emite ``translation_found`` por cada
    traducción al idioma pedido. Sirve tanto para «Traducir mis mods» (añade muchos) como
    para la descarga de un mod suelto (añade uno): mismo mecanismo, una sola cola."""

    scanning = Signal(str, int)                 # (game_domain, mod_id) que se empieza a leer
    translation_found = Signal(str, int, str)   # (game_domain, mod_id_traduccion, nombre)
    idle = Signal(int)                           # cola vacía; nº de páginas leídas en la tanda

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

    def add(self, mods) -> None:
        """``mods``: iterable de (game_domain, mod_id, mod_name). Los nuevos se encolan y, si
        el escáner está parado, arranca."""
        for dom, mid, name in mods:
            if not mid or int(mid) in self._seen:
                continue
            self._seen.add(int(mid))
            self._queue.append((dom, int(mid), name or ""))
        if not self._busy:
            self._done_count = 0
            self._next()

    @property
    def busy(self) -> bool:
        return self._busy

    # ------------------------------------------------------------------
    def _next(self) -> None:
        if not self._queue:
            self._busy = False
            self._current = None
            self.idle.emit(self._done_count)
            return
        self._busy = True
        self._current = self._queue.popleft()
        dom, mid, _ = self._current
        self.scanning.emit(dom, mid)
        self._page.load(QUrl(f"https://www.nexusmods.com/{dom}/mods/{mid}?tab=description"))

    def _on_loaded(self, ok: bool) -> None:
        # Margen para que el sitio (React) renderice la sección antes de extraer.
        QTimer.singleShot(1300, self._extract)

    def _extract(self) -> None:
        if self._current is None:
            return
        self._page.runJavaScript(EXTRACT_JS, self._on_extracted)

    def _on_extracted(self, result) -> None:
        if self._current is None:
            return
        dom, mid, _ = self._current
        try:
            items = json.loads(result or "[]")
        except Exception:
            items = []
        for it in items:
            tmid = it.get("modId")
            lang = (it.get("lang") or "").lower()
            if tmid and int(tmid) != mid and any(w in lang for w in self._lang_words):
                self.translation_found.emit(dom, int(tmid), it.get("name", "") or "")
        self._done_count += 1
        self._next()
