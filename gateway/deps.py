"""Authentication for the two kinds of caller: the platform admin and merchants."""
import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select

from .models import ApiKey, Merchant, utcnow
from .security import hash_api_key


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return authorization[7:].strip()


def require_admin(request: Request, authorization: str | None = Header(None)):
    state = request.app.state
    client = request.client.host if request.client else "unknown"
    if not state.admin_limiter.allow(client):
        raise HTTPException(429, "Too many requests")
    if not hmac.compare_digest(_bearer(authorization).encode(), state.settings.admin_token.encode()):
        raise HTTPException(401, "Invalid admin token")


@dataclass
class MerchantContext:
    merchant: Merchant
    mode: str
    key_id: int


def require_merchant(request: Request, authorization: str | None = Header(None), db=Depends(get_db)) -> MerchantContext:
    state = request.app.state
    key_hash = hash_api_key(state.settings.key_pepper, _bearer(authorization))
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    if not key or key.revoked_at:
        raise HTTPException(401, "Invalid API key")
    if key.merchant.status != "active":
        raise HTTPException(403, "Merchant account is suspended")
    if not state.limiter.allow(f"key:{key.id}"):
        raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
    key.last_used_at = utcnow()
    db.commit()
    return MerchantContext(key.merchant, key.mode, key.id)
