"""
Callbacks from EcoCash.

The URL carries a secret token tied to one transaction. Even with a valid token, the posted body is
never trusted: the gateway asks EcoCash for the real status, so a forged callback cannot mark a payment paid.
"""
import hmac

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select

from .deps import get_db
from .models import Transaction
from .security import inbound_token
from .service import deliver_webhook, refresh

router = APIRouter()


@router.post("/webhooks/eip/{transaction_id}/{token}")
def eip_callback(transaction_id: str, token: str, request: Request, background: BackgroundTasks, db=Depends(get_db)):
    state = request.app.state
    expected = inbound_token(state.settings.key_pepper, transaction_id)
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(404, "Not found")
    txn = db.scalar(select(Transaction).where(Transaction.public_id == transaction_id))
    if not txn:
        raise HTTPException(404, "Not found")
    try:
        if refresh(db, state, txn):
            background.add_task(deliver_webhook, state, txn.id)
    except HTTPException:
        pass
    return {"status": "received"}
