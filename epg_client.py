"""
EcoCash Payment Gateway (EPG) sandbox client.

All four EPG services go through one endpoint, selected by `serviceType`:
  - ENTERPRISECASHINV2         Enterprise Cash-In   (channelCode 700002)
  - CHANGEDISBURSEV2           Change Disbursement  (channelCode 800002)
  - EXTERNALTRANSACTIONLOOKUP  Transaction Lookup   (client credentials only)

Auth: X-Client-Id / X-Client-Secret headers, plus the shared channel API user
(username + encrypted password) in the body for services that move funds.
The API is synchronous: the outcome comes back on the same call.
"""
import os
import time
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

EPG_URL = "https://developers.ecocash.co.zw/api/serviceRequest"
CASH_IN_CHANNEL = "700002"
CHANGE_CHANNEL = "800002"


def new_reference() -> str:
    """Unique externalReference. Reusing one returns the original transaction."""
    return f"{int(time.time() * 1000)}{uuid.uuid4().int % 1000:03d}"


class EPGClient:
    def __init__(self):
        self.client_id = os.getenv("EPG_CLIENT_ID")
        self.client_secret = os.getenv("EPG_CLIENT_SECRET")
        self.api_username = os.getenv("EPG_API_USERNAME", "epg-sandbox-user-v1")
        # Generate this with the "Generate" button on the EPG playground page.
        # Any correctly encrypted value is accepted, so one value can be reused.
        self.api_password = os.getenv("EPG_API_PASSWORD_ENCRYPTED")

        missing = [name for name, value in {
            "EPG_CLIENT_ID": self.client_id,
            "EPG_CLIENT_SECRET": self.client_secret,
            "EPG_API_PASSWORD_ENCRYPTED": self.api_password,
        }.items() if not value]
        if missing:
            raise RuntimeError(f"Missing in .env: {', '.join(missing)}")

    def _post(self, payload: dict):
        headers = {
            "X-Client-Id": self.client_id,
            "X-Client-Secret": self.client_secret,
            "Content-Type": "application/json",
        }
        response = requests.post(EPG_URL, headers=headers, json=payload, timeout=30)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        return body, response.status_code

    def enterprise_cash_in(self, msisdn: str, amount, currency: str = "USD",
                           external_reference: str = None,
                           beneficiary_reference: str = "Grower203",
                           channel_code: str = CASH_IN_CHANNEL,
                           wallet_id: str = "12"):
        payload = {
            "serviceType": "ENTERPRISECASHINV2",
            "externalReference": external_reference or new_reference(),
            "beneficiaryReference": beneficiary_reference,
            "amount": str(amount),
            "currency": currency,
            "transferReference": "ENTERPRISECASHIN",
            "msisdn": msisdn,
            "username": self.api_username,
            "password": self.api_password,
            "channelCode": channel_code,
            "walletId": wallet_id,
        }
        return self._post(payload)

    def change_disbursement(self, receiver_number: str, amount, currency: str = "USD",
                            external_reference: str = None,
                            terminal_id: str = "TestTerminal001",
                            merchant_name: str = "Test Merchant",
                            channel_code: str = CHANGE_CHANNEL):
        payload = {
            "serviceType": "CHANGEDISBURSEV2",
            "externalReference": external_reference or new_reference(),
            "receiverNumber": receiver_number,
            "amount": str(amount),
            "currency": currency,
            "transferReference": "Change Disbursement",
            "username": self.api_username,
            "password": self.api_password,
            "terminalId": terminal_id,
            "channelCode": channel_code,
            "merchantName": merchant_name,
        }
        return self._post(payload)

    def transaction_lookup(self, transaction_reference: str, msisdn: str,
                           currency: str = "USD"):
        """
        transaction_reference is the externalReference of the ORIGINAL transaction.
        A fresh externalReference is generated for the lookup call itself.
        """
        payload = {
            "serviceType": "EXTERNALTRANSACTIONLOOKUP",
            "externalReference": new_reference(),
            "externalTransactionId": transaction_reference,
            "currency": currency,
            "msisdn": msisdn,
        }
        return self._post(payload)
