"""Tariff schedule: vazio windows, planned charge/defer events and the desired mode at a given instant.

All functions are pure. Inputs must be timezone-aware; outputs are aware datetimes in ``settings.TZ``.
Window arithmetic and comparisons are done in UTC so DST gaps/overlaps never confuse wall-clock maths.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from ..settings import TZ
from .model import MINUTES_PER_DAY, DayType, PeriodType, Tariff

Action = Literal["charge", "defer"]

# Days walked beyond the requested range so a window in progress at either edge keeps its true bounds
# (covers any vazio run shorter than this, e.g. Sat 23:00 → Mon 07:00).
_MARGIN_DAYS = 8


@dataclass(frozen=True)
class Event:
    at: datetime
    action: Action


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return dt


def _day_type(day: date) -> DayType:
    wd = day.weekday()
    return "saturday" if wd == 5 else "sunday" if wd == 6 else "weekday"


def _wall(day: date, minute: int) -> datetime:
    """Local wall-clock instant as UTC (minute 1440 = next day 00:00). Non-existent times resolve via fold=0."""
    if minute == MINUTES_PER_DAY:
        day, minute = day + timedelta(days=1), 0
    return datetime.combine(day, time(minute // 60, minute % 60)).replace(tzinfo=TZ).astimezone(UTC)


def period_at(t: Tariff, at: datetime) -> PeriodType:
    local = _aware(at).astimezone(TZ)
    minute = local.hour * 60 + local.minute
    for slot in t.season_for(local.month).days[_day_type(local.date())]:
        if slot.start <= minute < slot.end:
            return slot.period
    raise ValueError(f"no slot covers {local.isoformat()}")  # unreachable for a validated Tariff


def _windows_utc(t: Tariff, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    start_utc, end_utc = _aware(start).astimezone(UTC), _aware(end).astimezone(UTC)
    day = start.astimezone(TZ).date() - timedelta(days=_MARGIN_DAYS)
    last = end.astimezone(TZ).date() + timedelta(days=_MARGIN_DAYS)
    merged: list[tuple[datetime, datetime]] = []
    while day <= last:
        for slot in t.season_for(day.month).days[_day_type(day)]:
            if slot.period != "vazio":
                continue
            s, e = _wall(day, slot.start), _wall(day, slot.end)
            if merged and merged[-1][1] >= s:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        day += timedelta(days=1)
    return [(s, e) for s, e in merged if s <= end_utc and e > start_utc]


def vazio_windows(t: Tariff, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Merged vazio windows overlapping ``[start, end]``, with their true (unclipped) bounds."""
    return [(s.astimezone(TZ), e.astimezone(TZ)) for s, e in _windows_utc(t, start, end)]


def planned_events(t: Tariff, offset_min: int, start: datetime, days: int = 7) -> list[Event]:
    offset = timedelta(minutes=offset_min)
    start_utc = _aware(start).astimezone(UTC)
    events: list[Event] = []
    for s, e in _windows_utc(t, start, start + timedelta(days=days)):
        if e - s <= 2 * offset:
            continue
        events.append(Event((s + offset).astimezone(TZ), "charge"))
        events.append(Event((e - offset).astimezone(TZ), "defer"))
    return sorted((ev for ev in events if ev.at >= start_utc), key=lambda ev: ev.at.astimezone(UTC))


def desired_mode(t: Tariff, offset_min: int, at: datetime) -> Action:
    offset = timedelta(minutes=offset_min)
    at_utc = _aware(at).astimezone(UTC)
    in_window = any(s + offset <= at_utc < e - offset for s, e in _windows_utc(t, at, at))
    return "charge" if in_window else "defer"


def next_vazio_start(t: Tariff, offset_min: int, at: datetime) -> datetime:
    """Next charge instant (window start + offset) strictly after ``at``."""
    at_utc = _aware(at).astimezone(UTC)
    for ev in planned_events(t, offset_min, at, days=_MARGIN_DAYS):
        if ev.action == "charge" and ev.at > at_utc:
            return ev.at
    raise ValueError("no usable vazio window in the next days")
