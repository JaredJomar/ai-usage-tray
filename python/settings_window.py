import sys
import json
import time
import threading

import requests

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QSpinBox, QCheckBox, QGroupBox, QFrame,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QScrollArea,
)

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

    SPACE_1 = 4
    SPACE_2 = 8
    SPACE_3 = 12
    SPACE_4 = 16
    SPACE_5 = 20
    RADIUS_SM = 6
    RADIUS_MD = 10
    RADIUS_LG = 14
    FONT_CAPTION = 10
    FONT_BODY = 11
    FONT_LABEL = 12
    FONT_TITLE = 15
    FONT_VALUE = 20
    PROGRESS_HEIGHT = 8
    BUTTON_HEIGHT = 30


class AuthSignals(QObject):
    status_update = pyqtSignal(str, object)
    login_result = pyqtSignal(str, bool, str)
    usage_update = pyqtSignal(object)


def make_dot(color):
    return '<span style="color:{};">●</span>'.format(color)


def make_bar(percent, width=20):
    filled = round(width * min(percent, 100) / 100)
    color = Ctp.BLUE if percent < 80 else (Ctp.AMBER if percent < 95 else Ctp.RED)
    return '<span style="color:{};">{}</span><span style="color:{};">{}</span>'.format(
        color, "▰" * filled, Ctp.BORDER, "▱" * (width - filled),
    )


def format_reset(unix_ts):
    diff = int(unix_ts - time.time())
    if diff <= 0:
        return "now"
    hours, remainder = divmod(diff, 3600)
    minutes = remainder // 60
    return "{}h {}m".format(hours, minutes) if hours else "{}m".format(minutes)


