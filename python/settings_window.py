import sys
import json
import time
import threading

import requests

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QSpinBox, QCheckBox, QGroupBox, QFrame,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from auth import AuthManager, AuthStatus
from config import AppConfig, load_config, save_config, get_codex_auth_path
from logger import Logger

import winreg
from pathlib import Path


class Ctp:
    BG_START = "#0f172a"
    BG_END = "#1e3a5f"
    CARD = "#1e293b"
    CARD_ELEVATED = "#26354a"
    BORDER = "#334155"
    TEXT = "#f8fafc"
    SUBTEXT = "#94a3b8"
    MUTED = "#64748b"
    BLUE = "#38bdf8"
    BLUE_DEEP = "#0ea5e9"
    GREEN = "#22c55e"
    RED = "#ef4444"
    AMBER = "#f59e0b"
    WHITE = "#ffffff"


class AuthSignals(QObject):
    status_update = pyqtSignal(str, object)
    login_result = pyqtSignal(str, bool, str)
    usage_update = pyqtSignal(object)


def make_button(text, color=None, accent=False):
    btn = QPushButton(text)
    if accent and color:
        background = color
        foreground = Ctp.BG_START
        hover = Ctp.WHITE
        border = color
    else:
        background = Ctp.CARD_ELEVATED
        foreground = Ctp.TEXT
        hover = Ctp.BORDER
        border = Ctp.BORDER
    btn.setStyleSheet("""
        QPushButton {{
            background: {background}; color: {foreground}; border: 1px solid {border};
            border-radius: 8px; padding: 8px 16px; min-width: 88px;
            font-family: 'Aptos Display'; font-size: 12px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed {{ background: {border}; color: {foreground}; }}
        QPushButton:disabled {{ background: {card}; color: {muted}; border-color: {card}; }}
    """.format(
        background=background, foreground=foreground, border=border, hover=hover,
        card=Ctp.CARD, muted=Ctp.MUTED,
    ))
    return btn


def make_dot(color):
    return '<span style="color:{};">●</span>'.format(color)


def make_bar(percent, width=20):
    filled = round(width * min(percent, 100) / 100)
    return '<span style="color:{};">{}</span><span style="color:{};">{}</span>'.format(
        Ctp.BLUE if percent < 80 else (Ctp.AMBER if percent < 95 else Ctp.RED),
        "▰" * filled,
        Ctp.BORDER,
        "▱" * (width - filled),
    )


