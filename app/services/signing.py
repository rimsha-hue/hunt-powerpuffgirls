"""Small signed, expiring payloads for cookies (HMAC-SHA256 over the app secret)."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict[str, Any], secret: str, ttl_seconds: int, *, purpose: str) -> str:
    """`purpose` is mixed into the MAC so a token minted for one use can't be replayed for another."""
    body = _b64(
        json.dumps(
            {**payload, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")
        ).encode()
    )
    mac = hmac.new(secret.encode(), f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def unsign(token: str, secret: str, *, purpose: str) -> dict[str, Any] | None:
    try:
        body, mac = token.split(".", 1)
        expected = hmac.new(secret.encode(), f"{purpose}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(mac)):
            return None
        payload: dict[str, Any] = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload.get("exp"), int) or payload["exp"] < time.time():
        return None
    return payload
