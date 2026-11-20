"""Shared transaction logic: loading a merchant's provider, settling outcomes, merchant webhooks."""
import json
import secrets
import time
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from .models import AuditLog, Merchant, ProviderCredential, Transaction, WebhookDelivery, utcnow
from .providers import EipProvider, EpgProvider, ProviderError
from .security import WebhookUrlError, sign_webhook, validate_webhook_url

# A payout EcoCash has no record of after this long did not go through.
NOT_FOUND_GRACE = timedelta(seconds=60)


def audit(db, action, merchant_id=None, actor="admin", **detail):
    db.add(AuditLog(merchant_id=merchant_id, actor=actor, action=action, detail=json.dumps(detail, default=str)))


def new_reference(kind: str) -> str:
    if kind in ("cash_in", "change"):  # EPG references are numeric
        return f"{int(time.time() * 1000)}{secrets.randbelow(10**6):06d}"
    return f"GW{secrets.token_hex(8).upper()}"


def to_minor(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def to_major(minor: int) -> Decimal:
    return (Decimal(minor) / 100).quantize(Decimal("0.01"))


def serialize(txn: Transaction, db) -> dict:
    parent = db.get(Transaction, txn.parent_id).public_id if txn.parent_id else None
    return {
        "id": txn.public_id, "kind": txn.kind, "status": txn.status, "mode": txn.mode,
        "amount": f"{to_major(txn.amount_minor):.2f}", "currency": txn.currency, "msisdn": txn.msisdn,
        "ecocash_reference": txn.provider_reference, "code": txn.provider_code, "message": txn.provider_message,
        "refund_of": parent,
        "created_at": txn.created_at.isoformat() + "Z", "updated_at": txn.updated_at.isoformat() + "Z",
    }


def provider_for(db, state, merchant_id: int, mode: str, product: str):
    settings = state.settings
    url = {("test", "eip"): settings.eip_sandbox_url, ("test", "epg"): settings.epg_sandbox_url,
           ("live", "eip"): settings.eip_live_url, ("live", "epg"): settings.epg_live_url}[(mode, product)]
    if not url:
        raise HTTPException(503, "Live mode is not enabled on this gateway yet")
    row = db.scalar(select(ProviderCredential).where(
        ProviderCredential.merchant_id == merchant_id, ProviderCredential.mode == mode,
        ProviderCredential.product == product))
    if not row:
        raise HTTPException(409, f"EcoCash {product.upper()} credentials are not set for {mode} mode")
    creds = state.vault.decrypt_json(row.blob_enc)
    cls = EipProvider if product == "eip" else EpgProvider
    return cls(url, creds, state.http, settings.provider_timeout)


def apply_outcome(txn: Transaction, outcome) -> bool:
    """Records what EcoCash said. A finished transaction is never changed again."""
    if txn.status != "pending":
        return False
    txn.provider_code = outcome.code or txn.provider_code
    txn.provider_message = outcome.message or txn.provider_message
    txn.provider_reference = outcome.provider_reference or txn.provider_reference
    if outcome.status in ("succeeded", "failed"):
        txn.status = outcome.status
        return True
    return False


def refresh(db, state, txn: Transaction) -> bool:
    """Asks EcoCash for the truth about a pending transaction. Returns True if it finished."""
    if txn.status != "pending":
        return False
    try:
        if txn.kind in ("charge", "refund"):
            outcome = provider_for(db, state, txn.merchant_id, txn.mode, "eip").status(txn.msisdn, txn.reference)
        else:
            epg = provider_for(db, state, txn.merchant_id, txn.mode, "epg")
            outcome = epg.lookup(new_reference(txn.kind), txn.reference, txn.msisdn, txn.currency)
            if outcome.status == "not_found":
                if utcnow() - txn.created_at < NOT_FOUND_GRACE:
                    return False
                outcome.status, outcome.message = "failed", "EcoCash has no record of this transaction"
    except ProviderError:
        return False
    finished = apply_outcome(txn, outcome)
    db.commit()
    return finished


def deliver_webhook(state, txn_id: int):
    """Tells the merchant a transaction finished. Runs after the response has been sent."""
    with state.session_factory() as db:
        txn = db.get(Transaction, txn_id)
        merchant = db.get(Merchant, txn.merchant_id)
        if not merchant.webhook_url or not merchant.webhook_secret_enc:
            return
        delivery = WebhookDelivery(merchant_id=merchant.id, transaction_id=txn.id)
        db.add(delivery)
        try:
            validate_webhook_url(merchant.webhook_url, state.settings.allow_insecure_webhooks, state.resolver)
            body = json.dumps({"event": "transaction.updated", "data": serialize(txn, db)}).encode()
            secret = state.vault.decrypt_json(merchant.webhook_secret_enc)["secret"]
            response = state.webhook_http.request(
                "POST", merchant.webhook_url, data=body, timeout=5, allow_redirects=False,
                headers={"Content-Type": "application/json",
                         "X-Gateway-Signature": sign_webhook(secret, int(time.time()), body)})
            delivery.status_code = response.status_code
            delivery.ok = int(200 <= response.status_code < 300)
        except WebhookUrlError as exc:
            delivery.error = str(exc)
        except Exception as exc:  # a merchant's broken endpoint must never break a payment
            delivery.error = type(exc).__name__
        db.commit()
