from datetime import UTC, datetime, timedelta

from psacc.settings import TZ
from psacc.tariff.model import Season, Slot, Tariff
from psacc.tariff.presets import EDA_TRI_SEMANAL
from psacc.tariff.schedule import (
    Event,
    desired_mode,
    next_vazio_start,
    period_at,
    planned_events,
    vazio_windows,
)

OFFSET = 10


def local(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def pairs(events):
    return [(e.action, e.at.strftime("%a %d/%m %H:%M")) for e in events]


def test_winter_weekday_events():
    events = planned_events(EDA_TRI_SEMANAL, OFFSET, local(2026, 11, 12), days=2)
    assert pairs(events[:4]) == [
        ("charge", "Thu 12/11 00:10"),
        ("defer", "Thu 12/11 06:50"),
        ("charge", "Fri 13/11 00:10"),
        ("defer", "Fri 13/11 06:50"),
    ]
    assert all(e.at.tzinfo is TZ for e in events)


def test_weekend_merge():
    events = planned_events(EDA_TRI_SEMANAL, OFFSET, local(2026, 11, 14, 20), days=2)
    assert pairs(events[:2]) == [("charge", "Sat 14/11 23:10"), ("defer", "Mon 16/11 06:50")]
    assert not any(e.at.hour == 0 and e.at.minute == 0 for e in events)
    windows = vazio_windows(EDA_TRI_SEMANAL, local(2026, 11, 15, 12), local(2026, 11, 15, 13))
    assert windows == [(local(2026, 11, 14, 23), local(2026, 11, 16, 7))]


def test_saturday_multiple_windows():
    events = planned_events(EDA_TRI_SEMANAL, OFFSET, local(2026, 11, 13, 12), days=2)
    saturday = [e for e in events if e.at.date() == local(2026, 11, 14).date()]
    assert pairs(saturday) == [
        ("charge", "Sat 14/11 00:10"),
        ("defer", "Sat 14/11 11:20"),
        ("charge", "Sat 14/11 13:40"),
        ("defer", "Sat 14/11 17:50"),
        ("charge", "Sat 14/11 23:10"),
    ]


def test_season_switch():
    windows = vazio_windows(EDA_TRI_SEMANAL, local(2026, 5, 31, 12), local(2026, 5, 31, 13))
    assert windows == [(local(2026, 5, 30, 23), local(2026, 6, 1, 7))]
    assert period_at(EDA_TRI_SEMANAL, local(2026, 6, 1, 11)) == "ponta"
    assert period_at(EDA_TRI_SEMANAL, local(2026, 5, 29, 11)) == "cheias"


def test_dst_weekend():
    events = planned_events(EDA_TRI_SEMANAL, OFFSET, local(2026, 3, 28, 20), days=2)
    assert pairs(events[:2]) == [("charge", "Sat 28/03 23:10"), ("defer", "Mon 30/03 06:50")]
    (s, e), *_ = vazio_windows(EDA_TRI_SEMANAL, local(2026, 3, 29, 12), local(2026, 3, 29, 13))
    assert (s, e) == (local(2026, 3, 28, 23), local(2026, 3, 30, 7))
    # Sat 23:00 WET-1 = Sun 00:00 UTC; Mon 07:00 UTC+0 = Mon 07:00 UTC -> 31 h
    assert e.astimezone(UTC) - s.astimezone(UTC) == timedelta(hours=31)


def test_dst_end_weekend_does_not_crash():
    windows = vazio_windows(EDA_TRI_SEMANAL, local(2026, 10, 25, 0, 30), local(2026, 10, 25, 2))
    assert windows == [(local(2026, 10, 24, 23), local(2026, 10, 26, 7))]
    assert desired_mode(EDA_TRI_SEMANAL, OFFSET, datetime(2026, 10, 25, 0, 30, tzinfo=UTC)) == "charge"


def _short_vazio_tariff():
    day = [
        Slot(start=0, end=60, period="cheias"),
        Slot(start=60, end=75, period="vazio"),
        Slot(start=75, end=1440, period="cheias"),
    ]
    season = Season(name="s", months=list(range(1, 13)), days={"weekday": day, "saturday": day, "sunday": day})
    return Tariff(name="short", seasons=[season])


def test_short_window_skipped():
    tariff = _short_vazio_tariff()
    start = local(2026, 11, 12)
    assert vazio_windows(tariff, start, start + timedelta(days=1))
    assert planned_events(tariff, OFFSET, start) == []
    for minute in range(0, 24 * 60, 5):
        assert desired_mode(tariff, OFFSET, start + timedelta(minutes=minute)) == "defer"


def test_desired_mode_samples():
    samples = {(0, 5): "defer", (0, 10): "charge", (6, 49): "charge", (6, 50): "defer", (19, 0): "defer"}
    for (h, m), mode in samples.items():
        assert desired_mode(EDA_TRI_SEMANAL, OFFSET, local(2026, 11, 12, h, m)) == mode, (h, m)


def test_next_vazio_start():
    assert next_vazio_start(EDA_TRI_SEMANAL, OFFSET, local(2026, 11, 12, 7)) == local(2026, 11, 13, 0, 10)
    # strictly after
    assert next_vazio_start(EDA_TRI_SEMANAL, OFFSET, local(2026, 11, 13, 0, 10)) == local(2026, 11, 14, 0, 10)


def test_event_is_dataclass():
    assert Event(at=local(2026, 11, 12), action="charge").action == "charge"
