"""Who can call what, what is stored, and what leaks."""
import pytest
from cryptography.fernet import Fernet

from gateway.config import ConfigError, Settings
from gateway.security import Vault, WebhookUrlError, validate_webhook_url
from tests.conftest import ADMIN, PHONE, idem
from tests.fakes import fake_resolver


def test_admin_api_needs_the_admin_token(platform):
    body = {"name": "Shop"}
    assert platform.client.post("/admin/merchants", json=body).status_code == 401
    assert platform.client.post("/admin/merchants", json=body,
                                headers={"Authorization": "Bearer wrong"}).status_code == 401
    merchant = platform.onboard()
    # A merchant key is not an admin token.
    assert platform.client.get(f"/admin/merchants/{merchant.id}", headers=merchant.auth).status_code == 401


def test_merchant_api_needs_a_valid_key(platform):
    assert platform.client.get("/v1/transactions").status_code == 401
    assert platform.client.get("/v1/transactions", headers={"Authorization": "Bearer uh_test_nope"}).status_code == 401
    assert platform.client.get("/v1/transactions", headers=ADMIN).status_code == 401


def test_revoked_key_and_suspended_merchant_are_refused(platform):
    merchant = platform.onboard()
    assert platform.client.get("/v1/transactions", headers=merchant.auth).status_code == 200

    platform.client.patch(f"/admin/merchants/{merchant.id}", headers=ADMIN, json={"status": "suspended"})
    assert platform.client.get("/v1/transactions", headers=merchant.auth).status_code == 403
    platform.client.patch(f"/admin/merchants/{merchant.id}", headers=ADMIN, json={"status": "active"})

    platform.client.delete(f"/admin/merchants/{merchant.id}/api-keys/{merchant.key_id}", headers=ADMIN)
    assert platform.client.get("/v1/transactions", headers=merchant.auth).status_code == 401


def test_no_secret_is_stored_in_plain_text(platform):
    merchant = platform.onboard()
    platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()},
                         json={"msisdn": PHONE, "amount": "1"})
    raw = platform.db_path.read_bytes()
    for secret in (merchant.key, merchant.webhook_secret, merchant.eip["password"], merchant.eip["merchant_pin"],
                   merchant.epg["client_secret"], merchant.epg["api_password_encrypted"]):
        assert secret.encode() not in raw


def test_secrets_are_never_returned_after_creation(platform):
    merchant = platform.onboard()
    view = platform.client.get(f"/admin/merchants/{merchant.id}", headers=ADMIN)
    audit = platform.client.get(f"/admin/merchants/{merchant.id}/audit", headers=ADMIN)
    assert view.json()["credentials_set"] == ["test/eip", "test/epg"]
    for text in (view.text, audit.text):
        for secret in (merchant.key, merchant.webhook_secret, merchant.eip["password"], merchant.epg["client_secret"]):
            assert secret not in text


def test_one_merchant_cannot_see_anothers_transactions(platform):
    a, b = platform.onboard("A"), platform.onboard("B")
    txn = platform.client.post("/v1/payouts/cash-in", headers={**a.auth, **idem()},
                               json={"msisdn": PHONE, "amount": "1"}).json()
    assert platform.client.get(f"/v1/transactions/{txn['id']}", headers=a.auth).status_code == 200
    assert platform.client.get(f"/v1/transactions/{txn['id']}", headers=b.auth).status_code == 404
    assert platform.client.get("/v1/transactions", headers=b.auth).json()["data"] == []
    # B cannot refund A's charge either.
    r = platform.client.post("/v1/refunds", headers={**b.auth, **idem()}, json={"transaction_id": txn["id"]})
    assert r.status_code == 404


def test_each_merchant_uses_only_their_own_ecocash_credentials(platform):
    a, b = platform.onboard("A"), platform.onboard("B")
    for merchant in (a, b):
        platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()},
                             json={"msisdn": PHONE, "amount": "1"})
        platform.client.post("/v1/charges", headers={**merchant.auth, **idem()},
                             json={"msisdn": PHONE, "amount": "5"})
    calls = platform.ecocash.calls
    assert [c["headers"]["X-Client-Id"] for c in calls if c["headers"]] == ["client-1", "client-2"]
    assert [c["auth"][0] for c in calls if c["auth"]] == ["eip-user-1", "eip-user-2"]


