import pytest
import dataclasses
from datetime import UTC, datetime, timedelta

from psacc import store
from psacc.history.trips import TripDetector
from psacc.places import Place
from tests.fakes import default_snapshot

T0 = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
HOME = Place(id=1, name="Casa", kind="home", lat=1.0, lon=1.0)
WORK = Place(id=2, name="Trabalho", kind="work", lat=2.0, lon=2.0)


def snap(minutes, km, soc=80.0, lat=1.0, lon=1.0, **kw):
    t = T0 + timedelta(minutes=minutes)
    return dataclasses.replace(
        default_snapshot(), at=t, energy_at=t, mileage_km=km, soc=soc, latitude=lat, longitude=lon, **kw
    )


def run(det, steps):
    out = []
    for s, where, place in steps:
        t = det.feed(s, where, place)
        if t is not None:
            out.append(t)
    return out


def test_trip_home_to_work():
    d = TripDetector(46)
    trips = run(
        d,
        [
            (snap(0, 1000), "home", HOME),
            (snap(30, 1010, soc=75, lat=1.5, lon=1.5), "other", None),
            (snap(45, 1020, soc=70, lat=2.0, lon=2.0), "work", WORK),
            (snap(50, 1020, soc=70, lat=2.0, lon=2.0), "work", WORK),  # < 10 min after: not yet
            (snap(60, 1020, soc=70, lat=2.0, lon=2.0), "work", WORK),
        ],
    )
    assert len(trips) == 1
    t = trips[0]
    assert (t.from_kind, t.from_name, t.to_kind, t.to_name) == ("home", "Casa", "work", "Trabalho")
    assert (t.from_lat, t.from_lon, t.to_lat, t.to_lon) == (1.0, 1.0, 2.0, 2.0)
    assert t.km == 20
    assert t.departed_at == T0 and t.arrived_at == T0 + timedelta(minutes=45)
    assert (t.soc_start, t.soc_end) == (80.0, 70.0)
    assert t.kwh == pytest.approx(4.6) and t.kwh_100km == pytest.approx(23.0)
    assert d.feed(snap(70, 1020, soc=70), "work", WORK) is None  # parked again


def test_short_noise_ignored():
    d = TripDetector(46)
    trips = run(
        d,
        [
            (snap(0, 1000), "home", HOME),
            (snap(10, 1000.2), "home", HOME),
            (snap(25, 1000.2), "home", HOME),
            (snap(40, 1000.2), "home", HOME),
        ],
    )
    assert trips == []


def test_trip_without_soc_drop_kwh_zero():
    d = TripDetector(46)
    trips = run(
        d,
        [
            (snap(0, 1000, soc=60), "home", HOME),
            (snap(20, 1005, soc=65), "work", WORK),
            (snap(40, 1005, soc=65), "work", WORK),
        ],
    )
    assert len(trips) == 1 and trips[0].kwh == 0 and trips[0].kwh_100km == 0


def test_charging_during_parked_not_a_trip():
    d = TripDetector(46)
    trips = run(
        d,
        [
            (snap(0, 1000, soc=40, charging=True, plugged=True), "home", HOME),
            (snap(30, 1000, soc=50, charging=True, plugged=True), "home", HOME),
            (snap(60, 1000, soc=60, charging=True, plugged=True), "home", HOME),
            (snap(90, 1000, soc=70, charging=False, plugged=True), "home", HOME),
        ],
    )
    assert trips == []


def test_stale_and_unknown_odometer_ignored():
    d = TripDetector(46)
    assert d.feed(snap(0, 1000), "home", HOME) is None
    assert d.feed(snap(0, 1100), "home", HOME) is None  # same timestamp: nothing new
    assert d.feed(snap(5, None), "home", HOME) is None  # unknown is not movement


def test_store_trips_roundtrip():
    d = TripDetector(46)
    t = run(d, [(snap(0, 1000), "home", HOME), (snap(20, 1010), "work", WORK), (snap(40, 1010), "work", WORK)])[0]
    saved = store.save_trip(t)
    assert saved.id is not None
    got = store.trips(10)
    assert len(got) == 1 and got[0].km == 10 and got[0].departed_at == T0
    assert store.trips(10, offset=1) == []


def test_mqtt_updates_during_drive_do_not_close_trip():
    d = TripDetector(46)
    d.feed(snap(0, 1000), "home", HOME)
    d.feed(snap(10, 1010, soc=78), "other", None)
    base = snap(10, 1010, soc=78)
    pushes = [dataclasses.replace(base, at=T0 + timedelta(minutes=m), soc=77 - m / 10) for m in (15, 25)]
    assert [d.feed(p, "other", None) for p in pushes] == [None, None]
    assert d.feed(snap(30, 1025, soc=72), "other", None) is None  # still driving
    assert d.feed(snap(50, 1025, soc=72), "work", WORK).km == 25


def test_soc_end_is_at_arrival_not_after_plugging_in():
    d = TripDetector(46)
    t = run(
        d,
        [
            (snap(0, 1000, soc=80), "home", HOME),
            (snap(20, 1020, soc=70), "work", WORK),
            (snap(40, 1020, soc=75, charging=True, plugged=True), "work", WORK),
        ],
    )[0]
    assert t.soc_end == 70 and t.kwh == pytest.approx(4.6)
