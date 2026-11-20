"""
Talks to EcoCash with one merchant's credentials.

`http` is anything with requests' `.request(method, url, **kwargs)`, so tests swap in a fake.
Every call returns an Outcome. A network failure raises ProviderError, which callers treat as
"outcome unknown": the transaction stays pending and is settled by a later lookup, never by a blind retry.
"""
from dataclasses import dataclass
from decimal import Decimal

import requests


class ProviderError(Exception):
    """EcoCash could not be reached or gave no usable answer. The request may or may not have gone through."""


@dataclass
class Outcome:
    status: str  # succeeded | failed | pending | not_found
    code: str | None = None
    message: str | None = None
    provider_reference: str | None = None


def _body(response):
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(value):
    return None if value is None else str(value)[:500]


class EipProvider:
    """EcoCash Instant Payments: customer pays merchant by approving a USSD prompt."""

    def __init__(self, base_url: str, creds: dict, http, timeout: float = 30.0):
        self.base_url, self.creds, self.http, self.timeout = base_url.rstrip("/"), creds, http, timeout

    def _call(self, method, path, payload=None):
        try:
            return self.http.request(method, f"{self.base_url}{path}", json=payload, timeout=self.timeout,
                                     auth=(self.creds["username"], self.creds["password"]))
        except requests.RequestException as exc:
            raise ProviderError(type(exc).__name__) from exc

    def _merchant_fields(self):
        c = self.creds
        return {
            "merchantCode": c["merchant_code"],
            "merchantPin": c["merchant_pin"],
            "merchantNumber": c["merchant_number"],
            "countryCode": "ZW",
            "terminalID": c.get("terminal_id") or "12345",
            "location": c.get("location") or "Harare",
            "superMerchantName": c.get("super_merchant_name") or "EcoCash Sandbox",
            "merchantName": c.get("merchant_name") or "Test Merchant",
        }

    @staticmethod
    def _amount(amount: Decimal, currency: str, description: str):
        return {"charginginformation": {"amount": f"{amount:.2f}", "currency": currency, "description": description},
                "chargeMetaData": {"channel": "WEB"}}

    @staticmethod
    def _outcome(response, rejected_is_final: bool) -> Outcome:
        data = _body(response)
        raw = str(data.get("transactionOperationStatus") or data.get("ecocashResponseCode") or "").upper()
        message = _text(data.get("responseMessage") or data.get("message") or data.get("text"))
        reference = _text(data.get("ecocashReference"))
        if response.status_code >= 500:
            return Outcome("pending", f"HTTP_{response.status_code}", message)
        if response.status_code >= 400:
            status = "failed" if rejected_is_final else "pending"
            return Outcome(status, f"HTTP_{response.status_code}", message or "Rejected by EcoCash")
        if raw in ("COMPLETED", "SUCCESS", "SUCCEEDED"):
            return Outcome("succeeded", raw, message, reference)
        if raw in ("FAILED", "REJECTED", "DECLINED", "CANCELLED", "EXPIRED"):
            return Outcome("failed", raw, message, reference)
        # Anything unrecognised stays pending: a later lookup settles it. Never guess "failed".
        return Outcome("pending", raw or None, message, reference)

    def charge(self, reference, msisdn, amount, currency, description, notify_url) -> Outcome:
        payload = {
            "clientCorrelator": reference, "referenceCode": reference, "tranType": "MER", "endUserId": msisdn,
            "paymentAmount": self._amount(amount, currency, description),
            **self._merchant_fields(),
            "transactionOperationStatus": "Charged", "remarks": description, "notifyUrl": notify_url,
        }
        return self._outcome(self._call("POST", "/transactions/amount/", payload), rejected_is_final=True)

    def status(self, msisdn, reference) -> Outcome:
        return self._outcome(self._call("GET", f"/{msisdn}/transactions/amount/{reference}"), rejected_is_final=False)

    def refund(self, reference, msisdn, amount, currency, original_reference) -> Outcome:
        payload = {
            "clientCorrelator": reference, "referenceCode": reference, "tranType": "MER", "endUserId": msisdn,
            "originalEcocashReference": original_reference,
            "paymentAmount": self._amount(amount, currency, "Refund"),
            **self._merchant_fields(), "currencyCode": currency,
        }
        return self._outcome(self._call("POST", "/transactions/refund/", payload), rejected_is_final=True)


class EpgProvider:
    """EcoCash Payment Gateway: merchant pays a customer (cash-in, change). Synchronous."""

    CASH_IN_CHANNEL = "700002"
    CHANGE_CHANNEL = "800002"

    def __init__(self, url: str, creds: dict, http, timeout: float = 30.0):
        self.url, self.creds, self.http, self.timeout = url, creds, http, timeout

    def _call(self, payload):
        headers = {"X-Client-Id": self.creds["client_id"], "X-Client-Secret": self.creds["client_secret"]}
        try:
            return self.http.request("POST", self.url, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(type(exc).__name__) from exc

    def _channel_user(self):
        return {"username": self.creds.get("api_username") or "epg-sandbox-user-v1",
                "password": self.creds["api_password_encrypted"]}

    @staticmethod
    def _amount(amount: Decimal) -> str:
        return str(int(amount)) if amount == amount.to_integral_value() else f"{amount:.2f}"

    @staticmethod
    def _outcome(response) -> Outcome:
        data = _body(response)
        code = _text(data.get("responseCode"))
        message = _text(data.get("responseMessage") or data.get("message"))
        if response.status_code >= 500:
            return Outcome("pending", code or f"HTTP_{response.status_code}", message)
        if response.status_code < 300 and code == "00":
            return Outcome("succeeded", code, message, _text(data.get("transactionId")))
        return Outcome("failed", code or f"HTTP_{response.status_code}", message or "Rejected by EcoCash")

    def cash_in(self, reference, msisdn, amount, currency, beneficiary_reference) -> Outcome:
        return self._outcome(self._call({
            "serviceType": "ENTERPRISECASHINV2", "externalReference": reference,
            "beneficiaryReference": beneficiary_reference, "amount": self._amount(amount), "currency": currency,
            "transferReference": "ENTERPRISECASHIN", "msisdn": msisdn, **self._channel_user(),
            "channelCode": self.CASH_IN_CHANNEL, "walletId": "12",
        }))

    def change(self, reference, msisdn, amount, currency, terminal_id, merchant_name) -> Outcome:
        return self._outcome(self._call({
            "serviceType": "CHANGEDISBURSEV2", "externalReference": reference, "receiverNumber": msisdn,
            "amount": self._amount(amount), "currency": currency, "transferReference": "Change Disbursement",
            **self._channel_user(), "terminalId": terminal_id, "channelCode": self.CHANGE_CHANNEL,
            "merchantName": merchant_name,
        }))

    def lookup(self, lookup_reference, original_reference, msisdn, currency) -> Outcome:
        response = self._call({
            "serviceType": "EXTERNALTRANSACTIONLOOKUP", "externalReference": lookup_reference,
            "externalTransactionId": original_reference, "currency": currency, "msisdn": msisdn,
        })
        data = _body(response)
        code = _text(data.get("responseCode"))
        message = _text(data.get("responseMessage") or data.get("message"))
        if response.status_code < 300 and code == "00":
            return Outcome("succeeded", code, message, _text(data.get("transactionId")))
        if code == "888803":
            return Outcome("not_found", code, message)
        return Outcome("pending", code or f"HTTP_{response.status_code}", message)
