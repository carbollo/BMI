"""OAuth2 (Authorization Code + PKCE) para Nexus Mods.

Diseñado para ejecutarse DENTRO del navegador embebido (QtWebEngine): el usuario
inicia sesión en el webview y, como la página de login/autorización está en
``nexusmods.com``, ese mismo inicio de sesión deja la **sesión web** iniciada en el
perfil del navegador (necesaria para el flujo gratis "Mod Manager Download" → nxm://).
El webview detecta el redirect con ``acceptNavigationRequest`` y llama a
``LoginFlow.complete(redirect_url)``.

Endpoints oficiales (de https://users.nexusmods.com/.well-known/openid-configuration):
  authorize: https://users.nexusmods.com/oauth/authorize
  token:     https://users.nexusmods.com/oauth/token
  userinfo:  https://users.nexusmods.com/oauth/userinfo
  scopes:    public, openid   |   PKCE: S256   |   grants: authorization_code, refresh_token

⚠️ PENDIENTE DE REGISTRO: rellena ``CLIENT_ID`` y ``REDIRECT_URI`` (y ``CLIENT_SECRET``
solo si Nexus lo exige) cuando aprueben la app. Sin esos datos, el flujo lanza
``OAuthNotConfigured``.

Cómo enchufarlo (cuando haya CLIENT_ID):
  1) Botón "Iniciar sesión con Nexus" → ``flow = LoginFlow(); webview.load(flow.authorize_url())``.
  2) En ``WebPage.acceptNavigationRequest``: si ``LoginFlow.is_redirect(url)`` →
     ``token = flow.complete(url); session.set_token(token)`` (y el webview queda logueado en la web).
  3) En ``nexus_api`` / ``nexus_graphql``: usar ``session.auth_header()`` (Bearer) en vez de ``apikey``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import requests

from .config import app_data_dir
from . import __app_name__, __version__
from . import secure_store

# --- Endpoints (autoritativos, del openid-configuration de Nexus) ---
ISSUER = "https://users.nexusmods.com"
AUTHORIZE_ENDPOINT = "https://users.nexusmods.com/oauth/authorize"
TOKEN_ENDPOINT = "https://users.nexusmods.com/oauth/token"
USERINFO_ENDPOINT = "https://users.nexusmods.com/oauth/userinfo"

# --- Config de la app (registrada en Nexus como app pública/nativa) ----------
CLIENT_ID = "bmi"     # Client ID aprobado por Nexus
# App PÚBLICA de escritorio: se autentica SOLO con PKCE, sin secret. NO se debe incrustar
# el Client Secret en un .exe distribuido ni en el repo público (sería extraíble). Se deja
# vacío a propósito; si Nexus exigiera secret, habría que registrar la app como pública.
CLIENT_SECRET = ""
REDIRECT_URI = "http://127.0.0.1/callback"   # el webview embebido intercepta este redirect
SCOPES = ["public", "openid"]   # 'public' = acceso a la API; 'openid' = identidad (userinfo)


class OAuthError(RuntimeError):
    pass


class OAuthNotConfigured(OAuthError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Devuelve ``(code_verifier, code_challenge)`` con el método S256 (RFC 7636)."""
    verifier = _b64url(secrets.token_bytes(32))                      # 43 chars
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _require_config() -> None:
    if not CLIENT_ID or not REDIRECT_URI:
        raise OAuthNotConfigured(
            "OAuth sin configurar: rellena CLIENT_ID y REDIRECT_URI en oauth.py con "
            "los datos del registro de la app en Nexus."
        )


def _client_secret() -> str:
    """Secret del cliente OAuth. Normalmente VACÍO: BMI es una app PÚBLICA y usa solo PKCE.

    ⚠️ NUNCA se incrusta el secret en el código ni en el .exe (un binario distribuido es
    extraíble). Como respaldo — solo si Nexus llegara a exigir secret — se puede aportar
    FUERA del repo: en la variable de entorno ``BMI_NEXUS_OAUTH_SECRET`` o en un archivo
    ``oauth_client_secret.txt`` (en .gitignore) junto al ejecutable o en %APPDATA%/BMI.
    Aun así, en distribución lo correcto es PKCE sin secret.
    """
    if CLIENT_SECRET:
        return CLIENT_SECRET
    env = os.environ.get("BMI_NEXUS_OAUTH_SECRET")
    if env:
        return env.strip()
    bases = []
    if sys.argv and sys.argv[0]:
        try:
            bases.append(Path(sys.argv[0]).resolve().parent)
        except OSError:
            pass
    try:
        bases.append(app_data_dir())
    except Exception:
        pass
    for base in bases:
        f = base / "oauth_client_secret.txt"
        try:
            if f.is_file():
                return f.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return ""


