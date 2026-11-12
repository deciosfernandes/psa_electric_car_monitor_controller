import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from psacc import settings, store
from psacc.fake_gateway import FakeGateway, default_snapshot
from psacc.main import create_app
from psacc.tariff.schedule import planned_events

PASSWORD = "segredo123"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.delenv("PSACC_STATIC_DIR", raising=False)


def make_client(gateway_factory=FakeGateway) -> TestClient:
    return TestClient(create_app(db_url="sqlite://", gateway_factory=gateway_factory))


@pytest.fixture
def client():
    with make_client() as c:
        yield c


@pytest.fixture
def logged(client):
    assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    return client


def test_health_is_public(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_requires_login(client):
    for path in (
        "/api/status",
        "/api/battery",
        "/api/activity",
        "/api/automation",
        "/api/tariff",
        "/api/settings",
        "/api/setup/state",
        "/api/tariff/events",
    ):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/control/horn").status_code == 401
    assert client.get("/api/auth/state").json() == {"configured": False, "logged_in": False}


def test_first_run_setup_then_login(client):
    assert client.post("/api/auth/setup", json={"password": "curta"}).status_code == 422
    assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    assert client.get("/api/auth/state").json() == {"configured": True, "logged_in": True}
    assert client.post("/api/auth/setup", json={"password": "outra-password"}).status_code == 409

    client.post("/api/auth/logout")
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/auth/login", json={"password": "errada!!"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert client.get("/api/status").status_code == 200

    stored = store.get_config().password_hash
    assert stored and PASSWORD not in stored


def test_login_rate_limited(logged):
    logged.post("/api/auth/logout")
    for _ in range(5):
        assert logged.post("/api/auth/login", json={"password": "errada!!"}).status_code == 401
    r = logged.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 429
    assert "tentativas" in r.json()["detail"]


def test_status_shape(logged):
    body = logged.get("/api/status").json()
    assert set(body) == {
        "car",
        "period",
        "next_event",
        "automation",
        "override",
        "last_command",
        "mqtt_connected",
        "updated_at",
    }
    assert body["period"] in ("vazio", "cheias", "ponta")
    assert body["automation"] == {"enabled": True, "offset_min": 10, "battery_limit": 80}
    assert body["mqtt_connected"] is True
    assert body["next_event"]["action"] in ("charge", "defer")
    assert datetime.fromisoformat(body["next_event"]["at"]).utcoffset() is not None
    assert datetime.fromisoformat(body["updated_at"]).utcoffset() is not None


def test_status_serialises_car(logged, monkeypatch):
    snap = default_snapshot("VIN1")
    monkeypatch.setattr(logged.app.state.poller, "latest", lambda: snap)
    car = logged.get("/api/status").json()["car"]
    assert car["vin"] == "VIN1"
    assert car["soc"] == 50.0
    assert datetime.fromisoformat(car["at"]) == snap.at


def test_put_tariff_rejects_gap(logged):
    before = logged.get("/api/tariff").json()
    broken = {**before, "seasons": [dict(s) for s in before["seasons"]]}
    broken["seasons"][0] = {**broken["seasons"][0], "days": dict(broken["seasons"][0]["days"])}
    broken["seasons"][0]["days"]["weekday"] = broken["seasons"][0]["days"]["weekday"][1:]
    r = logged.put("/api/tariff", json=broken)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)
    assert "falta" in r.json()["detail"]
    assert logged.get("/api/tariff").json() == before


def test_put_tariff_roundtrip_and_reset(logged):
    tariff = logged.get("/api/tariff").json()
    tariff["name"] = "Meu tarifário"
    tariff["prices"] = [{"period": "vazio", "eur_kwh": 0.1, "valid_from": "2026-01-01"}]
    assert logged.put("/api/tariff", json=tariff).status_code == 200
    assert logged.get("/api/tariff").json()["name"] == "Meu tarifário"
    reset = logged.post("/api/tariff/reset").json()
    assert reset["name"] == "EDA tri-horário semanal"
    assert reset["prices"] == tariff["prices"]


