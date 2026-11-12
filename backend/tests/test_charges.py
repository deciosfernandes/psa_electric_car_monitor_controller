import dataclasses
from datetime import UTC, datetime, timedelta

from psacc.history.charges import ChargeDetector, session_kwh
from psacc.places import Place
from tests.fakes import default_snapshot

T0 = datetime(2026, 11, 12, 1, 0, tzinfo=UTC)
HOME = Place(id=1, name="Casa", kind="home", lat=1.0, lon=1.0)


def snap(minutes, soc, charging=True, plugged=True, **kw):
    t = T0 + timedelta(minutes=minutes)
    return dataclasses.replace(
        default_snapshot(), at=t, energy_at=t, soc=soc, charging=charging, plugged=plugged, charge_rate_kmh=30, **kw
    )


def kinds(events):
    return [e.kind for e in events]


def test_session_kwh():
    assert session_kwh(20, 70, 46) == 23.0
    assert session_kwh(70, 60, 46) == 0.0


def test_session_start_end():
    d = ChargeDetector()
    assert d.feed(snap(0, 50, charging=False), "home", HOME) == []
    ev = d.feed(snap(10, 50), "home", HOME)
    assert kinds(ev) == ["start", "sample"]
    s = ev[0].session
    assert (s.soc_start, s.place_kind, s.place_name, s.partial) == (50, "home", "Casa", False)
    assert s.started_at == T0 + timedelta(minutes=10)
    assert kinds(d.feed(snap(20, 55), "home", HOME)) == ["sample"]
    assert kinds(d.feed(snap(30, 56, charging=False), "home", HOME)) == ["sample"]  # one stop is not enough
    ev = d.feed(snap(40, 56, charging=False), "home", HOME)
    assert kinds(ev) == ["sample", "end"]
    ev = ev[1:]
    assert ev[0].session.soc_end == 56 and ev[0].session.ended_at == T0 + timedelta(minutes=40)
    assert d.feed(snap(50, 56, charging=False), "home", HOME) == []


def test_charging_resuming_resets_stop_count():
    d = ChargeDetector()
    d.feed(snap(0, 50), "home", HOME)
    d.feed(snap(10, 51, charging=False), "home", HOME)
    assert kinds(d.feed(snap(20, 52), "home", HOME)) == ["sample"]
    assert kinds(d.feed(snap(30, 52, charging=False), "home", HOME)) == ["sample"]


def test_session_started_mid_charge_is_partial():
    d = ChargeDetector()
    ev = d.feed(snap(0, 40), "work", None)
    assert kinds(ev) == ["start", "sample"]
    assert ev[0].session.partial is True and ev[0].session.place_kind == "work"
    d2 = ChargeDetector()
    d2.feed(snap(0, 40, charging=False), "home", HOME)
    assert d2.feed(snap(10, 40), "home", HOME)[0].session.partial is False


def test_soc_rise_while_plugged_starts_session():
    d = ChargeDetector()
    d.feed(snap(0, 40, charging=None), "home", HOME)
    ev = d.feed(snap(10, 42, charging=None), "home", HOME)
    assert kinds(ev) == ["start", "sample", "sample"]
    assert ev[0].session.soc_start == 40 and ev[0].session.started_at == T0


def test_unplug_ends_session():
    d = ChargeDetector()
    d.feed(snap(0, 50), "home", HOME)
    d.feed(snap(10, 52), "home", HOME)
    ev = d.feed(snap(20, 52, charging=False, plugged=False), "home", HOME)
    assert kinds(ev) == ["sample", "end"] and ev[1].session.soc_end == 52


def test_stale_energy_ignored():
    d = ChargeDetector()
    d.feed(snap(0, 50), "home", HOME)
    stale = dataclasses.replace(snap(10, 50, charging=False), energy_at=T0)
    assert d.feed(stale, "home", HOME) == []
    assert d.feed(dataclasses.replace(stale, at=T0 + timedelta(minutes=20)), "home", HOME) == []
    assert kinds(d.feed(snap(30, 53), "home", HOME)) == ["sample"]  # no pending stop was counted


def test_unknown_charging_is_not_a_stop():
    d = ChargeDetector()
    d.feed(snap(0, 50), "home", HOME)
    assert d.feed(snap(10, 50, charging=None), "home", HOME) == []
    assert d.feed(snap(20, 50, charging=None), "home", HOME) == []
