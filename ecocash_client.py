import os
import requests
from dotenv import load_dotenv
import uuid

load_dotenv()

class EcoCashClient:
    def __init__(self):
        self.username = os.getenv("ECOCASH_USERNAME")
        self.password = os.getenv("ECOCASH_PASSWORD")
        self.base_url = "https://developers.ecocash.co.zw/sandbox/payment/v1"
        
        # Merchant details provided by the EcoCash Sandbox
        self.merchant_code = os.getenv("ECOCASH_MERCHANT_CODE")
        self.merchant_pin = os.getenv("ECOCASH_MERCHANT_PIN")
        self.merchant_number = os.getenv("ECOCASH_MERCHANT_NUMBER")
        self.terminal_id = os.getenv("ECOCASH_TERMINAL_ID", "12345") # Can usually be a dummy string in sandbox

    def initiate_charge(self, phone_number: str, amount: float, reference: str = None):
        """
        Initiates a USSD push (Charge Request) to the customer's phone.
        """
        url = f"{self.base_url}/transactions/amount/"
        
        # Auto-generate a reference if none is provided
        if not reference:
            reference = f"REF-{uuid.uuid4().hex[:8].upper()}"
            
        payload = {
            "clientCorrelator": reference,
            "referenceCode": reference,
            "tranType": "MER",
            "endUserId": phone_number,
            "paymentAmount": {
                "charginginformation": {
                    "amount": f"{amount:.2f}",
                    "currency": "USD", # Defaulting to USD based on your example
                    "description": "Online Payment"
                },
                "chargeMetaData": {
                    "channel": "WEB"
                }
            },
            "merchantCode": self.merchant_code,
            "merchantPin": self.merchant_pin,
            "merchantNumber": self.merchant_number,
            "countryCode": "ZW",
            "terminalID": self.terminal_id,
            "location": "Harare",
            "superMerchantName": "EcoCash Sandbox",
            "merchantName": "Test Merchant",
            "transactionOperationStatus": "Charged",
            "remarks": "Online Payment",
            "notifyUrl": os.getenv("ECOCASH_NOTIFY_URL", "https://myapp.example.com/webhook/eip")
        }
        
        response = requests.post(url, auth=(self.username, self.password), json=payload)
        
        return response.json(), response.status_code

    def check_transaction_status(self, phone_number: str, client_correlator: str):
        """
        Looks up the status of a previously initiated transaction using the customer phone and clientCorrelator.
        """
        # Note the specific structure: /{end_user_id}/transactions/amount/{client_correlator}
        url = f"{self.base_url}/{phone_number}/transactions/amount/{client_correlator}"
        
        response = requests.get(url, auth=(self.username, self.password))
        
        return response.json(), response.status_code

    def refund_transaction(self, phone_number: str, amount: float, original_ecocash_reference: str, reference: str = None):
        """
        Refunds a previously successful transaction using the EcoCash network reference.
        """
        url = f"{self.base_url}/transactions/refund/"
        
        if not reference:
            reference = f"REFUND-{uuid.uuid4().hex[:6].upper()}"
            
        payload = {
            "clientCorrelator": reference,
            "referenceCode": reference,
            "tranType": "MER",
            "endUserId": phone_number,
            "originalEcocashReference": original_ecocash_reference,
            "paymentAmount": {
                "charginginformation": {
                    "amount": f"{amount:.2f}",
                    "currency": "USD",
                    "description": "Refund"
                },
                "chargeMetaData": {
                    "channel": "WEB"
                }
            },
            "merchantCode": self.merchant_code,
            "merchantPin": self.merchant_pin,
            "merchantNumber": self.merchant_number,
            "currencyCode": "USD",
            "countryCode": "ZW",
            "terminalID": self.terminal_id,
            "location": "Harare",
            "superMerchantName": "EcoCash Sandbox",
            "merchantName": "Test Merchant"
        }
        
        response = requests.post(url, auth=(self.username, self.password), json=payload)
        return response.json(), response.status_code

# --- Quick Test ---
if __name__ == "__main__":
    client = EcoCashClient()
    
    # We use a placeholder test number. You must replace this with a valid 
    # test number from the Sandbox "Test Data" tab.
    TEST_PHONE_NUMBER = "263770000000"  # Notice we removed the '+' based on the new documentation snippet
    
    print(f"Initiating a charge of $10.00 to {TEST_PHONE_NUMBER}...")
    result, status = client.initiate_charge(phone_number=TEST_PHONE_NUMBER, amount=10.00)
    
    print(f"\nStatus Code: {status}")
    print("Response:")
    print(result)
    
    # Check if the charge was successfully accepted for processing
    if status in (200, 201):
        import time
        correlator = result.get('clientCorrelator')
        print(f"\n--- Polling Transaction: {correlator} ---")
        
        max_attempts = 10
        attempt = 1
        
        while attempt <= max_attempts:
            print(f"Attempt {attempt}/{max_attempts}: Checking status...")
            lookup_result, lookup_status = client.check_transaction_status(TEST_PHONE_NUMBER, correlator)
            
            # The exact key might be 'transactionOperationStatus' or 'transactionStatus'
            current_status = lookup_result.get('transactionOperationStatus', 'UNKNOWN')
            print(f"Current Status: {current_status}")
            
            # If it's no longer pending, break out of the loop
            if current_status not in ["PENDING SUBSCRIBER VALIDATION", "PENDING"]:
                import json
                print("\n" + "="*40)
                print("           RESULT PAGE")
                print("="*40)
                print(f"Final Status: {current_status}")
                print("Full Details:")
                print(json.dumps(lookup_result, indent=2))
                print("="*40)
                break
                
            time.sleep(5)  # Wait 5 seconds before checking again
            attempt += 1
            
        if attempt > max_attempts:
            print("\n❌ Stopped polling. Transaction took too long (still pending).")
