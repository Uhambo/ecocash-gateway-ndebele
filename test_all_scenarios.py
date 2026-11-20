import time
import json
import ecocash_client

def log_output(msg, log_file):
    print(msg)
    log_file.write(msg + "\n")

def test_scenario(client, phone, amount, scenario_name, expected_pin, log_file):
    log_output(f"\n{'='*50}", log_file)
    log_output(f"SCENARIO: {scenario_name}", log_file)
    log_output(f"Please enter PIN: {expected_pin} on your phone when prompted!", log_file)
    log_output(f"{'='*50}", log_file)
    
    result, status = client.initiate_charge(phone_number=phone, amount=amount)
    
    if status not in (200, 201):
        log_output(f"[FAIL] Failed to initiate. Status: {status}", log_file)
        return
        
    correlator = result.get('clientCorrelator')
    log_output(f"Waiting for PIN entry... (correlator: {correlator})", log_file)
    
    max_attempts = 12
    for attempt in range(1, max_attempts + 1):
        lookup_result, lookup_status = client.check_transaction_status(phone, correlator)
        current_status = lookup_result.get('transactionOperationStatus', 'UNKNOWN')
        
        if current_status not in ["PENDING SUBSCRIBER VALIDATION", "PENDING"]:
            log_output(f"\n[SUCCESS] Scenario Finished! Final Status: {current_status}", log_file)
            log_output("Response Details:", log_file)
            log_output(json.dumps(lookup_result, indent=2), log_file)
            return
            
        time.sleep(5)
        
    log_output("\n[TIMEOUT] Scenario Timed Out! Did you enter the PIN on your phone?", log_file)

if __name__ == "__main__":
    client = ecocash_client.EcoCashClient()
    TEST_PHONE = "263770000000"
    
    scenarios = [
        ("Successful Payment", "0000"),
        ("Insufficient Funds", "1111"),
        ("Incorrect PIN", "2222"),
        ("Limit Exceeded", "9999")
    ]
    
    with open("scenarios.log", "w", encoding="utf-8") as log_file:
        log_output("Starting Automated Sandbox Scenario Tests...", log_file)
        log_output("A 2-minute gap will be placed between scenarios.\n", log_file)
        
        for i, (name, pin) in enumerate(scenarios):
            test_scenario(client, TEST_PHONE, 10.00, name, pin, log_file)
            
            if i < len(scenarios) - 1:
                log_output("\nWaiting 2 minutes for the next scenario...", log_file)
                time.sleep(120)  # 2 minute gap
                
        log_output("\nAll scenarios completed!", log_file)
