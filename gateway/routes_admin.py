"""Platform admin API: onboard merchants, store their EcoCash credentials, issue API keys."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from .deps import get_db, require_admin
from .models import ApiKey, AuditLog, Merchant, ProviderCredential, WebhookDelivery, utcnow
from .schemas import ApiKeyIn, EipCredentials, EpgCredentials, MerchantIn, MerchantPatch
from .security import WebhookUrlError, hash_api_key, new_api_key, validate_webhook_url
from .service import audit, to_major, to_minor

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _merchant(db, merchant_id: str) -> Merchant:
    merchant = db.scalar(select(Merchant).where(Merchant.public_id == merchant_id))
    if not merchant:
        raise HTTPException(404, "Merchant not found")
    return merchant


def _check_url(request: Request, url: str):
    state = request.app.state
    try:
        validate_webhook_url(url, state.settings.allow_insecure_webhooks, state.resolver)
    except WebhookUrlError as exc:
        raise HTTPException(422, str(exc))


def _view(db, merchant: Merchant) -> dict:
    creds = db.scalars(select(ProviderCredential).where(ProviderCredential.merchant_id == merchant.id)).all()
    keys = db.scalars(select(ApiKey).where(ApiKey.merchant_id == merchant.id)).all()
    return {
        "id": merchant.public_id, "name": merchant.name, "status": merchant.status,
        "webhook_url": merchant.webhook_url,
        "max_payout": f"{to_major(merchant.max_payout_minor):.2f}" if merchant.max_payout_minor else None,
        "credentials_set": sorted(f"{c.mode}/{c.product}" for c in creds),
        "api_keys": [{"id": k.id, "mode": k.mode, "prefix": k.prefix, "label": k.label,
                      "revoked": bool(k.revoked_at),
                      "last_used_at": k.last_used_at.isoformat() + "Z" if k.last_used_at else None} for k in keys],
    }


@router.post("/merchants", status_code=201)
def create_merchant(body: MerchantIn, request: Request, db=Depends(get_db)):
    if body.webhook_url:
        _check_url(request, body.webhook_url)
    webhook_secret = "whsec_" + secrets.token_urlsafe(32)
    merchant = Merchant(
        name=body.name, webhook_url=body.webhook_url,
        webhook_secret_enc=request.app.state.vault.encrypt_json({"secret": webhook_secret}),
        max_payout_minor=to_minor(body.max_payout) if body.max_payout else None)
    db.add(merchant)
    db.flush()
    audit(db, "merchant.created", merchant.id, name=body.name)
    db.commit()
    # The webhook secret is shown once, here. It cannot be read back later.
    return {**_view(db, merchant), "webhook_secret": webhook_secret}


@router.get("/merchants/{merchant_id}")
def get_merchant(merchant_id: str, db=Depends(get_db)):
    return _view(db, _merchant(db, merchant_id))


@router.patch("/merchants/{merchant_id}")
def update_merchant(merchant_id: str, body: MerchantPatch, request: Request, db=Depends(get_db)):
    merchant = _merchant(db, merchant_id)
    changes = body.model_dump(exclude_unset=True)
    if "webhook_url" in changes:
        if body.webhook_url:
            _check_url(request, body.webhook_url)
        merchant.webhook_url = body.webhook_url
    if "max_payout" in changes:
        merchant.max_payout_minor = to_minor(body.max_payout) if body.max_payout else None
    if body.status:
        merchant.status = body.status
    audit(db, "merchant.updated", merchant.id, fields=sorted(changes))
    db.commit()
    return _view(db, merchant)


@router.put("/merchants/{merchant_id}/credentials/{mode}/eip", status_code=204)
def set_eip_credentials(merchant_id: str, mode: str, body: EipCredentials, request: Request, db=Depends(get_db)):
    _store_credentials(db, request, merchant_id, mode, "eip", body)


@router.put("/merchants/{merchant_id}/credentials/{mode}/epg", status_code=204)
def set_epg_credentials(merchant_id: str, mode: str, body: EpgCredentials, request: Request, db=Depends(get_db)):
    _store_credentials(db, request, merchant_id, mode, "epg", body)


def _store_credentials(db, request, merchant_id, mode, product, body):
    if mode not in ("test", "live"):
        raise HTTPException(404, "Mode must be test or live")
    merchant = _merchant(db, merchant_id)
    blob = request.app.state.vault.encrypt_json(body.model_dump(exclude_none=True))
    row = db.scalar(select(ProviderCredential).where(
        ProviderCredential.merchant_id == merchant.id, ProviderCredential.mode == mode,
        ProviderCredential.product == product))
    if row:
        row.blob_enc = blob
    else:
        db.add(ProviderCredential(merchant_id=merchant.id, mode=mode, product=product, blob_enc=blob))
    audit(db, "credentials.set", merchant.id, mode=mode, product=product)  # never the values
    db.commit()


@router.delete("/merchants/{merchant_id}/credentials/{mode}/{product}", status_code=204)
def delete_credentials(merchant_id: str, mode: str, product: str, db=Depends(get_db)):
    merchant = _merchant(db, merchant_id)
    row = db.scalar(select(ProviderCredential).where(
        ProviderCredential.merchant_id == merchant.id, ProviderCredential.mode == mode,
        ProviderCredential.product == product))
    if not row:
        raise HTTPException(404, "No such credentials")
    db.delete(row)
    audit(db, "credentials.deleted", merchant.id, mode=mode, product=product)
    db.commit()


@router.post("/merchants/{merchant_id}/api-keys", status_code=201)
def create_api_key(merchant_id: str, body: ApiKeyIn, request: Request, db=Depends(get_db)):
    merchant = _merchant(db, merchant_id)
    key = new_api_key(body.mode)
    row = ApiKey(merchant_id=merchant.id, mode=body.mode, prefix=key[:14], label=body.label,
                 key_hash=hash_api_key(request.app.state.settings.key_pepper, key))
    db.add(row)
    db.flush()
    audit(db, "api_key.created", merchant.id, key_id=row.id, mode=body.mode)
    db.commit()
    # The key is shown once, here. Only its hash is stored.
    return {"id": row.id, "mode": row.mode, "prefix": row.prefix, "api_key": key}


@router.delete("/merchants/{merchant_id}/api-keys/{key_id}", status_code=204)
def revoke_api_key(merchant_id: str, key_id: int, db=Depends(get_db)):
    merchant = _merchant(db, merchant_id)
    row = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.merchant_id == merchant.id))
    if not row:
        raise HTTPException(404, "API key not found")
    row.revoked_at = row.revoked_at or utcnow()
    audit(db, "api_key.revoked", merchant.id, key_id=row.id)
    db.commit()


@router.get("/merchants/{merchant_id}/audit")
def merchant_audit(merchant_id: str, db=Depends(get_db)):
    merchant = _merchant(db, merchant_id)
    rows = db.scalars(select(AuditLog).where(AuditLog.merchant_id == merchant.id)
                      .order_by(AuditLog.id.desc()).limit(200)).all()
    return [{"at": r.created_at.isoformat() + "Z", "actor": r.actor, "action": r.action, "detail": r.detail}
            for r in rows]


@router.get("/merchants/{merchant_id}/webhook-deliveries")
def webhook_deliveries(merchant_id: str, db=Depends(get_db)):
    merchant = _merchant(db, merchant_id)
    rows = db.scalars(select(WebhookDelivery).where(WebhookDelivery.merchant_id == merchant.id)
                      .order_by(WebhookDelivery.id.desc()).limit(200)).all()
    return [{"at": r.created_at.isoformat() + "Z", "ok": bool(r.ok), "status_code": r.status_code, "error": r.error}
            for r in rows]
