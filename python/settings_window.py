"""
Settings window with authentication management and live usage display.
Uses PyQt6 with Catppuccin Mocha theme.
"""
import sys
import json
import time
import threading

import requests

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QSpinBox, QCheckBox, QGroupBox, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from auth import AuthManager, AuthStatus
from config import AppConfig, load_config, save_config, get_codex_auth_path
from logger import Logger

import sys
import winreg
from pathlib import Path


# ── Catppuccin Mocha Tokens ─────────────────────────────────────────────────
class Ctp:
    BG       = "#1e1e2e"
    SURFACE  = "#313244"
    OVERLAY  = "#45475a"
    TEXT     = "#cdd6f4"
    SUBTEXT  = "#a6adc8"
    BLUE     = "#89b4fa"
    GREEN    = "#a6e3a1"
    RED      = "#f38ba8"
    PEACH    = "#fab387"
    MAUVE    = "#cba6f7"
    SURFACE2 = "#585b70"
    BASE     = "#181825"
    MANTLE   = "#11111b"
    LAVENDER = "#b4befe"


# ── Signals bridge for thread → UI ───────────────────────────────────────────
class AuthSignals(QObject):
    status_update = pyqtSignal(str, object)
    login_result = pyqtSignal(str, bool, str)
    usage_update = pyqtSignal(object)


# ── Styled widgets ───────────────────────────────────────────────────────────
def make_button(text, color=None, accent=False):
    """Create a styled button with optional accent color."""
    btn = QPushButton(text)
    style = """
        QPushButton {{
            background: {bg};
            color: {text};
            border: 1px solid {border};
            padding: 8px 20px;
            border-radius: 8px;
            font-weight: 500;
            min-width: 90px;
        }}
        QPushButton:hover {{
            background: {hover};
        }}
        QPushButton:pressed {{
            background: {pressed};
        }}
        QPushButton:disabled {{
            background: {bg};
            color: {muted};
            border-color: {bg};
        }}
    """
    if accent and color:
        bg = Ctp.SURFACE
        btn.setStyleSheet(style.format(
            bg=bg, text=color, border=color,
            hover=Ctp.OVERLAY, pressed=Ctp.SURFACE2,
            muted=Ctp.SURFACE2
        ))
    else:
        btn.setStyleSheet(style.format(
            bg=Ctp.SURFACE, text=Ctp.TEXT, border=Ctp.OVERLAY,
            hover=Ctp.OVERLAY, pressed=Ctp.SURFACE2,
            muted=Ctp.SURFACE2
        ))
    return btn


def make_dot(color):
    """Return a colored circle string."""
    return '<span style="color:{};">●</span>'.format(color)


def make_bar(percent, width=20):
    """Build a Unicode block bar: filled ▰ + empty ▱."""
    filled = round(width * min(percent, 100) / 100)
    return '<span style="color:{};">{}</span><span style="color:{};">{}</span>'.format(
        Ctp.BLUE if percent < 80 else (Ctp.PEACH if percent < 95 else Ctp.RED),
        "▰" * filled,
        Ctp.SURFACE2,
        "▱" * (width - filled)
    )


def format_reset(unix_ts):
    """Human-readable time remaining until a unix timestamp."""
    now = time.time()
    diff = int(unix_ts - now)
    if diff <= 0:
        return "now"
    h, rem = divmod(diff, 3600)
    m = rem // 60
    if h > 0:
        return "{}h {}m".format(h, m)
    return "{}m".format(m)


