"""Isolated Qt dashboard process for Codex usage and local activity metadata."""
from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, QThread, Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QCursor, QFontDatabase, QPainter, QPen, QResizeEvent
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

from cache import CacheManager
from config import AppConfig, SectionVisibility, set_dark_mode, set_section_visibility
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
    PANEL_WIDTH = 500
    PREFERRED_HEIGHT = 640
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
    RADIUS_BAR = 4
    BORDER_WIDTH = 1
    FONT_EYEBROW = 13
    FONT_BODY = 15
    FONT_TAB = 16
    FONT_MODEL = 17
    FONT_SECTION = 18
    FONT_METRIC = 22
    FONT_CLOSE = 19
    FONT_TITLE = 26
    FONT_USAGE = 32
    LETTER_SPACING = 1
    BUTTON_HEIGHT = 28
    PERIOD_HEIGHT = 28
    BAR_HEIGHT = 8
    CLOSE_BUTTON_WIDTH = 28
    SWITCH_WIDTH = 40
    SWITCH_HEIGHT = 22
    SWITCH_THUMB_MARGIN = 3
    SWITCH_TEXT_GAP = 8
    LIGHT_PALETTE = {
        "window": "#f3f7fa",
        "shell": "#ffffff",
        "surface": "#f7fafc",
        "primary_surface": "#e8f6fa",
        "weekly_surface": "#ebf7f1",
        "segment_surface": "#edf2f5",
        "border": "#cbd9e1",
        "primary_border": "#9bd5e3",
        "weekly_border": "#a8d9c5",
        "text": "#163247",
        "muted": "#587082",
        "accent": "#0b7187",
        "accent_hover": "#075d70",
        "accent_disabled": "#9cc4cd",
        "weekly_value": "#17694f",
        "bar_fill": "#1f8f5f",
        "bar_track": "#e3edf1",
        "control_text": "#ffffff",
    }
    DARK_PALETTE = {
        "window": "#0a0b0d",
        "shell": "#131417",
        "surface": "#1c1e22",
        "primary_surface": "#16232a",
        "weekly_surface": "#15241e",
        "segment_surface": "#22252a",
        "border": "#33373d",
        "primary_border": "#2f8193",
        "weekly_border": "#34735a",
        "text": "#f2f4f6",
        "muted": "#a5adb6",
        "accent": "#4fc3d7",
        "accent_hover": "#82d9e7",
        "accent_disabled": "#466c76",
        "weekly_value": "#75d4a5",
        "bar_fill": "#34d399",
        "bar_track": "#2a2e34",
        "control_text": "#102126",
    }

    @classmethod
    def palette(cls, dark_mode: bool) -> dict[str, str]:
        return cls.DARK_PALETTE if dark_mode else cls.LIGHT_PALETTE


