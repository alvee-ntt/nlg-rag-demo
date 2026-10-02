"""Demo sign-in: one shared credential in front of the whole salesDJ app.

This is a gate, not an identity system: there are no user accounts and no roles. It
exists because the app can be published on the internet and ``/v1/speech/token`` mints
Azure Speech tokens against a real key, so an ungated deployment is billable by anyone
who finds the URL.

One random authentication token is issued per process to everyone who signs in. Each
successful credential submission also gets a unique trace-session marker in a second
HttpOnly cookie. A restart signs everyone out. ``LOGIN_USERNAME`` / ``LOGIN_PASSWORD``
set the credential; ``COOKIE_SECURE=true`` marks the cookies Secure behind TLS.

What is open without signing in: ``/health`` (readiness probes cannot sign in), the
sign-in endpoints, the static app files under ``/app`` (the page itself renders the
sign-in screen; nothing in it is secret), and the ``/`` API banner. Everything under
``/v1`` and the interactive API docs require the cookie.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .config import Settings

COOKIE_NAME = "salesdj_auth"
TRACE_SESSION_COOKIE_NAME = "salesdj_trace_session"
LOGIN_ROUTE = "/app/learn.html#/login"
_SAFE_TRACE_SESSION = re.compile(r"^[A-Za-z0-9_.-]+$")

_OPEN_PATHS = {"/", "/health", "/v1/auth/login", "/v1/auth/logout", "/v1/auth/me"}
# Prompt Studio and its API are deliberately open for this demo. Production prompt
# authoring will need a separate, explicit authorization design.
_OPEN_PREFIXES = ("/app/", "/learn", "/prepare", "/prompts", "/v1/prompt-admin/")


class Auth:
    def __init__(self, settings: Settings) -> None:
        self.username = settings.login_username
        self.password = settings.login_password
        self.secure = settings.cookie_secure
        self.token = secrets.token_urlsafe(32)
        self.trace_root = Path(settings.foundry_trace_path) if settings.foundry_trace_path else None

    def is_authed(self, request: Request) -> bool:
        value = request.cookies.get(COOKIE_NAME, "")
        return bool(value) and secrets.compare_digest(value, self.token)

    def check(self, username: str, password: str) -> bool:
        # compare_digest on both, and always both, so a wrong username costs the same
        # as a wrong password.
        return secrets.compare_digest(username, self.username) & secrets.compare_digest(
            password, self.password
        )

    def set_cookie(self, response: Response) -> str:
        trace_session_id = self._create_trace_session()
        response.set_cookie(
            COOKIE_NAME, self.token, path="/", httponly=True, samesite="lax", secure=self.secure,
            max_age=60 * 60 * 24 * 14,
        )
        response.set_cookie(
            TRACE_SESSION_COOKIE_NAME, trace_session_id, path="/", httponly=True,
            samesite="lax", secure=self.secure, max_age=60 * 60 * 24 * 14,
        )
        return trace_session_id

    def _create_trace_session(self) -> str:
        now = datetime.now().astimezone()
        timestamp = (
            now.strftime("%Y-%m-%d_%H-%M-%S-")
            + f"{now.microsecond // 1000:03d}_"
            + now.strftime("%z")
        )
        username = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.username).strip("-.") or "user"
        session_id = f"{timestamp}_{username}_{secrets.token_hex(4)}"
        if self.trace_root is not None:
            session_dir = self.trace_root / session_id
            session_dir.mkdir(parents=True, exist_ok=False)
            (session_dir / "session.json").write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "username": self.username,
                        "created_at": now.isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        return session_id

    @staticmethod
    def trace_session_id(request: Request) -> str | None:
        value = request.cookies.get(TRACE_SESSION_COOKIE_NAME, "")
        return value if value and _SAFE_TRACE_SESSION.fullmatch(value) else None

    @staticmethod
    def clear_cookie(response: Response) -> None:
        response.delete_cookie(COOKIE_NAME, path="/")
        response.delete_cookie(TRACE_SESSION_COOKIE_NAME, path="/")

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