# ---------------------------------------------------------------------------
@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    scope: str = ""
    expires_at: float = 0.0          # epoch absoluto (0 = sin caducidad conocida)

    @classmethod
    def from_response(cls, data: dict) -> "OAuthToken":
        expires_in = float(data.get("expires_in") or 0)
        return cls(
            access_token=data.get("access_token", "") or "",
            refresh_token=data.get("refresh_token", "") or "",
            token_type=data.get("token_type", "Bearer") or "Bearer",
            scope=data.get("scope", "") or "",
            # margen de 60 s para no apurar la caducidad
            expires_at=(time.time() + expires_in - 60) if expires_in else 0.0,
        )

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at

    def authorization_header(self) -> dict:
        return {"Authorization": f"{self.token_type or 'Bearer'} {self.access_token}"}


# ---------------------------------------------------------------------------
class LoginFlow:
    """Un intento de login: genera PKCE + state, da la URL de autorización y, con el
    redirect que captura el webview, lo cambia por el token."""

    def __init__(self):
        _require_config()
        self.verifier, self._challenge = generate_pkce()
        self.state = secrets.token_urlsafe(24)

    def authorize_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(SCOPES),
            "state": self.state,
            "code_challenge": self._challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTHORIZE_ENDPOINT}?{urlencode(params)}"

    @staticmethod
    def is_redirect(url: str) -> bool:
        """True si ``url`` es nuestra ``REDIRECT_URI`` (para que el webview la detecte
        en ``acceptNavigationRequest`` y no la trate como navegación normal)."""
        if not REDIRECT_URI:
            return False
        u, r = urlparse(url), urlparse(REDIRECT_URI)
        return (u.scheme, u.netloc, u.path) == (r.scheme, r.netloc, r.path)

    def parse_code(self, redirect_url: str) -> str:
        """Extrae el ``code`` del redirect validando el ``state``. Lanza ``OAuthError``."""
        q = parse_qs(urlparse(redirect_url).query)
        if "error" in q:
            msg = f"{q.get('error', [''])[0]} {q.get('error_description', [''])[0]}".strip()
            raise OAuthError(f"Autorización denegada: {msg}")
        if q.get("state", [""])[0] != self.state:
            raise OAuthError("El 'state' no coincide (posible CSRF); cancela y reinténtalo.")
        code = q.get("code", [""])[0]
        if not code:
            raise OAuthError("El redirect no traía 'code'.")
        return code

    def complete(self, redirect_url: str) -> OAuthToken:
        """Del redirect capturado al token (authorization_code + PKCE)."""
        return exchange_code(self.parse_code(redirect_url), self.verifier)


# ---------------------------------------------------------------------------
def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Application-Name": __app_name__,
        "Application-Version": __version__,
        "User-Agent": f"{__app_name__}/{__version__}",
    }


def _token_request(payload: dict) -> OAuthToken:
    _require_config()
    secret = _client_secret()
    if secret:
        payload["client_secret"] = secret     # solo si Nexus lo exige (respaldo, fuera del repo)
    resp = requests.post(TOKEN_ENDPOINT, data=payload, headers=_headers(), timeout=30)
    if not resp.ok:
        raise OAuthError(f"El token endpoint devolvió {resp.status_code}: {resp.text[:200]}")
    return OAuthToken.from_response(resp.json())


def exchange_code(code: str, code_verifier: str) -> OAuthToken:
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
    })


def refresh_token(refresh: str) -> OAuthToken:
    tok = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": CLIENT_ID,
    })
    if not tok.refresh_token:        # si el servidor no devuelve uno nuevo, conserva el anterior
        tok.refresh_token = refresh
    return tok