def test_test_and_live_data_are_kept_apart(make_platform):
    platform = make_platform(eip_live_url="https://eip.fake/v1/live", epg_live_url="https://epg.fake/serviceRequest")
    merchant = platform.onboard()
    live = platform.client.post(f"/admin/merchants/{merchant.id}/api-keys", headers=ADMIN, json={"mode": "live"}).json()
    live_auth = {"Authorization": f"Bearer {live['api_key']}"}
    txn = platform.client.post("/v1/payouts/cash-in", headers={**merchant.auth, **idem()},
                               json={"msisdn": PHONE, "amount": "1"}).json()
    assert platform.client.get(f"/v1/transactions/{txn['id']}", headers=live_auth).status_code == 404
    # Test credentials are never used for a live call.
    r = platform.client.post("/v1/payouts/cash-in", headers={**live_auth, **idem()}, json={"msisdn": PHONE, "amount": "1"})
    assert r.status_code == 409 and "live mode" in r.json()["detail"]


def test_live_keys_are_refused_until_live_is_enabled(platform):
    merchant = platform.onboard(mode="live", credentials=False)
    r = platform.client.post("/v1/charges", headers={**merchant.auth, **idem()}, json={"msisdn": PHONE, "amount": "5"})
    assert r.status_code == 503
    assert platform.ecocash.calls == []


def test_rate_limit_per_key(make_platform):
    platform = make_platform(rate_limit_per_minute=3)
    a, b = platform.onboard("A"), platform.onboard("B")
    codes = [platform.client.get("/v1/transactions", headers=a.auth).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert platform.client.get("/v1/transactions", headers=b.auth).status_code == 200  # others unaffected


@pytest.mark.parametrize("url", [
    "http://shop.example/hook",            # not https
    "https://localhost/hook",              # this machine
    "https://evil.internal/hook",          # private network
    "https://metadata.internal/latest",    # cloud metadata address
    "https://user:pass@shop.example/hook", # embedded credentials
    "https://does-not-resolve.invalid/hook",
])
def test_unsafe_webhook_urls_are_rejected(platform, url):
    r = platform.client.post("/admin/merchants", headers=ADMIN, json={"name": "Shop", "webhook_url": url})
    assert r.status_code == 422
    with pytest.raises(WebhookUrlError):
        validate_webhook_url(url, resolver=fake_resolver)


def test_master_key_rotation_keeps_old_secrets_readable():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    token = Vault([old]).encrypt_json({"pin": "1234"})
    assert Vault([new, old]).decrypt_json(token) == {"pin": "1234"}
    with pytest.raises(RuntimeError):
        Vault([new]).decrypt_json(token)


def test_app_refuses_weak_or_missing_secrets():
    good = dict(master_keys=(Fernet.generate_key().decode(),), key_pepper="p" * 40, admin_token="a" * 40)
    Settings(**good).validate()
    for bad in ({"master_keys": ()}, {"key_pepper": "short"}, {"admin_token": "short"},
                {"env": "production", "public_base_url": "http://gw.example"},
                {"env": "production", "public_base_url": "https://gw.example", "allow_insecure_webhooks": True}):
        with pytest.raises(ConfigError):
            Settings(**{**good, **bad}).validate()


def test_errors_do_not_leak_internals(platform):
    merchant = platform.onboard()
    platform.ecocash.request = lambda *a, **k: (_ for _ in ()).throw(ValueError("secret detail"))
    client = type(platform.client)(platform.app, raise_server_exceptions=False)
    r = client.post("/v1/charges", headers={**merchant.auth, **idem()}, json={"msisdn": PHONE, "amount": "5"})
    assert r.status_code == 500 and r.json() == {"detail": "Internal error"}
    assert r.headers["X-Content-Type-Options"] == "nosniff"
