import json
import time
import requests
from pathlib import Path
from typing import Optional
from config import get_codex_auth_path
from logger import Logger


class CodexProvider:
    API_URL = "https://chatgpt.com/backend-api/wham/usage"
    USER_AGENT = "codex_cli_rs"

    def __init__(self, cache):
        self._cache = cache

    def fetch(self) -> Optional[dict]:
        cached = self._cache.get_cached("codex")
        if cached is not None:
            return cached

        auth_path = get_codex_auth_path()
        if not auth_path.exists():
            Logger.warn("codex", "Auth file not found: {}".format(auth_path))
            return None

        try:
            with open(auth_path, "r") as f:
                auth = json.load(f)

            access_token = auth.get("tokens", {}).get("access_token")
            if not access_token:
                Logger.warn("codex", "No access token found")
                return None

            resp = requests.get(
                self.API_URL,
                headers={
                    "Authorization": "Bearer {}".format(access_token),
                    "User-Agent": self.USER_AGENT,
                },
                timeout=15,
            )

            if resp.status_code == 401:
                Logger.warn("codex", "Token expired, attempting refresh")
                if self._refresh_token(auth, auth_path):
                    return self.fetch()
                return None

            if resp.status_code != 200:
                Logger.error("codex", "API returned HTTP {}".format(resp.status_code))
                return None

            data = resp.json()
            usage = self._parse_usage(data)
            if usage:
                self._cache.save("codex", usage)
            return usage

        except Exception as e:
            Logger.error("codex", "Failed to fetch: {}".format(e))
            return None

    def _refresh_token(self, auth: dict, auth_path: Path) -> bool:
        refresh_token = auth.get("tokens", {}).get("refresh_token")
        if not refresh_token:
            return False

        try:
            resp = requests.post(
                "https://auth.openai.com/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                },
                timeout=15,
            )

            if resp.status_code != 200:
                Logger.error("codex", "Token refresh failed: HTTP {}".format(resp.status_code))
                return False

            token_data = resp.json()
            auth["tokens"]["access_token"] = token_data["access_token"]
            if "refresh_token" in token_data:
                auth["tokens"]["refresh_token"] = token_data["refresh_token"]
            auth["tokens"]["expires_at"] = time.time() + token_data.get("expires_in", 3600)

            auth_path.parent.mkdir(parents=True, exist_ok=True)
            with open(auth_path, "w") as f:
                json.dump(auth, f, indent=2)

            Logger.info("codex", "Token refreshed successfully")
            return True

        except Exception as e:
            Logger.error("codex", "Token refresh error: {}".format(e))
            return False

    def _parse_usage(self, data: dict) -> Optional[dict]:
        try:
            rl = data.get("rate_limit", {})
            primary = rl.get("primary_window", {})
            secondary = rl.get("secondary_window", {})
            credits = data.get("rate_limit_reset_credits", {})
            plan = data.get("plan_type", "unknown")

            return {
                "five_hour": float(primary.get("used_percent", 0)),
                "five_hour_reset": primary.get("reset_at", ""),
                "seven_day": float(secondary.get("used_percent", 0)),
                "seven_day_reset": secondary.get("reset_at", ""),
                "reset_credits": credits.get("available_count", 0),
                "plan_type": plan,
            }
        except Exception as e:
            Logger.error("codex", "Failed to parse usage: {}".format(e))
            return None
