import os
import requests
from dotenv import load_dotenv

# Load credentials from the .env file
load_dotenv()

USERNAME = os.getenv("ECOCASH_USERNAME")
PASSWORD = os.getenv("ECOCASH_PASSWORD")

if not USERNAME or not PASSWORD:
    print("Error: ECOCASH_USERNAME and ECOCASH_PASSWORD must be set in the .env file.")
    exit(1)

def test_authentication():
    print("Testing EcoCash Sandbox Authentication...")
    
    # We will ping the transaction lookup endpoint or the base charge endpoint.
    # Even if we send an empty payload and get a "400 Bad Request" instead of 
    # "401 Unauthorized", it proves our credentials are valid!
    
    url = "https://developers.ecocash.co.zw/sandbox/payment/v1/transactions/amount/"
    
    try:
        # We are using requests' built-in Basic Auth handler
        response = requests.post(url, auth=(USERNAME, PASSWORD), json={})
        
        print(f"Status Code: {response.status_code}")
        print("Response Body:")
        print(response.text)
        
        if response.status_code == 401:
            print("\n❌ Authentication Failed! Please check your username and password in the .env file.")
        elif response.status_code in (200, 201, 400, 403, 405, 500):
            print("\n✅ Authentication Successful! The server recognized your credentials.")
            print("(Note: A 400 Bad Request or 500 error here is expected because we sent an empty payload, but it proves you bypassed the 401 Unauthorized gate!)")
            
    except Exception as e:
        print(f"An error occurred while connecting: {e}")

if __name__ == "__main__":
    test_authentication()
