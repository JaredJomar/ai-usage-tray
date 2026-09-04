"""Isolated Qt dashboard process for Codex usage and local activity metadata."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, QSize, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from cache import CacheManager
from config import AppConfig
from local_codex import (
    LocalCodexSnapshot,
    UsageSnapshot,
    read_local_codex_snapshot,
    read_usage_snapshots,
    record_usage_snapshot,
)
from logger import Logger
from providers.codex import CodexProvider


@dataclass(frozen=True)
class DashboardState:
    five_hour: str
    five_hour_reset: str
    seven_day: str
    seven_day_reset: str
    plan: str
    credits: str
    credit_expiry: str
    last_update: str
    sessions_24h: str
    sessions_7d: str
    total_sessions: str
    tokens: str
    model_rows: tuple[tuple[str, str], ...]
    local_observation: str
    usage_history: tuple[UsageSnapshot, ...]


class DashboardTheme:
    PANEL_WIDTH = 420
    PREFERRED_HEIGHT = 580
    PANEL_MARGIN = 8
    ANCHOR_GAP = 10
    NONE = 0
    SPACE_TIGHT = 2
    SPACE_1 = 4
    SPACE_2 = 8
    SPACE_3 = 12
    SPACE_4 = 16
    RADIUS_PANEL = 18
    RADIUS_CARD = 12
    RADIUS_CONTROL = 8
    BORDER_WIDTH = 1
    FONT_EYEBROW = 10
    FONT_BODY = 11
    FONT_SECTION = 13
    FONT_METRIC = 15
    FONT_CLOSE = 18
    FONT_TITLE = 20
    FONT_USAGE = 24
    LETTER_SPACING = 1
    BUTTON_HEIGHT = 28
    CLOSE_BUTTON_WIDTH = 28
    WINDOW = "#f3f7fa"
    SHELL = "#ffffff"
    SURFACE = "#f7fafc"
    PRIMARY_SURFACE = "#e8f6fa"
    WEEKLY_SURFACE = "#ebf7f1"
    BORDER = "#cbd9e1"
    PRIMARY_BORDER = "#9bd5e3"
    WEEKLY_BORDER = "#a8d9c5"
    TEXT = "#163247"
    MUTED = "#587082"
    ACCENT = "#0b7187"
    ACCENT_HOVER = "#075d70"
    ACCENT_DISABLED = "#9cc4cd"
    WEEKLY_VALUE = "#17694f"
    CONTROL_TEXT = "#ffffff"


def calculate_popup_position(anchor: QPoint, panel_size: QSize, available: QRect) -> QPoint:
    """Place a panel above its tray anchor while keeping it on the active screen."""
    minimum_x = available.left() + DashboardTheme.PANEL_MARGIN
    minimum_y = available.top() + DashboardTheme.PANEL_MARGIN
    maximum_x = max(minimum_x, available.right() - panel_size.width() + 1 - DashboardTheme.PANEL_MARGIN)
    maximum_y = max(minimum_y, available.bottom() - panel_size.height() + 1 - DashboardTheme.PANEL_MARGIN)
    preferred_x = anchor.x() - panel_size.width() + DashboardTheme.ANCHOR_GAP
    preferred_y = anchor.y() - panel_size.height() - DashboardTheme.ANCHOR_GAP
    return QPoint(
        min(max(preferred_x, minimum_x), maximum_x),
        min(max(preferred_y, minimum_y), maximum_y),
    )


def parse_dashboard_anchor(arguments: list[str]) -> Optional[QPoint]:
    coordinates: dict[str, int] = {}
    for argument in arguments:
        for name in ("x", "y"):
            prefix = "--dashboard-{}=".format(name)
            if argument.startswith(prefix):
                try:
                    coordinates[name] = int(argument[len(prefix):])
                except ValueError:
                    return None
    if set(coordinates) == {"x", "y"}:
        return QPoint(coordinates["x"], coordinates["y"])
    return None


def _format_countdown(value: object, now: Optional[datetime] = None) -> str:
    if not value:
        return "Not available"
    try:
        if isinstance(value, str):
            reset_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            reset_time = datetime.fromtimestamp(float(value), tz=timezone.utc)
        remaining = reset_time.astimezone(timezone.utc) - (now or datetime.now(timezone.utc))
    except (TypeError, ValueError, OSError, OverflowError):
        return "Not available"
    if remaining.total_seconds() <= 0:
        return "Reset due"
    days, seconds = remaining.days, remaining.seconds
    if days:
        return "{}d {}h".format(days, seconds // 3600)
    if seconds >= 3600:
        return "{}h {}m".format(seconds // 3600, (seconds % 3600) // 60)
    return "{}m".format(max(1, seconds // 60))


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "Not available"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp.astimezone().strftime("%b %d, %I:%M %p")
    except ValueError:
        return "Not available"


def _format_tokens(value: Optional[int]) -> str:
    if value is None:
        return "Not available"
    return "{:,}".format(value)


def _nearest_credit_expiry(api_data: dict) -> Optional[object]:
    direct_expiry = api_data.get("reset_credits_expires_at")
    if direct_expiry:
        return direct_expiry
    entries = api_data.get("reset_credit_expiries")
    if not isinstance(entries, list):
        return None
    expiry_values = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("expires_at"):
            expiry_values.append(entry["expires_at"])
    return min(expiry_values) if expiry_values else None


def _format_usage(value: object) -> str:
    try:
        return "{:.0f}%".format(float(value))
    except (TypeError, ValueError):
        return "Not available"


def build_dashboard_state(
    usage: Optional[dict], local: LocalCodexSnapshot,
    now: Optional[datetime] = None,
    history: tuple[UsageSnapshot, ...] = (),
) -> DashboardState:
    """Create display-safe strings without inventing unavailable metrics."""
    api_data = usage or {}
    five_hour = api_data.get("five_hour")
    seven_day = api_data.get("seven_day")
    plan = api_data.get("plan_type") or "Not available"
    credits = api_data.get("reset_credits")
    model_rows = tuple((model, "{} sessions".format(count)) for model, count in local.model_breakdown.items())
    if not model_rows:
        model_rows = (("Model breakdown", "Not available"),)
    observed = "No local limit observation available"
    if local.observed_primary or local.observed_secondary:
        observed = "Local rate-limit observation available (not used for live limits)"
    return DashboardState(
        five_hour=_format_usage(five_hour),
        five_hour_reset=_format_countdown(api_data.get("five_hour_reset"), now),
        seven_day="{:.0f}%".format(float(seven_day)) if seven_day is not None else "Not available",
        seven_day_reset=_format_countdown(api_data.get("seven_day_reset"), now),
        plan=str(plan),
        credits=str(credits) if credits is not None else "Not available",
        credit_expiry=_format_countdown(_nearest_credit_expiry(api_data), now),
        last_update=_format_timestamp(local.last_updated_at),
        sessions_24h=str(local.sessions_last_24h),
        sessions_7d=str(local.sessions_last_7d),
        total_sessions=str(local.session_count),
        tokens=_format_tokens(local.tokens_used),
        model_rows=model_rows,
        local_observation=observed,
        usage_history=history,
    )


class DashboardRefreshWorker(QThread):
    refreshed = pyqtSignal(object, object)

    def __init__(self, config: AppConfig):
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            local = read_local_codex_snapshot()
            usage = None
            if self._config.codex_enabled:
                cache = CacheManager(self._config.cache_ttl_seconds)
                cached = cache.get_cached("codex")
                usage = CodexProvider(cache).fetch()
                if cached is None:
                    record_usage_snapshot(usage, local)
            self.refreshed.emit(usage, local)
        except Exception as error:
            Logger.error("dashboard", "Refresh failed: {}".format(error))
            self.refreshed.emit(None, LocalCodexSnapshot())


class DashboardWindow(QWidget):
    def __init__(self, config: AppConfig, anchor: Optional[QPoint] = None):
        super().__init__()
        self._config = config
        self._anchor = anchor
        self._worker: Optional[DashboardRefreshWorker] = None
        self._closing = False
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        self.setObjectName("codexDashboard")
        self.setWindowTitle("Codex Dashboard")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setFixedWidth(DashboardTheme.PANEL_WIDTH)
        self.setMinimumHeight(DashboardTheme.NONE)
        self.resize(DashboardTheme.PANEL_WIDTH, DashboardTheme.PREFERRED_HEIGHT)
        self.setStyleSheet("""
            QWidget#codexDashboard {{ background: {window}; color: {text}; font-family: 'Aptos', 'Segoe UI', sans-serif; }}
            QFrame#shell {{ background: {shell}; border: {border_width}px solid {border}; border-radius: {panel_radius}px; }}
            QScrollArea#dashboardScroll, QScrollArea#dashboardScroll QWidget {{ border: 0; background: {shell}; }}
            QScrollBar:vertical {{ width: {scrollbar_width}px; background: {shell}; margin: {space_1}px; }}
            QScrollBar::handle:vertical {{ background: {border}; border-radius: {scrollbar_radius}px; min-height: {scrollbar_minimum}px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QFrame#usageCard {{ background: {surface}; border: {border_width}px solid {border}; border-radius: {card_radius}px; }}
            QFrame#primaryCard {{ background: {primary_surface}; border: {border_width}px solid {primary_border}; border-radius: {card_radius}px; }}
            QFrame#weeklyCard {{ background: {weekly_surface}; border: {border_width}px solid {weekly_border}; border-radius: {card_radius}px; }}
            QFrame#metricCard, QFrame#modelRow {{ background: {surface}; border: {border_width}px solid {border}; border-radius: {card_radius}px; }}
            QLabel#eyebrow {{ color: {accent}; font-size: {font_eyebrow}px; font-weight: 700; letter-spacing: {letter_spacing}px; }}
            QLabel#title {{ color: {text}; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: {font_title}px; font-weight: 700; }}
            QLabel#subtitle, QLabel#muted {{ color: {muted}; font-size: {font_body}px; }}
            QLabel#cardTitle {{ color: {accent}; font-size: {font_eyebrow}px; font-weight: 700; }}
            QLabel#usageValue {{ color: {accent}; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: {font_usage}px; font-weight: 700; }}
            QLabel#weeklyValue {{ color: {weekly_value}; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: {font_usage}px; font-weight: 700; }}
            QLabel#metricValue {{ color: {text}; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: {font_metric}px; font-weight: 700; }}
            QLabel#sectionTitle {{ color: {text}; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: {font_section}px; font-weight: 700; }}
            QPushButton#refreshButton {{ background: {accent}; color: {control_text}; border: 0; border-radius: {control_radius}px; min-height: {button_height}px; padding: 0 {space_2}px; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-weight: 700; }}
            QPushButton#refreshButton:hover {{ background: {accent_hover}; }}
            QPushButton#refreshButton:disabled {{ background: {accent_disabled}; }}
            QToolButton#closeButton {{ color: {muted}; border: 0; border-radius: {control_radius}px; font-size: {font_close}px; min-width: {close_button_width}px; min-height: {button_height}px; }}
            QToolButton#closeButton:hover {{ background: {surface}; color: {text}; }}
        """.format(
            window=DashboardTheme.WINDOW, text=DashboardTheme.TEXT, shell=DashboardTheme.SHELL,
            border=DashboardTheme.BORDER, border_width=DashboardTheme.BORDER_WIDTH,
            panel_radius=DashboardTheme.RADIUS_PANEL,
            scrollbar_width=DashboardTheme.SPACE_2, space_1=DashboardTheme.SPACE_1,
            scrollbar_radius=DashboardTheme.SPACE_1, scrollbar_minimum=DashboardTheme.SPACE_4 * 2,
            surface=DashboardTheme.SURFACE, primary_surface=DashboardTheme.PRIMARY_SURFACE,
            primary_border=DashboardTheme.PRIMARY_BORDER, card_radius=DashboardTheme.RADIUS_CARD,
            weekly_surface=DashboardTheme.WEEKLY_SURFACE, weekly_border=DashboardTheme.WEEKLY_BORDER,
            accent=DashboardTheme.ACCENT, muted=DashboardTheme.MUTED,
            weekly_value=DashboardTheme.WEEKLY_VALUE, control_radius=DashboardTheme.RADIUS_CONTROL,
            font_eyebrow=DashboardTheme.FONT_EYEBROW, font_title=DashboardTheme.FONT_TITLE,
            font_body=DashboardTheme.FONT_BODY, font_usage=DashboardTheme.FONT_USAGE,
            font_metric=DashboardTheme.FONT_METRIC, font_section=DashboardTheme.FONT_SECTION,
            letter_spacing=DashboardTheme.LETTER_SPACING, control_text=DashboardTheme.CONTROL_TEXT,
            button_height=DashboardTheme.BUTTON_HEIGHT, font_close=DashboardTheme.FONT_CLOSE,
            close_button_width=DashboardTheme.CLOSE_BUTTON_WIDTH,
            space_2=DashboardTheme.SPACE_2, accent_hover=DashboardTheme.ACCENT_HOVER,
            accent_disabled=DashboardTheme.ACCENT_DISABLED,
        ))

        page = QVBoxLayout(self)
        page.setContentsMargins(DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE)
        shell = QFrame(objectName="shell")
        page.addWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_2, DashboardTheme.SPACE_3)
        root.setSpacing(DashboardTheme.SPACE_2)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(DashboardTheme.SPACE_TIGHT)
        title_block.addWidget(QLabel("CODEX • LIVE LIMITS", objectName="eyebrow"))
        title_block.addWidget(QLabel("Usage dashboard", objectName="title"))
        self._status = QLabel("Refreshing current limits…", objectName="subtitle")
        title_block.addWidget(self._status)
        header.addLayout(title_block)
        header.addStretch()
        self._refresh_button = QPushButton("Refresh", objectName="refreshButton")
        self._refresh_button.clicked.connect(self.refresh)
        header.addWidget(self._refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        close_button = QToolButton(objectName="closeButton")
        close_button.setText("X")
        close_button.setToolTip("Close dashboard")
        close_button.clicked.connect(self.close)
        header.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        scroll_area = QScrollArea(objectName="dashboardScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        scroll_area.setWidget(scroll_content)
        root.addWidget(scroll_area, 1)
        content = QVBoxLayout(scroll_content)
        content.setContentsMargins(DashboardTheme.SPACE_1, DashboardTheme.SPACE_1, DashboardTheme.SPACE_1, DashboardTheme.SPACE_1)
        content.setSpacing(DashboardTheme.SPACE_2)

        usage_layout = QHBoxLayout()
        usage_layout.setSpacing(DashboardTheme.SPACE_2)
        self._five_card = self._make_limit_card("5-HOUR WINDOW", "primaryCard")
        self._weekly_card = self._make_limit_card("WEEKLY WINDOW", "weeklyCard")
        usage_layout.addWidget(self._five_card[0])
        usage_layout.addWidget(self._weekly_card[0])
        content.addLayout(usage_layout)

        meta = self._make_usage_meta()
        content.addWidget(meta)

        activity_title = QLabel("Local activity", objectName="sectionTitle")
        content.addWidget(activity_title)
        activity = QGridLayout()
        activity.setSpacing(DashboardTheme.SPACE_2)
        self._sessions_24h = self._make_metric(activity, 0, "LAST 24 HOURS")
        self._sessions_7d = self._make_metric(activity, 1, "LAST 7 DAYS")
        self._total_sessions = self._make_metric(activity, 2, "ALL SESSIONS")
        content.addLayout(activity)
        self._trend_notice = QLabel("Usage-rate and daily trend charts are not available from local metadata.", objectName="muted")
        self._trend_notice.setWordWrap(True)
        content.addWidget(self._trend_notice)

        self._trend = QLabel("Usage history: collecting snapshots…", objectName="muted")
        self._trend.setWordWrap(True)
        content.addWidget(self._trend)

        table_title = QLabel("Tokens & models", objectName="sectionTitle")
        content.addWidget(table_title)
        self._tokens = QLabel("Not available", objectName="metricValue")
        self._tokens.setWordWrap(True)
        content.addWidget(self._tokens)
        self._model_list = QVBoxLayout()
        self._model_list.setSpacing(DashboardTheme.SPACE_1)
        content.addLayout(self._model_list)
        self._last_update = QLabel("Last local update: Not available", objectName="muted")
        self._last_update.setWordWrap(True)
        content.addWidget(self._last_update)
        self._observation = QLabel("", objectName="muted")
        self._observation.setWordWrap(True)
        content.addWidget(self._observation)
        content.addStretch()

    def _make_limit_card(self, title: str, name: str) -> tuple[QFrame, QLabel, QLabel]:
        card = QFrame(objectName=name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2)
        layout.setSpacing(DashboardTheme.SPACE_1)
        layout.addWidget(QLabel(title, objectName="cardTitle"))
        value = QLabel("Not available", objectName="usageValue" if name == "primaryCard" else "weeklyValue")
        layout.addWidget(value)
        reset = QLabel("Resets: Not available", objectName="muted")
        layout.addWidget(reset)
        return card, value, reset

    def _make_usage_meta(self) -> QFrame:
        frame = QFrame(objectName="usageCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2)
        layout.setSpacing(DashboardTheme.SPACE_1)
        self._plan = QLabel("Plan: Not available", objectName="metricValue")
        self._credits = QLabel("Reset credits: Not available", objectName="muted")
        self._credit_expiry = QLabel("Next reset credit expiry: Not available", objectName="muted")
        layout.addWidget(self._plan)
        layout.addWidget(self._credits)
        layout.addWidget(self._credit_expiry)
        return frame

    def _make_metric(self, layout: QGridLayout, column: int, title: str) -> QLabel:
        card = QFrame(objectName="metricCard")
        content = QVBoxLayout(card)
        content.setContentsMargins(DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2)
        content.setSpacing(DashboardTheme.SPACE_1)
        content.addWidget(QLabel(title, objectName="cardTitle"))
        value = QLabel("0", objectName="metricValue")
        content.addWidget(value)
        layout.addWidget(card, 0, column)
        return value

    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._refresh_button.setEnabled(False)
        self._status.setText("Refreshing current limits…")
        self._worker = DashboardRefreshWorker(self._config)
        self._worker.refreshed.connect(self._render)
        self._worker.finished.connect(self._refresh_finished)
        self._worker.start()

    def _refresh_finished(self) -> None:
        self._refresh_button.setEnabled(True)
        if self._closing:
            self.deleteLater()
            application = QApplication.instance()
            if application:
                application.quit()

    def _render(self, usage: Optional[dict], local: LocalCodexSnapshot) -> None:
        state = build_dashboard_state(usage, local, history=tuple(read_usage_snapshots()))
        self._five_card[1].setText(state.five_hour)
        self._five_card[2].setText("Resets: {}".format(state.five_hour_reset))
        self._weekly_card[1].setText(state.seven_day)
        self._weekly_card[2].setText("Resets: {}".format(state.seven_day_reset))
        self._plan.setText("Plan: {}".format(state.plan))
        self._credits.setText("Reset credits: {}".format(state.credits))
        self._credit_expiry.setText("Next reset credit expiry: {}".format(state.credit_expiry))
        self._sessions_24h.setText(state.sessions_24h)
        self._sessions_7d.setText(state.sessions_7d)
        self._total_sessions.setText(state.total_sessions)
        self._tokens.setText("Tokens used (local aggregate): {}".format(state.tokens))
        self._last_update.setText("Last local update: {}".format(state.last_update))
        self._observation.setText(state.local_observation)
        recent = state.usage_history[-12:]
        if recent:
            values = [entry.five_hour for entry in recent if entry.five_hour is not None]
            if values:
                blocks = ".:-=+*#@"
                maximum = max(max(values), 1)
                sparkline = "".join(blocks[min(7, int(value / maximum * 7))] for value in values)
                self._trend.setText("5-hour usage history  {}   ({} snapshots)".format(sparkline, len(recent)))
            else:
                self._trend.setText("Usage history: waiting for valid snapshots")
        else:
            self._trend.setText("Usage history: collecting snapshots…")
        self._status.setText("Current limits loaded" if usage else "Current limits unavailable; local metadata shown")
        while self._model_list.count():
            child = self._model_list.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for model, count in state.model_rows:
            row_widget = QFrame(objectName="modelRow")
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(DashboardTheme.SPACE_2, DashboardTheme.SPACE_1, DashboardTheme.SPACE_2, DashboardTheme.SPACE_1)
            row.setSpacing(DashboardTheme.SPACE_2)
            model_label = QLabel(model, objectName="muted")
            model_label.setWordWrap(True)
            model_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(model_label, 1)
            row.addStretch()
            count_label = QLabel(count, objectName="metricValue")
            count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            count_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
            row.addWidget(count_label)
            self._model_list.addWidget(row_widget)

    def place_near_anchor(self, anchor: Optional[QPoint] = None) -> None:
        """Resize for and clamp to the anchor's available screen geometry."""
        requested_anchor = anchor or self._anchor or QCursor.pos()
        application = QApplication.instance()
        screen = application.screenAt(requested_anchor) if application else None
        if screen is None and application:
            screen = application.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 800, 600)
        fallback_anchor = QPoint(
            available.right() - DashboardTheme.PANEL_MARGIN,
            available.bottom() - DashboardTheme.PANEL_MARGIN,
        )
        active_anchor = requested_anchor if available.contains(requested_anchor) else fallback_anchor
        maximum_height = max(1, available.height() - DashboardTheme.PANEL_MARGIN * 2)
        panel_height = min(DashboardTheme.PREFERRED_HEIGHT, maximum_height)
        self.resize(DashboardTheme.PANEL_WIDTH, panel_height)
        self.move(calculate_popup_position(active_anchor, self.size(), available))

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._closing = True
            self.hide()
            event.ignore()
            return
        event.accept()


def show_dashboard(config: AppConfig, anchor: Optional[QPoint] = None) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = DashboardWindow(config, anchor)
    window.place_near_anchor(anchor)
    window.show()
    window.raise_()
    app.exec()
