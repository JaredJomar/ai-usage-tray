"""
System tray manager with Omarchy-style UI
"""
import ctypes
import os
import sys
import subprocess
import threading
import time
import webbrowser
import winreg
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
import pystray

from config import AppConfig, load_config, save_config
from cache import CacheManager
from providers.codex import CodexProvider
from logger import Logger
from auth import AuthManager

REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "AIUsageTray"


class TrayManager:
    def __init__(self, config: AppConfig):
        self._config = config
        self._cache = CacheManager(config.cache_ttl_seconds)
        self._codex = CodexProvider(self._cache)
        self._auth = AuthManager()
        
        self._codex_data: Optional[dict] = None
        self._last_notify_time = datetime.min
        self._credit_alerts_sent: set[tuple[str, str, int]] = set()
        self._credit_alert_lock = threading.Lock()
        self._window_launch_lock = threading.Lock()
        self._last_dashboard_launch = 0.0
        
        self._tray_icon: Optional[pystray.Icon] = None
        self._running = False

    @staticmethod
    def _is_auto_start_enabled() -> bool:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_KEY, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    @staticmethod
    def _set_auto_start(enable: bool):
        try:
            exe_path = sys.executable
            if getattr(sys, 'frozen', False):
                exe_path = '"{}"'.format(sys.executable)
            else:
                exe_path = '"{}" "{}"'.format(sys.executable, str(Path(__file__).parent / "main.py"))

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_KEY, 0, winreg.KEY_SET_VALUE)
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
                Logger.info("tray", "Auto-start enabled: {}".format(exe_path))
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    Logger.info("tray", "Auto-start disabled")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            Logger.error("tray", "Failed to set auto-start: {}".format(e))

    def run(self):
        self._running = True

        if self._config.launch_on_startup and not self._is_auto_start_enabled():
            self._set_auto_start(True)

        icon = self._create_icon((68, 255, 68))
        auto_start_checked = self._is_auto_start_enabled()
        menu = pystray.Menu(
            pystray.MenuItem("Show Dashboard", self._on_show_dashboard, default=True),
            pystray.MenuItem("Refresh Now", self._on_refresh),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Login to Codex", self._on_login_codex),
            pystray.MenuItem("Settings", self._on_show_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Launch on Startup",
                self._on_toggle_auto_start,
                checked=lambda item: self._is_auto_start_enabled()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Log Folder", self._on_open_log_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit)
        )
        
        self._tray_icon = pystray.Icon(
            "ai-usage",
            icon=icon,
            title="Codex Usage Tray - Loading...",
            menu=menu
        )
        
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        threading.Thread(target=self._refresh_usage, daemon=True).start()
        
        self._tray_icon.run()
    
    def _refresh_loop(self):
        while self._running:
            time.sleep(self._config.refresh_interval_seconds)
            if self._running:
                self._refresh_usage()
    
    def _refresh_usage(self):
        try:
            Logger.info("main", "Refreshing usage data...")
            
            if self._config.codex_enabled:
                self._codex_data = self._codex.fetch()
            
            self._update_icon()
            self._update_tooltip()
            
            if self._config.notifications_enabled:
                self._check_notifications()
            
            Logger.info("main", "Usage data refreshed successfully")
        except Exception as e:
            Logger.error("main", "Failed to refresh: {}".format(e))
    
    def _update_icon(self):
        max_usage = self._get_max_usage()
        
        if max_usage >= 85:
            color = (255, 68, 68)
        elif max_usage >= 60:
            color = (255, 170, 0)
        else:
            color = (68, 255, 68)
        
        icon = self._create_icon(color)
        self._tray_icon.icon = icon
    
    def _update_tooltip(self):
        tooltip = self._build_tooltip()
        self._tray_icon.title = tooltip[:127]
    
    def _build_tooltip(self) -> str:
        if not self._codex_data:
            return "Codex Usage\nNo data\nRight-click > Login"

        h5 = int(self._codex_data["five_hour"])
        h7 = int(self._codex_data["seven_day"])
        r5 = self._format_countdown(self._codex_data["five_hour_reset"])
        r7 = self._format_countdown(self._codex_data["seven_day_reset"])
        credits = self._codex_data.get("reset_credits", 0)
        nearest_credit_expiry = self._nearest_credit_expiry()
        credit_summary = "RESETS: {}".format(credits)
        if nearest_credit_expiry:
            credit_summary += " | expires {}".format(self._format_countdown(nearest_credit_expiry))

        return "CODEX USAGE\n{}\n{}\n{}".format(
            self._format_usage_row("5h", h5, r5),
            self._format_usage_row("7d", h7, r7),
            credit_summary,
        )

    def _format_usage_row(self, period: str, percentage: int, reset: str) -> str:
        return "{:<2} | [{}] | {:>3}% | reset {}".format(
            period,
            self._get_progress_bar(percentage),
            percentage,
            reset,
        )

    def _get_progress_bar(self, percentage: float) -> str:
        filled = int(percentage / 100.0 * 6)
        filled = max(0, min(6, filled))
        return "#" * filled + "-" * (6 - filled)
    
    def _format_countdown(self, timestamp) -> str:
        reset_time = self._parse_timestamp(timestamp)
        if reset_time is None:
            return "-"
        diff = reset_time - datetime.now(timezone.utc)

        if diff.total_seconds() <= 0:
            return "expired"

        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60

        if days > 0:
            return "{}d {}h".format(days, hours)
        if hours > 0:
            return "{}h {}m".format(hours, minutes)
        if minutes > 0:
            return "{}m".format(minutes)
        return "< 1m"

    @staticmethod
    def _parse_timestamp(value: object) -> Optional[datetime]:
        try:
            if isinstance(value, bool) or value is None:
                return None
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return None
                try:
                    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    value = float(value)
                else:
                    if timestamp.tzinfo is None:
                        return timestamp.replace(tzinfo=timezone.utc)
                    return timestamp.astimezone(timezone.utc)
            if isinstance(value, (int, float)):
                if abs(value) >= 100_000_000_000:
                    value /= 1000
                return datetime.fromtimestamp(value, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
        return None

    def _credit_expiries(self) -> list[tuple[str, str, datetime]]:
        if not self._codex_data:
            return []
        entries = self._codex_data.get("reset_credit_expiries", [])
        if not isinstance(entries, list):
            entries = []
        if not entries and self._codex_data.get("reset_credits_expires_at"):
            entries = [{"expires_at": self._codex_data["reset_credits_expires_at"]}]
        valid_expiries = []
        for entry in entries:
            if isinstance(entry, dict):
                expiry_text = entry.get("expires_at")
                credit_id = str(entry.get("id") or expiry_text)
            else:
                expiry_text = entry
                credit_id = str(expiry_text)
            expiry = self._parse_timestamp(expiry_text)
            if expiry is None or expiry <= datetime.now(timezone.utc):
                continue
            valid_expiries.append((credit_id, str(expiry_text), expiry))
        return sorted(valid_expiries, key=lambda entry: (entry[2], entry[0]))

    def _nearest_credit_expiry(self) -> Optional[str]:
        expiries = self._credit_expiries()
        return expiries[0][1] if expiries else None
    
    def _get_max_usage(self) -> float:
        max_usage = 0.0
        if self._codex_data:
            max_usage = max(max_usage, self._codex_data["five_hour"], self._codex_data["seven_day"])
        return max_usage
    
    def _check_notifications(self):
        if not self._codex_data:
            return

        self._check_reset_credit_notifications(datetime.now(timezone.utc))

        now = datetime.now()
        cooldown = timedelta(minutes=self._config.notify_cooldown_minutes)
        
        if now - self._last_notify_time < cooldown:
            return
        
        usage = self._codex_data["five_hour"]
        if usage >= self._config.notify_critical_threshold:
            self._send_notification("codex", usage, "critical")
            self._last_notify_time = now
        elif usage >= self._config.notify_warn_threshold:
            self._send_notification("codex", usage, "normal")
            self._last_notify_time = now

    def _check_reset_credit_notifications(self, now: datetime) -> None:
        with self._credit_alert_lock:
            thresholds = (
                self._config.reset_credit_notify_60_minutes,
                self._config.reset_credit_notify_15_minutes,
            )
            expiries = self._credit_expiries()
            active_credits = {(credit_id, expiry_text) for credit_id, expiry_text, _ in expiries}
            self._credit_alerts_sent.intersection_update(
                {key for key in self._credit_alerts_sent if key[:2] in active_credits},
            )
            for credit_id, expiry_text, expiry in expiries:
                minutes_remaining = (expiry - now).total_seconds() / 60
                if minutes_remaining <= 0:
                    continue
                threshold = thresholds[1] if minutes_remaining <= thresholds[1] else None
                if threshold is None and minutes_remaining <= thresholds[0]:
                    threshold = thresholds[0]
                if threshold is None:
                    continue
                alert_key = (credit_id, expiry_text, threshold)
                if alert_key in self._credit_alerts_sent:
                    continue
                self._send_credit_notification(threshold, minutes_remaining)
                self._credit_alerts_sent.add(alert_key)

    def _send_notification(self, provider: str, usage: float, urgency: str):
        Logger.info("main", "ALERT: {} usage at {}% ({})".format(provider.upper(), int(usage), urgency))
        self._send_native_notification(
            "{} usage is at {}% ({})".format(provider.upper(), int(usage), urgency),
            "AI Usage Tray",
        )

    def _send_credit_notification(self, threshold: int, minutes_remaining: float) -> None:
        Logger.info("main", "ALERT: Codex reset credit expires within {} minutes".format(threshold))
        self._send_native_notification(
            "A reset credit expires in about {} minutes ({} minutes alert).".format(
                max(1, int(minutes_remaining)), threshold,
            ),
            "Codex reset credit expiry",
        )

    def _send_native_notification(self, message: str, title: str) -> None:
        supports_notifications = self._tray_icon is not None and (
            getattr(self._tray_icon, "HAS_NOTIFICATION", False)
            or getattr(pystray.Icon, "HAS_NOTIFICATION", False)
        )
        if not supports_notifications:
            Logger.info("main", "Native notifications are unavailable; alert logged")
            return
        try:
            self._tray_icon.notify(message, title)
        except Exception as error:
            Logger.warn("main", "Native notification failed: {}".format(error))
    
    def _create_icon(self, color: tuple) -> Image.Image:
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        draw.ellipse(
            [4, 4, size - 4, size - 4],
            fill=color + (255,),
            outline=(255, 255, 255, 60),
            width=2,
        )

        max_usage = self._get_max_usage()
        text = "{}".format(int(max_usage))

        try:
            font = ImageFont.truetype("segoeui.ttf", 22)
        except Exception:
            try:
                font = ImageFont.truetype("arial.ttf", 22)
            except Exception:
                font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (size - text_width) // 2
        y = (size - text_height) // 2 - 2

        draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

        return image
    
    def _on_refresh(self, icon, item):
        threading.Thread(target=self._refresh_usage, daemon=True).start()

    def _launch_window(self, argument: str):
        with self._window_launch_lock:
            now = time.monotonic()
            if argument == "--dashboard" and now - self._last_dashboard_launch < 5:
                Logger.info("tray", "Dashboard launch debounced")
                return
            if argument == "--dashboard":
                self._last_dashboard_launch = now
            extra_arguments = []
            if argument == "--dashboard":
                cursor_position = self._get_cursor_position()
                if cursor_position:
                    extra_arguments = [
                        "--dashboard-x={}".format(cursor_position[0]),
                        "--dashboard-y={}".format(cursor_position[1]),
                    ]
            if getattr(sys, "frozen", False):
                command = [sys.executable, argument, *extra_arguments]
            else:
                command = [sys.executable, str(Path(__file__).parent / "main.py"), argument, *extra_arguments]
            try:
                child_log = self._open_child_log() if argument == "--dashboard" else None
                process = subprocess.Popen(
                    command,
                    stderr=child_log,
                    stdout=subprocess.DEVNULL if child_log is not None else None,
                    env=self._child_environment(),
                )
                if argument == "--dashboard":
                    threading.Thread(
                        target=self._retry_window_launch,
                        args=(process, command),
                        daemon=True,
                    ).start()
            except OSError as error:
                Logger.error("tray", "Failed to open window: {}".format(error))

    @staticmethod
    def _child_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        return environment

    @staticmethod
    def _open_child_log():
        try:
            log_dir = Path.home() / "AppData" / "Local" / "AI-Usage-Tray" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return open(log_dir / "dashboard-child.log", "ab")
        except OSError:
            return None

    @staticmethod
    def _retry_window_launch(process, command: list[str]) -> None:
        for delay in (2, 5, 10):
            time.sleep(delay)
            if process.poll() is None:
                return
            if process.poll() == 0:
                return
            Logger.warn("tray", "Dashboard exited during startup; retrying")
            try:
                child_log = TrayManager._open_child_log()
                process = subprocess.Popen(
                    command,
                    stderr=child_log,
                    stdout=subprocess.DEVNULL if child_log is not None else None,
                    env=TrayManager._child_environment(),
                )
            except OSError as error:
                Logger.error("tray", "Dashboard retry failed: {}".format(error))
                return
        if process.poll() not in (None, 0):
            Logger.error("tray", "Dashboard failed to start after retries; see dashboard-child.log")

    @staticmethod
    def _get_cursor_position() -> Optional[tuple[int, int]]:
        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = Point()
        try:
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                return point.x, point.y
        except (AttributeError, OSError) as error:
            Logger.warn("tray", "Could not read cursor position: {}".format(error))
        return None

    def _on_show_dashboard(self, icon, item):
        self._launch_window("--dashboard")

    def _on_show_settings(self, icon, item):
        self._launch_window("--settings")
    
    def _on_login_codex(self, icon, item):
        def on_result(success, message):
            Logger.info("tray", "Codex login result: {}".format(message))
        threading.Thread(
            target=lambda: self._auth.login_codex(on_result),
            daemon=True
        ).start()
    
    def _on_toggle_auto_start(self, icon, item):
        current = self._is_auto_start_enabled()
        enabled = not current
        self._set_auto_start(enabled)
        self._config.launch_on_startup = enabled
        save_config(self._config)

    def _on_open_log_folder(self, icon, item):
        log_dir = Path.home() / "AppData" / "Local" / "AI-Usage-Tray" / "logs"
        if log_dir.exists():
            subprocess.Popen(["explorer", str(log_dir)])
        else:
            Logger.warn("tray", "Log folder not found: {}".format(log_dir))

    def _on_quit(self, icon, item):
        self._running = False
        icon.stop()
