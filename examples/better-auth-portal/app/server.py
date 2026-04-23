"""Tiny Better Auth-shaped example server for qazy.

Serves a login page at `/`, a protected dashboard at `/dashboard`, and a
`POST /api/auth/sign-in/email` endpoint that sets a `better-auth.session_token`
cookie on success. The contract mirrors what qazy's built-in Better Auth flow
expects so the scenario can exercise auto-auth end to end.
"""
from __future__ import annotations

import json
import os
import secrets
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


EXPECTED_EMAIL = "student@example.com"
EXPECTED_PASSWORD = "tester123"
COOKIE_NAME = "better-auth.session_token"
APP_DIR = Path(__file__).resolve().parent

# In-memory session store. Tokens are opaque 32-byte hex strings issued by
# sign-in; the dashboard just checks for presence.
SESSIONS: set[str] = set()


LOGIN_PAGE = (APP_DIR / "login.html").read_text(encoding="utf-8")
DASHBOARD_PAGE = (APP_DIR / "dashboard.html").read_text(encoding="utf-8")
STYLES = (APP_DIR / "styles.css").read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # silence default stderr logging
        return

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send_html(LOGIN_PAGE)
            return
        if self.path == "/styles.css":
            self._send(HTTPStatus.OK, STYLES.encode(), "text/css")
            return
        if self.path == "/dashboard":
            if not self._authenticated():
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self._send_html(DASHBOARD_PAGE)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/api/auth/sign-in/email":
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return

        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected application/json"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return

        email = str(payload.get("email", "")).strip()
        password = str(payload.get("password", ""))
        if email != EXPECTED_EMAIL or password != EXPECTED_PASSWORD:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"})
            return

        token = secrets.token_hex(32)
        SESSIONS.add(token)
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax",
        )
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"user": {"email": email}}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authenticated(self) -> bool:
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        cookies = SimpleCookie()
        cookies.load(raw)
        morsel = cookies.get(COOKIE_NAME)
        return morsel is not None and morsel.value in SESSIONS

    def _send_html(self, body: str) -> None:
        self._send(HTTPStatus.OK, body.encode(), "text/html; charset=utf-8")

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
