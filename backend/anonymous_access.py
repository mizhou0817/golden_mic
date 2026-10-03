from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass


ANONYMOUS_SESSION_COOKIE = "__Host-golden_mic_session"
ANONYMOUS_CSRF_HEADER = "X-CSRF-Token"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


@dataclass(frozen=True)
class AnonymousSession:
    session_id: str
    csrf_token: str
    issued_at: int
    expires_at: int


def issue_anonymous_session(
    secret: str,
    ttl_seconds: int,
    *,
    now: int | None = None,
) -> tuple[str, AnonymousSession]:
    _validate_secret(secret)
    issued_at = int(time.time()) if now is None else now
    session = AnonymousSession(
        session_id=secrets.token_urlsafe(24),
        csrf_token=secrets.token_urlsafe(32),
        issued_at=issued_at,
        expires_at=issued_at + ttl_seconds,
    )
    payload = {
        "v": 1,
        "sid": session.session_id,
        "csrf": session.csrf_token,
        "iat": session.issued_at,
        "exp": session.expires_at,
    }
    encoded_payload = _encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_encode(signature)}", session


def verify_anonymous_session(
    token: str | None,
    secret: str,
    ttl_seconds: int,
    *,
    now: int | None = None,
) -> AnonymousSession | None:
    if not token or len(token) > 4096:
        return None
    _validate_secret(secret)
    try:
        encoded_payload, encoded_signature = token.split(".", maxsplit=1)
        payload_bytes = _decode(encoded_payload, max_length=2048)
        supplied_signature = _decode(encoded_signature, max_length=128)
    except (UnicodeError, ValueError):
        return None
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"v", "sid", "csrf", "iat", "exp"}:
        return None
    session_id = payload.get("sid")
    csrf_token = payload.get("csrf")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    if (
        payload.get("v") != 1
        or not isinstance(session_id, str)
        or not _TOKEN_PATTERN.fullmatch(session_id)
        or not isinstance(csrf_token, str)
        or not _TOKEN_PATTERN.fullmatch(csrf_token)
        or type(issued_at) is not int
        or type(expires_at) is not int
    ):
        return None
    current_time = int(time.time()) if now is None else now
    if issued_at > current_time + 60 or expires_at <= current_time:
        return None
    if expires_at <= issued_at or expires_at - issued_at > ttl_seconds:
        return None
    return AnonymousSession(
        session_id=session_id,
        csrf_token=csrf_token,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def csrf_token_matches(session: AnonymousSession, supplied_token: str | None) -> bool:
    return bool(supplied_token) and hmac.compare_digest(session.csrf_token, supplied_token)


def _validate_secret(secret: str) -> None:
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("匿名会话签名密钥至少需要 32 字节。")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str, *, max_length: int) -> bytes:
    if not value or len(value) > max_length or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url segment")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)