# ── Main Window ──────────────────────────────────────────────────────────────
class SettingsWindow(QWidget):
    REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    APP_NAME = "AIUsageTray"

    def __init__(self, auth_manager: AuthManager, config: AppConfig):
        super().__init__()
        self._auth = auth_manager
        self._config = config
        self._signals = AuthSignals()
        self._signals.status_update.connect(self._on_status_update)
        self._signals.login_result.connect(self._on_login_result)
        self._signals.usage_update.connect(self._on_usage_update)
        self._usage_loading = False
        self._init_ui()

    @classmethod
    def _is_auto_start_enabled(cls) -> bool:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.REGISTRY_RUN_KEY, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, cls.APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    @classmethod
    def _set_auto_start(cls, enable: bool):
        try:
            if getattr(sys, 'frozen', False):
                exe_path = sys.executable
            else:
                exe_path = '"{}" "{}"'.format(sys.executable, str(Path(__file__).parent / "main.py"))

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.REGISTRY_RUN_KEY, 0, winreg.KEY_SET_VALUE)
            if enable:
                winreg.SetValueEx(key, cls.APP_NAME, 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, cls.APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            Logger.error("settings", "Failed to set auto-start: {}".format(e))

    def _init_ui(self):
        self.setWindowTitle("AI Usage Tray — Settings")
        self.setFixedSize(620, 680)
        self.setStyleSheet("""
            QWidget {{
                background-color: {bg};
                color: {text};
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
            }}
            QTabWidget::pane {{
                border: 1px solid {overlay};
                border-radius: 10px;
                background: {bg};
            }}
            QTabBar {{
                background: transparent;
            }}
            QTabBar::tab {{
                background: {surface};
                color: {text};
                padding: 10px 28px;
                border: 1px solid {overlay};
                border-bottom: none;
                border-radius: 10px 10px 0 0;
                margin-right: 3px;
                font-weight: 500;
            }}
            QTabBar::tab:selected {{
                background: {bg};
                border-color: {overlay};
                color: {blue};
            }}
            QTabBar::tab:hover:!selected {{
                background: {overlay};
            }}
            QGroupBox {{
                border: 1px solid {overlay};
                border-radius: 10px;
                margin-top: 14px;
                padding: 18px 14px 14px 14px;
                font-weight: 600;
                font-size: 13px;
                color: {lavender};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                color: {lavender};
            }}
            QSpinBox {{
                background: {surface};
                color: {text};
                border: 1px solid {overlay};
                border-radius: 6px;
                padding: 6px 10px;
                min-width: 80px;
                font-size: 13px;
            }}
            QSpinBox:focus {{
                border-color: {blue};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background: {overlay};
                border: none;
                width: 20px;
                border-radius: 3px;
            }}
            QCheckBox {{
                spacing: 10px;
                font-size: 13px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {overlay};
                border-radius: 5px;
                background: {surface};
            }}
            QCheckBox::indicator:checked {{
                background: {blue};
                border-color: {blue};
            }}
            QCheckBox::indicator:hover {{
                border-color: {blue};
            }}
            QFrame {{
                border: none;
            }}
        """.format(
            bg=Ctp.BG, text=Ctp.TEXT, surface=Ctp.SURFACE,
            overlay=Ctp.OVERLAY, blue=Ctp.BLUE, lavender=Ctp.LAVENDER
        ))

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(4)

        tabs = QTabWidget()
        tabs.addTab(self._create_auth_tab(), "  Authentication  ")
        tabs.addTab(self._create_settings_tab(), "  Settings  ")
        root.addWidget(tabs)

    # ── Auth Tab ─────────────────────────────────────────────────────────
    def _create_auth_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header
        title = QLabel("OpenAI Codex")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: {};".format(Ctp.TEXT))
        layout.addWidget(title)

        subtitle = QLabel("Authentication & Usage Monitor")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: {}; font-size: 12px;".format(Ctp.SUBTEXT))
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        # ── Auth Status Group ────────────────────────────────────────────
        auth_group = QGroupBox("Connection Status")
        auth_lay = QVBoxLayout(auth_group)
        auth_lay.setSpacing(10)

        status_row = QHBoxLayout()
        self._status_dot = QLabel(make_dot(Ctp.SUBTEXT))
        self._status_dot.setFont(QFont("Segoe UI", 14))
        status_row.addWidget(self._status_dot)

        self._codex_status = QLabel("Checking...")
        self._codex_status.setWordWrap(True)
        self._codex_status.setStyleSheet("font-size: 13px;")
        status_row.addWidget(self._codex_status, 1)
        auth_lay.addLayout(status_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._codex_login_btn = make_button("Login", accent=True, color=Ctp.GREEN)
        self._codex_login_btn.clicked.connect(self._login_codex)
        btn_row.addWidget(self._codex_login_btn)

        self._codex_logout_btn = make_button("Logout", accent=True, color=Ctp.RED)
        self._codex_logout_btn.clicked.connect(self._logout_codex)
        self._codex_logout_btn.setEnabled(False)
        btn_row.addWidget(self._codex_logout_btn)

        auth_lay.addLayout(btn_row)
        layout.addWidget(auth_group)

        # ── Usage Display Group ──────────────────────────────────────────
        usage_group = QGroupBox("Live Usage")
        usage_lay = QVBoxLayout(usage_group)
        usage_lay.setSpacing(10)

        self._usage_stack = QVBoxLayout()

        # Loading placeholder
        self._usage_loading_label = QLabel("Loading usage data...")
        self._usage_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._usage_loading_label.setStyleSheet("color: {}; padding: 12px;".format(Ctp.SUBTEXT))
        self._usage_stack.addWidget(self._usage_loading_label)

        # Actual usage container (hidden until data arrives)
        self._usage_container = QWidget()
        uc_layout = QVBoxLayout(self._usage_container)
        uc_layout.setContentsMargins(0, 0, 0, 0)
        uc_layout.setSpacing(10)
        self._usage_container.setVisible(False)

        # 5-hour window
        self._usage_5h_title = QLabel()
        self._usage_5h_title.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self._usage_5h_title.setStyleSheet("color: {};".format(Ctp.TEXT))
        uc_layout.addWidget(self._usage_5h_title)

        self._usage_5h_bar_label = QLabel()
        self._usage_5h_bar_label.setFont(QFont("Consolas", 13))
        self._usage_5h_bar_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        uc_layout.addWidget(self._usage_5h_bar_label)

        self._usage_5h_detail = QLabel()
        self._usage_5h_detail.setStyleSheet("color: {}; font-size: 11px;".format(Ctp.SUBTEXT))
        uc_layout.addWidget(self._usage_5h_detail)

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("background: {}; max-height: 1px;".format(Ctp.OVERLAY))
        uc_layout.addWidget(sep1)

        # 7-day window
        self._usage_7d_title = QLabel()
        self._usage_7d_title.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self._usage_7d_title.setStyleSheet("color: {};".format(Ctp.TEXT))
        uc_layout.addWidget(self._usage_7d_title)

        self._usage_7d_bar_label = QLabel()
        self._usage_7d_bar_label.setFont(QFont("Consolas", 13))
        self._usage_7d_bar_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        uc_layout.addWidget(self._usage_7d_bar_label)

        self._usage_7d_detail = QLabel()
        self._usage_7d_detail.setStyleSheet("color: {}; font-size: 11px;".format(Ctp.SUBTEXT))
        uc_layout.addWidget(self._usage_7d_detail)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background: {}; max-height: 1px;".format(Ctp.OVERLAY))
        uc_layout.addWidget(sep2)

        # Reset credits
        self._usage_credits_label = QLabel()
        self._usage_credits_label.setStyleSheet("font-size: 12px;")
        uc_layout.addWidget(self._usage_credits_label)

        # Plan type badge
        self._plan_badge = QLabel()
        self._plan_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plan_badge.setStyleSheet("""
            background: {surface};
            color: {mauve};
            border: 1px solid {overlay};
            border-radius: 6px;
            padding: 4px 12px;
            font-size: 11px;
            font-weight: 600;
            max-width: 120px;
        """.format(surface=Ctp.SURFACE, mauve=Ctp.MAUVE, overlay=Ctp.OVERLAY))
        uc_layout.addWidget(self._plan_badge, alignment=Qt.AlignmentFlag.AlignCenter)

        self._usage_stack.addWidget(self._usage_container)
        usage_lay.addLayout(self._usage_stack)

        # Refresh button
        usage_btn_row = QHBoxLayout()
        usage_btn_row.addStretch()
        self._usage_refresh_btn = make_button("↻  Refresh", accent=True, color=Ctp.BLUE)
        self._usage_refresh_btn.clicked.connect(self._fetch_usage)
        usage_btn_row.addWidget(self._usage_refresh_btn)
        usage_lay.addLayout(usage_btn_row)

        layout.addWidget(usage_group)
        layout.addStretch()

        # Initial status check + usage fetch
        QTimer.singleShot(100, self._refresh_status)
        QTimer.singleShot(300, self._fetch_usage)

        return widget

    # ── Settings Tab ─────────────────────────────────────────────────────
    def _create_settings_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        title = QLabel("General Settings")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: {};".format(Ctp.TEXT))
        layout.addWidget(title)

        layout.addSpacing(4)

        # ── Polling Group ────────────────────────────────────────────────
        poll_group = QGroupBox("Polling")
        poll_lay = QVBoxLayout(poll_group)
        poll_lay.setSpacing(10)

        refresh_row = QHBoxLayout()
        refresh_row.addWidget(QLabel("Refresh Interval:"))
        refresh_row.addStretch()
        self._refresh_spin = QSpinBox()
        self._refresh_spin.setRange(30, 3600)
        self._refresh_spin.setValue(self._config.refresh_interval_seconds)
        self._refresh_spin.setSuffix(" s")
        refresh_row.addWidget(self._refresh_spin)
        poll_lay.addLayout(refresh_row)

        cache_row = QHBoxLayout()
        cache_row.addWidget(QLabel("Cache TTL:"))
        cache_row.addStretch()
        self._cache_spin = QSpinBox()
        self._cache_spin.setRange(10, 300)
        self._cache_spin.setValue(self._config.cache_ttl_seconds)
        self._cache_spin.setSuffix(" s")
        cache_row.addWidget(self._cache_spin)
        poll_lay.addLayout(cache_row)

        layout.addWidget(poll_group)

        # ── Notifications Group ──────────────────────────────────────────
        notif_group = QGroupBox("Notifications")
        notif_lay = QVBoxLayout(notif_group)
        notif_lay.setSpacing(10)

        self._notify_check = QCheckBox("Enable Notifications")
        self._notify_check.setChecked(self._config.notifications_enabled)
        notif_lay.addWidget(self._notify_check)

        warn_row = QHBoxLayout()
        warn_row.addWidget(QLabel("Warning Threshold:"))
        warn_row.addStretch()
        self._warn_spin = QSpinBox()
        self._warn_spin.setRange(50, 100)
        self._warn_spin.setValue(self._config.notify_warn_threshold)
        self._warn_spin.setSuffix(" %")
        warn_row.addWidget(self._warn_spin)
        notif_lay.addLayout(warn_row)

        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel("Critical Threshold:"))
        crit_row.addStretch()
        self._crit_spin = QSpinBox()
        self._crit_spin.setRange(50, 100)
        self._crit_spin.setValue(self._config.notify_critical_threshold)
        self._crit_spin.setSuffix(" %")
        crit_row.addWidget(self._crit_spin)
        notif_lay.addLayout(crit_row)

        layout.addWidget(notif_group)

        # ── Codex Group ──────────────────────────────────────────────────
        codex_group = QGroupBox("Codex")
        codex_lay = QVBoxLayout(codex_group)
        codex_lay.setSpacing(8)

        self._codex_check = QCheckBox("Enable Codex Monitoring")
        self._codex_check.setChecked(self._config.codex_enabled)
        codex_lay.addWidget(self._codex_check)

        layout.addWidget(codex_group)

        # ── Startup Group ────────────────────────────────────────────────
        startup_group = QGroupBox("Startup")
        startup_lay = QVBoxLayout(startup_group)
        startup_lay.setSpacing(8)

        self._autostart_check = QCheckBox("Launch on Windows Startup")
        self._autostart_check.setChecked(self._is_auto_start_enabled())
        startup_lay.addWidget(self._autostart_check)

        layout.addWidget(startup_group)
        layout.addSpacing(6)

        # Save button
        self._save_btn = make_button("Save Settings", accent=True, color=Ctp.GREEN)
        self._save_btn.clicked.connect(self._save_settings)
        layout.addWidget(self._save_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()

        return widget

    # ── Status Handling ──────────────────────────────────────────────────
    def _refresh_status(self):
        def _worker():
            codex_status = self._auth.get_codex_status()
            self._signals.status_update.emit("codex", codex_status)
        threading.Thread(target=_worker, daemon=True).start()

    def _on_status_update(self, provider, status):
        label = self._codex_status
        dot = self._status_dot
        login_btn = self._codex_login_btn
        logout_btn = self._codex_logout_btn

        if status.authenticated:
            text = "Authenticated"
            if status.email:
                text += "\n{}".format(status.email)
            if status.message:
                text += "\n{}".format(status.message)
            label.setText(text)
            label.setStyleSheet("color: {}; font-size: 13px;".format(Ctp.GREEN))
            dot.setText(make_dot(Ctp.GREEN))
            login_btn.setEnabled(False)
            logout_btn.setEnabled(True)
        else:
            text = "Not Authenticated"
            if status.message:
                text += "\n{}".format(status.message)
            label.setText(text)
            label.setStyleSheet("color: {}; font-size: 13px;".format(Ctp.RED))
            dot.setText(make_dot(Ctp.RED))
            login_btn.setEnabled(True)
            logout_btn.setEnabled(False)

    # ── Login / Logout ───────────────────────────────────────────────────
    def _login_codex(self):
        self._codex_login_btn.setEnabled(False)
        self._codex_login_btn.setText("Logging in...")

        def _worker():
            def callback(success, message):
                self._signals.login_result.emit("codex", success, message)
            self._auth.login_codex(callback)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_login_result(self, provider, success, message):
        self._codex_login_btn.setText("Login")
        self._codex_login_btn.setEnabled(not success)

        if success:
            self._refresh_status()
            self._fetch_usage()
        else:
            Logger.error("settings", "{} login failed: {}".format(provider, message))

    def _logout_codex(self):
        self._auth.logout_codex()
        self._refresh_status()
        self._usage_container.setVisible(False)
        self._usage_loading_label.setVisible(True)
        self._usage_loading_label.setText("Not authenticated. Please log in.")

    # ── Usage Fetching ───────────────────────────────────────────────────
    def _fetch_usage(self):
        if self._usage_loading:
            return
        self._usage_loading = True
        self._usage_loading_label.setVisible(True)
        self._usage_loading_label.setText("Loading usage data...")
        self._usage_container.setVisible(False)
        self._usage_refresh_btn.setEnabled(False)

        def _worker():
            data = self._fetch_usage_direct()
            self._signals.usage_update.emit(data)

        threading.Thread(target=_worker, daemon=True).start()

    def _fetch_usage_direct(self):
        """Fetch usage directly from the API using the stored access token."""
        auth_path = get_codex_auth_path()
        if not auth_path.exists():
            return {"error": "Not authenticated. Please log in first."}

        try:
            with open(auth_path, "r") as f:
                auth = json.load(f)

            access_token = auth.get("tokens", {}).get("access_token")
            if not access_token:
                return {"error": "No access token found. Please log in."}

            resp = requests.get(
                "https://chatgpt.com/backend-api/wham/usage",
                headers={
                    "Authorization": "Bearer {}".format(access_token),
                    "User-Agent": "codex_cli_rs",
                },
                timeout=15,
            )

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                return {"error": "Token expired. Please log in again."}
            else:
                return {"error": "API returned HTTP {}.".format(resp.status_code)}
        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your connection."}
        except requests.exceptions.ConnectionError:
            return {"error": "No internet connection."}
        except Exception as e:
            return {"error": "Failed: {}".format(e)}

    def _on_usage_update(self, data):
        self._usage_loading = False
        self._usage_refresh_btn.setEnabled(True)

        if not data or "error" in data:
            self._usage_loading_label.setText(data.get("error", "Unknown error"))
            self._usage_loading_label.setStyleSheet("color: {}; padding: 12px;".format(Ctp.RED))
            return

        self._usage_loading_label.setVisible(False)
        self._usage_container.setVisible(True)

        rl = data.get("rate_limit", {})
        primary = rl.get("primary_window", {})
        secondary = rl.get("secondary_window", {})
        credits = data.get("rate_limit_reset_credits", {})
        plan = data.get("plan_type", "unknown")

        # 5-hour window (primary)
        p_used = primary.get("used_percent", 0)
        p_reset = primary.get("reset_at", 0)
        self._usage_5h_title.setText("Hourly Window  {}".format(make_dot(
            Ctp.GREEN if p_used < 80 else (Ctp.PEACH if p_used < 95 else Ctp.RED)
        )))
        self._usage_5h_bar_label.setText("{}  {}%".format(make_bar(p_used), p_used))
        self._usage_5h_detail.setText("Resets in: {}   |   Used: {}%".format(
            format_reset(p_reset), p_used
        ))

        # 7-day window (secondary)
        s_used = secondary.get("used_percent", 0)
        s_reset = secondary.get("reset_at", 0)
        self._usage_7d_title.setText("Weekly Window  {}".format(make_dot(
            Ctp.GREEN if s_used < 80 else (Ctp.PEACH if s_used < 95 else Ctp.RED)
        )))
        self._usage_7d_bar_label.setText("{}  {}%".format(make_bar(s_used), s_used))
        self._usage_7d_detail.setText("Resets in: {}   |   Used: {}%".format(
            format_reset(s_reset), s_used
        ))

        # Reset credits
        avail = credits.get("available_count", 0)
        self._usage_credits_label.setText(
            '{} Reset Credits: <span style="color:{}; font-weight:600;">{}</span>'.format(
                make_dot(Ctp.LAVENDER), Ctp.LAVENDER if avail > 0 else Ctp.RED, avail
            )
        )

        # Plan badge
        self._plan_badge.setText("Plan: {}".format(plan.upper()))

    # ── Save Settings ────────────────────────────────────────────────────
    def _save_settings(self):
        try:
            self._config.refresh_interval_seconds = self._refresh_spin.value()
            self._config.cache_ttl_seconds = self._cache_spin.value()
            self._config.notifications_enabled = self._notify_check.isChecked()
            self._config.notify_warn_threshold = self._warn_spin.value()
            self._config.notify_critical_threshold = self._crit_spin.value()
            self._config.codex_enabled = self._codex_check.isChecked()
            save_config(self._config)

            autostart = self._autostart_check.isChecked()
            self._set_auto_start(autostart)
            Logger.info("settings", "Settings saved successfully")

            # Brief visual feedback
            orig_text = self._save_btn.text()
            self._save_btn.setText("Saved!")
            self._save_btn.setEnabled(False)
            QTimer.singleShot(1200, lambda: (
                self._save_btn.setText(orig_text),
                self._save_btn.setEnabled(True)
            ))
        except Exception as e:
            Logger.error("settings", "Failed to save settings: {}".format(e))


# ── Entry Points ─────────────────────────────────────────────────────────────
def show_settings(auth_manager: AuthManager, config: AppConfig):
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = SettingsWindow(auth_manager, config)
    window.show()
    app.exec()


if __name__ == "__main__":
    from auth import AuthManager
    from config import load_config

    auth = AuthManager()
    config = load_config()
    show_settings(auth, config)