class ElidedLabel(QLabel):
    def __init__(self, text: str, object_name: str):
        super().__init__(text, objectName=object_name)
        self._full_text = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            self.contentsRect().width(),
        ))

    def minimumSizeHint(self) -> QSize:
        return QSize(DashboardTheme.NONE, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(DashboardTheme.NONE, super().sizeHint().height())


class ToggleSwitch(QCheckBox):
    """A QCheckBox painted as a rounded track with a sliding circular thumb.

    Keeps the QCheckBox API (setChecked/isChecked/toggled, objectName lookup,
    keyboard Space toggling, accessibility state) while replacing the default
    square indicator with a real on/off switch.
    """

    def __init__(self, text: str, object_name: str):
        super().__init__(text)
        self.setObjectName(object_name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._switch_palette = DashboardTheme.LIGHT_PALETTE

    def set_switch_palette(self, palette: dict[str, str]) -> None:
        """Recolor the painted switch for the active light/dark theme."""
        self._switch_palette = palette
        self.update()

    def sizeHint(self) -> QSize:
        width = (
            DashboardTheme.SWITCH_WIDTH + DashboardTheme.SWITCH_TEXT_GAP
            + self.fontMetrics().horizontalAdvance(self.text())
        )
        height = max(
            DashboardTheme.SWITCH_HEIGHT,
            self.fontMetrics().height() + DashboardTheme.SPACE_2,
        )
        return QSize(max(width, DashboardTheme.NONE), height)

    def minimumSizeHint(self) -> QSize:
        return QSize(
            DashboardTheme.SWITCH_WIDTH + DashboardTheme.SWITCH_TEXT_GAP,
            self.sizeHint().height(),
        )

    def hitButton(self, position: QPoint) -> bool:
        return self.rect().contains(position)

    def paintEvent(self, event) -> None:
        palette = self._switch_palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        track_width = DashboardTheme.SWITCH_WIDTH
        track_height = DashboardTheme.SWITCH_HEIGHT
        track_left = rect.right() - track_width
        track_top = rect.center().y() - track_height // 2
        track = QRectF(track_left, track_top, track_width, track_height)
        checked = self.isChecked()

        if not self.isEnabled():
            painter.setOpacity(0.45)
        if self.underMouse() and self.isEnabled():
            hover = rect.adjusted(0, 1, 0, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette["primary_surface"]))
            painter.drawRoundedRect(
                QRectF(hover), DashboardTheme.RADIUS_CONTROL, DashboardTheme.RADIUS_CONTROL,
            )
        if self.hasFocus():
            focus_ring = track.adjusted(-2.0, -2.0, 2.0, 2.0)
            painter.setPen(QPen(QColor(palette["accent"]), DashboardTheme.BORDER_WIDTH))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                focus_ring, focus_ring.height() / 2, focus_ring.height() / 2,
            )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["accent"] if checked else palette["segment_surface"]))
        painter.drawRoundedRect(track, track_height / 2, track_height / 2)
        if not checked:
            painter.setPen(QPen(
                QColor(palette["border"]), DashboardTheme.BORDER_WIDTH,
            ))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track, track_height / 2, track_height / 2)

        margin = DashboardTheme.SWITCH_THUMB_MARGIN
        thumb_radius = (track_height - margin * 2) / 2
        travel = track_width - margin * 2 - thumb_radius * 2
        thumb_left = track_left + margin + (travel if checked else 0)
        thumb = QRectF(
            thumb_left, track_top + margin, thumb_radius * 2, thumb_radius * 2,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["shell"]))
        painter.drawEllipse(thumb)

        painter.setPen(QColor(palette["text"]))
        text_right = track_left - DashboardTheme.SWITCH_TEXT_GAP
        text_width = max(0, text_right - rect.left())
        elided = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, text_width,
        )
        painter.drawText(
            QRectF(rect.left(), rect.top(), text_width, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided,
        )


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


def _format_relative_time(value: Optional[str], now: Optional[datetime] = None) -> str:
    """Format a timestamp as a relative time string (e.g., 'just now', '5m ago', '2h 30m ago')."""
    if not value:
        return "unknown"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        timestamp_utc = timestamp.astimezone(timezone.utc)
        diff = reference - timestamp_utc
        total_seconds = int(diff.total_seconds())
        if total_seconds <= 0:
            return "just now"
        if total_seconds < 60:
            return "{}s ago".format(total_seconds)
        minutes = total_seconds // 60
        if minutes < 60:
            return "{}m ago".format(minutes)
        hours = minutes // 60
        remaining_minutes = minutes % 60
        if remaining_minutes == 0:
            return "{}h ago".format(hours)
        return "{}h {}m ago".format(hours, remaining_minutes)
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"


def _parse_model_updated_at(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


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


def _remaining_balance(used_text: str) -> tuple[str, int]:
    """Derive remaining percent display and bar value from a used-percent string.

    Returns ("Not available", 0) whenever the used value is unavailable so the
    UI never invents a balance.
    """
    if not used_text.endswith("%"):
        return "Not available", 0
    try:
        used = float(used_text[:-1])
    except ValueError:
        return "Not available", 0
    remaining = max(0.0, min(100.0, 100.0 - used))
    return "{:.0f}%".format(remaining), int(round(remaining))


def _parse_snapshot_time(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _filter_history_period(
    history: tuple[UsageSnapshot, ...], days: Optional[int],
    now: Optional[datetime] = None,
) -> tuple[UsageSnapshot, ...]:
    """Keep snapshots whose timestamp falls inside the selected day window."""
    if days is None:
        return history
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = reference - timedelta(days=days)
    kept = []
    for snapshot in history:
        stamp = _parse_snapshot_time(snapshot.timestamp)
        if stamp is not None and stamp >= cutoff:
            kept.append(snapshot)
    return tuple(kept)


def _group_history_by_day(history: tuple[UsageSnapshot, ...]) -> list[tuple[str, float, int]]:
    """Aggregate five-hour usage samples into per-day averages from real snapshots."""
    buckets: dict[date, list[float]] = {}
    for snapshot in history:
        if snapshot.five_hour is None:
            continue
        stamp = _parse_snapshot_time(snapshot.timestamp)
        if stamp is None:
            continue
        buckets.setdefault(stamp.astimezone().date(), []).append(float(snapshot.five_hour))
    grouped: list[tuple[str, float, int]] = []
    for day in sorted(buckets):
        values = buckets[day]
        grouped.append((day.strftime("%b %d"), sum(values) / len(values), len(values)))
    return grouped


def _dashboard_font_family() -> str:
    for family in ("Segoe UI", "Arial", "DejaVu Sans", "Liberation Sans"):
        if family in QFontDatabase.families():
            return family
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    if font_id >= 0:
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    return "Sans Serif"


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
    reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    model_cutoff = reference_time - timedelta(hours=72)

    model_rows = []
    for model, count in local.model_breakdown.items():
        tokens = local.model_tokens_used.get(model)
        last_updated = local.model_last_updated_at.get(model)
        updated_at = _parse_model_updated_at(last_updated)
        if updated_at is None or updated_at < model_cutoff:
            continue
        tokens_str = _format_tokens(tokens)
        relative_time = _format_relative_time(last_updated, reference_time)
        model_rows.append((model, "{} tokens • last used {}".format(tokens_str, relative_time)))
    if not model_rows:
        model_rows = [("Model breakdown", "Not available")]

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
        model_rows=tuple(model_rows),
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
        self._refresh_scheduled = False
        self._last_state: Optional[DashboardState] = None
        self._period_days: Optional[int] = 7
        self._init_ui()
        # Defer initial refresh until event loop is running (queued after startup)
        QTimer.singleShot(0, self._schedule_initial_refresh)

    def _schedule_initial_refresh(self) -> None:
        if not self._refresh_scheduled:
            self._refresh_scheduled = True
            self.refresh()

    def _init_ui(self) -> None:
        self.setObjectName("codexDashboard")
        self.setWindowTitle("Codex Analytics")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(DashboardTheme.PANEL_WIDTH)
        self.setMinimumHeight(DashboardTheme.NONE)
        self.resize(DashboardTheme.PANEL_WIDTH, DashboardTheme.PREFERRED_HEIGHT)
        self._apply_theme()

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
        title_block.addWidget(QLabel("CODEX • ANALYTICS", objectName="eyebrow"))
        title = QLabel("Codex Analytics", objectName="title")
        title.setWordWrap(True)
        self._title = title
        title_block.addWidget(title)
        self._status = QLabel("Refreshing current limits…", objectName="subtitle")
        title_block.addWidget(self._status)
        header.addLayout(title_block)
        header.addStretch()
        self._refresh_button = QPushButton("Refresh", objectName="refreshButton")
        self._refresh_button.clicked.connect(self.refresh)
        header.addWidget(self._refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        self._settings_button = QToolButton(objectName="settingsToggle")
        self._settings_button.setText("⚙")
        self._settings_button.setToolTip("Toggle dashboard settings")
        self._settings_button.setAccessibleName("Toggle dashboard settings")
        self._settings_button.setCheckable(True)
        self._settings_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._settings_button.toggled.connect(self._toggle_settings_panel)
        header.addWidget(self._settings_button, alignment=Qt.AlignmentFlag.AlignTop)
        self._theme_button = QToolButton(objectName="themeToggle")
        self._theme_button.clicked.connect(self._toggle_dark_mode)
        self._apply_theme()
        header.addWidget(self._theme_button, alignment=Qt.AlignmentFlag.AlignTop)
        close_button = QToolButton(objectName="closeButton")
        close_button.setText("X")
        close_button.setToolTip("Close dashboard")
        close_button.clicked.connect(self.close)
        header.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        tabs = QHBoxLayout()
        tabs.setSpacing(DashboardTheme.SPACE_4)
        self._usage_tab = QToolButton(objectName="usageTab")
        self._usage_tab.setText("Usage")
        self._usage_tab.setCheckable(True)
        self._usage_tab.setChecked(True)
        self._review_tab = QToolButton(objectName="reviewTab")
        self._review_tab.setText("Code review")
        self._review_tab.setCheckable(True)
        tab_group = QButtonGroup(self)
        tab_group.setExclusive(True)
        tab_group.addButton(self._usage_tab)
        tab_group.addButton(self._review_tab)
        self._usage_tab.clicked.connect(lambda: self._show_usage_page(True))
        self._review_tab.clicked.connect(lambda: self._show_usage_page(False))
        tabs.addWidget(self._usage_tab)
        tabs.addWidget(self._review_tab)
        tabs.addStretch()
        root.addLayout(tabs)

        self._settings_panel = QFrame(objectName="settingsPanel")
        self._settings_panel.setVisible(False)
        root.addWidget(self._settings_panel)
        settings_layout = QVBoxLayout(self._settings_panel)
        settings_layout.setContentsMargins(
            DashboardTheme.SPACE_3, DashboardTheme.SPACE_3,
            DashboardTheme.SPACE_3, DashboardTheme.SPACE_3,
        )
        settings_layout.setSpacing(DashboardTheme.SPACE_2)
        settings_layout.addWidget(QLabel("SHOW SECTIONS", objectName="cardTitle"))
        self._section_checks: dict[str, QCheckBox] = {}
        for key, text, object_name in (
            ("codex_analytics", "Codex Analytics", "codexAnalyticsSectionCheck"),
            ("balance", "Balance", "balanceSectionCheck"),
            ("credits", "Credits", "creditsSectionCheck"),
            ("usage_breakdown", "Usage breakdown", "usageBreakdownSectionCheck"),
            ("local_activity", "Local activity", "localActivitySectionCheck"),
            ("history", "History", "historySectionCheck"),
        ):
            check = ToggleSwitch(text, object_name)
            check.setChecked(getattr(self._config.section_visibility, key))
            check.setToolTip("Show or hide the {} section".format(text))
            check.setAccessibleName("{} section".format(text))
            settings_layout.addWidget(check)
            self._section_checks[key] = check
        self._apply_toggle_colors()
        for key, check in self._section_checks.items():
            check.toggled.connect(
                lambda checked, name=key: self._on_section_visibility_toggled(name, checked),
            )

        scroll_area = QScrollArea(objectName="dashboardScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        scroll_area.setWidget(scroll_content)
        root.addWidget(scroll_area, 1)
        content = QVBoxLayout(scroll_content)
        content.setContentsMargins(DashboardTheme.SPACE_1, DashboardTheme.SPACE_1, DashboardTheme.SPACE_1, DashboardTheme.SPACE_1)
        content.setSpacing(DashboardTheme.SPACE_3)

        self._usage_page = QWidget(objectName="usagePage")
        content.addWidget(self._usage_page)
        usage = QVBoxLayout(self._usage_page)
        usage.setContentsMargins(DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE)
        usage.setSpacing(DashboardTheme.SPACE_3)

        period_row = QHBoxLayout()
        period_row.setSpacing(DashboardTheme.SPACE_2)
        self._period_group = QButtonGroup(self)
        self._period_group.setExclusive(True)
        self._period_7d = self._make_period_button("7D", "period7D", checked=True)
        self._period_1m = self._make_period_button("1M", "period1M")
        self._period_custom = self._make_period_button("Custom", "periodCustom")
        self._period_custom.setEnabled(False)
        self._period_custom.setToolTip("Custom range is not available")
        for button in (self._period_7d, self._period_1m, self._period_custom):
            self._period_group.addButton(button)
            period_row.addWidget(button)
        self._period_7d.clicked.connect(lambda: self._select_period(7))
        self._period_1m.clicked.connect(lambda: self._select_period(30))
        period_row.addStretch()
        self._group_by_day = QToolButton(objectName="groupByDay")
        self._group_by_day.setText("Group by Day")
        self._group_by_day.setCheckable(True)
        self._group_by_day.setToolTip("Average usage snapshots per day")
        self._group_by_day.toggled.connect(lambda _: self._update_history())
        period_row.addWidget(self._group_by_day)
        usage.addLayout(period_row)

        balance_title = QLabel("Balance", objectName="sectionTitle")
        usage.addWidget(balance_title)
        balance_row = QHBoxLayout()
        balance_row.setSpacing(DashboardTheme.SPACE_2)
        self._five_balance = self._make_balance_card("5-HOUR REMAINING", "primaryCard", "five")
        self._weekly_balance = self._make_balance_card("WEEKLY REMAINING", "weeklyCard", "weekly")
        balance_row.addWidget(self._five_balance[0])
        balance_row.addWidget(self._weekly_balance[0])
        usage.addLayout(balance_row)

        self._credits_card = self._make_credits_card()
        usage.addWidget(self._credits_card)

        self._history_card = self._make_history_card()
        usage.addWidget(self._history_card)

        breakdown_title = QLabel("Usage breakdown", objectName="sectionTitle")
        usage.addWidget(breakdown_title)
        self._breakdown_card = self._make_breakdown_card()
        usage.addWidget(self._breakdown_card)

        activity_title = QLabel("Local activity", objectName="sectionTitle")
        usage.addWidget(activity_title)
        activity = QGridLayout()
        activity.setSpacing(DashboardTheme.SPACE_2)
        sessions_card, self._sessions_24h = self._make_metric(activity, 0, "LAST 24 HOURS")
        sessions_week_card, self._sessions_7d = self._make_metric(activity, 1, "LAST 7 DAYS")
        sessions_total_card, self._total_sessions = self._make_metric(activity, 2, "ALL SESSIONS")
        usage.addLayout(activity)
        self._last_update = QLabel("Last local update: Not available", objectName="muted")
        self._last_update.setWordWrap(True)
        usage.addWidget(self._last_update)
        self._observation = QLabel("", objectName="muted")
        self._observation.setWordWrap(True)
        usage.addWidget(self._observation)
        usage.addStretch()

        self._section_widgets: dict[str, list[QWidget]] = {
            "codex_analytics": [self._title],
            "balance": [balance_title, self._five_balance[0], self._weekly_balance[0]],
            "credits": [self._credits_card],
            "usage_breakdown": [breakdown_title, self._breakdown_card],
            "history": [self._history_card, self._group_by_day],
            "local_activity": [
                activity_title, sessions_card, sessions_week_card,
                sessions_total_card, self._last_update, self._observation,
            ],
        }
        self._apply_section_visibility(self._config.section_visibility)

        self._review_page = QWidget(objectName="reviewPage")
        self._review_page.setVisible(False)
        content.addWidget(self._review_page)
        review = QVBoxLayout(self._review_page)
        review.setContentsMargins(DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE)
        review.setSpacing(DashboardTheme.SPACE_2)
        review.addWidget(QLabel("Code review", objectName="sectionTitle"))
        review_notice = QLabel(
            "Code review analytics are not available from local metadata.",
            objectName="reviewNotice",
        )
        review_notice.setWordWrap(True)
        review.addWidget(review_notice)
        review.addStretch()

    def _show_usage_page(self, show_usage: bool) -> None:
        self._usage_page.setVisible(show_usage)
        self._review_page.setVisible(not show_usage)

    def _select_period(self, days: int) -> None:
        self._period_days = days
        self._update_history()

    def _make_period_button(self, text: str, name: str, checked: bool = False) -> QToolButton:
        button = QToolButton(objectName=name)
        button.setText(text)
        button.setCheckable(True)
        button.setChecked(checked)
        return button

    def _apply_theme(self) -> None:
        palette = DashboardTheme.palette(self._config.dark_mode)
        font_family = _dashboard_font_family()
        self.setStyleSheet("""
            QWidget#codexDashboard {{ background: transparent; color: {text}; font-family: '{font_family}'; }}
            QFrame#shell {{ background: {shell}; border: {border_width}px solid {border}; border-radius: {panel_radius}px; }}
            QScrollArea#dashboardScroll, QScrollArea#dashboardScroll QWidget {{ border: 0; background: {shell}; }}
            QScrollBar:vertical {{ width: {scrollbar_width}px; background: {shell}; margin: {space_1}px; }}
            QScrollBar::handle:vertical {{ background: {border}; border-radius: {scrollbar_radius}px; min-height: {scrollbar_minimum}px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QFrame#usageCard, QFrame#creditsCard, QFrame#metricCard, QFrame#modelRow {{ background: {surface}; border: {border_width}px solid {border}; border-radius: {card_radius}px; }}
            QFrame#settingsPanel {{ background: {surface}; border: {border_width}px solid {border}; border-radius: {card_radius}px; }}
            QFrame#primaryCard {{ background: {primary_surface}; border: {border_width}px solid {primary_border}; border-radius: {card_radius}px; }}
            QFrame#weeklyCard {{ background: {weekly_surface}; border: {border_width}px solid {weekly_border}; border-radius: {card_radius}px; }}
            QLabel#eyebrow {{ color: {accent}; font-size: {font_eyebrow}px; font-weight: 700; letter-spacing: {letter_spacing}px; }}
            QLabel#title {{ color: {text}; font-family: '{font_family}'; font-size: {font_title}px; font-weight: 700; }}
            QLabel#subtitle, QLabel#muted, QLabel#reviewNotice {{ color: {muted}; font-size: {font_body}px; }}
            QLabel#historyValue {{ color: {text}; font-size: {font_body}px; }}
            QLabel#cardTitle {{ color: {accent}; font-size: {font_eyebrow}px; font-weight: 700; }}
            QLabel#fiveRemainingValue, QLabel#weeklyRemainingValue {{ color: {bar_fill}; font-family: '{font_family}'; font-size: {font_usage}px; font-weight: 700; }}
            QLabel#creditsValue {{ color: {text}; font-family: '{font_family}'; font-size: {font_metric}px; font-weight: 700; }}
            QLabel#metricValue {{ color: {text}; font-family: '{font_family}'; font-size: {font_metric}px; font-weight: 700; }}
            QLabel#sectionTitle {{ color: {text}; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: {font_section}px; font-weight: 700; }}
            QLabel#modelName {{ color: {text}; font-size: {font_model}px; font-weight: 700; }}
            QLabel#modelDetail {{ color: {muted}; font-size: {font_body}px; }}
            QProgressBar#fiveRemainingBar, QProgressBar#weeklyRemainingBar {{ background: {bar_track}; border: 0; border-radius: {bar_radius}px; min-height: {bar_height}px; max-height: {bar_height}px; text-visible: false; }}
            QProgressBar#fiveRemainingBar::chunk, QProgressBar#weeklyRemainingBar::chunk {{ background: {bar_fill}; border-radius: {bar_radius}px; }}
            QToolButton#usageTab, QToolButton#reviewTab {{ background: transparent; color: {muted}; border: 0; border-bottom: 2px solid transparent; padding: {space_1}px {space_2}px; font-family: '{font_family}'; font-size: {font_tab}px; font-weight: 700; }}
            QToolButton#usageTab:hover, QToolButton#reviewTab:hover {{ color: {text}; }}
            QToolButton#usageTab:checked, QToolButton#reviewTab:checked {{ color: {text}; border-bottom-color: {accent}; }}
            QToolButton#period7D, QToolButton#period1M, QToolButton#periodCustom, QToolButton#groupByDay {{ background: {segment_surface}; color: {muted}; border: {border_width}px solid {border}; border-radius: {control_radius}px; padding: 0 {space_3}px; min-height: {period_height}px; font-size: {font_tab}px; font-weight: 700; }}
            QToolButton#period7D:hover, QToolButton#period1M:hover, QToolButton#groupByDay:hover {{ color: {text}; }}
            QToolButton#period7D:checked, QToolButton#period1M:checked, QToolButton#groupByDay:checked {{ background: {accent}; color: {control_text}; border-color: {accent}; }}
            QToolButton#periodCustom {{ color: {accent_disabled}; }}
            QPushButton#refreshButton {{ background: {accent}; color: {control_text}; border: 0; border-radius: {control_radius}px; min-height: {button_height}px; padding: 0 {space_2}px; font-family: '{font_family}'; font-size: {font_tab}px; font-weight: 700; }}
            QPushButton#refreshButton:hover {{ background: {accent_hover}; }}
            QPushButton#refreshButton:disabled {{ background: {accent_disabled}; }}
            QToolButton#themeToggle {{ background: {surface}; color: {text}; border: {border_width}px solid {border}; border-radius: {control_radius}px; font-size: {font_section}px; min-width: {close_button_width}px; min-height: {button_height}px; }}
            QToolButton#themeToggle:hover {{ background: {primary_surface}; border-color: {primary_border}; }}
            QToolButton#settingsToggle {{ background: {surface}; color: {text}; border: {border_width}px solid {border}; border-radius: {control_radius}px; font-size: {font_section}px; min-width: {close_button_width}px; min-height: {button_height}px; }}
            QToolButton#settingsToggle:hover {{ background: {primary_surface}; border-color: {primary_border}; }}
            QToolButton#settingsToggle:checked {{ background: {accent}; color: {control_text}; border-color: {accent}; }}
             QCheckBox#codexAnalyticsSectionCheck, QCheckBox#balanceSectionCheck, QCheckBox#creditsSectionCheck, QCheckBox#usageBreakdownSectionCheck, QCheckBox#localActivitySectionCheck, QCheckBox#historySectionCheck {{ background: transparent; color: {text}; font-family: '{font_family}'; font-size: {font_body}px; }}
            QToolButton#closeButton {{ color: {muted}; border: 0; border-radius: {control_radius}px; font-size: {font_close}px; min-width: {close_button_width}px; min-height: {button_height}px; }}
            QToolButton#closeButton:hover {{ background: {surface}; color: {text}; }}
        """.format(
            **palette,
            font_family=font_family,
            border_width=DashboardTheme.BORDER_WIDTH,
            panel_radius=DashboardTheme.RADIUS_PANEL,
            scrollbar_width=DashboardTheme.SPACE_2,
            space_1=DashboardTheme.SPACE_1,
            space_2=DashboardTheme.SPACE_2,
            space_3=DashboardTheme.SPACE_3,
            scrollbar_radius=DashboardTheme.SPACE_1,
            scrollbar_minimum=DashboardTheme.SPACE_4 * 2,
            card_radius=DashboardTheme.RADIUS_CARD,
            control_radius=DashboardTheme.RADIUS_CONTROL,
            bar_radius=DashboardTheme.RADIUS_BAR,
            bar_height=DashboardTheme.BAR_HEIGHT,
            font_eyebrow=DashboardTheme.FONT_EYEBROW,
            font_title=DashboardTheme.FONT_TITLE,
            font_body=DashboardTheme.FONT_BODY,
            font_tab=DashboardTheme.FONT_TAB,
            font_model=DashboardTheme.FONT_MODEL,
            font_usage=DashboardTheme.FONT_USAGE,
            font_metric=DashboardTheme.FONT_METRIC,
            font_section=DashboardTheme.FONT_SECTION,
            letter_spacing=DashboardTheme.LETTER_SPACING,
            button_height=DashboardTheme.BUTTON_HEIGHT,
            period_height=DashboardTheme.PERIOD_HEIGHT,
            font_close=DashboardTheme.FONT_CLOSE,
            close_button_width=DashboardTheme.CLOSE_BUTTON_WIDTH,
        ))
        self._apply_toggle_colors()
        if hasattr(self, "_theme_button"):
            if self._config.dark_mode:
                self._theme_button.setText("☀")
                self._theme_button.setToolTip("Switch to light mode")
                self._theme_button.setAccessibleName("Switch to light mode")
            else:
                self._theme_button.setText("☾")
                self._theme_button.setToolTip("Switch to dark mode")
                self._theme_button.setAccessibleName("Switch to dark mode")

    def _apply_toggle_colors(self) -> None:
        """Feed the active theme palette to the painted section switches."""
        if not hasattr(self, "_section_checks"):
            return
        palette = DashboardTheme.palette(self._config.dark_mode)
        for check in self._section_checks.values():
            check.set_switch_palette(palette)

    def _toggle_dark_mode(self) -> None:
        self._config = set_dark_mode(not self._config.dark_mode)
        self._apply_theme()

    def _toggle_settings_panel(self, checked: bool) -> None:
        """Show or hide the inline dashboard settings panel (never a popup)."""
        self._settings_panel.setVisible(checked)

    def _on_section_visibility_toggled(self, key: str, checked: bool) -> None:
        """Apply a section checkbox immediately and persist the choice."""
        updated = replace(self._config.section_visibility, **{key: checked})
        self._config = set_section_visibility(updated)
        for widget in self._section_widgets.get(key, []):
            widget.setVisible(checked)

    def _apply_section_visibility(self, visibility: SectionVisibility) -> None:
        for key, widgets in self._section_widgets.items():
            visible = getattr(visibility, key)
            for widget in widgets:
                widget.setVisible(visible)

    def _make_balance_card(self, title: str, name: str, prefix: str) -> tuple[QFrame, QLabel, QProgressBar, QLabel]:
        card = QFrame(objectName=name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3)
        layout.setSpacing(DashboardTheme.SPACE_2)
        layout.addWidget(QLabel(title, objectName="cardTitle"))
        value = QLabel("Not available", objectName="{}RemainingValue".format(prefix))
        layout.addWidget(value)
        bar = QProgressBar(objectName="{}RemainingBar".format(prefix))
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        layout.addWidget(bar)
        reset = QLabel("Resets: Not available", objectName="muted")
        reset.setWordWrap(True)
        layout.addWidget(reset)
        return card, value, bar, reset

    def _make_credits_card(self) -> QFrame:
        frame = QFrame(objectName="creditsCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3)
        layout.setSpacing(DashboardTheme.SPACE_1)
        layout.addWidget(QLabel("CREDITS REMAINING", objectName="cardTitle"))
        self._credits_value = QLabel("Not available", objectName="creditsValue")
        layout.addWidget(self._credits_value)
        self._credits_expiry = QLabel("Expires in: Not available", objectName="muted")
        self._credits_expiry.setWordWrap(True)
        layout.addWidget(self._credits_expiry)
        return frame

    def _make_history_card(self) -> QFrame:
        frame = QFrame(objectName="usageCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3)
        layout.setSpacing(DashboardTheme.SPACE_2)
        layout.addWidget(QLabel("HISTORY", objectName="cardTitle"))
        self._trend = QLabel("Usage history: collecting snapshots…", objectName="historyValue")
        self._trend.setWordWrap(True)
        layout.addWidget(self._trend)
        self._trend_notice = QLabel(
            "Usage-rate and daily trend charts are not available from local metadata.",
            objectName="muted",
        )
        self._trend_notice.setWordWrap(True)
        layout.addWidget(self._trend_notice)
        return frame

    def _make_breakdown_card(self) -> QFrame:
        frame = QFrame(objectName="usageCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3)
        layout.setSpacing(DashboardTheme.SPACE_2)
        self._plan = QLabel("Plan: Not available", objectName="metricValue")
        layout.addWidget(self._plan)
        self._tokens = QLabel("Not available", objectName="muted")
        self._tokens.setWordWrap(True)
        layout.addWidget(self._tokens)
        self._model_list = QVBoxLayout()
        self._model_list.setSpacing(DashboardTheme.SPACE_1)
        layout.addLayout(self._model_list)
        return frame

    def _make_metric(self, layout: QGridLayout, column: int, title: str) -> tuple[QFrame, QLabel]:
        card = QFrame(objectName="metricCard")
        content = QVBoxLayout(card)
        content.setContentsMargins(DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2, DashboardTheme.SPACE_2)
        content.setSpacing(DashboardTheme.SPACE_1)
        content.addWidget(QLabel(title, objectName="cardTitle"))
        value = QLabel("0", objectName="metricValue")
        content.addWidget(value)
        layout.addWidget(card, 0, column)
        return card, value

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
        self._last_state = state
        for parts, used_text, reset_text in (
            (self._five_balance, state.five_hour, "Resets: {}".format(state.five_hour_reset)),
            (self._weekly_balance, state.seven_day, "Resets: {}".format(state.seven_day_reset)),
        ):
            remaining_text, bar_value = _remaining_balance(used_text)
            parts[1].setText(remaining_text)
            parts[2].setValue(bar_value)
            parts[3].setText(reset_text)
        self._credits_value.setText(state.credits)
        self._credits_expiry.setText("Expires in: {}".format(state.credit_expiry))
        self._plan.setText("Plan: {}".format(state.plan))
        self._sessions_24h.setText(state.sessions_24h)
        self._sessions_7d.setText(state.sessions_7d)
        self._total_sessions.setText(state.total_sessions)
        self._tokens.setText("Tokens used (local aggregate): {}".format(state.tokens))
        self._last_update.setText("Last local update: {}".format(state.last_update))
        self._observation.setText(state.local_observation)
        self._update_history()
        self._status.setText("Current limits loaded" if usage else "Current limits unavailable; local metadata shown")
        while self._model_list.count():
            child = self._model_list.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for model, detail in state.model_rows:
            row_widget = QFrame(objectName="modelRow")
            row_widget.setMaximumWidth(
                DashboardTheme.PANEL_WIDTH - (
                    DashboardTheme.SPACE_3
                    + DashboardTheme.SPACE_2 * 2
                    + DashboardTheme.SPACE_1 * 2
                ),
            )
            row = QVBoxLayout(row_widget)
            row.setContentsMargins(
                DashboardTheme.SPACE_2,
                DashboardTheme.SPACE_2,
                DashboardTheme.SPACE_2,
                DashboardTheme.SPACE_2,
            )
            row.setSpacing(DashboardTheme.SPACE_TIGHT)
            model_label = ElidedLabel(model, "modelName")
            row.addWidget(model_label)
            detail_label = QLabel(detail, objectName="modelDetail")
            detail_label.setWordWrap(True)
            row.addWidget(detail_label)
            self._model_list.addWidget(row_widget)

    def _update_history(self) -> None:
        """Refresh the History card from local snapshots using the selected period."""
        state = self._last_state
        if state is None:
            return
        history = state.usage_history
        if not history:
            self._trend.setText("Usage history: collecting snapshots…")
            return
        filtered = _filter_history_period(history, self._period_days)
        if not filtered:
            self._trend.setText("Usage history: no snapshots in selected period")
            return
        if self._group_by_day.isChecked():
            grouped = _group_history_by_day(filtered)
            if not grouped:
                self._trend.setText("Usage history: waiting for valid snapshots")
                return
            lines = [
                "{}  ·  avg {:.0f}%  ·  {} sample{}".format(
                    day, average, count, "" if count == 1 else "s",
                )
                for day, average, count in grouped[-8:]
            ]
            self._trend.setText("\n".join(lines))
            return
        values = [entry.five_hour for entry in filtered if entry.five_hour is not None]
        if values:
            blocks = ".:-=+*#@"
            maximum = max(max(values), 1)
            sparkline = "".join(blocks[min(7, int(value / maximum * 7))] for value in values)
            self._trend.setText("5-hour usage history  {}   ({} snapshots)".format(sparkline, len(filtered)))
        else:
            self._trend.setText("Usage history: waiting for valid snapshots")

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
            self._worker.requestInterruption()
            self._worker.terminate()
            self._worker.wait(1000)
        event.accept()
        application = QApplication.instance()
        if application:
            application.quit()


def show_dashboard(config: AppConfig, anchor: Optional[QPoint] = None) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = DashboardWindow(config, anchor)
    window.place_near_anchor(anchor)
    window.show()
    window.raise_()
    app.exec()