def fetch_userinfo(access_token: str) -> dict:
    """Datos del usuario (scope 'openid'): nombre, etc. Útil para mostrar quién entró."""
    resp = requests.get(
        USERINFO_ENDPOINT,
        headers={**_headers(), "Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not resp.ok:
        raise OAuthError(f"userinfo devolvió {resp.status_code}")
    return resp.json()


# ---------------------------------------------------------------------------
class TokenStore:
    """Persiste el token OAuth CIFRADO en ``%APPDATA%/BMI/oauth_token.dat`` con DPAPI de
    Windows (ligado a la cuenta del usuario; ver ``secure_store``). Nunca se escribe en claro.

    Migra automáticamente el antiguo ``oauth_token.json`` en texto plano: lo lee, lo reescribe
    cifrado y BORRA el archivo en claro. Si DPAPI no está disponible (dev fuera de Windows),
    el token no se persiste (queda solo en memoria) en lugar de guardarse sin cifrar."""

    def __init__(self, path: Path | None = None):
        base = app_data_dir()
        self.path = path or (base / "oauth_token.dat")     # cifrado (DPAPI)
        self._legacy = base / "oauth_token.json"           # plano antiguo (a migrar/borrar)

    def _decode(self, raw: bytes) -> "OAuthToken | None":
        try:
            data = json.loads(raw.decode("utf-8"))
            return OAuthToken(**{k: data[k] for k in data
                                 if k in OAuthToken.__dataclass_fields__})
        except Exception:
            return None

    def load(self) -> OAuthToken | None:
        # 1) Token cifrado (formato actual).
        try:
            if self.path.is_file():
                return self._decode(secure_store.unprotect(self.path.read_bytes()))
        except Exception:
            pass
        # 2) Migración: token antiguo en texto plano -> re-cifrar y borrar el plano.
        try:
            if self._legacy.is_file():
                tok = self._decode(self._legacy.read_bytes())
                if tok is not None:
                    try:
                        self.save(tok)          # reescribe cifrado
                    except Exception:
                        pass
                try:
                    self._legacy.unlink()       # elimina el archivo en claro del disco
                except OSError:
                    pass
                return tok
        except Exception:
            pass
        return None

    def save(self, token: OAuthToken) -> None:
        raw = json.dumps(asdict(token)).encode("utf-8")
        try:
            blob = secure_store.protect(raw)
        except secure_store.SecureStoreUnavailable:
            # Sin DPAPI (dev no-Windows): NO escribir en claro; el token vive solo en memoria.
            return
        self.path.write_bytes(blob)
        # Por si acaso quedara un token plano antiguo, elimínalo.
        try:
            if self._legacy.is_file():
                self._legacy.unlink()
        except OSError:
            pass

    def clear(self) -> None:
        for p in (self.path, self._legacy):
            try:
                p.unlink()
            except OSError:
                pass


class OAuthSession:
    """Sesión viva: carga el token, lo refresca cuando caduca y da la cabecera Authorization."""

    def __init__(self, store: TokenStore | None = None):
        self.store = store or TokenStore()
        self.token: OAuthToken | None = self.store.load()

    @property
    def is_logged_in(self) -> bool:
        return self.token is not None and bool(self.token.access_token)

    def set_token(self, token: OAuthToken) -> None:
        self.token = token
        self.store.save(token)

    def logout(self) -> None:
        self.token = None
        self.store.clear()

    def valid_access_token(self) -> str:
        if not self.token:
            raise OAuthError("No hay sesión OAuth: inicia sesión con Nexus.")
        if self.token.is_expired and self.token.refresh_token:
            self.set_token(refresh_token(self.token.refresh_token))
        return self.token.access_token

    def access_token_or_none(self) -> str | None:
        """Token de acceso vigente (refrescándolo si caducó) o None si no hay sesión.
        Pensado como ``bearer_provider`` para los clientes de API/GraphQL."""
        if not self.is_logged_in:
            return None
        try:
            return self.valid_access_token()
        except OAuthError:
            return None

    def auth_header(self) -> dict:
        self.valid_access_token()
        return self.token.authorization_header()
