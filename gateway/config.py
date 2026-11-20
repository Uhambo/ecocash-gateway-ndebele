"""Settings, read from the environment. The app refuses to start with weak or missing secrets."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

EIP_SANDBOX_URL = "https://developers.ecocash.co.zw/sandbox/payment/v1"
EPG_SANDBOX_URL = "https://developers.ecocash.co.zw/api/serviceRequest"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    # Fernet keys. The first encrypts; all of them decrypt, so keys can be rotated.
    master_keys: tuple
    # Secret mixed into API-key hashes and inbound webhook tokens. Do not rotate casually:
    # changing it invalidates every issued API key.
    key_pepper: str
    admin_token: str
    database_url: str = "sqlite:///./gateway.db"
    # Public https address of this server. EcoCash calls back to it.
    public_base_url: str = "http://localhost:8000"
    env: str = "development"
    eip_sandbox_url: str = EIP_SANDBOX_URL
    epg_sandbox_url: str = EPG_SANDBOX_URL
    # Live URLs are issued by EcoCash with production access. Live keys are refused until set.
    eip_live_url: str = ""
    epg_live_url: str = ""
    rate_limit_per_minute: int = 120
    # Largest single payout (cash-in or change) unless a merchant has their own limit. Minor units.
    default_max_payout_minor: int = 50_000
    allow_insecure_webhooks: bool = False
    provider_timeout: float = 30.0

    def validate(self):
        if not self.master_keys:
            raise ConfigError("GATEWAY_MASTER_KEY is not set. Run: python -m gateway.keygen")
        if len(self.key_pepper) < 32:
            raise ConfigError("GATEWAY_KEY_PEPPER must be at least 32 characters. Run: python -m gateway.keygen")
        if len(self.admin_token) < 32:
            raise ConfigError("GATEWAY_ADMIN_TOKEN must be at least 32 characters. Run: python -m gateway.keygen")
        if self.env == "production":
            if not self.public_base_url.startswith("https://"):
                raise ConfigError("GATEWAY_PUBLIC_BASE_URL must be https in production")
            if self.allow_insecure_webhooks:
                raise ConfigError("GATEWAY_ALLOW_INSECURE_WEBHOOKS cannot be on in production")
        return self

    @classmethod
    def from_env(cls):
        load_dotenv()
        env = os.getenv
        return cls(
            master_keys=tuple(k.strip() for k in env("GATEWAY_MASTER_KEY", "").split(",") if k.strip()),
            key_pepper=env("GATEWAY_KEY_PEPPER", ""),
            admin_token=env("GATEWAY_ADMIN_TOKEN", ""),
            database_url=env("GATEWAY_DATABASE_URL", cls.database_url),
            public_base_url=env("GATEWAY_PUBLIC_BASE_URL", cls.public_base_url).rstrip("/"),
            env=env("GATEWAY_ENV", cls.env),
            eip_live_url=env("ECOCASH_EIP_LIVE_URL", "").rstrip("/"),
            epg_live_url=env("ECOCASH_EPG_LIVE_URL", ""),
            rate_limit_per_minute=int(env("GATEWAY_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute)),
            default_max_payout_minor=int(env("GATEWAY_MAX_PAYOUT_MINOR", cls.default_max_payout_minor)),
            allow_insecure_webhooks=env("GATEWAY_ALLOW_INSECURE_WEBHOOKS", "").lower() in ("1", "true", "yes"),
        ).validate()