def test_put_automation_out_of_range_422(logged):
    r = logged.put("/api/automation", json={"enabled": True, "offset_min": 90, "battery_limit": 80})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)
    r = logged.put("/api/automation", json={"enabled": True, "offset_min": 10, "battery_limit": 20})
    assert r.status_code == 422
    assert logged.get("/api/automation").json() == {"enabled": True, "offset_min": 10, "battery_limit": 80}

    ok = logged.put("/api/automation", json={"enabled": False, "offset_min": 15, "battery_limit": 90})
    assert ok.status_code == 200
    assert logged.get("/api/automation").json() == {"enabled": False, "offset_min": 15, "battery_limit": 90}


def test_charge_now_sets_override(logged):
    r = logged.post("/api/control/charge-now")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert store.get_override() is not None
    assert logged.get("/api/status").json()["override"]["reason"] == "manual"

    assert logged.post("/api/control/cancel-override").json()["ok"] is True
    assert store.get_override() is None


def test_charge_now_with_automation_off(logged):
    logged.put("/api/automation", json={"enabled": False, "offset_min": 10, "battery_limit": 80})
    gateway = logged.app.state.gateway
    gateway.snap = dataclasses.replace(gateway.snap, plugged=False)
    assert logged.post("/api/control/charge-now").json()["ok"] is False
    gateway.snap = dataclasses.replace(gateway.snap, plugged=True)
    assert logged.post("/api/control/charge-now").json()["ok"] is True
    assert store.get_override() is None
    assert ("set_charge_mode", "charge") in [c[:2] for c in gateway.calls]


def test_control_commands_use_gateway(logged):
    gateway = logged.app.state.gateway
    assert logged.post("/api/control/lock", json={"locked": False}).json()["ok"] is True
    assert logged.post("/api/control/precondition", json={"on": True}).json()["ok"] is True
    gateway.fail_next = 1
    assert logged.post("/api/control/horn").json()["ok"] is False
    assert ("lock", False) in gateway.calls and ("precondition", True) in gateway.calls


def test_tariff_events_7_days(logged):
    events = logged.get("/api/tariff/events", params={"days": 7}).json()
    now = datetime.now(timezone.utc)
    expected = planned_events(store.get_tariff(), 10, now, days=7)
    assert len(events) in (len(expected), len(expected) + 1)  # one may elapse between the two calls
    ats = [datetime.fromisoformat(e["at"]) for e in events]
    assert all(a.utcoffset() is not None for a in ats)
    assert all(now - timedelta(minutes=1) <= a <= now + timedelta(days=7) for a in ats)
    assert {e["action"] for e in events} == {"charge", "defer"}


def test_settings_hide_webhook(logged):
    r = logged.put(
        "/api/settings",
        json={"discord_webhook": "https://discord.example/hook", "discord_info": True, "refresh_min": 10},
    )
    assert r.status_code == 200
    body = logged.get("/api/settings").json()
    assert body == {"discord_webhook_set": True, "discord_info": True, "refresh_min": 10}
    assert "discord.example" not in r.text


def test_without_psa_account_car_endpoints_503():
    with make_client(gateway_factory=lambda: None) as c:
        c.post("/api/auth/setup", json={"password": PASSWORD})
        r = c.post("/api/control/horn")
        assert r.status_code == 503
        assert r.json()["detail"] == "Conta PSA não configurada"
        assert c.post("/api/control/charge-now").status_code == 503
        status = c.get("/api/status").json()
        assert status["car"] is None and status["mqtt_connected"] is False


def test_otp_activation_rebuilds_gateway(logged, monkeypatch):
    from psacc import setup_flow

    monkeypatch.setattr(setup_flow, "activate_otp", lambda sms_code, pin: None)
    old_gateway, old_poller = logged.app.state.gateway, logged.app.state.poller
    assert logged.post("/api/setup/otp", json={"sms_code": "123456", "pin": "1234"}).status_code == 200
    assert logged.app.state.gateway is not old_gateway
    assert logged.app.state.poller is not old_poller
    assert old_poller._stop.is_set()


def test_spa_fallback_does_not_shadow_api(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setenv("PSACC_STATIC_DIR", str(static))
    with make_client() as c:
        assert "spa" in c.get("/").text
        assert "spa" in c.get("/automacao").text
        assert c.get("/api/nao-existe").status_code == 404
        assert c.get("/api/health").json() == {"ok": True}
