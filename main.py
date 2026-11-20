"""Starts the multi-merchant gateway:  python main.py   (or: uvicorn main:app)"""
from gateway.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    # Listens on this machine only. Put a TLS-terminating proxy in front for anything public.
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
