"""Request bodies. Unknown fields are rejected, amounts are exact decimals, numbers are normalised."""
import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Currency = Literal["USD", "ZWG"]
Amount = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), max_digits=12, decimal_places=2)]


def _msisdn(value: str) -> str:
    digits = re.sub(r"[\s+\-]", "", value)
    if digits.startswith("0"):
        digits = "263" + digits[1:]
    elif digits.startswith("7"):
        digits = "263" + digits
    if not re.fullmatch(r"2637[78]\d{7}", digits):
        raise ValueError("must be an Econet number, for example 263771234567")
    return digits


Msisdn = Annotated[str, AfterValidator(_msisdn)]
Short = Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[\w .,\-]+$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChargeIn(Strict):
    msisdn: Msisdn
    amount: Amount
    currency: Currency = "USD"
    description: Short = "Online Payment"


class RefundIn(Strict):
    transaction_id: Annotated[str, Field(pattern=r"^txn_[0-9a-f]{24}$")]
    amount: Amount | None = None  # default: everything not yet refunded


class CashInIn(Strict):
    msisdn: Msisdn
    amount: Amount
    currency: Currency = "USD"
    beneficiary_reference: Short = "Payout"


class ChangeIn(Strict):
    msisdn: Msisdn
    amount: Amount
    currency: Currency = "USD"
    terminal_id: Short
    merchant_name: Short


# ---- admin ----

class MerchantIn(Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    webhook_url: Annotated[str, Field(max_length=500)] | None = None
    max_payout: Amount | None = None


class MerchantPatch(Strict):
    webhook_url: Annotated[str, Field(max_length=500)] | None = None
    max_payout: Amount | None = None
    status: Literal["active", "suspended"] | None = None


class ApiKeyIn(Strict):
    mode: Literal["test", "live"]
    label: Annotated[str, Field(max_length=100)] = ""


Secret = Annotated[str, Field(min_length=1, max_length=2000)]


class EipCredentials(Strict):
    username: Secret
    password: Secret
    merchant_code: Secret
    merchant_pin: Secret
    merchant_number: Secret
    terminal_id: Short | None = None
    merchant_name: Short | None = None
    super_merchant_name: Short | None = None
    location: Short | None = None


class EpgCredentials(Strict):
    client_id: Secret
    client_secret: Secret
    api_username: Secret = "epg-sandbox-user-v1"
    api_password_encrypted: Secret
