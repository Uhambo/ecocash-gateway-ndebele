import ecocash_client

if __name__ == "__main__":
    client = ecocash_client.EcoCashClient()
    
    # 1. Ensure you have the exact same phone number used in the original payment
    TEST_PHONE = "263770000000"
    
    # 2. Paste the `ecocashReference` (e.g., MP2610...) from your SUCCESSFUL payment result page
    ORIGINAL_REF = "REPLACE_ME_WITH_ECOCASH_REFERENCE" 
    
    # 3. Ensure the amount exactly matches the original transaction
    REFUND_AMOUNT = 10.00 

    if ORIGINAL_REF == "REPLACE_ME_WITH_ECOCASH_REFERENCE":
        print("❌ Please update ORIGINAL_REF with a successful ecocashReference first!")
        exit(1)

    print(f"Initiating refund of ${REFUND_AMOUNT} for transaction {ORIGINAL_REF}...")
    
    result, status = client.refund_transaction(
        phone_number=TEST_PHONE,
        amount=REFUND_AMOUNT, 
        original_ecocash_reference=ORIGINAL_REF
    )

    print("\n" + "="*40)
    print("        REFUND RESULT")
    print("="*40)
    print(f"Status Code: {status}")
    
    import json
    print(json.dumps(result, indent=2))
    print("="*40)
