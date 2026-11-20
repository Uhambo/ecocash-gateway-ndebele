"""
Runs the real gateway against the real EcoCash sandbox, using the sandbox credentials in .env.

It starts the gateway in-process with a throwaway database and throwaway secrets, onboards one
merchant through the admin API, then pays through the merchant API exactly as a customer would.

    python sandbox_check.py            # payouts (cash-in, change), retry safety, tenant isolation
    python sandbox_check.py --charge   # also sends a USSD prompt to EPG_TEST_MSISDN; enter PIN 0000
"""
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Settings

load_dotenv()
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + str(detail) if detail else ''}")


def main():
    phone = os.environ["EPG_TEST_MSISDN"]
    admin_token = secrets.token_urlsafe(48)
    admin = {"Authorization": f"Bearer {admin_token}"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = Settings(master_keys=(Fernet.generate_key().decode(),), key_pepper=secrets.token_urlsafe(48),
                            admin_token=admin_token, database_url=f"sqlite:///{(Path(tmp) / 'check.db').as_posix()}",
                            public_base_url="https://gateway.example")
        client = TestClient(create_app(settings))

        def onboard(name):
            merchant = client.post("/admin/merchants", headers=admin, json={"name": name}).json()
            key = client.post(f"/admin/merchants/{merchant['id']}/api-keys", headers=admin, json={"mode": "test"}).json()
            return merchant["id"], {"Authorization": f"Bearer {key['api_key']}"}

        def idem():
            return {"Idempotency-Key": f"check-{secrets.token_hex(8)}"}

        merchant_id, auth = onboard("Sandbox merchant")
        r = client.put(f"/admin/merchants/{merchant_id}/credentials/test/epg", headers=admin, json={
            "client_id": os.environ["EPG_CLIENT_ID"], "client_secret": os.environ["EPG_CLIENT_SECRET"],
            "api_username": os.getenv("EPG_API_USERNAME", "epg-sandbox-user-v1"),
            "api_password_encrypted": os.environ["EPG_API_PASSWORD_ENCRYPTED"]})
        check("Store EPG credentials", r.status_code == 204, r.status_code)

        headers = {**auth, **idem()}
        r = client.post("/v1/payouts/cash-in", headers=headers, json={"msisdn": phone, "amount": "1"})
        cash = r.json()
        check("Cash-in through the gateway", r.status_code == 201 and cash.get("status") == "succeeded",
              f"{cash.get('status')} {cash.get('ecocash_reference') or cash.get('code')}")

        r = client.post("/v1/payouts/cash-in", headers=headers, json={"msisdn": phone, "amount": "1"})
        check("Same request again returns the same payout, not a new one",
              r.status_code == 200 and r.json().get("id") == cash.get("id"))

        r = client.post("/v1/payouts/change", headers={**auth, **idem()}, json={
            "msisdn": phone, "amount": "2.50", "terminal_id": "Till 1", "merchant_name": "Uhambo Test"})
        check("Change disbursement with a decimal amount", r.json().get("status") == "succeeded",
              f"{r.json().get('status')} {r.json().get('ecocash_reference') or r.json().get('code')}")

        r = client.post("/v1/payouts/cash-in", headers={**auth, **idem()}, json={"msisdn": "263770000001", "amount": "1"})
        check("Unregistered number fails with EcoCash's code", r.json().get("code") == "IC50103", r.json().get("code"))

        other_id, other_auth = onboard("Second merchant")
        r = client.get(f"/v1/transactions/{cash['id']}", headers=other_auth)
        check("Second merchant cannot see the first merchant's payout", r.status_code == 404, r.status_code)
        r = client.post("/v1/payouts/cash-in", headers={**other_auth, **idem()}, json={"msisdn": phone, "amount": "1"})
        check("Second merchant without credentials cannot pay", r.status_code == 409, r.status_code)

        if "--charge" in sys.argv:
            r = client.put(f"/admin/merchants/{merchant_id}/credentials/test/eip", headers=admin, json={
                "username": os.environ["ECOCASH_USERNAME"], "password": os.environ["ECOCASH_PASSWORD"],
                "merchant_code": os.environ["ECOCASH_MERCHANT_CODE"], "merchant_pin": os.environ["ECOCASH_MERCHANT_PIN"],
                "merchant_number": os.environ["ECOCASH_MERCHANT_NUMBER"]})
            check("Store EIP credentials", r.status_code == 204, r.status_code)
            r = client.post("/v1/charges", headers={**auth, **idem()}, json={"msisdn": phone, "amount": "10"})
            txn = r.json()
            check("Charge accepted and waiting for the customer", r.status_code == 202, f"{r.status_code} {txn}")
            if r.status_code == 202:
                print(f"    Enter PIN 0000 on {phone} now...")
                for _ in range(18):
                    time.sleep(5)
                    txn = client.get(f"/v1/transactions/{txn['id']}", headers=auth).json()
                    if txn["status"] != "pending":
                        break
                check("Charge succeeded after PIN entry", txn["status"] == "succeeded",
                      f"{txn['status']} ref={txn.get('ecocash_reference')} code={txn.get('code')} "
                      f"message={txn.get('message')}")
                if txn["status"] == "succeeded":
                    r = client.post("/v1/refunds", headers={**auth, **idem()}, json={"transaction_id": txn["id"]})
                    check("Refund of that charge", r.json().get("status") == "succeeded",
                          f"{r.json().get('status')} {r.json().get('code')} {r.json().get('message')}")

        client.app.state.session_factory.kw["bind"].dispose()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
