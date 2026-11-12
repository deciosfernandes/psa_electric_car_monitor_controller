import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from psacc import settings, store
from psacc.fake_gateway import default_snapshot
from psacc.places import Place, PositionFix, distance_m, locate, place_at, update_fix

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
HOME = Place(id=1, name="Casa", kind="home", lat=10.0, lon=20.0)
WORK = Place(id=2, name="Trabalho", kind="work", lat=10.01, lon=20.0)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    store.init_db("sqlite://")


def car(**kw):
    return dataclasses.replace(default_snapshot(), **{"mileage_km": 1000.0, **kw})


def test_distance_haversine():
    assert distance_m(10.0, 20.0, 10.01, 20.0) == pytest.approx(1111.9, abs=3)
    assert distance_m(10.0, 20.0, 10.0, 20.0) == 0


def test_place_at_nearest_inside_radius():
    near = Place(id=3, name="Perto", kind="other", lat=10.0005, lon=20.0, radius_m=500)
    assert place_at(10.0001, 20.0, [HOME, near]) == HOME  # inside both, home is nearer
    assert place_at(10.0004, 20.0, [HOME, near]) == near  # outside home's 150 m, inside the other
    assert place_at(10.5, 20.0, [HOME, near]) is None


def test_update_fix_only_when_newer():
    c = car(latitude=10.0, longitude=20.0, position_at=NOW)
    fix = update_fix(c, None)
    assert fix == PositionFix(lat=10.0, lon=20.0, at=NOW, mileage_km=1000.0)
    assert update_fix(c, fix) is None
    assert update_fix(dataclasses.replace(c, position_at=NOW + timedelta(minutes=1)), fix).at > NOW
    assert update_fix(dataclasses.replace(c, latitude=None), None) is None
    assert update_fix(dataclasses.replace(c, position_at=None), None) is None


def test_locate_unset_without_home():
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW, mileage_km=1000.0)
    assert locate(NOW, car(), fix, [WORK]) == ("unset", None)


def test_locate_no_fix_is_unknown():
    assert locate(NOW, car(), None, [HOME]) == ("unknown", None)


def test_locate_places_and_other():
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW, mileage_km=1000.0)
    assert locate(NOW, car(), fix, [HOME, WORK]) == ("home", HOME)
    assert locate(NOW, car(), fix.model_copy(update={"lat": 10.01}), [HOME, WORK]) == ("work", WORK)
    assert locate(NOW, car(), fix.model_copy(update={"lat": 11.0}), [HOME, WORK]) == ("other", None)


def test_locate_old_fix_same_mileage_is_home():
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW - timedelta(hours=5), mileage_km=1000.0)
    assert locate(NOW, car(mileage_km=1000.05), fix, [HOME]) == ("home", HOME)


def test_locate_old_fix_moved_is_unknown():
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW - timedelta(hours=5), mileage_km=1000.0)
    assert locate(NOW, car(mileage_km=1012.0), fix, [HOME]) == ("unknown", None)


def test_locate_recent_fix_moved_odometer_still_trusted():
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW - timedelta(minutes=30), mileage_km=1000.0)
    assert locate(NOW, car(mileage_km=1012.0), fix, [HOME]) == ("home", HOME)


def test_locate_old_fix_unknown_car_odometer_is_unknown():
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW - timedelta(hours=5), mileage_km=1000.0)
    assert locate(NOW, dataclasses.replace(car(), mileage_km=None), fix, [HOME]) == ("unknown", None)


def test_locate_old_fix_unknown_fix_odometer_is_unknown():
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW - timedelta(hours=5), mileage_km=None)
    assert locate(NOW, car(mileage_km=1000.0), fix, [HOME]) == ("unknown", None)


def test_locate_naive_fix_time_is_utc():
    naive = (NOW - timedelta(hours=5)).replace(tzinfo=None)
    fix = PositionFix(lat=10.0, lon=20.0, at=naive, mileage_km=1000.0)
    assert locate(NOW, car(), fix, [HOME]) == ("home", HOME)
    assert locate(NOW, car(mileage_km=1012.0), fix, [HOME]) == ("unknown", None)


def test_place_validation():
    with pytest.raises(ValueError):
        Place(name="X", kind="home", lat=1, lon=1, price_eur_kwh=0.2)
    with pytest.raises(ValueError):
        Place(name="X", kind="other", lat=1, lon=1, radius_m=10)
    assert Place(name="X", kind="other", lat=1, lon=1, price_eur_kwh=0.2).radius_m == 150


def test_store_places_roundtrip_and_delete():
    saved = store.save_place(Place(name="Casa", kind="home", lat=10.0, lon=20.0))
    assert saved.id is not None
    assert store.list_places() == [saved]
    updated = store.save_place(saved.model_copy(update={"name": "Casa nova"}))  # same home: not a second one
    assert store.list_places() == [updated] and updated.id == saved.id
    assert store.delete_place(saved.id) is True
    assert store.delete_place(saved.id) is False
    assert store.list_places() == []


def test_save_second_home_rejected():
    store.save_place(Place(name="Casa", kind="home", lat=10.0, lon=20.0))
    with pytest.raises(ValueError, match="casa"):
        store.save_place(Place(name="Outra", kind="home", lat=11.0, lon=20.0))
    store.save_place(Place(name="Trabalho", kind="work", lat=11.0, lon=20.0))
    with pytest.raises(ValueError, match="trabalho"):
        store.save_place(Place(name="Outro", kind="work", lat=12.0, lon=20.0))
    assert len(store.list_places()) == 2


def test_position_fix_roundtrip():
    assert store.get_position_fix() is None
    fix = PositionFix(lat=10.0, lon=20.0, at=NOW, mileage_km=5.0)
    store.set_position_fix(fix)
    assert store.get_position_fix() == fix
