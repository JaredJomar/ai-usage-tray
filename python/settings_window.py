import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QCheckBox, QGroupBox, QFrame,
)

from auth import AuthManager
from config import AppConfig, save_config
from logger import Logger

import winreg
from pathlib import Path


class Ctp:
    BG = "#0f172a"
    CARD = "#1e293b"
    CARD_ELEVATED = "#26354a"
    BORDER = "#334155"
    TEXT = "#f8fafc"
    SUBTEXT = "#94a3b8"
    BLUE = "#38bdf8"
    GREEN = "#22c55e"

    SPACE_1 = 4
    SPACE_2 = 8
    SPACE_3 = 12
    SPACE_4 = 16
    SPACE_5 = 20
    RADIUS_SM = 6
    RADIUS_MD = 10
    FONT_CAPTION = 10
    FONT_BODY = 11
    FONT_LABEL = 12
    FONT_TITLE = 15
    BUTTON_HEIGHT = 30


class SettingsWindow(QWidget):
    REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    APP_NAME = "AIUsageTray"

    def __init__(self, auth_manager: AuthManager, config: AppConfig):
        super().__init__()
        self._auth = auth_manager
        self._config = config
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
        self.setWindowTitle("AI Usage Tray - Settings")
        self.setFixedSize(380, 360)
        self.setStyleSheet("""
            QWidget#settingsWindow {{
                background: {bg}; color: {text};
                font-family: 'Aptos'; font-size: {body}px;
            }}
            QGroupBox {{
                background: {card}; border: 1px solid {border};
                border-radius: {radius_md}px;
                margin-top: {group_margin}px;
                padding: {group_top}px {space_3}px {space_3}px;
                color: {text}; font-family: 'Aptos Display';
                font-size: {label}px; font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: {space_2}px;
                padding: 0 {space_1}px;
            }}
            QSpinBox {{
                background: {card_elevated}; color: {text};
                border: 1px solid {border}; border-radius: {radius_sm}px;
                padding: {space_1}px {space_2}px; min-width: {spin_width}px;
            }}
            QSpinBox:focus {{ border-color: {blue}; }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: {spin_button}px; background: {border}; border: 0;
            }}
            QCheckBox {{ color: {text}; spacing: {space_2}px; }}
            QCheckBox::indicator {{
                width: {checkbox}px; height: {checkbox}px;
                border-radius: {radius_sm}px; border: 1px solid {border};
                background: {card_elevated};
            }}
            QCheckBox::indicator:checked {{
                background: {blue}; border-color: {blue};
            }}
            QPushButton {{
                background: {blue}; color: {bg}; border: 0;
                border-radius: {radius_sm}px; padding: 0 {space_4}px;
                min-width: {button_width}px; height: {button_height}px;
                font-family: 'Aptos Display'; font-size: {body}px; font-weight: 700;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
        """.format(
            bg=Ctp.BG, card=Ctp.CARD, card_elevated=Ctp.CARD_ELEVATED,
            border=Ctp.BORDER, text=Ctp.TEXT, subtext=Ctp.SUBTEXT,
            blue=Ctp.BLUE, body=Ctp.FONT_BODY, label=Ctp.FONT_LABEL,
            radius_md=Ctp.RADIUS_MD, radius_sm=Ctp.RADIUS_SM,
            group_margin=Ctp.SPACE_3, group_top=Ctp.SPACE_3 + Ctp.SPACE_1,
            space_1=Ctp.SPACE_1, space_2=Ctp.SPACE_2, space_3=Ctp.SPACE_3,
            space_4=Ctp.SPACE_4, spin_width=Ctp.SPACE_5 * 4,
            spin_button=Ctp.SPACE_2 * 2, checkbox=Ctp.SPACE_4,
            button_width=Ctp.SPACE_5 * 7, button_height=Ctp.BUTTON_HEIGHT,
        ))

        root = QVBoxLayout(self)
        root.setContentsMargins(Ctp.SPACE_4, Ctp.SPACE_4, Ctp.SPACE_4, Ctp.SPACE_4)
        root.setSpacing(Ctp.SPACE_3)

        title = QLabel("Settings")
        title.setStyleSheet(
            "color: {}; font-family: 'Aptos Display'; font-size: {}px; font-weight: 700;".format(
                Ctp.TEXT, Ctp.FONT_TITLE,
            )
        )
        root.addWidget(title)

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
        self._add_row(polling_layout, "Refresh interval", self._refresh_spin)
        self._add_row(polling_layout, "Cache TTL", self._cache_spin)
        root.addWidget(polling)

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
        self._add_row(notifications_layout, "Warning", self._warn_spin)
        self._add_row(notifications_layout, "Critical", self._crit_spin)
        root.addWidget(notifications)

        startup = QGroupBox("Startup")
        startup_layout = QVBoxLayout(startup)
        self._autostart_check = QCheckBox("Launch on Windows startup")
        self._autostart_check.setChecked(self._is_auto_start_enabled())
        startup_layout.addWidget(self._autostart_check)
        root.addWidget(startup)

        root.addStretch()

        self._save_btn = QPushButton("Save Settings")
        self._save_btn.clicked.connect(self._save_settings)
        root.addWidget(self._save_btn)

    def _add_row(self, layout, title, control):
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        row.addStretch()
        row.addWidget(control)
        layout.addLayout(row)

    def _save_settings(self):
        try:
            self._config.refresh_interval_seconds = self._refresh_spin.value()
            self._config.cache_ttl_seconds = self._cache_spin.value()
            self._config.notifications_enabled = self._notify_check.isChecked()
            self._config.notify_warn_threshold = self._warn_spin.value()
            self._config.notify_critical_threshold = self._crit_spin.value()
            self._config.launch_on_startup = self._autostart_check.isChecked()
            save_config(self._config)
            self._set_auto_start(self._autostart_check.isChecked())
            Logger.info("settings", "Settings saved successfully")
            self._save_btn.setText("Saved!")
            self._save_btn.setEnabled(False)
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: (
                self._save_btn.setText("Save Settings"),
                self._save_btn.setEnabled(True),
            ))
        except Exception as error:
            Logger.error("settings", "Failed to save: {}".format(error))


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
