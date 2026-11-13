"""Pure rules: interval validation, pricing, refunds and the live-hold predicate.

Every function here takes plain values and returns a decision. The database
only stores the results. Keeping these pure is what lets the edge cases
(boundaries, daylight-saving gaps) be tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.errors import ApiError

HOUR = timedelta(hours=1)
MAX_HOURS = 4
FULL_REFUND_NOTICE = timedelta(hours=24)
HOLD_TTL_DEFAULT_SECONDS = 300


@dataclass(frozen=True)
class OpeningWindow:
    opening_hour: int  # local hour, inclusive
    closing_hour: int  # local hour, exclusive


@dataclass(frozen=True)
class PeakWindow:
    day_of_week: int  # ISO: 1 = Monday ... 7 = Sunday
    start_hour: int  # local hour, inclusive
    end_hour: int  # local hour, exclusive


def _invalid(detail: str) -> ApiError:
    return ApiError("invalid_interval", 422, detail)


def validate_interval(starts_at: datetime, ends_at: datetime, tz: ZoneInfo, window: OpeningWindow) -> int:
    """Check a booking interval and return its length in whole hours.

    A booking is 1 to 4 whole hours, starts on a local hour, stays on one local
    date, and sits inside the venue's opening window.
    """
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise _invalid("timestamps must include a UTC offset")
    seconds = (ends_at - starts_at).total_seconds()
    if seconds <= 0 or seconds % 3600 != 0:
        raise _invalid("the interval must be a whole number of hours")
    hours = int(seconds // 3600)
    if not 1 <= hours <= MAX_HOURS:
        raise _invalid(f"a booking lasts 1 to {MAX_HOURS} hours")
    local_start = starts_at.astimezone(tz)
    local_end = ends_at.astimezone(tz)
    if local_start.minute or local_start.second or local_start.microsecond:
        raise _invalid("bookings start on the hour")
    if local_start.date() != local_end.date():
        raise _invalid("a booking stays within one local day")
    if local_start.hour < window.opening_hour or local_start.hour + hours > window.closing_hour:
        raise ApiError("outside_opening_hours", 422, "the court is closed at that time")
    return hours


def local_wall_time_exists(local: datetime, tz: ZoneInfo) -> bool:
    """False when a wall-clock time falls in a daylight-saving gap that does not exist."""
    naive = local.replace(tzinfo=None)
    round_trip = local.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    return round_trip == naive


def is_peak(local: datetime, windows: list[PeakWindow]) -> bool:
    iso_day = local.isoweekday()
    return any(w.day_of_week == iso_day and w.start_hour <= local.hour < w.end_hour for w in windows)


def price_minor(
    starts_at: datetime,
    hours: int,
    tz: ZoneInfo,
    windows: list[PeakWindow],
    peak_rate_minor: int,
    offpeak_rate_minor: int,
) -> int:
    """Price a booking hour by hour in the venue's local time. Amounts are paise."""
    total = 0
    for i in range(hours):
        local = (starts_at + i * HOUR).astimezone(tz)
        total += peak_rate_minor if is_peak(local, windows) else offpeak_rate_minor
    return total


def refund_minor(now: datetime, starts_at: datetime, price: int) -> int:
    """Full price when cancelled with at least 24 hours' notice, otherwise nothing."""
    return price if starts_at - now >= FULL_REFUND_NOTICE else 0


def hold_is_live(now: datetime, hold_expires_at: datetime | None) -> bool:
    return hold_expires_at is not None and hold_expires_at > now


def local_occurrence_start(first_local: datetime, week_offset: int) -> datetime:
    """Wall-clock start of the n-th weekly occurrence, in the same local time."""
    return first_local + timedelta(days=7 * week_offset)


def local_midnight(day: datetime, tz: ZoneInfo) -> datetime:
    return datetime.combine(day.date(), time(0), tzinfo=tz)
