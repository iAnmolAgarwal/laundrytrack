"""Laundry-day arithmetic.

A hostel accepts laundry only on fixed weekdays (Tuesday and Friday by default,
but each student sets their own). Clothes handed in on one laundry day are
ready on the next one: Tuesday -> Friday, Friday -> the following Tuesday.
After that they can be collected on any day the student chooses. The counter
hours are the same for everyone.
"""
from datetime import date, datetime, time, timedelta

from django.conf import settings

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def default_weekdays():
    return sorted(set(getattr(settings, "LAUNDRY_WEEKDAYS", [1, 4])))


def parse_weekdays(value):
    """'1,4' or [1, 4] -> sorted unique weekday numbers (Mon=0); falls back to the default."""
    if isinstance(value, str):
        value = value.split(",")
    days = set()
    for part in value or []:
        try:
            day = int(part)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            days.add(day)
    return sorted(days) or default_weekdays()


def weekdays_label(days):
    names = [WEEKDAY_NAMES[d] for d in parse_weekdays(days)]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def next_pickup_date(drop_date: date, weekdays=None) -> date:
    """Earliest day the clothes are ready: the first laundry day strictly after the drop date."""
    days = parse_weekdays(weekdays) if weekdays is not None else default_weekdays()
    for offset in range(1, 8):
        candidate = drop_date + timedelta(days=offset)
        if candidate.weekday() in days:
            return candidate
    return drop_date + timedelta(days=3)


def _clock(setting, fallback):
    try:
        hour, minute = str(getattr(settings, setting, fallback)).split(":")
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        hour, minute = fallback.split(":")
        return time(int(hour), int(minute))


def counter_hours():
    """(opens, closes) as datetime.time, the same for every student."""
    return _clock("LAUNDRY_OPEN_TIME", "16:00"), _clock("LAUNDRY_CLOSE_TIME", "18:30")


def _fmt(t: time) -> str:
    return datetime.combine(date(2000, 1, 1), t).strftime("%I:%M %p").lstrip("0")


def counter_hours_label() -> str:
    opens, closes = counter_hours()
    return f"{_fmt(opens)} to {_fmt(closes)}"
