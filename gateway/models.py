"""Database tables. Every merchant-owned row carries merchant_id, and every query filters on it."""
import secrets
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, default=lambda: public_id("mer"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    webhook_secret_enc: Mapped[str | None] = mapped_column(Text)
    max_payout_minor: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(10))  # test | live
    prefix: Mapped[str] = mapped_column(String(20))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    merchant: Mapped[Merchant] = relationship()


class ProviderCredential(Base):
    """A merchant's own EcoCash credentials for one product and mode, stored encrypted."""
    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("merchant_id", "mode", "product"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(10))
    product: Mapped[str] = mapped_column(String(10))  # eip | epg
    blob_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("merchant_id", "mode", "idempotency_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, default=lambda: public_id("txn"))
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    mode: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(20))  # charge | refund | cash_in | change
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | succeeded | failed
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    msisdn: Mapped[str] = mapped_column(String(15))
    reference: Mapped[str] = mapped_column(String(40), unique=True)  # what we send to EcoCash
    provider_reference: Mapped[str | None] = mapped_column(String(60))  # EcoCash's transaction id
    provider_code: Mapped[str | None] = mapped_column(String(60))
    provider_message: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), index=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    ok: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """Who did what. Never holds secrets, credentials or full API keys."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def make_session_factory(database_url: str):
    kwargs = {"connect_args": {"check_same_thread": False}} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, **kwargs)
    Base.metadata.create_all(engine)  # use Alembic migrations once the schema starts changing in production
    return sessionmaker(engine, expire_on_commit=False)
