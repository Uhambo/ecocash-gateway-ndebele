"""Money movement: charges, callbacks, refunds, payouts, retries and timeouts."""
import json

import pytest

from gateway.security import inbound_token, verify_webhook
from tests.conftest import PHONE, idem


def charge(platform, merchant, amount="10.00", **extra):
    r = platform.client.post("/v1/charges", headers={**merchant.auth, **idem()},
                             json={"msisdn": PHONE, "amount": amount, **extra})
    assert r.status_code == 202, r.text
    return r.json()


def callback_url(platform, txn_id):
    return f"/webhooks/eip/{txn_id}/{inbound_token(platform.settings.key_pepper, txn_id)}"


def correlator(platform):
    return list(platform.ecocash.eip)[-1]


def paid_charge(platform, merchant, amount="10.00"):
    txn = charge(platform, merchant, amount)
    platform.ecocash.settle(correlator(platform), "COMPLETED")
    assert platform.client.post(callback_url(platform, txn["id"]), json={}).status_code == 200
    return platform.client.get(f"/v1/transactions/{txn['id']}", headers=merchant.auth).json()


# ---- charges and callbacks ----

def test_charge_completes_through_the_ecocash_callback(platform):
    merchant = platform.onboard()
    txn = charge(platform, merchant)
    assert txn["status"] == "pending" and txn["amount"] == "10.00"
    # EcoCash was told to call back on this gateway, at a URL only it knows.
    sent = platform.ecocash.calls[-1]["json"]
    assert sent["notifyUrl"] == "https://gateway.example" + callback_url(platform, txn["id"])
    assert sent["paymentAmount"]["charginginformation"]["amount"] == "10.00"

    platform.ecocash.settle(correlator(platform), "COMPLETED")
    platform.client.post(callback_url(platform, txn["id"]), json={"transactionOperationStatus": "COMPLETED"})
    final = platform.client.get(f"/v1/transactions/{txn['id']}", headers=merchant.auth).json()
    assert final["status"] == "succeeded" and final["ecocash_reference"].startswith("MP.")


def test_merchant_gets_a_signed_webhook_when_a_charge_finishes(platform):
    merchant = platform.onboard()
    final = paid_charge(platform, merchant)
    [delivery] = platform.merchant_server.received
    assert delivery["url"] == "https://shop.example/hook"
    event = json.loads(delivery["body"])
    assert event["event"] == "transaction.updated" and event["data"] == final
    signature = delivery["headers"]["X-Gateway-Signature"]
    assert verify_webhook(merchant.webhook_secret, signature, delivery["body"])
    assert not verify_webhook("whsec_wrong", signature, delivery["body"])
    assert not verify_webhook(merchant.webhook_secret, signature, delivery["body"] + b" ")


def test_forged_callback_cannot_mark_a_charge_paid(platform):
    merchant = platform.onboard()
    txn = charge(platform, merchant)
    forged = {"transactionOperationStatus": "COMPLETED", "ecocashReference": "MP.FAKE"}
    # Wrong token: rejected outright.
    assert platform.client.post(f"/webhooks/eip/{txn['id']}/{'0' * 40}", json=forged).status_code == 404
    # Right token but a lying body: EcoCash still says pending, so it stays pending.
    assert platform.client.post(callback_url(platform, txn["id"]), json=forged).status_code == 200
    assert platform.client.get(f"/v1/transactions/{txn['id']}", headers=merchant.auth).json()["status"] == "pending"
    assert platform.merchant_server.received == []


def test_declined_charge_is_marked_failed(platform):
    merchant = platform.onboard()
    txn = charge(platform, merchant)
    platform.ecocash.settle(correlator(platform), "FAILED")
    final = platform.client.get(f"/v1/transactions/{txn['id']}", headers=merchant.auth).json()
    assert final["status"] == "failed"
    assert len(platform.merchant_server.received) == 1


def test_finished_transaction_never_changes_again(platform):
    merchant = platform.onboard()
    final = paid_charge(platform, merchant)
    platform.ecocash.settle(correlator(platform), "FAILED")
    platform.client.post(callback_url(platform, final["id"]), json={})
    assert platform.client.get(f"/v1/transactions/{final['id']}", headers=merchant.auth).json()["status"] == "succeeded"
    assert len(platform.merchant_server.received) == 1


