"""Encryption at rest, API keys, webhook signing and webhook URL checks."""
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class Vault:
    """Encrypts merchant secrets before they reach the database."""

    def __init__(self, master_keys):
        self._fernet = MultiFernet([Fernet(k) for k in master_keys])

    def encrypt_json(self, data: dict) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def decrypt_json(self, token: str) -> dict:
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as exc:
            raise RuntimeError("Stored secret cannot be decrypted with the configured master keys") from exc


def new_api_key(mode: str) -> str:
    return f"uh_{mode}_{secrets.token_urlsafe(32)}"


def hash_api_key(pepper: str, key: str) -> str:
    """Only this hash is stored. Keys are long and random, so a keyed SHA-256 is enough."""
    return hmac.new(pepper.encode(), b"api-key:" + key.encode(), hashlib.sha256).hexdigest()


def inbound_token(pepper: str, txn_public_id: str) -> str:
    """Unguessable per-transaction token placed in the notifyUrl given to EcoCash."""
    return hmac.new(pepper.encode(), b"inbound:" + txn_public_id.encode(), hashlib.sha256).hexdigest()[:40]


def sign_webhook(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_webhook(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    """What a merchant runs on their side. Kept here so the tests prove the scheme round-trips."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign_webhook(secret, timestamp, body), header)


class WebhookUrlError(ValueError):
    pass


def validate_webhook_url(url: str, allow_insecure: bool = False, resolver=socket.getaddrinfo) -> str:
    """Stops a merchant pointing their webhook at this server's own network (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (allow_insecure and parsed.scheme == "http"):
        raise WebhookUrlError("Webhook URL must use https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise WebhookUrlError("Webhook URL must have a host and no embedded credentials")
    if allow_insecure:
        return url
    try:
        infos = resolver(parsed.hostname, parsed.port or 443)
    except OSError as exc:
        raise WebhookUrlError("Webhook host does not resolve") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise WebhookUrlError("Webhook host resolves to a private or reserved address")
    return url


class RateLimiter:
    """Sliding one-minute window per key, held in memory. Use Redis when running several instances."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True
