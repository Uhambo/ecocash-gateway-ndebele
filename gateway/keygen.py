"""Prints fresh secrets for .env:  python -m gateway.keygen"""
import secrets

from cryptography.fernet import Fernet

if __name__ == "__main__":
    print(f"GATEWAY_MASTER_KEY={Fernet.generate_key().decode()}")
    print(f"GATEWAY_KEY_PEPPER={secrets.token_urlsafe(48)}")
    print(f"GATEWAY_ADMIN_TOKEN={secrets.token_urlsafe(48)}")
