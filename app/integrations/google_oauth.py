"""Google sign-in: OAuth 2.0 authorization code flow with PKCE, state and nonce.

The ID token is taken from Google's token endpoint over a direct TLS connection, so its
issuer is authenticated by TLS and its signature need not be re-checked (OpenID Connect Core
3.1.3.7, rule 6). Its audience, issuer, expiry, nonce and email verification are always checked.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

logger = structlog.get_logger()

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CLOCK_SKEW_SECONDS = 60


class GoogleAuthError(Exception):
    """Sign-in failed. Details are logged by type only; users see a generic message."""


@dataclass(frozen=True)
class FlowSecrets:
    state: str
    verifier: str
    nonce: str

    @property
    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str


def new_flow() -> FlowSecrets:
    return FlowSecrets(
        state=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(64),
        nonce=secrets.token_urlsafe(32),
    )


def authorize_url(client_id: str, redirect_uri: str, flow: FlowSecrets) -> str:
    return (
        AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid email profile",
                "state": flow.state,
                "nonce": flow.nonce,
                "code_challenge": flow.challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
    )


def _claims(id_token: str) -> dict[str, Any]:
    try:
        payload = id_token.split(".")[1]
        claims: dict[str, Any] = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except (IndexError, ValueError) as exc:
        raise GoogleAuthError("malformed id_token") from exc
    return claims


def verify_id_token(
    id_token: str, *, client_id: str, nonce: str, now: float | None = None
) -> GoogleIdentity:
    claims = _claims(id_token)
    now = time.time() if now is None else now
    if claims.get("iss") not in ISSUERS:
        raise GoogleAuthError("wrong issuer")
    aud = claims.get("aud")
    if aud != client_id and not (isinstance(aud, list) and client_id in aud):
        raise GoogleAuthError("wrong audience")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] + CLOCK_SKEW_SECONDS < now:
        raise GoogleAuthError("expired")
    if claims.get("nonce") != nonce:
        raise GoogleAuthError("nonce mismatch")
    if claims.get("email_verified") is not True or not claims.get("email") or not claims.get("sub"):
        raise GoogleAuthError("email not verified")
    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=str(claims["email"]).lower(),
        name=str(claims.get("name") or ""),
    )


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


async def exchange_code(
    http: httpx.AsyncClient,
    *,
    code: str,
    verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> str:
    """Trade the authorization code for an ID token."""
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=3),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                response = await http.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "code_verifier": verifier,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": redirect_uri,
                    },
                    timeout=10.0,
                )
                response.raise_for_status()
                token = response.json().get("id_token")
                if not isinstance(token, str):
                    raise GoogleAuthError("no id_token in response")
                return token
    except httpx.HTTPError as exc:
        raise GoogleAuthError(f"token exchange failed: {type(exc).__name__}") from exc
    raise GoogleAuthError("unreachable")  # pragma: no cover
