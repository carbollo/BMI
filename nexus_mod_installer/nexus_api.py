"""Cliente de la API pública de Nexus Mods (v1, REST).

Documentación: https://app.swaggerhub.com/apis-docs/NexusMods/nexus-mods_public_api_params_in_form_data/1.0

Notas sobre cuentas GRATUITAS:
  - La API en sí es gratis: cualquiera genera una "Personal API Key" en
    https://www.nexusmods.com/users/myaccount?tab=api
  - El endpoint ``download_link.json`` SÓLO devuelve el enlace directo a usuarios
    Premium si se llama sin parámetros. Para cuentas gratis hay que pasar
    ``key`` + ``expires`` (que vienen del enlace nxm:// generado al pulsar el botón
    "Mod Manager Download" en la web). Eso es justo lo que hace esta app.
"""
from __future__ import annotations

import warnings
from typing import Optional

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import requests

from . import __app_name__, __version__
from .models import ModInfo, ModFileInfo

API_BASE = "https://api.nexusmods.com/v1"


class NexusApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimitError(NexusApiError):
    pass


class PremiumRequiredError(NexusApiError):
    """Se intentó obtener un enlace de descarga sin key/expires en cuenta gratis."""


class NexusApiClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._bearer = None          # callable -> access_token OAuth (o None); tiene prioridad
        self._session = requests.Session()
        self._user: Optional[dict] = None

    # ------------------------------------------------------------------
    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key
        self._user = None

    def set_bearer_provider(self, provider) -> None:
        """``provider``: función sin args que devuelve un access_token OAuth vigente (o None).
        Si devuelve token, se usa 'Authorization: Bearer' en vez de la API key personal."""
        self._bearer = provider

    def _bearer_token(self):
        if not self._bearer:
            return None
        try:
            return self._bearer()
        except Exception:
            return None

    def has_auth(self) -> bool:
        return bool(self._bearer_token()) or bool(self.api_key)

    def _headers(self) -> dict:
        h = {
            "Application-Name": __app_name__,
            "Application-Version": __version__,
            "User-Agent": f"{__app_name__}/{__version__}",
            "Accept": "application/json",
        }
        tok = self._bearer_token()
        if tok:
            h["Authorization"] = f"Bearer {tok}"     # OAuth (preferente)
        else:
            h["apikey"] = self.api_key
        return h

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        if not self.has_auth():
            raise NexusApiError("Inicia sesión con Nexus (o configura una API key en Ajustes).")
        url = f"{API_BASE}{path}"
        resp = self._session.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code == 429:
            raise RateLimitError("Límite de peticiones alcanzado (rate limit). Espera un momento.", 429)
        if resp.status_code in (401, 403):
            raise NexusApiError(
                f"Acceso denegado ({resp.status_code}). API key inválida o sin permiso.",
                resp.status_code,
            )
        if resp.status_code == 404:
            raise NexusApiError("No encontrado (404).", 404)
        if not resp.ok:
            raise NexusApiError(f"Error de la API ({resp.status_code}): {resp.text[:200]}", resp.status_code)
        return resp.json()

    # ------------------------------------------------------------------
    def validate(self) -> dict:
        """Valida la API key. Devuelve datos del usuario (incluye is_premium)."""
        data = self._get("/users/validate.json")
        assert isinstance(data, dict)
        self._user = data
        return data

    @property
    def is_premium(self) -> bool:
        return bool(self._user and self._user.get("is_premium"))

    @property
    def user_name(self) -> str:
        return (self._user or {}).get("name", "")

    # ------------------------------------------------------------------
    def get_mod(self, game_domain: str, mod_id: int) -> ModInfo:
        data = self._get(f"/games/{game_domain}/mods/{mod_id}.json")
        assert isinstance(data, dict)
        return ModInfo.from_api(data, game_domain)

    def get_files(self, game_domain: str, mod_id: int) -> list[ModFileInfo]:
        data = self._get(f"/games/{game_domain}/mods/{mod_id}/files.json")
        files = data.get("files", []) if isinstance(data, dict) else []
        return [ModFileInfo.from_api(f) for f in files]

    def get_file(self, game_domain: str, mod_id: int, file_id: int) -> ModFileInfo:
        data = self._get(f"/games/{game_domain}/mods/{mod_id}/files/{file_id}.json")
        assert isinstance(data, dict)
        return ModFileInfo.from_api(data)

    # ------------------------------------------------------------------
    def get_download_link(
        self,
        game_domain: str,
        mod_id: int,
        file_id: int,
        key: str | None = None,
        expires: int | None = None,
    ) -> str:
        """Obtiene la URL real de descarga (CDN).

        - Premium: funciona sin key/expires.
        - Gratis: requiere key+expires del enlace nxm://.
        """
        params = {}
        if key and expires:
            params["key"] = key
            params["expires"] = str(expires)

        path = f"/games/{game_domain}/mods/{mod_id}/files/{file_id}/download_link.json"
        try:
            data = self._get(path, params=params or None)
        except NexusApiError as e:
            if e.status in (403, 401) and not (key and expires):
                raise PremiumRequiredError(
                    "Tu cuenta es gratuita: para descargar necesitas pulsar "
                    "'Mod Manager Download' en la web de Nexus (genera un enlace nxm:// "
                    "con la clave temporal). La descarga directa por API es solo para Premium."
                ) from e
            raise

        if not isinstance(data, list) or not data:
            raise NexusApiError("La API no devolvió ningún enlace de descarga.")

        # data: [{name, short_name, URI}, ...]. Preferimos el primero (CDN principal).
        # Si hay un CDN marcado como preferido por el usuario, Nexus lo pone primero.
        return data[0]["URI"]

    # ------------------------------------------------------------------
    def search_md5(self, game_domain: str, md5: str) -> list:
        """Búsqueda por hash MD5 (útil para identificar archivos ya descargados)."""
        data = self._get(f"/games/{game_domain}/mods/md5_search/{md5}.json")
        return data if isinstance(data, list) else []
