"""Pure rule tests. No database is needed for anything in this file."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.rules import (
    OpeningWindow,
    PeakWindow,
    hold_is_live,
    local_wall_time_exists,
    price_minor,
    refund_minor,
    validate_interval,
)
from app.errors import ApiError

IST = ZoneInfo("Asia/Kolkata")
WINDOW = OpeningWindow(opening_hour=6, closing_hour=22)
PEAKS = [PeakWindow(day_of_week=d, start_hour=17, end_hour=21) for d in range(1, 8)]
TUESDAY = datetime(2026, 11, 10, tzinfo=IST)  # a Tuesday


def at(hour: int, day: datetime = TUESDAY) -> datetime:
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def test_one_hour_inside_opening_hours_is_valid() -> None:
    assert validate_interval(at(10), at(11), IST, WINDOW) == 1


def test_longest_allowed_booking_is_four_hours() -> None:
    assert validate_interval(at(6), at(10), IST, WINDOW) == 4


@pytest.mark.parametrize(
    "start,end",
    [
        (at(10), at(10)),  # zero length
        (at(10), at(10) + timedelta(minutes=30)),  # not whole hours
        (at(6), at(11)),  # five hours
        (at(10).replace(minute=30), at(11).replace(minute=30)),  # not on the hour
        (at(21), at(23)),  # runs past closing
    ],
)
def test_invalid_intervals_are_rejected(start: datetime, end: datetime) -> None:
    with pytest.raises(ApiError) as caught:
        validate_interval(start, end, IST, WINDOW)
    assert caught.value.code in {"invalid_interval", "outside_opening_hours"}


def test_booking_before_opening_is_outside_hours() -> None:
    with pytest.raises(ApiError) as caught:
        validate_interval(at(5), at(6), IST, WINDOW)
    assert caught.value.code == "outside_opening_hours"


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ApiError) as caught:
        validate_interval(datetime(2026, 11, 10, 10), datetime(2026, 11, 10, 11), IST, WINDOW)
    assert caught.value.code == "invalid_interval"


def test_tuesday_evening_is_two_peak_hours() -> None:
    assert price_minor(at(18), 2, IST, PEAKS, 80_000, 50_000) == 160_000


def test_hour_before_peak_is_off_peak_and_peak_hour_is_charged_peak() -> None:
    assert price_minor(at(16), 2, IST, PEAKS, 80_000, 50_000) == 130_000


def test_seventeen_oclock_belongs_to_the_peak_window() -> None:
    assert price_minor(at(17), 1, IST, PEAKS, 80_000, 50_000) == 80_000


def test_morning_without_peak_is_all_off_peak() -> None:
    assert price_minor(at(9), 4, IST, PEAKS, 80_000, 50_000) == 200_000


def test_exactly_twenty_four_hours_notice_is_a_full_refund() -> None:
    now = at(10)
    assert refund_minor(now, now + timedelta(hours=24), 160_000) == 160_000


def test_one_minute_short_of_twenty_four_hours_refunds_nothing() -> None:
    now = at(10)
    assert refund_minor(now, now + timedelta(hours=24) - timedelta(minutes=1), 160_000) == 0


def test_hold_is_live_only_before_its_expiry() -> None:
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert hold_is_live(now, now + timedelta(seconds=1))
    assert not hold_is_live(now, now)
    assert not hold_is_live(now, None)


def test_a_daylight_saving_gap_is_not_a_real_local_time() -> None:
    new_york = ZoneInfo("America/New_York")
    gap = datetime(2026, 3, 8, 2, 30, tzinfo=new_york)  # clocks jump from 02:00 to 03:00
    normal = datetime(2026, 3, 8, 4, 30, tzinfo=new_york)
    assert not local_wall_time_exists(gap, new_york)
    assert local_wall_time_exists(normal, new_york)
