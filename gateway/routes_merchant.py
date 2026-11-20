"""Merchant API. Every query is limited to the calling merchant and to the key's mode (test or live)."""
import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .deps import MerchantContext, get_db, require_merchant
from .models import Transaction
from .providers import ProviderError
from .schemas import CashInIn, ChangeIn, ChargeIn, RefundIn
from .security import inbound_token
from .service import (apply_outcome, audit, deliver_webhook, new_reference, provider_for, refresh,
                      serialize, to_major, to_minor)

router = APIRouter(prefix="/v1")

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100,
                                       pattern=r"^[A-Za-z0-9_\-:.]+$")]


def _own(db, ctx: MerchantContext, public_id: str) -> Transaction:
    txn = db.scalar(select(Transaction).where(
        Transaction.public_id == public_id, Transaction.merchant_id == ctx.merchant.id,
        Transaction.mode == ctx.mode))
    if not txn:
        raise HTTPException(404, "Transaction not found")
    return txn


def _begin(db, ctx, kind, body, idem_key, **fields):
    """
    Saves the transaction before any money moves, so a crash can never lose track of a request.
    A repeat of the same Idempotency-Key returns the first transaction instead of paying twice.
    """
    request_hash = hashlib.sha256(json.dumps([kind, body.model_dump(mode="json")], sort_keys=True).encode()).hexdigest()
    lookup = select(Transaction).where(Transaction.merchant_id == ctx.merchant.id, Transaction.mode == ctx.mode,
                                       Transaction.idempotency_key == idem_key)
    existing = db.scalar(lookup)
    if not existing:
        txn = Transaction(merchant_id=ctx.merchant.id, mode=ctx.mode, kind=kind, idempotency_key=idem_key,
                          request_hash=request_hash, reference=new_reference(kind), **fields)
        db.add(txn)
        try:
            db.commit()
            return txn, False
        except IntegrityError:  # two identical requests raced; the other one won
            db.rollback()
            existing = db.scalar(lookup)
    if existing.request_hash != request_hash:
        raise HTTPException(409, "This Idempotency-Key was already used with a different request")
    return existing, True


def _finish(db, request, background, response, ctx, txn, call):
    """Runs the EcoCash call and records the result. A network failure leaves the transaction pending."""
    try:
        outcome = call()
        finished = apply_outcome(txn, outcome)
    except ProviderError:
        txn.provider_message = "No answer from EcoCash. Outcome unknown. Fetch this transaction to check it."
        finished = False
    except HTTPException as exc:  # credentials missing, live mode off: nothing was sent
        txn.status, txn.provider_code, txn.provider_message = "failed", "NOT_SENT", str(exc.detail)
        db.commit()
        raise
    audit(db, f"{txn.kind}.{txn.status}", ctx.merchant.id, actor=f"key:{ctx.key_id}", transaction=txn.public_id)
    db.commit()
    if finished:
        background.add_task(deliver_webhook, request.app.state, txn.id)
    response.status_code = 202 if txn.status == "pending" else 201
    return serialize(txn, db)


@router.post("/charges")
def create_charge(body: ChargeIn, request: Request, response: Response, background: BackgroundTasks,
                  idem: IdempotencyKey, ctx: MerchantContext = Depends(require_merchant), db=Depends(get_db)):
    """Asks the customer to approve a payment on their phone. Finishes later; watch for the webhook."""
    txn, replay = _begin(db, ctx, "charge", body, idem, amount_minor=to_minor(body.amount),
                         currency=body.currency, msisdn=body.msisdn)
    if replay:
        return serialize(txn, db)
    state = request.app.state
    notify = f"{state.settings.public_base_url}/webhooks/eip/{txn.public_id}/" \
             f"{inbound_token(state.settings.key_pepper, txn.public_id)}"
    return _finish(db, request, background, response, ctx, txn,
                   lambda: provider_for(db, state, ctx.merchant.id, ctx.mode, "eip").charge(
                       txn.reference, body.msisdn, body.amount, body.currency, body.description, notify))


