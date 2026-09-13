"""Demo sign-in: one shared credential in front of the whole salesDJ app.

This is a gate, not an identity system: there are no user accounts and no roles. It
exists because the app can be published on the internet and ``/v1/speech/token`` mints
Azure Speech tokens against a real key, so an ungated deployment is billable by anyone
who finds the URL.

One random token is issued per process to everyone who signs in, carried in an
HttpOnly cookie. A restart signs everyone out. ``LOGIN_USERNAME`` / ``LOGIN_PASSWORD``
set the credential; ``COOKIE_SECURE=true`` marks the cookie Secure behind TLS.

What is open without signing in: ``/health`` (readiness probes cannot sign in), the
sign-in endpoints, the static app files under ``/app`` (the page itself renders the
sign-in screen; nothing in it is secret), and the ``/`` API banner. Everything under
``/v1`` and the interactive API docs require the cookie.
"""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .config import Settings

COOKIE_NAME = "salesdj_auth"
LOGIN_ROUTE = "/app/learn.html#/login"

_OPEN_PATHS = {"/", "/health", "/v1/auth/login", "/v1/auth/logout", "/v1/auth/me"}
_OPEN_PREFIXES = ("/app/", "/learn", "/prepare")


class Auth:
    def __init__(self, settings: Settings) -> None:
        self.username = settings.login_username
        self.password = settings.login_password
        self.secure = settings.cookie_secure
        self.token = secrets.token_urlsafe(32)

    def is_authed(self, request: Request) -> bool:
        value = request.cookies.get(COOKIE_NAME, "")
        return bool(value) and secrets.compare_digest(value, self.token)

    def check(self, username: str, password: str) -> bool:
        # compare_digest on both, and always both, so a wrong username costs the same
        # as a wrong password.
        return secrets.compare_digest(username, self.username) & secrets.compare_digest(
            password, self.password
        )

    def set_cookie(self, response: Response) -> None:
        response.set_cookie(
            COOKIE_NAME, self.token, path="/", httponly=True, samesite="lax", secure=self.secure,
            max_age=60 * 60 * 24 * 14,
        )

    @staticmethod
    def clear_cookie(response: Response) -> None:
        response.delete_cookie(COOKIE_NAME, path="/")

    @staticmethod
    def is_open(path: str) -> bool:
        return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)

    def gate(self, request: Request) -> Response | None:
        """The response to send instead of handling the request, or None to proceed."""
        path = request.url.path
        if self.is_open(path) or self.is_authed(request):
            return None
        if path.startswith("/v1/") or request.method != "GET":
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        # A browser landing on a gated page (the API docs, say) goes to the sign-in
        # screen and comes back here afterwards.
        target = quote(path + (f"?{request.url.query}" if request.url.query else ""), safe="/?=&")
        return RedirectResponse(url=f"{LOGIN_ROUTE}?next={target}", status_code=302)
