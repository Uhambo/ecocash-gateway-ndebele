import itertools
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.app import create_app  # noqa: E402
from gateway.config import Settings  # noqa: E402
from tests.fakes import EIP, EPG, FakeEcoCash, FakeMerchantServer, fake_resolver  # noqa: E402

ADMIN_TOKEN = "admin-token-for-tests-" + "a" * 32
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
PHONE = "263770000000"
_keys = itertools.count(1)


def idem():
    return {"Idempotency-Key": f"test-key-{next(_keys):06d}"}


class Platform:
    """One running gateway plus the fakes around it."""

    def __init__(self, tmp_path, **overrides):
        self.db_path = tmp_path / "gateway.db"
        self.ecocash = FakeEcoCash()
        self.merchant_server = FakeMerchantServer()
        self.settings = Settings(**{**dict(
            master_keys=(Fernet.generate_key().decode(),), key_pepper="pepper-" + "p" * 40,
            admin_token=ADMIN_TOKEN, database_url=f"sqlite:///{self.db_path.as_posix()}",
            public_base_url="https://gateway.example", eip_sandbox_url=EIP, epg_sandbox_url=EPG,
        ), **overrides})
        self.app = create_app(self.settings, http=self.ecocash, webhook_http=self.merchant_server,
                              resolver=fake_resolver)
        self.client = TestClient(self.app)
        self._n = 0

    def onboard(self, name="Shop", webhook_url="https://shop.example/hook", mode="test", credentials=True):
        """Creates a merchant with their own EcoCash accounts and returns a small handle."""
        self._n += 1
        n = self._n
        created = self.client.post("/admin/merchants", headers=ADMIN,
                                   json={"name": name, "webhook_url": webhook_url}).json()
        merchant_id = created["id"]
        eip = {"username": f"eip-user-{n}", "password": f"eip-pass-SECRET-{n}", "merchant_code": f"code{n}",
               "merchant_pin": f"pin-SECRET-{n}", "merchant_number": f"26377000000{n}"}
        epg = {"client_id": f"client-{n}", "client_secret": f"epg-SECRET-{n}", "api_password_encrypted": f"enc-{n}"}
        if credentials:
            self.ecocash.eip_accounts[eip["username"]] = eip["password"]
            self.ecocash.epg_clients[epg["client_id"]] = epg["client_secret"]
            for product, body in (("eip", eip), ("epg", epg)):
                r = self.client.put(f"/admin/merchants/{merchant_id}/credentials/{mode}/{product}",
                                    headers=ADMIN, json=body)
                assert r.status_code == 204, r.text
        key = self.client.post(f"/admin/merchants/{merchant_id}/api-keys", headers=ADMIN,
                               json={"mode": mode, "label": "tests"}).json()
        return type("Handle", (), {"id": merchant_id, "key": key["api_key"], "key_id": key["id"],
                                   "auth": {"Authorization": f"Bearer {key['api_key']}"},
                                   "webhook_secret": created["webhook_secret"], "eip": eip, "epg": epg})()


@pytest.fixture
def platform(tmp_path):
    return Platform(tmp_path)


@pytest.fixture
def make_platform(tmp_path):
    return lambda **overrides: Platform(tmp_path, **overrides)