def test_wrong_ecocash_credentials_fail_the_charge(platform):
    merchant = platform.onboard()
    platform.ecocash.eip_accounts.clear()
    r = platform.client.post("/v1/charges", headers={**merchant.auth, **idem()}, json={"msisdn": PHONE, "amount": "5"})
    assert r.status_code == 201 and r.json()["status"] == "failed" and r.json()["code"] == "HTTP_401"


def test_missing_credentials_are_reported_and_nothing_is_sent(platform):
    merchant = platform.onboard(credentials=False)
    r = platform.client.post("/v1/charges", headers={**merchant.auth, **idem()}, json={"msisdn": PHONE, "amount": "5"})
    assert r.status_code == 409
    assert platform.ecocash.calls == []


# ---- idempotency ----

def test_repeating_a_request_does_not_pay_twice(platform):
    merchant = platform.onboard()
    headers, body = {**merchant.auth, **idem()}, {"msisdn": PHONE, "amount": "3"}
    first = platform.client.post("/v1/payouts/cash-in", headers=headers, json=body)
    second = platform.client.post("/v1/payouts/cash-in", headers=headers, json=body)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(platform.ecocash.money_calls()) == 1


def test_reusing_a_key_for_a_different_request_is_refused(platform):
    merchant = platform.onboard()
    headers = {**merchant.auth, **idem()}
    platform.client.post("/v1/payouts/cash-in", headers=headers, json={"msisdn": PHONE, "amount": "3"})
    r = platform.client.post("/v1/payouts/cash-in", headers=headers, json={"msisdn": PHONE, "amount": "300"})
    assert r.status_code == 409
    assert len(platform.ecocash.money_calls()) == 1


def test_idempotency_key_is_required(platform):
    merchant = platform.onboard()
    for path, body in (("/v1/charges", {"msisdn": PHONE, "amount": "5"}),
                       ("/v1/payouts/cash-in", {"msisdn": PHONE, "amount": "5"})):
        assert platform.client.post(path, headers=merchant.auth, json=body).status_code == 422
    assert platform.ecocash.calls == []


def test_same_key_from_two_merchants_does_not_collide(platform):
    a, b = platform.onboard("A"), platform.onboard("B")
    key = idem()
    ra = platform.client.post("/v1/payouts/cash-in", headers={**a.auth, **key}, json={"msisdn": PHONE, "amount": "1"})
    rb = platform.client.post("/v1/payouts/cash-in", headers={**b.auth, **key}, json={"msisdn": PHONE, "amount": "1"})
    assert ra.json()["id"] != rb.json()["id"] and len(platform.ecocash.money_calls()) == 2


# ---- payouts ----

def test_cash_in_and_change_succeed(platform):
    merchant = platform.onboard()
    cash = platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()},
                                json={"msisdn": "0770000000", "amount": 1})
    assert cash.status_code == 201 and cash.json()["status"] == "succeeded"
    assert cash.json()["msisdn"] == PHONE  # local format was normalised
    sent = platform.ecocash.calls[-1]["json"]
    assert sent["channelCode"] == "700002" and sent["amount"] == "1" and sent["password"] == "enc-1"

    change = platform.client.post("/v1/payouts/change", headers={**merchant.auth, **idem()},
                                  json={"msisdn": PHONE, "amount": "2.50", "terminal_id": "Till 4",
                                        "merchant_name": "Corner Shop"})
    assert change.status_code == 201 and change.json()["status"] == "succeeded"
    sent = platform.ecocash.calls[-1]["json"]
    assert sent["channelCode"] == "800002" and sent["amount"] == "2.50" and sent["receiverNumber"] == PHONE
    assert len(platform.merchant_server.received) == 2


def test_payout_to_unregistered_number_fails_with_the_ecocash_code(platform):
    merchant = platform.onboard()
    r = platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()},
                             json={"msisdn": "263771111111", "amount": "1"})
    assert r.status_code == 201 and r.json()["status"] == "failed" and r.json()["code"] == "IC50103"


def test_payout_above_the_limit_is_refused_before_ecocash_is_called(platform):
    from tests.conftest import ADMIN
    merchant = platform.onboard()
    body = {"msisdn": PHONE, "amount": "500.01"}
    assert platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()}, json=body).status_code == 422
    platform.client.patch(f"/admin/merchants/{merchant.id}", headers=ADMIN, json={"max_payout": "20"})
    body = {"msisdn": PHONE, "amount": "20.01"}
    assert platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()}, json=body).status_code == 422
    assert platform.ecocash.calls == []


