"""Isolated Qt dashboard process for Codex usage and local activity metadata."""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from analytics import CostAnalytics, calculate_cost_analytics
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, QThread, Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QCursor, QFontDatabase, QPainter, QPen, QResizeEvent
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QButtonGroup, QCheckBox, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from cache import CacheManager
from config import AppConfig, SectionVisibility, set_dark_mode, set_section_visibility
from local_codex import (
    LocalCodexSnapshot,
    TokenUsageRecord,
    UsageSnapshot,
    read_local_codex_snapshot,
    read_token_usage_records,
    read_usage_snapshots,
    record_usage_snapshot,
)
from logger import Logger
from providers.codex import (
    CodexProvider,
    FetchResult,
    ResetConsumeResult,
    STATUS_API_ERROR,
    STATUS_AUTH_REQUIRED,
    STATUS_FRESH,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
)


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
    refresh_status: str = "Not available"
    refresh_detail: str = ""
    token_usage_records: tuple[TokenUsageRecord, ...] = ()


class DashboardTheme:
    PANEL_WIDTH = 500
    PREFERRED_HEIGHT = 640
    ANALYTICS_PANEL_WIDTH = 960
    ANALYTICS_PREFERRED_HEIGHT = 820
    PANEL_MARGIN = 8
    ANCHOR_GAP = 10
    NONE = 0
    SPACE_TIGHT = 2
    SPACE_1 = 4
    SPACE_2 = 8
    SPACE_3 = 12
    SPACE_4 = 16
    RADIUS_PANEL = 12
    RADIUS_CARD = 8
    RADIUS_CONTROL = 6
    RADIUS_BAR = 4
    BORDER_WIDTH = 1
    FONT_EYEBROW = 13
    FONT_BODY = 15
    FONT_TAB = 15
    FONT_MODEL = 17
    FONT_SECTION = 17
    FONT_METRIC = 22
    FONT_CLOSE = 19
    FONT_TITLE = 24
    FONT_USAGE = 30
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
        "window": "#101113",
        "shell": "#15171a",
        "surface": "#1c1e21",
        "primary_surface": "#182328",
        "weekly_surface": "#18241f",
        "segment_surface": "#25282c",
        "border": "#33383d",
        "primary_border": "#27778a",
        "weekly_border": "#347d63",
        "text": "#f2f4f6",
        "muted": "#a5adb6",
        "accent": "#20c8ec",
        "accent_hover": "#67dcf5",
        "accent_disabled": "#526b70",
        "weekly_value": "#30d6a3",
        "bar_fill": "#30d6a3",
        "bar_track": "#292e32",
        "control_text": "#102126",
    }

    @classmethod
    def palette(cls, dark_mode: bool) -> dict[str, str]:
        return cls.DARK_PALETTE if dark_mode else cls.LIGHT_PALETTE


class UsageHistoryChart(QWidget):
    """Small native line chart for locally recorded Codex usage snapshots."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._series: list[tuple[str, list[float], str]] = []
        self._percent = True
        self._currency = False
        self._title = "Daily limit usage"
        self._x_labels = ("Earlier", "Latest")
        self._palette = DashboardTheme.LIGHT_PALETTE
        self.setObjectName("analyticsChart")
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(
        self, title: str, series: list[tuple[str, list[float], str]],
        palette: dict[str, str], percent: bool = True,
        x_labels: tuple[str, str] = ("Earlier", "Latest"),
        currency: bool = False,
    ) -> None:
        self._title = title
        self._series = series
        self._palette = palette
        self._percent = percent
        self._currency = currency
        self._x_labels = x_labels
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self.rect().adjusted(12, 10, -12, -10)
        painter.setPen(QColor(self._palette["text"]))
        painter.drawText(QRectF(bounds.left(), bounds.top(), bounds.width(), 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._title)
        chart = QRectF(bounds.left() + 34, bounds.top() + 34, bounds.width() - 44, bounds.height() - 60)
        grid_pen = QPen(QColor(self._palette["border"]))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        max_value = max((max(values) for _, values, _ in self._series if values), default=0.0)
        scale = 100.0 if self._percent else max(max_value, 1.0)
        middle_label = "50%" if self._percent else self._format_axis(scale / 2, self._currency)
        top_label = "100%" if self._percent else self._format_axis(scale, self._currency)
        zero_label = "0%" if self._percent else self._format_axis(0, self._currency)
        for fraction, label in ((0.0, zero_label), (0.5, middle_label), (1.0, top_label)):
            y = chart.bottom() - fraction * chart.height()
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QColor(self._palette["muted"]))
            painter.drawText(QRectF(bounds.left(), y - 8, 30, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
            painter.setPen(grid_pen)
        if not any(values for _, values, _ in self._series):
            painter.setPen(QColor(self._palette["muted"]))
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, "No local data in this period")
            return
        for series_index, (name, values, color_name) in enumerate(self._series):
            if not values:
                continue
            color = QColor(self._palette[color_name])
            painter.setPen(QPen(color, 2))
            painter.setBrush(color)
            points = [QPointF(
                chart.left() + (index / max(1, len(values) - 1)) * chart.width(),
                chart.bottom() - max(0.0, min(scale, value)) / scale * chart.height(),
            ) for index, value in enumerate(values)]
            for first, second in zip(points, points[1:]):
                painter.drawLine(first, second)
        painter.setPen(QColor(self._palette["muted"]))
        painter.drawText(QRectF(chart.left(), chart.bottom() + 3, 80, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._x_labels[0])
        painter.drawText(QRectF(chart.right() - 80, chart.bottom() + 3, 80, 18),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._x_labels[1])

    @staticmethod
    def date_label(timestamp: str) -> str:
        try:
            stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone()
            return "{} {}".format(stamp.strftime("%b"), stamp.day)
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _format_axis(value: float, currency: bool = False) -> str:
        if currency:
            return "${:.2f}".format(value)
        if value >= 1_000_000_000:
            return "{:.1f}B".format(value / 1_000_000_000)
        if value >= 1_000_000:
            return "{:.1f}M".format(value / 1_000_000)
        if value >= 1_000:
            return "{:.1f}K".format(value / 1_000)
        return "{:.0f}".format(value)


class UsageSparkline(QWidget):
    """Compact 5-hour history line shown on the Usage page."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._values: list[float] = []
        self._palette = DashboardTheme.LIGHT_PALETTE
        self.setObjectName("usageSparkline")
        self.setMinimumHeight(48)
        self.setMaximumHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, values: list[float], palette: dict[str, str]) -> None:
        self._values = values
        self._palette = palette
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        chart = self.rect().adjusted(2, 6, -2, -6)
        grid_pen = QPen(QColor(self._palette["border"]))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        for fraction in (0.25, 0.75):
            y = chart.top() + chart.height() * fraction
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
        if not self._values:
            return
        scale = 100.0
        points = [QPointF(
            chart.left() + index / max(1, len(self._values) - 1) * chart.width(),
            chart.bottom() - max(0.0, min(scale, value)) / scale * chart.height(),
        ) for index, value in enumerate(self._values)]
        painter.setPen(QPen(QColor(self._palette["accent"]), 2))
        for first, second in zip(points, points[1:]):
            painter.drawLine(first, second)
        if len(points) == 1:
            painter.drawPoint(points[0])


