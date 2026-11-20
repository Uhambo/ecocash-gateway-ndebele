"""Builds the web app. Tests pass in their own settings and fake HTTP clients."""
import logging
import socket

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import routes_admin, routes_merchant, routes_webhooks
from .config import Settings
from .models import make_session_factory
from .security import RateLimiter, Vault

log = logging.getLogger("gateway")


def create_app(settings: Settings | None = None, http=None, webhook_http=None, resolver=None) -> FastAPI:
    settings = (settings or Settings.from_env()).validate()
    production = settings.env == "production"
    app = FastAPI(title="Uhambo EcoCash Gateway", version="1.0",
                  docs_url=None if production else "/docs", redoc_url=None, openapi_url=None if production else "/openapi.json")

    app.state.settings = settings
    app.state.vault = Vault(settings.master_keys)
    app.state.session_factory = make_session_factory(settings.database_url)
    app.state.http = http or requests.Session()  # to EcoCash
    app.state.webhook_http = webhook_http or requests.Session()  # to merchants
    app.state.resolver = resolver or socket.getaddrinfo
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.admin_limiter = RateLimiter(30)

    def harden(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        return harden(await call_next(request))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Log the error type only: messages can contain request data. Tell the caller nothing internal.
        log.error("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return harden(JSONResponse({"detail": "Internal error"}, status_code=500))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(routes_admin.router)
    app.include_router(routes_merchant.router)
    app.include_router(routes_webhooks.router)
    return app