def test_lost_response_is_settled_by_lookup_not_by_paying_again(platform):
    """EcoCash paid the customer but the reply never arrived. The gateway must not send the money twice."""
    merchant = platform.onboard()
    platform.ecocash.timeout_after = True
    headers = {**merchant.auth, **idem()}
    r = platform.client.post("/v1/payouts/cash-in", headers=headers, json={"msisdn": PHONE, "amount": "7"})
    assert r.status_code == 202 and r.json()["status"] == "pending"
    platform.ecocash.timeout_after = False

    # The merchant retries with the same key: no second payout.
    again = platform.client.post("/v1/payouts/cash-in", headers=headers, json={"msisdn": PHONE, "amount": "7"})
    assert again.json()["id"] == r.json()["id"]
    final = platform.client.get(f"/v1/transactions/{r.json()['id']}", headers=merchant.auth).json()
    assert final["status"] == "succeeded" and final["ecocash_reference"].startswith("CI.")
    assert len(platform.ecocash.money_calls()) == 1


def test_payout_that_never_reached_ecocash_stays_pending_then_fails(platform, monkeypatch):
    from datetime import timedelta

    from gateway import service
    merchant = platform.onboard()
    platform.ecocash.timeout_before = True
    r = platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()},
                             json={"msisdn": PHONE, "amount": "7"})
    platform.ecocash.timeout_before = False
    url = f"/v1/transactions/{r.json()['id']}"
    # Too soon to be sure, so it stays pending.
    assert platform.client.get(url, headers=merchant.auth).json()["status"] == "pending"
    monkeypatch.setattr(service, "NOT_FOUND_GRACE", timedelta(0))
    final = platform.client.get(url, headers=merchant.auth).json()
    assert final["status"] == "failed" and "no record" in final["message"]


# ---- refunds ----

def test_refund_full_then_nothing_left(platform):
    merchant = platform.onboard()
    paid = paid_charge(platform, merchant, "10.00")
    r = platform.client.post("/v1/refunds", headers={**merchant.auth, **idem()}, json={"transaction_id": paid["id"]})
    assert r.status_code == 201 and r.json()["status"] == "succeeded"
    assert r.json()["amount"] == "10.00" and r.json()["refund_of"] == paid["id"]
    assert platform.ecocash.calls[-1]["json"]["originalEcocashReference"] == paid["ecocash_reference"]
    again = platform.client.post("/v1/refunds", headers={**merchant.auth, **idem()}, json={"transaction_id": paid["id"]})
    assert again.status_code == 422


def test_partial_refunds_cannot_exceed_the_charge(platform):
    merchant = platform.onboard()
    paid = paid_charge(platform, merchant, "10.00")

    def refund(amount):
        return platform.client.post("/v1/refunds", headers={**merchant.auth, **idem()},
                                    json={"transaction_id": paid["id"], "amount": amount})
    assert refund("6.00").status_code == 201
    assert refund("4.01").status_code == 422
    assert refund("4.00").status_code == 201
    assert refund("0.01").status_code == 422


def test_unpaid_charge_and_payouts_cannot_be_refunded(platform):
    merchant = platform.onboard()
    pending = charge(platform, merchant)
    payout = platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()},
                                  json={"msisdn": PHONE, "amount": "1"}).json()
    for txn in (pending, payout):
        r = platform.client.post("/v1/refunds", headers={**merchant.auth, **idem()}, json={"transaction_id": txn["id"]})
        assert r.status_code == 422


# ---- input checks ----

@pytest.mark.parametrize("body", [
    {"msisdn": PHONE, "amount": "0"},
    {"msisdn": PHONE, "amount": "-5"},
    {"msisdn": PHONE, "amount": "1.001"},
    {"msisdn": PHONE, "amount": "1000000.01"},
    {"msisdn": PHONE, "amount": "abc"},
    {"msisdn": PHONE, "amount": "5", "currency": "EUR"},
    {"msisdn": "263712345678", "amount": "5"},       # not an Econet number
    {"msisdn": "12345", "amount": "5"},
    {"msisdn": PHONE, "amount": "5", "merchant_id": "mer_other"},  # unknown field
    {"msisdn": PHONE},
])
def test_bad_input_is_rejected_before_ecocash_is_called(platform, body):
    merchant = platform.onboard()
    for path in ("/v1/charges", "/v1/payouts/cash-in"):
        assert platform.client.post(path, headers={**merchant.auth, **idem()}, json=body).status_code == 422
    assert platform.ecocash.calls == []