def make_button(text, color=None, accent=False, compact=False):
    button = QPushButton(text)
    background = color if accent and color else Ctp.CARD_ELEVATED
    foreground = Ctp.BG_START if accent and color else Ctp.TEXT
    hover = Ctp.WHITE if accent and color else Ctp.BORDER
    min_width = Ctp.SPACE_5 * 4 if compact else Ctp.SPACE_5 * 5
    button.setMinimumHeight(Ctp.BUTTON_HEIGHT)
    button.setStyleSheet("""
        QPushButton {{
            background: {background}; color: {foreground}; border: 1px solid {border};
            border-radius: {radius}px; padding: 0 {horizontal}px; min-width: {min_width}px;
            font-family: 'Aptos Display'; font-size: {font_size}px; font-weight: 700;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed {{ background: {border}; color: {foreground}; }}
        QPushButton:disabled {{ background: {card}; color: {muted}; border-color: {card}; }}
    """.format(
        background=background, foreground=foreground, border=Ctp.BORDER, hover=hover,
        card=Ctp.CARD, muted=Ctp.MUTED, radius=Ctp.RADIUS_SM,
        horizontal=Ctp.SPACE_2, min_width=min_width, font_size=Ctp.FONT_BODY,
    ))
    return button


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
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, cls.REGISTRY_RUN_KEY, 0, winreg.KEY_READ,
            )
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
            if getattr(sys, "frozen", False):
                executable = sys.executable
            else:
                executable = '"{}" "{}"'.format(
                    sys.executable, str(Path(__file__).parent / "main.py"),
                )

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, cls.REGISTRY_RUN_KEY, 0, winreg.KEY_SET_VALUE,
            )
            if enable:
                winreg.SetValueEx(key, cls.APP_NAME, 0, winreg.REG_SZ, executable)
            else:
                try:
                    winreg.DeleteValue(key, cls.APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as error:
            Logger.error("settings", "Failed to set auto-start: {}".format(error))

    def _init_ui(self):
        self.setObjectName("settingsWindow")
        self.setWindowTitle("AI Usage Tray — Codex")
        self.setFixedSize(640, 780)
        self.setStyleSheet("""
            QWidget#settingsWindow {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {bg_start}, stop:1 {bg_end});
                color: {text}; font-family: 'Aptos'; font-size: {body}px;
            }}
            QTabWidget::pane {{ border: 0; background: transparent; }}
            QTabBar {{ background: transparent; }}
            QTabBar::tab {{
                background: transparent; color: {subtext}; border: 0;
                border-bottom: 2px solid transparent; padding: {tab_vertical}px {tab_horizontal}px;
                font-family: 'Aptos Display'; font-size: {label}px; font-weight: 700;
            }}
            QTabBar::tab:selected {{ color: {text}; border-bottom-color: {blue}; }}
            QTabBar::tab:hover:!selected {{ color: {text}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
            QFrame#dashboardCard, QFrame#statusBar {{
                background: {card}; border: 1px solid {border}; border-radius: {radius_lg}px;
            }}
            QGroupBox {{
                background: {card}; border: 1px solid {border}; border-radius: {radius_md}px;
                margin-top: {group_margin}px; padding: {group_top}px {space_3}px {space_3}px;
                color: {text}; font-family: 'Aptos Display'; font-size: {label}px; font-weight: 700;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: {space_2}px; padding: 0 {space_1}px; }}
            QSpinBox {{
                background: {card_elevated}; color: {text}; border: 1px solid {border};
                border-radius: {radius_sm}px; padding: {space_1}px {space_2}px; min-width: {spin_width}px;
            }}
            QSpinBox:focus {{ border-color: {blue}; }}
            QSpinBox::up-button, QSpinBox::down-button {{ width: {spin_button}px; background: {border}; border: 0; }}
            QCheckBox {{ color: {text}; spacing: {space_2}px; }}
            QCheckBox::indicator {{
                width: {checkbox}px; height: {checkbox}px; border-radius: {radius_sm}px;
                border: 1px solid {border}; background: {card_elevated};
            }}
            QCheckBox::indicator:checked {{ background: {blue}; border-color: {blue}; }}
            QTableWidget {{
                background: transparent; border: 0; gridline-color: {border}; color: {text};
                selection-background-color: {card_elevated}; selection-color: {text};
            }}
            QHeaderView::section {{
                background: transparent; color: {subtext}; border: 0; border-bottom: 1px solid {border};
                padding: {space_1}px; font-family: 'Aptos Display'; font-size: {caption}px; font-weight: 700;
            }}
            QTableCornerButton::section {{ background: transparent; border: 0; border-bottom: 1px solid {border}; }}
        """.format(
            bg_start=Ctp.BG_START, bg_end=Ctp.BG_END, card=Ctp.CARD,
            card_elevated=Ctp.CARD_ELEVATED, border=Ctp.BORDER, text=Ctp.TEXT,
            subtext=Ctp.SUBTEXT, blue=Ctp.BLUE, body=Ctp.FONT_BODY,
            label=Ctp.FONT_LABEL, caption=Ctp.FONT_CAPTION, radius_lg=Ctp.RADIUS_LG,
            radius_md=Ctp.RADIUS_MD, radius_sm=Ctp.RADIUS_SM, tab_vertical=Ctp.SPACE_2,
            tab_horizontal=Ctp.SPACE_4, group_margin=Ctp.SPACE_3,
            group_top=Ctp.SPACE_3 + Ctp.SPACE_1, space_1=Ctp.SPACE_1,
            space_2=Ctp.SPACE_2, space_3=Ctp.SPACE_3, spin_width=Ctp.SPACE_5 * 4,
            spin_button=Ctp.SPACE_2 * 2, checkbox=Ctp.SPACE_4,
        ))

        root = QVBoxLayout(self)
        root.setContentsMargins(Ctp.SPACE_4, Ctp.SPACE_3, Ctp.SPACE_4, Ctp.SPACE_3)
        root.setSpacing(Ctp.SPACE_1)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._create_dashboard_tab(), "Dashboard")
        self._tabs.addTab(self._create_settings_tab(), "⚙  Settings")
        root.addWidget(self._tabs)

    def _label(self, text, color=Ctp.TEXT, size=Ctp.FONT_BODY, weight=400):
        label = QLabel(text)
        label.setStyleSheet(
            "color: {}; font-size: {}px; font-weight: {};".format(color, size, weight),
        )
        return label

    def _section_label(self, text):
        return self._label(text, Ctp.SUBTEXT, Ctp.FONT_CAPTION, 700)

    def _card(self):
        card = QFrame()
        card.setObjectName("dashboardCard")
        return card

    def _set_progress_style(self, progress, percent):
        color = Ctp.BLUE if percent < 80 else (Ctp.AMBER if percent < 95 else Ctp.RED)
        progress.setStyleSheet("""
            QProgressBar {{ background: {border}; border: 0; border-radius: {radius}px; }}
            QProgressBar::chunk {{
                border-radius: {radius}px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {blue}, stop:1 {color});
            }}
        """.format(border=Ctp.BORDER, radius=Ctp.SPACE_1, blue=Ctp.BLUE, color=color))

    def _make_usage_card(self, title):
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3)
        layout.setSpacing(Ctp.SPACE_2)

        heading = QHBoxLayout()
        heading.setSpacing(Ctp.SPACE_2)
        heading.addWidget(self._label(title, Ctp.TEXT, Ctp.FONT_LABEL, 700))
        heading.addStretch()
        percent = self._label("0%", Ctp.TEXT, Ctp.FONT_VALUE, 700)
        heading.addWidget(percent)
        layout.addLayout(heading)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setTextVisible(False)
        progress.setFixedHeight(Ctp.PROGRESS_HEIGHT)
        self._set_progress_style(progress, 0)
        layout.addWidget(progress)

        details = QHBoxLayout()
        detail = self._label("Resets in: —", Ctp.SUBTEXT, Ctp.FONT_CAPTION)
        delta = self._label("▼ 4%", Ctp.GREEN, Ctp.FONT_CAPTION, 700)
        details.addWidget(detail)
        details.addStretch()
        details.addWidget(delta)
        layout.addLayout(details)
        return card, percent, progress, detail, delta

    def _create_header(self):
        header = self._card()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_2, Ctp.SPACE_3, Ctp.SPACE_2)
        layout.setSpacing(Ctp.SPACE_2)

        provider = QVBoxLayout()
        provider.setSpacing(0)
        self._provider_header = self._label(
            "{}  Codex 1  11%".format(make_dot(Ctp.BLUE)), Ctp.TEXT, Ctp.FONT_TITLE, 700,
        )
        provider.addWidget(self._provider_header)
        provider.addWidget(self._label("Live limit overview", Ctp.SUBTEXT, Ctp.FONT_CAPTION))
        layout.addLayout(provider)
        layout.addStretch()
        self._plan_badge = self._label("CODEX", Ctp.BLUE, Ctp.FONT_CAPTION, 700)
        self._plan_badge.setStyleSheet("""
            background: {background}; color: {color}; border: 1px solid {border};
            border-radius: {radius}px; padding: {vertical}px {horizontal}px;
            font-family: 'Aptos Display'; font-size: {font_size}px; font-weight: 700;
        """.format(
            background=Ctp.CARD_ELEVATED, color=Ctp.BLUE, border=Ctp.BORDER,
            radius=Ctp.RADIUS_SM, vertical=Ctp.SPACE_1, horizontal=Ctp.SPACE_2,
            font_size=Ctp.FONT_CAPTION,
        ))
        layout.addWidget(self._plan_badge)
        return header

    def _create_connection_row(self):
        connection = QHBoxLayout()
        connection.setContentsMargins(0, 0, 0, 0)
        connection.setSpacing(Ctp.SPACE_2)
        self._status_dot = self._label(make_dot(Ctp.SUBTEXT), Ctp.SUBTEXT, Ctp.FONT_LABEL)
        self._codex_status = self._label("Checking connection…", Ctp.SUBTEXT, Ctp.FONT_BODY)
        self._codex_login_btn = make_button("Login", Ctp.BLUE, accent=True, compact=True)
        self._codex_logout_btn = make_button("Logout", compact=True)
        self._codex_login_btn.clicked.connect(self._login_codex)
        self._codex_logout_btn.clicked.connect(self._logout_codex)
        self._codex_logout_btn.setEnabled(False)
        connection.addWidget(self._status_dot)
        connection.addWidget(self._codex_status, 1)
        connection.addWidget(self._codex_login_btn)
        connection.addWidget(self._codex_logout_btn)
        return connection

    def _create_account_strip(self):
        card = self._card()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_2, Ctp.SPACE_3, Ctp.SPACE_2)
        layout.setSpacing(Ctp.SPACE_2)
        layout.addWidget(self._section_label("ACCOUNT"))
        layout.addStretch()
        self._plan_detail = self._label("Plan Type: Codex", Ctp.SUBTEXT, Ctp.FONT_CAPTION)
        self._credits_detail = self._label("Reset Credits: 0", Ctp.SUBTEXT, Ctp.FONT_CAPTION)
        layout.addWidget(self._plan_detail)
        layout.addWidget(self._label("•", Ctp.BORDER, Ctp.FONT_CAPTION))
        layout.addWidget(self._credits_detail)
        return card

    def _chart_card(self, title, chart_html, delta):
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3)
        layout.setSpacing(Ctp.SPACE_1)
        top = QHBoxLayout()
        top.addWidget(self._label(title, Ctp.TEXT, Ctp.FONT_LABEL, 700))
        top.addStretch()
        top.addWidget(self._label(delta, Ctp.GREEN, Ctp.FONT_CAPTION, 700))
        layout.addLayout(top)
        chart = self._label(chart_html, Ctp.BLUE, Ctp.FONT_CAPTION)
        chart.setTextFormat(Qt.TextFormat.RichText)
        chart.setWordWrap(True)
        layout.addWidget(chart)
        layout.addWidget(self._label("Placeholder data · coming soon", Ctp.MUTED, Ctp.FONT_CAPTION))
        return card

    def _create_token_usage_card(self):
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3)
        layout.setSpacing(Ctp.SPACE_1)
        title = QHBoxLayout()
        title.addWidget(self._label("Token Usage", Ctp.TEXT, Ctp.FONT_LABEL, 700))
        title.addStretch()
        title.addWidget(self._label("Placeholder data · coming soon", Ctp.MUTED, Ctp.FONT_CAPTION))
        layout.addLayout(title)

        self._token_table = QTableWidget(4, 4)
        self._token_table.setHorizontalHeaderLabels(["Input", "Output", "Cache", "Cost"])
        self._token_table.setVerticalHeaderLabels(["Today", "Last 7 Days", "Last Month", "Last 30 Days"])
        self._token_table.verticalHeader().setVisible(True)
        self._token_table.verticalHeader().setDefaultSectionSize(Ctp.SPACE_4 + Ctp.SPACE_1)
        self._token_table.verticalHeader().setFixedWidth(Ctp.SPACE_5 * 5)
        self._token_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._token_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._token_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._token_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._token_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._token_table.setFixedHeight(Ctp.SPACE_5 * 6)
        self._token_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        values = [
            ("128k", "24k", "96k", "$1.82"),
            ("842k", "169k", "623k", "$11.46"),
            ("2.4m", "481k", "1.8m", "$33.70"),
            ("3.1m", "619k", "2.3m", "$42.80"),
        ]
        for row, row_values in enumerate(values):
            for column, value in enumerate(row_values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._token_table.setItem(row, column, item)
        layout.addWidget(self._token_table)
        return card

    def _create_model_card(self):
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3)
        layout.setSpacing(Ctp.SPACE_1)
        title = QHBoxLayout()
        title.addWidget(self._label("Model Breakdown", Ctp.TEXT, Ctp.FONT_LABEL, 700))
        title.addStretch()
        title.addWidget(self._label("Placeholder data · coming soon", Ctp.MUTED, Ctp.FONT_CAPTION))
        layout.addLayout(title)
        layout.addWidget(self._label(
            '<span style="color:{blue};">●</span> gpt-5.6-terra  <b>58%</b>'
            ' &nbsp; <span style="color:{amber};">●</span> gpt-5.6-sol  <b>29%</b>'
            ' &nbsp; <span style="color:{green};">●</span> gpt-5.6-luna  <b>13%</b>'.format(
                blue=Ctp.BLUE, amber=Ctp.AMBER, green=Ctp.GREEN,
            ), Ctp.SUBTEXT, Ctp.FONT_CAPTION,
        ))
        return card

    def _create_status_bar(self):
        bar = QFrame()
        bar.setObjectName("statusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_2, Ctp.SPACE_3, Ctp.SPACE_2)
        layout.setSpacing(Ctp.SPACE_2)
        self._updated_status = self._label(
            "{}  Updated just now".format(make_dot(Ctp.GREEN)), Ctp.SUBTEXT, Ctp.FONT_CAPTION,
        )
        self._usage_refresh_btn = make_button("Refresh", compact=True)
        self._usage_refresh_btn.clicked.connect(self._fetch_usage)
        layout.addWidget(self._updated_status)
        layout.addStretch()
        layout.addWidget(self._label("Codex v1.0.0", Ctp.SUBTEXT, Ctp.FONT_CAPTION, 700))
        layout.addWidget(self._usage_refresh_btn)
        return bar

    def _create_dashboard_tab(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Ctp.SPACE_1, Ctp.SPACE_2, Ctp.SPACE_1, Ctp.SPACE_2)
        layout.setSpacing(Ctp.SPACE_2)
        layout.addWidget(self._create_header())
        layout.addLayout(self._create_connection_row())
        layout.addWidget(self._section_label("RATE LIMITS"))

        (
            card, self._usage_5h_percent, self._usage_5h_bar,
            self._usage_5h_detail, self._usage_5h_delta,
        ) = self._make_usage_card("5-Hour Session")
        layout.addWidget(card)
        (
            card, self._usage_7d_percent, self._usage_7d_bar,
            self._usage_7d_detail, self._usage_7d_delta,
        ) = self._make_usage_card("Weekly Limit")
        layout.addWidget(card)
        layout.addWidget(self._create_account_strip())

        charts = QHBoxLayout()
        charts.setSpacing(Ctp.SPACE_2)
        charts.addWidget(self._chart_card(
            "Usage Rate", "<b>▁ ▂ ▃ ▅ ▄ ▆ ▇</b><br>4.8% / hour", "▼ 4%",
        ))
        charts.addWidget(self._chart_card(
            "Daily Usage", "<b>▂ ▁ ▄ ▃ ▆ ▅ ▇</b><br>M  T  W  T  F  S  S", "▲ 12%",
        ))
        layout.addLayout(charts)
        layout.addWidget(self._create_token_usage_card())
        layout.addWidget(self._create_model_card())
        layout.addWidget(self._create_status_bar())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        QTimer.singleShot(100, self._refresh_status)
        QTimer.singleShot(300, self._fetch_usage)
        return scroll

    def _create_settings_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_3, Ctp.SPACE_2)
        layout.setSpacing(Ctp.SPACE_2)
        layout.addWidget(self._label("Preferences", Ctp.TEXT, Ctp.FONT_VALUE, 700))
        layout.addWidget(self._label(
            "Control how AI Usage Tray monitors your Codex account.", Ctp.SUBTEXT, Ctp.FONT_BODY,
        ))

        polling = QGroupBox("Polling")
        polling_layout = QVBoxLayout(polling)
        self._refresh_spin = QSpinBox()
        self._refresh_spin.setRange(30, 3600)
        self._refresh_spin.setValue(self._config.refresh_interval_seconds)
        self._refresh_spin.setSuffix(" s")
        self._cache_spin = QSpinBox()
        self._cache_spin.setRange(10, 300)
        self._cache_spin.setValue(self._config.cache_ttl_seconds)
        self._cache_spin.setSuffix(" s")
        self._add_setting_row(polling_layout, "Refresh interval", self._refresh_spin)
        self._add_setting_row(polling_layout, "Cache TTL", self._cache_spin)
        layout.addWidget(polling)

        notifications = QGroupBox("Notifications")
        notifications_layout = QVBoxLayout(notifications)
        self._notify_check = QCheckBox("Enable notifications")
        self._notify_check.setChecked(self._config.notifications_enabled)
        notifications_layout.addWidget(self._notify_check)
        self._warn_spin = QSpinBox()
        self._warn_spin.setRange(50, 100)
        self._warn_spin.setValue(self._config.notify_warn_threshold)
        self._warn_spin.setSuffix(" %")
        self._crit_spin = QSpinBox()
        self._crit_spin.setRange(50, 100)
        self._crit_spin.setValue(self._config.notify_critical_threshold)
        self._crit_spin.setSuffix(" %")
        self._add_setting_row(notifications_layout, "Warning threshold", self._warn_spin)
        self._add_setting_row(notifications_layout, "Critical threshold", self._crit_spin)
        layout.addWidget(notifications)

        codex = QGroupBox("Codex")
        codex_layout = QVBoxLayout(codex)
        self._codex_check = QCheckBox("Enable Codex monitoring")
        self._codex_check.setChecked(self._config.codex_enabled)
        codex_layout.addWidget(self._codex_check)
        layout.addWidget(codex)

        startup = QGroupBox("Startup")
        startup_layout = QVBoxLayout(startup)
        self._autostart_check = QCheckBox("Launch on Windows startup")
        self._autostart_check.setChecked(self._is_auto_start_enabled())
        startup_layout.addWidget(self._autostart_check)
        layout.addWidget(startup)
        layout.addStretch()
        self._save_btn = make_button("Save Settings", Ctp.BLUE, accent=True)
        self._save_btn.clicked.connect(self._save_settings)
        layout.addWidget(self._save_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return widget

    def _add_setting_row(self, layout, title, control):
        row = QHBoxLayout()
        row.addWidget(self._label(title, Ctp.TEXT, Ctp.FONT_BODY))
        row.addStretch()
        row.addWidget(control)
        layout.addLayout(row)

    def _refresh_status(self):
        def worker():
            self._signals.status_update.emit("codex", self._auth.get_codex_status())

        threading.Thread(target=worker, daemon=True).start()

    def _on_status_update(self, provider, status):
        if status.authenticated:
            text = "Connected"
            if status.email:
                text += " · {}".format(status.email)
            if status.message:
                text += " · {}".format(status.message)
            color = Ctp.GREEN
            self._codex_login_btn.setEnabled(False)
            self._codex_logout_btn.setEnabled(True)
        else:
            text = "Not connected"
            if status.message:
                text += " · {}".format(status.message)
            color = Ctp.RED
            self._codex_login_btn.setEnabled(True)
            self._codex_logout_btn.setEnabled(False)
        self._codex_status.setText(text)
        self._codex_status.setStyleSheet("color: {}; font-size: {}px;".format(color, Ctp.FONT_BODY))
        self._status_dot.setText(make_dot(color))

    def _login_codex(self):
        self._codex_login_btn.setEnabled(False)
        self._codex_login_btn.setText("Logging in…")

        def worker():
            def callback(success, message):
                self._signals.login_result.emit("codex", success, message)

            self._auth.login_codex(callback)

        threading.Thread(target=worker, daemon=True).start()

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

        def worker():
            self._signals.usage_update.emit(self._fetch_usage_direct())

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_usage_direct(self):
        auth_path = get_codex_auth_path()
        if not auth_path.exists():
            return {"error": "Not authenticated. Please log in first."}
        try:
            with open(auth_path, "r") as auth_file:
                auth = json.load(auth_file)
            access_token = auth.get("tokens", {}).get("access_token")
            if not access_token:
                return {"error": "No access token found. Please log in."}
            response = requests.get(
                "https://chatgpt.com/backend-api/wham/usage",
                headers={
                    "Authorization": "Bearer {}".format(access_token),
                    "User-Agent": "codex_cli_rs",
                },
                timeout=15,
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code == 401:
                return {"error": "Token expired. Please log in again."}
            return {"error": "API returned HTTP {}.".format(response.status_code)}
        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your connection."}
        except requests.exceptions.ConnectionError:
            return {"error": "No internet connection."}
        except Exception as error:
            return {"error": "Failed: {}".format(error)}

    def _reset_detail(self, reset_at):
        if not reset_at:
            return "Resets in: —"
        reset_time = time.strftime("%I:%M %p", time.localtime(reset_at)).lstrip("0")
        return "Resets in: {} at {}  |  On track".format(format_reset(reset_at), reset_time)

    def _update_usage_card(self, percent_label, progress, detail, delta, used, reset_at):
        used = float(used or 0)
        percent_label.setText("{:.0f}%".format(used))
        progress.setValue(round(used))
        self._set_progress_style(progress, used)
        detail.setText(self._reset_detail(reset_at))
        color = Ctp.GREEN if used < 80 else (Ctp.AMBER if used < 95 else Ctp.RED)
        delta.setStyleSheet(
            "color: {}; font-size: {}px; font-weight: 700;".format(color, Ctp.FONT_CAPTION),
        )
        delta.setText("▼ 4%")

    def _on_usage_update(self, data):
        self._usage_loading = False
        self._usage_refresh_btn.setEnabled(True)
        if not data or "error" in data:
            message = data.get("error", "Unknown error") if data else "Unknown error"
            self._updated_status.setText("{}  {}".format(make_dot(Ctp.RED), message))
            return

        rate_limit = data.get("rate_limit", {})
        primary = rate_limit.get("primary_window", {})
        secondary = rate_limit.get("secondary_window", {})
        credits = data.get("rate_limit_reset_credits", {})
        plan = data.get("plan_type", "codex")
        primary_used = primary.get("used_percent", 0)
        secondary_used = secondary.get("used_percent", 0)
        credit_count = credits.get("available_count", 0)

        self._provider_header.setText(
            "{}  Codex 1  {:.0f}%".format(make_dot(Ctp.BLUE), float(primary_used or 0)),
        )
        self._plan_badge.setText(str(plan).upper())
        self._plan_detail.setText("Plan Type: {}".format(str(plan).title()))
        self._credits_detail.setText("Reset Credits: {}".format(credit_count))
        self._update_usage_card(
            self._usage_5h_percent, self._usage_5h_bar, self._usage_5h_detail,
            self._usage_5h_delta, primary_used, primary.get("reset_at", 0),
        )
        self._update_usage_card(
            self._usage_7d_percent, self._usage_7d_bar, self._usage_7d_detail,
            self._usage_7d_delta, secondary_used, secondary.get("reset_at", 0),
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
            self._config.launch_on_startup = self._autostart_check.isChecked()
            save_config(self._config)
            self._set_auto_start(self._autostart_check.isChecked())
            Logger.info("settings", "Settings saved successfully")
            original_text = self._save_btn.text()
            self._save_btn.setText("Saved")
            self._save_btn.setEnabled(False)
            QTimer.singleShot(1200, lambda: (
                self._save_btn.setText(original_text), self._save_btn.setEnabled(True),
            ))
        except Exception as error:
            Logger.error("settings", "Failed to save settings: {}".format(error))


def show_settings(auth_manager: AuthManager, config: AppConfig):
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    window = SettingsWindow(auth_manager, config)
    window.show()
    app.exec()


if __name__ == "__main__":
    auth = AuthManager()
    config = load_config()
    show_settings(auth, config)