def format_reset(unix_ts):
    now = time.time()
    diff = int(unix_ts - now)
    if diff <= 0:
        return "now"
    h, rem = divmod(diff, 3600)
    m = rem // 60
    if h > 0:
        return "{}h {}m".format(h, m)
    return "{}m".format(m)


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
        self.setObjectName("settingsWindow")
        self.setWindowTitle("AI Usage Tray — Codex Settings")
        self.setFixedSize(640, 720)
        self.setStyleSheet("""
            QWidget#settingsWindow {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {bg_start}, stop:1 {bg_end});
                color: {text}; font-family: 'Aptos'; font-size: 12px;
            }}
            QTabWidget::pane {{ border: 0; background: transparent; }}
            QTabBar {{ background: transparent; }}
            QTabBar::tab {{
                background: transparent; color: {subtext}; border: 0;
                border-bottom: 2px solid transparent; padding: 9px 20px;
                font-family: 'Aptos Display'; font-size: 12px; font-weight: 600;
            }}
            QTabBar::tab:selected {{ color: {text}; border-bottom-color: {blue}; }}
            QTabBar::tab:hover:!selected {{ color: {text}; }}
            QFrame#usageCard, QFrame#headerCard, QFrame#statusBar {{
                background: {card}; border: 1px solid {border}; border-radius: 12px;
            }}
            QGroupBox {{
                background: {card}; border: 1px solid {border}; border-radius: 10px;
                margin-top: 12px; padding: 15px 13px 12px;
                color: {text}; font-family: 'Aptos Display'; font-size: 12px; font-weight: 600;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
            QSpinBox {{
                background: {card_elevated}; color: {text}; border: 1px solid {border};
                border-radius: 7px; padding: 6px 8px; min-width: 82px;
            }}
            QSpinBox:focus {{ border-color: {blue}; }}
            QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; background: {border}; border: 0; }}
            QCheckBox {{ color: {text}; spacing: 8px; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px; border-radius: 5px;
                border: 1px solid {border}; background: {card_elevated};
            }}
            QCheckBox::indicator:checked {{ background: {blue}; border-color: {blue}; }}
            QTableWidget {{
                background: transparent; border: 0; gridline-color: {border}; color: {text};
                selection-background-color: {card_elevated}; selection-color: {text};
            }}
            QHeaderView::section {{
                background: transparent; color: {subtext}; border: 0; border-bottom: 1px solid {border};
                padding: 4px 5px; font-family: 'Aptos Display'; font-size: 10px; font-weight: 600;
            }}
        """.format(
            bg_start=Ctp.BG_START, bg_end=Ctp.BG_END, card=Ctp.CARD,
            card_elevated=Ctp.CARD_ELEVATED, border=Ctp.BORDER, text=Ctp.TEXT,
            subtext=Ctp.SUBTEXT, blue=Ctp.BLUE,
        ))

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(4)
        tabs = QTabWidget()
        tabs.addTab(self._create_auth_tab(), "Usage")
        tabs.addTab(self._create_settings_tab(), "Settings")
        root.addWidget(tabs)

    def _section_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("color: {}; font-family: 'Aptos Display'; font-size: 11px; font-weight: 700;".format(Ctp.SUBTEXT))
        return label

    def _make_usage_card(self, title):
        card = QFrame()
        card.setObjectName("usageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)

        heading = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet("color: {}; font-family: 'Aptos Display'; font-size: 13px; font-weight: 700;".format(Ctp.TEXT))
        percent = QLabel("0%")
        percent.setStyleSheet("color: {}; font-family: 'Aptos Display'; font-size: 18px; font-weight: 700;".format(Ctp.TEXT))
        heading.addWidget(label)
        heading.addStretch()
        heading.addWidget(percent)
        layout.addLayout(heading)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setTextVisible(False)
        progress.setFixedHeight(9)
        layout.addWidget(progress)

        meta = QHBoxLayout()
        detail = QLabel("Resets in: —")
        detail.setStyleSheet("color: {}; font-size: 10px;".format(Ctp.SUBTEXT))
        delta = QLabel("▼4%")
        delta.setStyleSheet("color: {}; font-family: 'Aptos Display'; font-size: 10px; font-weight: 700;".format(Ctp.GREEN))
        meta.addWidget(detail)
        meta.addStretch()
        meta.addWidget(delta)
        layout.addLayout(meta)

        micro = QLabel()
        micro.setVisible(False)
        layout.addWidget(micro)
        return card, percent, progress, detail, delta, micro

    def _set_progress_style(self, progress, percent):
        color = Ctp.BLUE if percent < 80 else (Ctp.AMBER if percent < 95 else Ctp.RED)
        progress.setStyleSheet("""
            QProgressBar {{ background: {border}; border: 0; border-radius: 4px; }}
            QProgressBar::chunk {{
                border-radius: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {blue}, stop:1 {color});
            }}
        """.format(border=Ctp.BORDER, blue=Ctp.BLUE, color=color))

    def _create_auth_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 10, 5, 0)
        layout.setSpacing(9)

        header = QFrame()
        header.setObjectName("headerCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 11, 15, 11)
        provider = QVBoxLayout()
        provider.setSpacing(1)
        self._provider_header = QLabel("{}  Codex 1  11%".format(make_dot(Ctp.BLUE)))
        self._provider_header.setStyleSheet("color: {}; font-family: 'Aptos Display'; font-size: 15px; font-weight: 700;".format(Ctp.TEXT))
        provider.addWidget(self._provider_header)
        provider_caption = QLabel("Usage overview")
        provider_caption.setStyleSheet("color: {}; font-size: 10px;".format(Ctp.SUBTEXT))
        provider.addWidget(provider_caption)
        header_layout.addLayout(provider)
        header_layout.addStretch()
        self._plan_badge = QLabel("CODEX")
        self._plan_badge.setStyleSheet("background: {}; color: {}; border-radius: 7px; padding: 5px 8px; font-family: 'Aptos Display'; font-size: 10px; font-weight: 700;".format(Ctp.CARD_ELEVATED, Ctp.BLUE))
        header_layout.addWidget(self._plan_badge)
        layout.addWidget(header)

        connection = QHBoxLayout()
        self._status_dot = QLabel(make_dot(Ctp.SUBTEXT))
        self._status_dot.setStyleSheet("font-size: 13px;")
        connection.addWidget(self._status_dot)
        self._codex_status = QLabel("Checking connection…")
        self._codex_status.setStyleSheet("color: {}; font-size: 11px;".format(Ctp.SUBTEXT))
        connection.addWidget(self._codex_status, 1)
        self._codex_login_btn = make_button("Login", accent=True, color=Ctp.BLUE)
        self._codex_login_btn.clicked.connect(self._login_codex)
        connection.addWidget(self._codex_login_btn)
        self._codex_logout_btn = make_button("Logout")
        self._codex_logout_btn.clicked.connect(self._logout_codex)
        self._codex_logout_btn.setEnabled(False)
        connection.addWidget(self._codex_logout_btn)
        layout.addLayout(connection)

        layout.addWidget(self._section_label("RATE LIMITS"))
        (
            card, self._usage_5h_percent, self._usage_5h_bar, self._usage_5h_detail,
            self._usage_5h_delta, self._usage_5h_micro,
        ) = self._make_usage_card("5-Hour Session")
        layout.addWidget(card)
        (
            card, self._usage_7d_percent, self._usage_7d_bar, self._usage_7d_detail,
            self._usage_7d_delta, self._usage_7d_micro,
        ) = self._make_usage_card("Weekly Limit")
        layout.addWidget(card)

        table_card = QFrame()
        table_card.setObjectName("usageCard")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(14, 10, 14, 10)
        table_layout.setSpacing(3)
        table_layout.addWidget(self._section_label("TOKEN USAGE"))
        self._token_table = QTableWidget(1, 4)
        self._token_table.setHorizontalHeaderLabels(["Input", "Output", "Cache", "Cost"])
        self._token_table.setVerticalHeaderLabels(["Session"])
        self._token_table.verticalHeader().setVisible(False)
        self._token_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._token_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._token_table.setFixedHeight(49)
        self._token_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column in range(4):
            item = QTableWidgetItem("—")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._token_table.setItem(0, column, item)
        table_layout.addWidget(self._token_table)
        layout.addWidget(table_card)

        model_row = QHBoxLayout()
        model_row.addWidget(self._section_label("MODEL BREAKDOWN"))
        model_row.addStretch()
        self._model_summary = QLabel("Codex · current session")
        self._model_summary.setStyleSheet("color: {}; font-size: 10px;".format(Ctp.SUBTEXT))
        model_row.addWidget(self._model_summary)
        layout.addLayout(model_row)

        status_bar = QFrame()
        status_bar.setObjectName("statusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(12, 6, 12, 6)
        self._updated_status = QLabel("{}  Updated just now".format(make_dot(Ctp.GREEN)))
        self._updated_status.setStyleSheet("color: {}; font-size: 10px;".format(Ctp.SUBTEXT))
        status_layout.addWidget(self._updated_status)
        status_layout.addStretch()
        self._usage_refresh_btn = make_button("Refresh")
        self._usage_refresh_btn.clicked.connect(self._fetch_usage)
        status_layout.addWidget(self._usage_refresh_btn)
        layout.addWidget(status_bar)

        QTimer.singleShot(100, self._refresh_status)
        QTimer.singleShot(300, self._fetch_usage)
        return widget

    def _create_settings_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 8, 10, 6)
        layout.setSpacing(9)

        title = QLabel("Preferences")
        title.setStyleSheet("color: {}; font-family: 'Aptos Display'; font-size: 19px; font-weight: 700;".format(Ctp.TEXT))
        layout.addWidget(title)
        subtitle = QLabel("Control how AI Usage Tray monitors your Codex account.")
        subtitle.setStyleSheet("color: {}; font-size: 11px;".format(Ctp.SUBTEXT))
        layout.addWidget(subtitle)

        poll_group = QGroupBox("Polling")
        poll_lay = QVBoxLayout(poll_group)
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(QLabel("Refresh interval"))
        refresh_row.addStretch()
        self._refresh_spin = QSpinBox()
        self._refresh_spin.setRange(30, 3600)
        self._refresh_spin.setValue(self._config.refresh_interval_seconds)
        self._refresh_spin.setSuffix(" s")
        refresh_row.addWidget(self._refresh_spin)
        poll_lay.addLayout(refresh_row)
        cache_row = QHBoxLayout()
        cache_row.addWidget(QLabel("Cache TTL"))
        cache_row.addStretch()
        self._cache_spin = QSpinBox()
        self._cache_spin.setRange(10, 300)
        self._cache_spin.setValue(self._config.cache_ttl_seconds)
        self._cache_spin.setSuffix(" s")
        cache_row.addWidget(self._cache_spin)
        poll_lay.addLayout(cache_row)
        layout.addWidget(poll_group)

        notif_group = QGroupBox("Notifications")
        notif_lay = QVBoxLayout(notif_group)
        self._notify_check = QCheckBox("Enable notifications")
        self._notify_check.setChecked(self._config.notifications_enabled)
        notif_lay.addWidget(self._notify_check)
        warn_row = QHBoxLayout()
        warn_row.addWidget(QLabel("Warning threshold"))
        warn_row.addStretch()
        self._warn_spin = QSpinBox()
        self._warn_spin.setRange(50, 100)
        self._warn_spin.setValue(self._config.notify_warn_threshold)
        self._warn_spin.setSuffix(" %")
        warn_row.addWidget(self._warn_spin)
        notif_lay.addLayout(warn_row)
        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel("Critical threshold"))
        crit_row.addStretch()
        self._crit_spin = QSpinBox()
        self._crit_spin.setRange(50, 100)
        self._crit_spin.setValue(self._config.notify_critical_threshold)
        self._crit_spin.setSuffix(" %")
        crit_row.addWidget(self._crit_spin)
        notif_lay.addLayout(crit_row)
        layout.addWidget(notif_group)

        codex_group = QGroupBox("Codex")
        codex_lay = QVBoxLayout(codex_group)
        self._codex_check = QCheckBox("Enable Codex monitoring")
        self._codex_check.setChecked(self._config.codex_enabled)
        codex_lay.addWidget(self._codex_check)
        layout.addWidget(codex_group)

        startup_group = QGroupBox("Startup")
        startup_lay = QVBoxLayout(startup_group)
        self._autostart_check = QCheckBox("Launch on Windows startup")
        self._autostart_check.setChecked(self._is_auto_start_enabled())
        startup_lay.addWidget(self._autostart_check)
        layout.addWidget(startup_group)

        layout.addStretch()
        self._save_btn = make_button("Save Settings", accent=True, color=Ctp.BLUE)
        self._save_btn.clicked.connect(self._save_settings)
        layout.addWidget(self._save_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return widget

    def _refresh_status(self):
        def _worker():
            codex_status = self._auth.get_codex_status()
            self._signals.status_update.emit("codex", codex_status)
        threading.Thread(target=_worker, daemon=True).start()

    def _on_status_update(self, provider, status):
        if status.authenticated:
            text = "Connected"
            if status.email:
                text += " · {}".format(status.email)
            if status.message:
                text += " · {}".format(status.message)
            self._codex_status.setText(text)
            self._codex_status.setStyleSheet("color: {}; font-size: 11px;".format(Ctp.GREEN))
            self._status_dot.setText(make_dot(Ctp.GREEN))
            self._codex_login_btn.setEnabled(False)
            self._codex_logout_btn.setEnabled(True)
        else:
            text = "Not connected"
            if status.message:
                text += " · {}".format(status.message)
            self._codex_status.setText(text)
            self._codex_status.setStyleSheet("color: {}; font-size: 11px;".format(Ctp.RED))
            self._status_dot.setText(make_dot(Ctp.RED))
            self._codex_login_btn.setEnabled(True)
            self._codex_logout_btn.setEnabled(False)

    def _login_codex(self):
        self._codex_login_btn.setEnabled(False)
        self._codex_login_btn.setText("Logging in…")

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
        self._provider_header.setText("{}  Codex 1  11%".format(make_dot(Ctp.BLUE)))
        self._updated_status.setText("{}  Sign in to refresh usage".format(make_dot(Ctp.SUBTEXT)))

    def _fetch_usage(self):
        if self._usage_loading:
            return
        self._usage_loading = True
        self._usage_refresh_btn.setEnabled(False)
        self._updated_status.setText("{}  Refreshing usage…".format(make_dot(Ctp.BLUE)))

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

    def _reset_detail(self, reset_at):
        if not reset_at:
            return "Resets in: —"
        reset_time = time.strftime("%I:%M %p", time.localtime(reset_at)).lstrip("0")
        return "Resets in: {} at {}  |  On track".format(format_reset(reset_at), reset_time)

    def _update_usage_card(self, percent_label, progress, detail, delta, micro, used, reset_at):
        used = float(used or 0)
        percent_label.setText("{:.0f}%".format(used))
        progress.setValue(round(used))
        self._set_progress_style(progress, used)
        detail.setText(self._reset_detail(reset_at))
        state_color = Ctp.GREEN if used < 80 else (Ctp.AMBER if used < 95 else Ctp.RED)
        delta.setStyleSheet("color: {}; font-family: 'Aptos Display'; font-size: 10px; font-weight: 700;".format(state_color))
        delta.setText("▼4%")
        micro.setText(make_bar(used, 12))

    def _on_usage_update(self, data):
        self._usage_loading = False
        self._usage_refresh_btn.setEnabled(True)
        if not data or "error" in data:
            message = data.get("error", "Unknown error") if data else "Unknown error"
            self._updated_status.setText("{}  {}".format(make_dot(Ctp.RED), message))
            return

        rl = data.get("rate_limit", {})
        primary = rl.get("primary_window", {})
        secondary = rl.get("secondary_window", {})
        credits = data.get("rate_limit_reset_credits", {})
        plan = data.get("plan_type", "codex")
        p_used = primary.get("used_percent", 0)
        s_used = secondary.get("used_percent", 0)

        self._provider_header.setText("{}  Codex 1  {:.0f}%".format(make_dot(Ctp.BLUE), float(p_used or 0)))
        self._plan_badge.setText(str(plan).upper())
        self._model_summary.setText("Codex · {} reset credit{}".format(
            credits.get("available_count", 0),
            "s" if credits.get("available_count", 0) != 1 else "",
        ))
        self._update_usage_card(
            self._usage_5h_percent, self._usage_5h_bar, self._usage_5h_detail,
            self._usage_5h_delta, self._usage_5h_micro, p_used, primary.get("reset_at", 0),
        )
        self._update_usage_card(
            self._usage_7d_percent, self._usage_7d_bar, self._usage_7d_detail,
            self._usage_7d_delta, self._usage_7d_micro, s_used, secondary.get("reset_at", 0),
        )
        self._updated_status.setText("{}  Updated just now".format(make_dot(Ctp.GREEN)))

    def _save_settings(self):
        try:
            self._config.refresh_interval_seconds = self._refresh_spin.value()
            self._config.cache_ttl_seconds = self._cache_spin.value()
            self._config.notifications_enabled = self._notify_check.isChecked()
            self._config.notify_warn_threshold = self._warn_spin.value()
            self._config.notify_critical_threshold = self._crit_spin.value()
            self._config.codex_enabled = self._codex_check.isChecked()
            save_config(self._config)

            self._set_auto_start(self._autostart_check.isChecked())
            Logger.info("settings", "Settings saved successfully")
            original_text = self._save_btn.text()
            self._save_btn.setText("Saved")
            self._save_btn.setEnabled(False)
            QTimer.singleShot(1200, lambda: (
                self._save_btn.setText(original_text),
                self._save_btn.setEnabled(True),
            ))
        except Exception as e:
            Logger.error("settings", "Failed to save settings: {}".format(e))


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
