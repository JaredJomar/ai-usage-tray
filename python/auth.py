"""
Authentication management for Codex

Uses OpenAI Authorization Code + PKCE flow (same as Codex CLI).
Opens browser for login, captures callback on local server.
"""
import json
import hashlib
import base64
import secrets
import webbrowser
import time
import threading
import socket
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import requests

from config import get_codex_auth_path
from logger import Logger


# --- PKCE Helpers ---

def _generate_pkce():
    verifier_bytes = secrets.token_bytes(64)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# --- Auth Status ---

class AuthStatus:
    def __init__(self, provider, authenticated, email="",
                 plan="", expires_at="", message=""):
        self.provider = provider
        self.authenticated = authenticated
        self.email = email
        self.plan = plan
        self.expires_at = expires_at
        self.message = message


# --- Local Callback Server ---

class _CallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    auth_state = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/auth/callback" and "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            _CallbackHandler.auth_state = params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authentication successful!</h2>"
                b"<p>You can close this window and return to AI Usage Tray.</p>"
                b"</body></html>"
            )
        elif "error" in params:
            error = params.get("error_description", [params["error"][0]])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "<html><body><h2>Auth failed</h2><p>{}</p></body></html>".format(error)
            self.wfile.write(msg.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def _find_free_port(preferred=1455):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", preferred))
            return preferred
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- Auth Manager ---

class AuthManager:
    CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    CODEX_ISSUER = "https://auth.openai.com"
    CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
    CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
    CODEX_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"

    def __init__(self):
        self._status_callbacks = []

    def register_status_callback(self, callback):
        self._status_callbacks.append(callback)

    def _notify_status(self, provider, status):
        for cb in self._status_callbacks:
            try:
                cb(provider, status)
            except Exception:
                pass

    # --- Token Refresh ---

    def _refresh_token(self, refresh_token):
        try:
            resp = requests.post(
                self.CODEX_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.CODEX_CLIENT_ID,
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            Logger.warn("auth", "Token refresh HTTP {}: {}".format(
                resp.status_code, resp.text[:200]))
            return None
        except Exception as e:
            Logger.warn("auth", "Token refresh error: {}".format(e))
            return None

    def _ensure_valid_token(self):
        """Refresh token if expired, return valid access_token or None"""
        auth_path = get_codex_auth_path()
        if not auth_path.exists():
            return None

        try:
            with open(auth_path, "r") as f:
                auth = json.load(f)

            tokens = auth.get("tokens", {})
            access_token = tokens.get("access_token")
            expires_at = tokens.get("expires_at", 0)
            refresh_token = tokens.get("refresh_token")

            if not access_token:
                return None

            now_ms = int(time.time() * 1000)
            if expires_at and now_ms >= expires_at and refresh_token:
                Logger.info("auth", "Token expired, refreshing...")
                new_data = self._refresh_token(refresh_token)
                if new_data:
                    tokens["access_token"] = new_data["access_token"]
                    tokens["id_token"] = new_data.get("id_token", tokens.get("id_token"))
                    if new_data.get("refresh_token"):
                        tokens["refresh_token"] = new_data["refresh_token"]
                    tokens["expires_at"] = int(time.time() * 1000) + (
                        new_data.get("expires_in", 3600) * 1000
                    )
                    auth["tokens"] = tokens
                    with open(auth_path, "w") as f:
                        json.dump(auth, f, indent=2)
                    Logger.info("auth", "Token refreshed successfully")
                    return new_data["access_token"]

            return access_token
        except Exception as e:
            Logger.error("auth", "Error checking token: {}".format(e))
            return None

    # --- Codex Status ---

    def get_codex_status(self):
        auth_path = get_codex_auth_path()
        if not auth_path.exists():
            return AuthStatus("codex", False,
                              message="Not configured. Click Login.")

        try:
            token = self._ensure_valid_token()
            if not token:
                return AuthStatus("codex", False,
                                  message="No valid token. Click Login.")

            headers = {
                "Authorization": "Bearer {}".format(token),
                "User-Agent": "codex_cli_rs",
            }
            resp = requests.get(
                "https://chatgpt.com/backend-api/wham/usage",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                return AuthStatus("codex", True,
                                  message="Authenticated.")
            elif resp.status_code == 401:
                return AuthStatus("codex", False,
                                  message="Token invalid. Click Login.")
            else:
                return AuthStatus("codex", False,
                                  message="API returned {}.".format(resp.status_code))
        except Exception as e:
            return AuthStatus("codex", False,
                              message="Error: {}".format(e))

    # --- Codex Login (Browser PKCE) ---

    def login_codex(self, callback=None):
        try:
            Logger.info("auth", "Starting Codex browser login (PKCE)...")

            def run_login():
                try:
                    port = _find_free_port(1455)
                    redirect_uri = "http://localhost:{}/auth/callback".format(port)

                    pkce_verifier, pkce_challenge = _generate_pkce()
                    state = secrets.token_urlsafe(32)

                    auth_params = urlencode({
                        "response_type": "code",
                        "client_id": self.CODEX_CLIENT_ID,
                        "redirect_uri": redirect_uri,
                        "scope": self.CODEX_SCOPE,
                        "code_challenge": pkce_challenge,
                        "code_challenge_method": "S256",
                        "id_token_add_organizations": "true",
                        "codex_cli_simplified_flow": "true",
                        "state": state,
                        "originator": "codex_cli_rs",
                    })
                    auth_url = "{}?{}".format(self.CODEX_AUTHORIZE_URL, auth_params)

                    Logger.info("auth", "Opening browser for Codex login...")
                    Logger.info("auth", "Callback: {}".format(redirect_uri))

                    _CallbackHandler.auth_code = None
                    _CallbackHandler.auth_state = None

                    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
                    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                    server_thread.start()

                    webbrowser.open(auth_url)

                    start = time.time()
                    timeout = 300

                    while time.time() - start < timeout:
                        if _CallbackHandler.auth_code:
                            break
                        time.sleep(0.5)

                    code = _CallbackHandler.auth_code
                    server.shutdown()

                    if not code:
                        Logger.error("auth", "Login timed out after 5 minutes")
                        if callback:
                            callback(False, "Login timed out. Please try again.")
                        return

                    Logger.info("auth", "Authorization code received, exchanging for token...")

                    token_data = self._exchange_code(code, pkce_verifier, redirect_uri)
                    if not token_data:
                        Logger.error("auth", "Token exchange failed")
                        if callback:
                            callback(False, "Token exchange failed.")
                        return

                    self._save_codex_tokens(token_data)
                    Logger.info("auth", "Codex login successful!")
                    if callback:
                        callback(True, "Login successful!")
                    self._notify_status("codex", self.get_codex_status())

                except Exception as e:
                    Logger.error("auth", "Codex login error: {}".format(e))
                    if callback:
                        callback(False, str(e))

            threading.Thread(target=run_login, daemon=True).start()
            return True

        except Exception as e:
            Logger.error("auth", "Codex login failed: {}".format(e))
            if callback:
                callback(False, str(e))
            return False

    def _exchange_code(self, code, pkce_verifier, redirect_uri):
        try:
            resp = requests.post(
                self.CODEX_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.CODEX_CLIENT_ID,
                    "code_verifier": pkce_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            Logger.error("auth", "Token exchange HTTP {}: {}".format(
                resp.status_code, resp.text[:300]))
            return None
        except Exception as e:
            Logger.error("auth", "Token exchange error: {}".format(e))
            return None

    def _save_codex_tokens(self, token_data):
        auth_path = get_codex_auth_path()
        auth_path.parent.mkdir(parents=True, exist_ok=True)

        existing = {}
        if auth_path.exists():
            try:
                with open(auth_path, "r") as f:
                    existing = json.load(f)
            except Exception:
                pass

        if "tokens" not in existing:
            existing["tokens"] = {}

        tokens = existing["tokens"]
        tokens["access_token"] = token_data.get("access_token")
        tokens["id_token"] = token_data.get("id_token")
        if token_data.get("refresh_token"):
            tokens["refresh_token"] = token_data["refresh_token"]
        tokens["expires_at"] = int(time.time() * 1000) + (
            token_data.get("expires_in", 3600) * 1000
        )

        with open(auth_path, "w") as f:
            json.dump(existing, f, indent=2)

    def logout_codex(self):
        try:
            auth_path = get_codex_auth_path()
            if auth_path.exists():
                auth_path.unlink()
                Logger.info("auth", "Codex logged out")
                self._notify_status("codex", self.get_codex_status())
                return True
            return False
        except Exception as e:
            Logger.error("auth", "Logout failed: {}".format(e))
            return False

    def get_all_status(self):
        return {
            "codex": self.get_codex_status(),
        }
