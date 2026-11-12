import os
import stat
from datetime import datetime, timezone

import pytest

from psacc import settings, store
from psacc.store import AppConfig, LastCommand, Override


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    store.init_db("sqlite://")


def utc(h):
    return datetime(2026, 11, 12, h, 0, tzinfo=timezone.utc)


def test_config_defaults():
    cfg = store.get_config()
    assert cfg.offset_min == 10
    assert cfg.battery_limit == 80
    assert cfg.automation_enabled is True
    assert cfg.battery_limit_enabled is True
    assert cfg.session_secret
    assert store.get_config().session_secret == cfg.session_secret


def test_config_roundtrip():
    cfg = store.get_config()
    cfg.battery_limit = 90
    cfg.vin = "VIN1"
    store.save_config(cfg)
    assert store.get_config() == cfg
    with pytest.raises(ValueError):
        AppConfig(session_secret="x", battery_limit=10)


def test_activity_newest_first():
    store.log_activity("info", "a", utc(1))
    store.log_activity("error", "b", utc(3))
    store.log_activity("action", "c", utc(2))
    acts = store.recent_activity()
    assert [a.message for a in acts] == ["b", "c", "a"]
    assert acts[0].at == utc(3)
    assert len(store.recent_activity(limit=2)) == 2


def test_override_set_and_clear():
    assert store.get_override() is None
    o = Override(started_at=utc(5), reason="manual")
    store.set_override(o)
    assert store.get_override() == o
    store.set_override(None)
    assert store.get_override() is None


def test_last_command_roundtrip():
    assert store.get_last_command() is None
    c = LastCommand(mode="charge", at=utc(5), verified=True, attempts=2)
    store.set_last_command(c)
    assert store.get_last_command() == c


def test_battery_samples_since():
    for h, soc in ((1, 50.0), (2, 60.0), (3, 70.0)):
        store.add_battery_sample(utc(h), soc, h == 3)
    got = store.battery_samples(utc(2))
    assert [s.soc for s in got] == [60.0, 70.0]
    assert got[1].charging is True and got[0].at == utc(2)


def test_psa_file(tmp_path):
    assert store.get_psa() is None
    store.save_psa({"a": 1})
    assert (tmp_path / "psa.json").exists()
    assert store.get_psa() == {"a": 1}


def test_otp_path_follows_data_dir(tmp_path):
    from psacc.psa.otp import otp
    assert otp.otp_path() == tmp_path / "otp.bin"
    otp.save_otp({"x": 1})
    assert otp.load_otp() == {"x": 1}


def test_psa_corrupt_file_is_none(tmp_path):
    (tmp_path / "psa.json").write_text("{trunc", encoding="utf-8")
    assert store.get_psa() is None


def test_psa_overwrite_leaves_no_tmp(tmp_path):
    store.save_psa({"a": 1})
    store.save_psa({"a": 2})
    assert store.get_psa() == {"a": 2}
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("psa")] == ["psa.json"]


def test_tariff_defaults_to_preset_and_roundtrips():
    from psacc.tariff.presets import EDA_TRI_SEMANAL

    assert store.get_tariff() == EDA_TRI_SEMANAL
    custom = EDA_TRI_SEMANAL.model_copy(update={"name": "Outro"})
    store.save_tariff(custom)
    assert store.get_tariff() == custom


def test_init_db_creates_config_row_once():
    # the row (and its random session secret) exists before any request, so concurrent first
    # get_config() calls can never create two different secrets
    with store._session() as s:
        row = s.get(store.ConfigRow, 1)
    assert row is not None
    secret = AppConfig.model_validate_json(row.data).session_secret
    assert secret
    assert store.get_config().session_secret == secret


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_db_file_restricted_to_owner(tmp_path, monkeypatch):
    chmods = []
    real_chmod = os.chmod
    monkeypatch.setattr(store.os, "chmod", lambda p, m: chmods.append((str(p), m)) or real_chmod(p, m))
    store.init_db()  # the default file database under DATA_DIR
    db = tmp_path / "psacc.db"
    assert db.exists()
    assert (str(db), 0o600) in chmods
    if os.name == "posix":
        assert _mode(db) == 0o600


def test_otp_saved_atomically_owner_only(tmp_path):
    from psacc.psa.otp import otp

    otp.save_otp({"x": 1})
    otp.save_otp({"x": 2})
    assert otp.load_otp() == {"x": 2}
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("otp")] == ["otp.bin"]
    if os.name == "posix":
        assert _mode(tmp_path / "otp.bin") == 0o600


def test_activity_source_defaults_to_automation():
    store.log_activity("error", "motor", utc(1))
    store.log_activity("error", "comando", utc(2), source="control")
    acts = store.recent_activity()
    assert [(a.message, a.source) for a in acts] == [("comando", "control"), ("motor", "automation")]


def test_activity_source_column_added_to_existing_db(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE activityrow (id INTEGER PRIMARY KEY, at DATETIME, kind VARCHAR, message VARCHAR)")
    con.execute("INSERT INTO activityrow (at, kind, message) VALUES ('2026-11-12 01:00:00', 'info', 'antigo')")
    con.commit()
    con.close()
    store.init_db(f"sqlite:///{db}")
    assert [(a.message, a.source) for a in store.recent_activity()] == [("antigo", "automation")]


def test_trip_with_unknown_energy_round_trips():
    from psacc.history.trips import Trip

    dep, arr = datetime(2026, 11, 13, tzinfo=timezone.utc), datetime(2026, 11, 13, 1, tzinfo=timezone.utc)
    store.save_trip(Trip(departed_at=dep, arrived_at=arr, from_kind="home", to_kind="work", km=5))
    assert [(t.kwh, t.kwh_100km) for t in store.trips(10)] == [(None, None)]


def test_health_sample_upsert_one_per_day():
    from datetime import date

    store.add_health_sample(date(2026, 11, 12), 95.0)
    store.add_health_sample(date(2026, 11, 12), 94.0)
    store.add_health_sample(date(2026, 11, 11), 96.0)
    assert store.health_samples() == [(date(2026, 11, 11), 96.0), (date(2026, 11, 12), 94.0)]


def test_capacity_default_and_range():
    assert store.get_config().battery_capacity_kwh == 46
    for bad in (9, 151):
        with pytest.raises(ValueError):
            AppConfig(battery_capacity_kwh=bad)