class CostCompositionBar(QWidget):
    """Compact stacked bar for uncached input, cache reads, and output cost."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._values = (0.0, 0.0, 0.0)
        self._palette = DashboardTheme.LIGHT_PALETTE
        self.setObjectName("costCompositionBar")
        self.setFixedHeight(12)

    def set_data(self, values: tuple[float, float, float], palette: dict[str, str]) -> None:
        self._values = tuple(max(0.0, value) for value in values)
        self._palette = palette
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette["bar_track"]))
        painter.drawRoundedRect(bounds, 4, 4)
        total = sum(self._values)
        if total <= 0:
            return
        colors = (
            QColor(self._palette["accent"]),
            QColor(self._palette["weekly_value"]),
            QColor(self._palette["muted"]),
        )
        left = bounds.left()
        remaining = bounds.width()
        for index, (value, color) in enumerate(zip(self._values, colors)):
            if value <= 0:
                continue
            width = remaining if index == len(self._values) - 1 else bounds.width() * value / total
            rect = QRectF(left, bounds.top(), width, bounds.height())
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 4, 4)
            left += width
            remaining = bounds.right() - left


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


def _format_currency(value: float) -> str:
    """Format the API-equivalent analytics estimate as USD."""
    return "${:,.2f}".format(value)


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


def _format_epoch_age(timestamp: Optional[float], now: Optional[datetime] = None) -> str:
    if timestamp is None:
        return "unknown"
    try:
        iso = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"
    return _format_relative_time(iso, now)


def _refresh_status_text(
    result: Optional[FetchResult], usage: Optional[dict],
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """Map a fetch outcome to a display-safe status line plus supporting detail."""
    if result is None:
        if usage:
            return "Current limits loaded", ""
        return "Current limits unavailable; local metadata shown", ""
    age = _format_epoch_age(result.last_success, now)
    if result.status == STATUS_FRESH:
        return "Current limits updated {}".format(age), result.detail
    if result.status == STATUS_STALE:
        return "Refresh failed · showing limits from {}".format(age), result.detail
    if result.status == STATUS_AUTH_REQUIRED:
        if result.usage is not None:
            return "Login required · showing limits from {}".format(age), result.detail
        return "Login required to refresh limits", result.detail
    if result.status == STATUS_API_ERROR:
        return "Refresh failed: {}".format(result.detail or "network error"), result.detail
    return "Current limits unavailable; local metadata shown", result.detail


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
    result: Optional[FetchResult] = None,
    token_usage_records: tuple[TokenUsageRecord, ...] = (),
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
    refresh_status, refresh_detail = _refresh_status_text(result, usage, now)
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
        refresh_status=refresh_status,
        refresh_detail=refresh_detail,
        token_usage_records=token_usage_records,
    )


class DashboardRefreshWorker(QThread):
    refreshed = pyqtSignal(object, object, object, object)

    def __init__(self, config: AppConfig):
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            local = read_local_codex_snapshot()
            token_usage_records = read_token_usage_records()
            usage = None
            result = None
            if self._config.codex_enabled:
                cache = CacheManager(self._config.cache_ttl_seconds)
                cached = cache.get_cached("codex")
                result = CodexProvider(cache).fetch_result()
                usage = result.usage
                if cached is None and result.status == STATUS_FRESH:
                    record_usage_snapshot(usage, local)
            self.refreshed.emit(usage, local, result, token_usage_records)
        except Exception as error:
            Logger.error("dashboard", "Refresh failed: {}".format(error))
            self.refreshed.emit(
                None,
                LocalCodexSnapshot(),
                FetchResult(STATUS_API_ERROR, None, None, "Unexpected error"),
                (),
            )


class DashboardResetWorker(QThread):
    consumed = pyqtSignal(object)

    def __init__(self, config: AppConfig, request_id: str, credit_id: Optional[str] = None):
        super().__init__()
        self._config = config
        self._request_id = request_id
        self._credit_id = credit_id

    def run(self) -> None:
        try:
            cache = CacheManager(self._config.cache_ttl_seconds)
            result = CodexProvider(cache).consume_reset_credit(self._request_id, self._credit_id)
        except Exception as error:
            Logger.error("dashboard", "Reset consume failed: {}".format(error))
            result = ResetConsumeResult("error", "Unexpected error")
        self.consumed.emit(result)


class DashboardWindow(QWidget):
    STATUS_TICK_MS = 30000

    def __init__(self, config: AppConfig, anchor: Optional[QPoint] = None):
        super().__init__()
        self._config = config
        self._anchor = anchor
        self._worker: Optional[DashboardRefreshWorker] = None
        self._reset_worker: Optional[DashboardResetWorker] = None
        self._closing = False
        self._refresh_after_reset = False
        self._refresh_scheduled = False
        self._last_state: Optional[DashboardState] = None
        self._last_usage: Optional[dict] = None
        self._last_success: Optional[float] = None
        self._rendered_result: Optional[FetchResult] = None
        self._period_days: Optional[int] = 7
        self._analytics_days = 30
        self._analytics_metric = "cost"
        self._init_ui()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(self.STATUS_TICK_MS)
        self._status_timer.timeout.connect(self._update_status_display)
        self._status_timer.start()
        # Defer initial refresh until event loop is running (queued after startup)
        QTimer.singleShot(0, self._schedule_initial_refresh)

    def _schedule_initial_refresh(self) -> None:
        if not self._refresh_scheduled:
            self._refresh_scheduled = True
            self.refresh()

    def _init_ui(self) -> None:
        self.setObjectName("codexDashboard")
        self.setWindowTitle("Codex Usage")
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
        title_block.addWidget(QLabel("Codex account", objectName="eyebrow"))
        title = QLabel("Codex Usage", objectName="title")
        title.setWordWrap(True)
        self._title = title
        title_block.addWidget(title)
        self._status = QLabel("Refreshing current limits…", objectName="subtitle")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
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
        tabs.addWidget(self._usage_tab)
        self._analytics_tab = QToolButton(objectName="analyticsTab")
        self._analytics_tab.setText("Analytics")
        self._analytics_tab.setCheckable(True)
        tabs.addWidget(self._analytics_tab)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_group.addButton(self._usage_tab)
        self._tab_group.addButton(self._analytics_tab)
        self._usage_tab.clicked.connect(lambda: self._select_page("usage"))
        self._analytics_tab.clicked.connect(lambda: self._select_page("analytics"))
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
        self._breakdown_card = self._make_breakdown_card()
        activity_title = QLabel("Local activity", objectName="sectionTitle")
        activity_card = QFrame(objectName="usageCard")
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(
            DashboardTheme.SPACE_2, DashboardTheme.SPACE_2,
            DashboardTheme.SPACE_2, DashboardTheme.SPACE_2,
        )
        activity_layout.setSpacing(DashboardTheme.SPACE_1)
        self._sessions_24h = QLabel("—", objectName="activityValue")
        self._sessions_7d = QLabel("—", objectName="activityValue")
        self._total_sessions = QLabel("—", objectName="activityValue")
        for label, value in (
            ("Last 24 hours", self._sessions_24h),
            ("Last 7 days", self._sessions_7d),
            ("All sessions", self._total_sessions),
        ):
            row = QHBoxLayout()
            row.setSpacing(DashboardTheme.SPACE_1)
            description = QLabel(label, objectName="muted")
            row.addWidget(description, 1)
            row.addWidget(value)
            activity_layout.addLayout(row)
        self._last_update = QLabel("Last local update: Not available", objectName="muted")
        self._last_update.setWordWrap(True)
        activity_layout.addWidget(self._last_update)
        self._observation = QLabel("", objectName="muted")
        self._observation.setWordWrap(True)
        activity_layout.addWidget(self._observation)

        usage_columns = QHBoxLayout()
        usage_columns.setSpacing(DashboardTheme.SPACE_2)
        breakdown_column = QVBoxLayout()
        breakdown_column.setSpacing(DashboardTheme.SPACE_1)
        breakdown_column.addWidget(breakdown_title)
        breakdown_column.addWidget(self._breakdown_card)
        activity_column = QVBoxLayout()
        activity_column.setSpacing(DashboardTheme.SPACE_1)
        activity_column.addWidget(activity_title)
        activity_column.addWidget(activity_card)
        usage_columns.addLayout(breakdown_column, 1)
        usage_columns.addLayout(activity_column, 1)
        usage.addLayout(usage_columns)
        usage.addStretch()

        self._analytics_page = QWidget(objectName="analyticsPage")
        self._analytics_page.setVisible(False)
        content.addWidget(self._analytics_page)
        analytics = QVBoxLayout(self._analytics_page)
        analytics.setContentsMargins(DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE)
        analytics.setSpacing(DashboardTheme.SPACE_3)

        controls = QVBoxLayout()
        controls.setSpacing(DashboardTheme.SPACE_1)
        range_controls = QHBoxLayout()
        range_controls.setSpacing(DashboardTheme.SPACE_1)
        self._analytics_period_group = QButtonGroup(self)
        self._analytics_period_group.setExclusive(True)
        for label, name, days in (
            ("Past 24h", "analytics24h", 1),
            ("7 days", "analytics7D", 7),
            ("30 days", "analytics30D", 30),
            ("90 days", "analytics90D", 90),
        ):
            button = self._make_period_button(label, name, checked=days == self._analytics_days)
            button.clicked.connect(lambda checked=False, value=days: self._select_analytics_period(value))
            self._analytics_period_group.addButton(button)
            range_controls.addWidget(button)
        range_controls.addStretch(1)
        controls.addLayout(range_controls)
        metric_controls = QHBoxLayout()
        metric_controls.setSpacing(DashboardTheme.SPACE_1)
        metric_controls.addStretch(1)
        self._analytics_metric_group = QButtonGroup(self)
        self._analytics_metric_group.setExclusive(True)
        for label, name, metric in (("Cost", "analyticsCost", "cost"),
                                    ("Tokens", "analyticsTokens", "tokens"),
                                    ("Limits", "analyticsLimits", "limits")):
            button = self._make_period_button(label, name, checked=metric == self._analytics_metric)
            if metric == "cost":
                button.setToolTip("Estimated API-equivalent cost from local token usage")
            button.clicked.connect(lambda checked=False, value=metric: self._select_analytics_metric(value))
            self._analytics_metric_group.addButton(button)
            metric_controls.addWidget(button)
        controls.addLayout(metric_controls)
        analytics.addLayout(controls)

        self._analytics_headline = QLabel("Usage trend", objectName="analyticsHeadline")
        analytics.addWidget(self._analytics_headline)
        self._analytics_legend = QHBoxLayout()
        self._analytics_legend.setSpacing(DashboardTheme.SPACE_3)
        self._analytics_legend_5h_swatch = QFrame(objectName="analyticsLegend5hSwatch")
        self._analytics_legend_5h_swatch.setFixedSize(14, 2)
        self._analytics_legend_5h = QLabel("5h", objectName="analyticsLegend5h")
        self._analytics_legend_7d_swatch = QFrame(objectName="analyticsLegend7dSwatch")
        self._analytics_legend_7d_swatch.setFixedSize(14, 2)
        self._analytics_legend_7d = QLabel("7d", objectName="analyticsLegend7d")
        self._analytics_legend_tokens = QLabel("Tokens", objectName="analyticsLegendTokens")
        self._analytics_legend.addWidget(self._analytics_legend_5h_swatch)
        self._analytics_legend.addWidget(self._analytics_legend_5h)
        self._analytics_legend.addWidget(self._analytics_legend_7d_swatch)
        self._analytics_legend.addWidget(self._analytics_legend_7d)
        self._analytics_legend.addWidget(self._analytics_legend_tokens)
        self._analytics_legend.addStretch()
        analytics.addLayout(self._analytics_legend)
        self._analytics_chart = UsageHistoryChart()
        analytics.addWidget(self._analytics_chart)
        self._analytics_summary = QLabel("Local snapshots are collected over time.", objectName="muted")
        self._analytics_summary.setWordWrap(True)
        analytics.addWidget(self._analytics_summary)
        analytics.addWidget(QLabel("Totals", objectName="sectionTitle"))
        totals = QGridLayout()
        totals.setSpacing(DashboardTheme.SPACE_2)
        self._analytics_total_value = self._make_metric(totals, 0, "LATEST VALUE")[1]
        self._analytics_total_samples = self._make_metric(totals, 1, "SNAPSHOTS")[1]
        self._analytics_total_sessions = self._make_metric(totals, 2, "SESSIONS")[1]
        self._analytics_total_labels = [
            totals.itemAt(column).widget().layout().itemAt(0).widget()
            for column in range(3)
        ]
        analytics.addLayout(totals)

        self._analytics_cost_details = QWidget(objectName="analyticsCostDetails")
        cost_details = QVBoxLayout(self._analytics_cost_details)
        cost_details.setContentsMargins(DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE, DashboardTheme.NONE)
        cost_details.setSpacing(DashboardTheme.SPACE_2)
        self._analytics_cost_totals_grid = QGridLayout()
        self._analytics_cost_totals_grid.setSpacing(DashboardTheme.SPACE_2)
        self._analytics_cost_totals: dict[str, QLabel] = {}
        for column, (key, label) in enumerate((
            ("processed", "PROCESSED TOKENS"),
            ("cached", "CACHED INPUT"),
            ("uncached", "UNCACHED INPUT"),
            ("output", "OUTPUT"),
            ("savings", "CACHE SAVINGS"),
        )):
            self._analytics_cost_totals[key] = self._make_metric(
                self._analytics_cost_totals_grid, column, label,
            )[1]
        cost_details.addLayout(self._analytics_cost_totals_grid)
        cost_details.addWidget(QLabel("Cost by type", objectName="sectionTitle"))
        self._analytics_cost_bar = CostCompositionBar()
        cost_details.addWidget(self._analytics_cost_bar)
        cost_legend = QHBoxLayout()
        cost_legend.setSpacing(DashboardTheme.SPACE_3)
        self._analytics_cost_legend = {}
        for key, label, color_name in (
            ("input", "Input", "accent"),
            ("cache", "Cache read", "weekly_value"),
            ("output", "Output", "muted"),
        ):
            swatch = QFrame(objectName="costLegend{}".format(key.title()))
            swatch.setFixedSize(8, 8)
            value_label = QLabel("{} —".format(label), objectName="muted")
            self._analytics_cost_legend[key] = (swatch, value_label, color_name, label)
            cost_legend.addWidget(swatch)
            cost_legend.addWidget(value_label)
        cost_legend.addStretch()
        cost_details.addLayout(cost_legend)
        cost_details.addWidget(QLabel("Breakdown by model", objectName="sectionTitle"))
        self._analytics_model_table = QTableWidget(0, 4, objectName="analyticsModelTable")
        self._analytics_model_table.setHorizontalHeaderLabels(("Model", "Cost", "Share", "Tokens"))
        self._analytics_model_table.verticalHeader().setVisible(False)
        self._analytics_model_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._analytics_model_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._analytics_model_table.setAlternatingRowColors(True)
        self._analytics_model_table.setShowGrid(False)
        self._analytics_model_table.setFixedHeight(230)
        header = self._analytics_model_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        cost_details.addWidget(self._analytics_model_table)
        self._analytics_cost_note = QLabel("", objectName="muted")
        self._analytics_cost_note.setWordWrap(True)
        cost_details.addWidget(self._analytics_cost_note)
        analytics.addWidget(self._analytics_cost_details)
        self._analytics_note = QLabel(
            "Cost is an API-equivalent estimate from local Codex token records, not your plan invoice.",
            objectName="muted",
        )
        self._analytics_note.setWordWrap(True)
        analytics.addWidget(self._analytics_note)
        analytics.addStretch()

        self._section_widgets: dict[str, list[QWidget]] = {
            "balance": [balance_title, self._five_balance[0], self._weekly_balance[0]],
            "credits": [self._credits_card],
            "usage_breakdown": [breakdown_title, self._breakdown_card],
            "history": [self._history_card],
            "local_activity": [
                activity_title, activity_card,
            ],
        }
        self._apply_section_visibility(self._config.section_visibility)

    def _select_page(self, page: str) -> None:
        analytics = page == "analytics"
        self._usage_page.setVisible(not analytics)
        self._analytics_page.setVisible(analytics)
        self.setFixedWidth(
            DashboardTheme.ANALYTICS_PANEL_WIDTH if analytics else DashboardTheme.PANEL_WIDTH,
        )
        if self.isVisible():
            self.place_near_anchor()
        self._update_analytics()

    def _select_analytics_period(self, days: int) -> None:
        self._analytics_days = days
        self._update_analytics()

    def _select_analytics_metric(self, metric: str) -> None:
        self._analytics_metric = metric
        self._update_analytics()

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
            QLabel#subtitle, QLabel#muted {{ color: {muted}; font-size: {font_body}px; }}
            QLabel#historyValue {{ color: {text}; font-size: {font_body}px; }}
            QLabel#activityValue {{ color: {text}; font-size: {font_body}px; font-weight: 700; }}
            QLabel#cardTitle {{ color: {accent}; font-size: {font_eyebrow}px; font-weight: 700; }}
            QLabel#fiveRemainingValue, QLabel#weeklyRemainingValue {{ color: {bar_fill}; font-family: '{font_family}'; font-size: {font_usage}px; font-weight: 700; }}
            QLabel#creditsValue {{ color: {text}; font-family: '{font_family}'; font-size: {font_metric}px; font-weight: 700; }}
            QLabel#metricValue {{ color: {text}; font-family: '{font_family}'; font-size: {font_metric}px; font-weight: 700; }}
            QLabel#sectionTitle {{ color: {text}; font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: {font_section}px; font-weight: 700; }}
            QLabel#modelName {{ color: {text}; font-size: {font_model}px; font-weight: 700; }}
            QLabel#modelDetail {{ color: {muted}; font-size: {font_body}px; }}
            QProgressBar#fiveRemainingBar, QProgressBar#weeklyRemainingBar {{ background: {bar_track}; border: 0; border-radius: {bar_radius}px; min-height: {bar_height}px; max-height: {bar_height}px; text-visible: false; }}
            QProgressBar#fiveRemainingBar::chunk, QProgressBar#weeklyRemainingBar::chunk {{ background: {bar_fill}; border-radius: {bar_radius}px; }}
            QToolButton#usageTab, QToolButton#analyticsTab {{ background: transparent; color: {muted}; border: 0; border-bottom: 2px solid transparent; padding: {space_1}px {space_2}px; font-family: '{font_family}'; font-size: {font_tab}px; font-weight: 700; }}
            QToolButton#usageTab:checked, QToolButton#analyticsTab:checked {{ color: {text}; border-bottom-color: {accent}; }}
            QToolButton#groupByDay, QToolButton#analytics24h, QToolButton#analytics7D, QToolButton#analytics30D, QToolButton#analytics90D, QToolButton#analyticsCost, QToolButton#analyticsTokens, QToolButton#analyticsLimits {{ background: {segment_surface}; color: {muted}; border: {border_width}px solid {border}; border-radius: {control_radius}px; padding: 0 {space_1}px; min-height: {period_height}px; font-size: {font_tab}px; font-weight: 700; }}
            QToolButton#groupByDay:hover, QToolButton#analytics24h:hover, QToolButton#analytics7D:hover, QToolButton#analytics30D:hover, QToolButton#analytics90D:hover, QToolButton#analyticsCost:hover, QToolButton#analyticsTokens:hover, QToolButton#analyticsLimits:hover {{ color: {text}; }}
            QToolButton#groupByDay:checked, QToolButton#analytics24h:checked, QToolButton#analytics7D:checked, QToolButton#analytics30D:checked, QToolButton#analytics90D:checked, QToolButton#analyticsCost:checked, QToolButton#analyticsTokens:checked, QToolButton#analyticsLimits:checked {{ background: {accent}; color: {control_text}; border-color: {accent}; }}
            QToolButton#analyticsCost:disabled {{ color: {accent_disabled}; }}
            QLabel#analyticsHeadline {{ color: {text}; font-family: '{font_family}'; font-size: {font_title}px; font-weight: 700; }}
            QLabel#analyticsLegend5h {{ color: {accent}; font-size: {font_body}px; font-weight: 600; }}
            QLabel#analyticsLegend7d {{ color: {weekly_value}; font-size: {font_body}px; font-weight: 600; }}
            QLabel#analyticsLegendTokens {{ color: {muted}; font-size: {font_body}px; font-weight: 600; }}
            QFrame#analyticsLegend5hSwatch {{ background: {accent}; border: 0; }}
            QFrame#analyticsLegend7dSwatch {{ background: {weekly_value}; border: 0; }}
            QWidget#analyticsChart {{ background: {surface}; border: {border_width}px solid {border}; border-radius: {card_radius}px; }}
            QFrame#costLegendInput {{ background: {accent}; border: 0; border-radius: 2px; }}
            QFrame#costLegendCache {{ background: {weekly_value}; border: 0; border-radius: 2px; }}
            QFrame#costLegendOutput {{ background: {muted}; border: 0; border-radius: 2px; }}
            QTableWidget#analyticsModelTable {{ background: {surface}; alternate-background-color: {segment_surface}; color: {text}; border: {border_width}px solid {border}; border-radius: {card_radius}px; gridline-color: {border}; selection-background-color: {primary_surface}; }}
            QTableWidget#analyticsModelTable QHeaderView::section {{ background: {segment_surface}; color: {muted}; border: 0; border-bottom: {border_width}px solid {border}; padding: {space_1}px {space_2}px; font-weight: 700; }}
            QTableWidget#analyticsModelTable::item {{ padding: {space_1}px {space_2}px; border-bottom: {border_width}px solid {border}; }}
            QScrollArea#dashboardScroll QWidget#usageSparkline {{ background: transparent; border: 0; }}
            QPushButton#refreshButton {{ background: {accent}; color: {control_text}; border: 0; border-radius: {control_radius}px; min-height: {button_height}px; padding: 0 {space_2}px; font-family: '{font_family}'; font-size: {font_tab}px; font-weight: 700; }}
            QPushButton#refreshButton:hover {{ background: {accent_hover}; }}
            QPushButton#refreshButton:disabled {{ background: {accent_disabled}; }}
            QToolButton#themeToggle {{ background: {surface}; color: {text}; border: {border_width}px solid {border}; border-radius: {control_radius}px; font-size: {font_section}px; min-width: {close_button_width}px; min-height: {button_height}px; }}
            QToolButton#themeToggle:hover {{ background: {primary_surface}; border-color: {primary_border}; }}
            QToolButton#settingsToggle {{ background: {surface}; color: {text}; border: {border_width}px solid {border}; border-radius: {control_radius}px; font-size: {font_section}px; min-width: {close_button_width}px; min-height: {button_height}px; }}
            QToolButton#settingsToggle:hover {{ background: {primary_surface}; border-color: {primary_border}; }}
            QToolButton#settingsToggle:checked {{ background: {accent}; color: {control_text}; border-color: {accent}; }}
             QCheckBox#balanceSectionCheck, QCheckBox#creditsSectionCheck, QCheckBox#usageBreakdownSectionCheck, QCheckBox#localActivitySectionCheck, QCheckBox#historySectionCheck {{ background: transparent; color: {text}; font-family: '{font_family}'; font-size: {font_body}px; }}
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
        if hasattr(self, "_analytics_chart") and hasattr(self, "_last_state"):
            self._update_analytics()
        if hasattr(self, "_history_chart") and hasattr(self, "_last_state"):
            self._update_history()
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
        heading = QHBoxLayout()
        heading.setSpacing(DashboardTheme.SPACE_2)
        heading.addWidget(QLabel("CREDITS REMAINING", objectName="cardTitle"))
        heading.addStretch()
        self._use_reset_button = QToolButton(objectName="useResetButton")
        self._use_reset_button.setText("Use reset")
        self._use_reset_button.setEnabled(False)
        self._use_reset_button.setToolTip("Use an available reset credit now, even with remaining balance")
        self._use_reset_button.setAccessibleName("Use reset credit")
        self._use_reset_button.clicked.connect(self._confirm_reset)
        heading.addWidget(self._use_reset_button)
        layout.addLayout(heading)
        self._credits_value = QLabel("Not available", objectName="creditsValue")
        layout.addWidget(self._credits_value)
        self._credits_expiry = QLabel("Expires in: Not available", objectName="muted")
        self._credits_expiry.setWordWrap(True)
        layout.addWidget(self._credits_expiry)
        return frame

    def _available_credit_id(self) -> Optional[str]:
        entries = (self._last_usage or {}).get("reset_credit_expiries")
        if not isinstance(entries, list) or not entries:
            return None
        first = entries[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
        return None

    @staticmethod
    def _has_available_reset_credit(usage: dict) -> bool:
        credits = usage.get("reset_credits")
        return (
            isinstance(credits, (int, float))
            and not isinstance(credits, bool)
            and credits > 0
        )

    def _render_reset_button_state(self) -> None:
        usage = self._last_usage or {}
        self._use_reset_button.setEnabled(
            self._has_available_reset_credit(usage)
            and not (self._reset_worker is not None and self._reset_worker.isRunning())
        )

    def _confirm_reset(self) -> None:
        if (
            not self._use_reset_button.isEnabled()
            or self._last_usage is None
            or not self._has_available_reset_credit(self._last_usage)
            or (self._reset_worker is not None and self._reset_worker.isRunning())
        ):
            return
        usage = self._last_usage
        weekly = usage.get("seven_day")
        expiry = _nearest_credit_expiry(usage) or "unknown"
        message = (
            "5-hour remaining: {}\n"
            "Weekly remaining: {}\n"
            "Reset credits available: {}\n"
            "Nearest credit expiry: {}"
        ).format(
            _remaining_balance(_format_usage(usage.get("five_hour")))[0],
            _remaining_balance(_format_usage(weekly))[0],
            usage.get("reset_credits"),
            expiry,
        )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Use reset credit")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText("Use one available reset credit now?")
        dialog.setInformativeText(message + "\n\nThe service may reject the request.")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        dialog.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        dialog.setStyleSheet(self.styleSheet())
        dialog.setObjectName("resetConfirmation")
        dialog.setMinimumWidth(440)
        dialog.adjustSize()
        dialog.show()
        dialog.adjustSize()
        dialog.move(self.screen().availableGeometry().center() - dialog.rect().center())
        answer = dialog.exec()
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._use_reset_button.setEnabled(False)
        self._status.setVisible(True)
        self._status.setText("Using reset credit…")
        self._reset_worker = DashboardResetWorker(
            self._config,
            str(uuid.uuid4()),
            self._available_credit_id(),
        )
        self._reset_worker.consumed.connect(self._reset_finished)
        self._reset_worker.finished.connect(self._reset_worker.deleteLater)
        self._reset_worker.start()

    def _reset_finished(self, result: ResetConsumeResult) -> None:
        self._reset_worker = None
        if result.status in ("reset", "alreadyRedeemed"):
            self._last_usage = None
            self._render_reset_button_state()
            self._status.setText("Reset request completed; refreshing limits…")
            if self._worker is not None and self._worker.isRunning():
                self._refresh_after_reset = True
            else:
                self.refresh()
            return
        message = "Reset credit not used: {}".format(result.detail or "The service rejected the request")
        self._status.setText(message)
        self._status.setToolTip(message)
        QMessageBox.warning(self, "Reset credit not used", message)
        self._render_reset_button_state()

    def _make_history_card(self) -> QFrame:
        frame = QFrame(objectName="usageCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3, DashboardTheme.SPACE_3)
        layout.setSpacing(DashboardTheme.SPACE_1)
        heading = QHBoxLayout()
        heading.setSpacing(DashboardTheme.SPACE_2)
        heading.addWidget(QLabel("HISTORY", objectName="cardTitle"))
        heading.addStretch()
        self._group_by_day = QToolButton(objectName="groupByDay")
        self._group_by_day.setText("Group by Day")
        self._group_by_day.setCheckable(True)
        self._group_by_day.setToolTip("Average usage snapshots per day")
        self._group_by_day.toggled.connect(lambda _: self._update_history())
        heading.addWidget(self._group_by_day)
        layout.addLayout(heading)
        self._history_chart = UsageSparkline()
        layout.addWidget(self._history_chart)
        self._trend = QLabel("Usage history: collecting snapshots…", objectName="historyValue")
        self._trend.setWordWrap(True)
        layout.addWidget(self._trend)
        self._trend_notice = QLabel(
            "Open Analytics for charts, time ranges, and metric filters.",
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
        self._status.setToolTip("")
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
        elif self._refresh_after_reset:
            self._refresh_after_reset = False
            self.refresh()

    def _render(
        self, usage: Optional[dict], local: LocalCodexSnapshot,
        result: Optional[FetchResult] = None,
        token_usage_records: tuple[TokenUsageRecord, ...] = (),
    ) -> None:
        if result is not None and usage is None and self._last_usage is not None:
            usage = self._last_usage
            if result.status in (STATUS_API_ERROR, STATUS_UNAVAILABLE):
                result = FetchResult(STATUS_STALE, usage, self._last_success, result.detail)
            elif result.status == STATUS_AUTH_REQUIRED:
                result = FetchResult(STATUS_AUTH_REQUIRED, usage, self._last_success, result.detail)
        if usage is not None:
            self._last_usage = usage
            if result is not None and result.last_success is not None:
                self._last_success = result.last_success
        self._rendered_result = result
        state = build_dashboard_state(
            usage, local, history=tuple(read_usage_snapshots()), result=result,
            token_usage_records=token_usage_records,
        )
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
        self._render_reset_button_state()
        self._plan.setText("Plan: {}".format(state.plan))
        self._sessions_24h.setText(state.sessions_24h)
        self._sessions_7d.setText(state.sessions_7d)
        self._total_sessions.setText(state.total_sessions)
        self._tokens.setText("Tokens used (local aggregate): {}".format(state.tokens))
        self._last_update.setText("Last local update: {}".format(state.last_update))
        self._observation.setText(state.local_observation)
        self._update_history()
        self._update_analytics()
        self._status.setText(state.refresh_status)
        self._status.setToolTip(state.refresh_detail or state.refresh_status)
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

    def _update_status_display(self) -> None:
        """Refresh only the subtitle from the last fetch result without a network call."""
        if self._worker is not None and self._worker.isRunning():
            return
        if self._rendered_result is None and self._last_usage is None:
            return
        status, detail = _refresh_status_text(self._rendered_result, self._last_usage)
        self._status.setText(status)
        self._status.setToolTip(detail or status)

    def _update_analytics(self) -> None:
        state = self._last_state
        palette = DashboardTheme.palette(self._config.dark_mode)
        if state is None:
            self._analytics_chart.set_data("Usage trend", [], palette)
            self._analytics_headline.setText("Loading local usage…")
            return
        cost_data = calculate_cost_analytics(
            state.token_usage_records, self._analytics_days,
        )
        metric = self._analytics_metric
        self._analytics_cost_details.setVisible(metric in ("cost", "tokens"))
        self._analytics_legend_5h.setVisible(metric == "limits")
        self._analytics_legend_7d.setVisible(metric == "limits")
        self._analytics_legend_5h_swatch.setVisible(metric == "limits")
        self._analytics_legend_7d_swatch.setVisible(metric == "limits")
        self._analytics_legend_tokens.setVisible(metric == "tokens")

        if metric == "cost":
            has_priced_cost = any(row.cost is not None for row in cost_data.model_rows)
            cost_title = _format_currency(cost_data.total_cost) if has_priced_cost else "Cost unavailable"
            self._analytics_headline.setText(cost_title)
            self._analytics_chart.set_data(
                "Daily estimated cost",
                [("Cost", list(cost_data.daily_costs), "accent")] if has_priced_cost else [],
                palette, percent=False, x_labels=cost_data.chart_labels, currency=True,
            )
            self._analytics_total_labels[0].setText("ESTIMATED COST")
            self._analytics_total_labels[1].setText("RESPONSES")
            self._analytics_total_labels[2].setText("SESSIONS")
            self._analytics_total_value.setText(cost_title if has_priced_cost else "—")
            self._analytics_total_samples.setText(str(cost_data.responses))
            self._analytics_total_sessions.setText(str(cost_data.sessions))
            self._analytics_summary.setText(
                "API-equivalent estimate · USD · {} local responses in this period".format(cost_data.responses)
                if has_priced_cost else "No priced local Codex responses were found in this period."
            )
            self._set_cost_detail_values(cost_data, as_tokens=True)
            self._set_model_table(cost_data, share_by_tokens=False)
        elif metric == "tokens":
            self._analytics_headline.setText(_format_tokens(cost_data.processed_tokens))
            self._analytics_chart.set_data(
                "Daily processed tokens",
                [("Tokens", list(cost_data.daily_tokens), "accent")] if cost_data.responses else [],
                palette, percent=False, x_labels=cost_data.chart_labels,
            )
            self._analytics_total_labels[0].setText("PROCESSED TOKENS")
            self._analytics_total_labels[1].setText("RESPONSES")
            self._analytics_total_labels[2].setText("SESSIONS")
            self._analytics_total_value.setText(_format_tokens(cost_data.processed_tokens))
            self._analytics_total_samples.setText(str(cost_data.responses))
            self._analytics_total_sessions.setText(str(cost_data.sessions))
            self._analytics_summary.setText(
                "Locally recorded input and output tokens · cached input is included in processed totals"
                if cost_data.responses else "No local token records were found in this period."
            )
            self._set_cost_detail_values(cost_data, as_tokens=False)
            self._set_model_table(cost_data, share_by_tokens=True)
        else:
            filtered = _filter_history_period(state.usage_history, self._analytics_days)
            latest = filtered[-1] if filtered else None
            self._analytics_legend_tokens.setVisible(False)
            self._analytics_headline.setText("Codex limits")
            self._analytics_chart.set_data(
                "Usage by limit window",
                [("5h", [float(row.five_hour) for row in filtered if row.five_hour is not None], "accent"),
                 ("7d", [float(row.seven_day) for row in filtered if row.seven_day is not None], "weekly_value")],
                palette, percent=True,
                x_labels=(UsageHistoryChart.date_label(filtered[0].timestamp),
                          UsageHistoryChart.date_label(filtered[-1].timestamp))
                if filtered else ("Earlier", "Latest"),
            )
            self._analytics_total_labels[0].setText("LATEST VALUE")
            self._analytics_total_labels[1].setText("SNAPSHOTS")
            self._analytics_total_labels[2].setText("SESSIONS")
            self._analytics_total_value.setText(
                "{:.0f}% / {:.0f}%".format(latest.five_hour, latest.seven_day)
                if latest and latest.five_hour is not None and latest.seven_day is not None else "—"
            )
            self._analytics_total_samples.setText(str(len(filtered)))
            self._analytics_total_sessions.setText(str(latest.session_count if latest else 0))
            self._analytics_summary.setText(
                "Latest local limit snapshots · separate 5-hour and weekly windows"
                if filtered else "Limit snapshots appear here after the next successful refresh."
            )

    def _set_cost_detail_values(self, data: CostAnalytics, as_tokens: bool) -> None:
        if as_tokens:
            self._analytics_cost_totals["processed"].setText(_format_tokens(data.processed_tokens))
            self._analytics_cost_totals["cached"].setText(_format_tokens(data.cached_input_tokens))
            self._analytics_cost_totals["uncached"].setText(_format_tokens(data.uncached_input_tokens))
            self._analytics_cost_totals["output"].setText(_format_tokens(data.output_tokens))
            self._analytics_cost_totals["savings"].setText(_format_currency(data.cache_savings))
            legend_values = {
                "input": _format_currency(data.input_cost),
                "cache": _format_currency(data.cached_input_cost),
                "output": _format_currency(data.output_cost),
            }
            bar_values = (data.input_cost, data.cached_input_cost, data.output_cost)
        else:
            self._analytics_cost_totals["processed"].setText(_format_tokens(data.processed_tokens))
            self._analytics_cost_totals["cached"].setText(_format_tokens(data.cached_input_tokens))
            self._analytics_cost_totals["uncached"].setText(_format_tokens(data.uncached_input_tokens))
            self._analytics_cost_totals["output"].setText(_format_tokens(data.output_tokens))
            self._analytics_cost_totals["savings"].setText(_format_currency(data.cache_savings))
            legend_values = {
                "input": _format_tokens(data.uncached_input_tokens),
                "cache": _format_tokens(data.cached_input_tokens),
                "output": _format_tokens(data.output_tokens),
            }
            bar_values = (
                float(data.uncached_input_tokens),
                float(data.cached_input_tokens),
                float(data.output_tokens),
            )
        self._analytics_cost_bar.set_data(bar_values, DashboardTheme.palette(self._config.dark_mode))
        for key, (swatch, label, color_name, title) in self._analytics_cost_legend.items():
            label.setText("{} {}".format(title, legend_values[key]))
            swatch.setStyleSheet("background: {}; border-radius: 2px;".format(
                DashboardTheme.palette(self._config.dark_mode)[color_name],
            ))
        if data.unpriced_models:
            self._analytics_cost_note.setText(
                "Unpriced models excluded from cost: {}. Their token counts remain included.".format(
                    ", ".join(data.unpriced_models),
                )
            )
        else:
            self._analytics_cost_note.setText(
                "Standard USD rates as of 2026-11-06 · Codex cache writes are not charged · speed mode is not recorded locally."
            )

    def _set_model_table(self, data: CostAnalytics, share_by_tokens: bool) -> None:
        self._analytics_model_table.setRowCount(len(data.model_rows))
        for row_index, row in enumerate(data.model_rows):
            values = (
                row.model,
                _format_currency(row.cost) if row.cost is not None else "Unpriced",
                "{:.1f}%".format(row.token_share if share_by_tokens else row.share)
                if (row.token_share if share_by_tokens else row.share) is not None else "—",
                _format_tokens(row.processed_tokens),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._analytics_model_table.setItem(row_index, column, item)
        if not data.model_rows:
            self._analytics_model_table.setToolTip("No local model usage was recorded in this time range.")
        else:
            self._analytics_model_table.setToolTip("")

    def _update_history(self) -> None:
        """Refresh the History card from local snapshots using the selected period."""
        state = self._last_state
        if state is None:
            return
        history = state.usage_history
        palette = DashboardTheme.palette(self._config.dark_mode)
        if not history:
            self._history_chart.set_data([], palette)
            self._trend.setText("Usage history: collecting snapshots…")
            return
        filtered = _filter_history_period(history, self._period_days)
        if not filtered:
            self._history_chart.set_data([], palette)
            self._trend.setText("Usage history: no snapshots in selected period")
            return
        values = [entry.five_hour for entry in filtered if entry.five_hour is not None]
        self._history_chart.set_data(values, palette)
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
        if values:
            self._trend.setText("5-hour usage · {} snapshots".format(len(values)))
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
        analytics = self._analytics_page.isVisible()
        preferred_height = (
            DashboardTheme.ANALYTICS_PREFERRED_HEIGHT if analytics
            else DashboardTheme.PREFERRED_HEIGHT
        )
        panel_width = (
            DashboardTheme.ANALYTICS_PANEL_WIDTH if analytics
            else DashboardTheme.PANEL_WIDTH
        )
        panel_height = min(preferred_height, maximum_height)
        self.resize(panel_width, panel_height)
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