@router.post("/refunds")
def create_refund(body: RefundIn, request: Request, response: Response, background: BackgroundTasks,
                  idem: IdempotencyKey, ctx: MerchantContext = Depends(require_merchant), db=Depends(get_db)):
    charge = _own(db, ctx, body.transaction_id)
    # Row lock (on Postgres) so two refunds of one charge cannot both pass the remaining-amount check.
    db.execute(select(Transaction.id).where(Transaction.id == charge.id).with_for_update())
    if charge.kind != "charge" or charge.status != "succeeded" or not charge.provider_reference:
        raise HTTPException(422, "Only a succeeded charge can be refunded")
    refunded = db.scalar(select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
        Transaction.parent_id == charge.id, Transaction.status != "failed",
        Transaction.idempotency_key != idem)) or 0
    remaining = charge.amount_minor - refunded
    amount_minor = to_minor(body.amount) if body.amount else remaining
    if amount_minor <= 0 or amount_minor > remaining:
        raise HTTPException(422, f"Refund exceeds what is left on this charge ({to_major(max(remaining, 0)):.2f})")
    txn, replay = _begin(db, ctx, "refund", body, idem, amount_minor=amount_minor, currency=charge.currency,
                         msisdn=charge.msisdn, parent_id=charge.id)
    if replay:
        return serialize(txn, db)
    state = request.app.state
    return _finish(db, request, background, response, ctx, txn,
                   lambda: provider_for(db, state, ctx.merchant.id, ctx.mode, "eip").refund(
                       txn.reference, charge.msisdn, to_major(amount_minor), charge.currency,
                       charge.provider_reference))


def _check_payout_limit(request, ctx, amount):
    limit = ctx.merchant.max_payout_minor or request.app.state.settings.default_max_payout_minor
    if to_minor(amount) > limit:
        raise HTTPException(422, f"Amount is above this account's payout limit ({to_major(limit):.2f})")


@router.post("/payouts/cash-in")
def create_cash_in(body: CashInIn, request: Request, response: Response, background: BackgroundTasks,
                   idem: IdempotencyKey, ctx: MerchantContext = Depends(require_merchant), db=Depends(get_db)):
    """Pays money from the merchant's EcoCash channel into a customer's wallet."""
    _check_payout_limit(request, ctx, body.amount)
    txn, replay = _begin(db, ctx, "cash_in", body, idem, amount_minor=to_minor(body.amount),
                         currency=body.currency, msisdn=body.msisdn)
    if replay:
        return serialize(txn, db)
    state = request.app.state
    return _finish(db, request, background, response, ctx, txn,
                   lambda: provider_for(db, state, ctx.merchant.id, ctx.mode, "epg").cash_in(
                       txn.reference, body.msisdn, body.amount, body.currency, body.beneficiary_reference))


@router.post("/payouts/change")
def create_change(body: ChangeIn, request: Request, response: Response, background: BackgroundTasks,
                  idem: IdempotencyKey, ctx: MerchantContext = Depends(require_merchant), db=Depends(get_db)):
    """Pays a customer their change into their wallet at the till."""
    _check_payout_limit(request, ctx, body.amount)
    txn, replay = _begin(db, ctx, "change", body, idem, amount_minor=to_minor(body.amount),
                         currency=body.currency, msisdn=body.msisdn)
    if replay:
        return serialize(txn, db)
    state = request.app.state
    return _finish(db, request, background, response, ctx, txn,
                   lambda: provider_for(db, state, ctx.merchant.id, ctx.mode, "epg").change(
                       txn.reference, body.msisdn, body.amount, body.currency, body.terminal_id, body.merchant_name))


@router.get("/transactions/{transaction_id}")
def get_transaction(transaction_id: str, request: Request, background: BackgroundTasks,
                    ctx: MerchantContext = Depends(require_merchant), db=Depends(get_db)):
    """Returns a transaction. If it is still pending, EcoCash is asked for the latest status first."""
    txn = _own(db, ctx, transaction_id)
    if txn.status == "pending":
        try:
            if refresh(db, request.app.state, txn):
                background.add_task(deliver_webhook, request.app.state, txn.id)
        except HTTPException:
            pass
    return serialize(txn, db)


@router.get("/transactions")
def list_transactions(limit: int = 50, ctx: MerchantContext = Depends(require_merchant), db=Depends(get_db)):
    rows = db.scalars(select(Transaction).where(
        Transaction.merchant_id == ctx.merchant.id, Transaction.mode == ctx.mode)
        .order_by(Transaction.id.desc()).limit(max(1, min(limit, 200)))).all()
    return {"data": [serialize(t, db) for t in rows]}
