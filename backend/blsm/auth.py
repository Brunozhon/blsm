from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import Database

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    salt_encoded = base64.urlsafe_b64encode(salt).decode()
    digest_encoded = base64.urlsafe_b64encode(digest).decode()
    return f"scrypt${salt_encoded}${digest_encoded}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, digest_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass(slots=True)
class SessionManager:
    secret: bytes
    lifetime_seconds: int = 12 * 60 * 60

    def create(
        self,
        subject: str = "admin",
        *,
        lifetime_seconds: int | None = None,
        purpose: str = "session",
    ) -> str:
        lifetime = lifetime_seconds or self.lifetime_seconds
        payload = {
            "sub": subject,
            "iat": int(time.time()),
            "exp": int(time.time()) + lifetime,
            "aud": purpose,
            "nonce": secrets.token_urlsafe(8),
        }
        body = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=")
        signature = hmac.new(self.secret, body, hashlib.sha256).digest()
        return f"{body.decode()}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"

    def verify(self, token: str, *, purpose: str = "session") -> dict[str, object] | None:
        try:
            body_text, signature_text = token.split(".", 1)
            body = body_text.encode()
            expected = hmac.new(self.secret, body, hashlib.sha256).digest()
            supplied = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            if not hmac.compare_digest(expected, supplied):
                return None
            payload = json.loads(
                base64.urlsafe_b64decode(body_text + "=" * (-len(body_text) % 4))
            )
            if int(payload["exp"]) < int(time.time()):
                return None
            if payload.get("aud") != purpose:
                return None
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None


def configure_admin(db: Database, bootstrap_password: str | None) -> None:
    if db.get_setting("admin_password") is None and bootstrap_password:
        if len(bootstrap_password) < 12:
            raise RuntimeError("BLSM_ADMIN_PASSWORD must contain at least 12 characters")
        db.set_setting("admin_password", hash_password(bootstrap_password))


async def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> str:
    manager: SessionManager = request.app.state.sessions
    if credentials is None or manager.verify(credentials.credentials) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return credentials.credentials

