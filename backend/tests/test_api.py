import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from psacc import settings, store
from psacc.auth import FAILURE_WINDOW_S, MAX_FAILURES, LoginRateLimiter
from psacc.fake_gateway import FakeGateway, default_snapshot
from psacc.main import CSRF_HEADER, create_app
from psacc.history.charges import ChargeSession
from psacc.places import Place
from psacc.tariff.schedule import planned_events

PASSWORD = "segredo123"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.delenv("PSACC_STATIC_DIR", raising=False)


def make_client(gateway_factory=FakeGateway, csrf=True, db_url="sqlite://") -> TestClient:
    headers = {CSRF_HEADER: "1"} if csrf else {}
    return TestClient(create_app(db_url=db_url, gateway_factory=gateway_factory), headers=headers)


@pytest.fixture
def client():
    with make_client() as c:
        yield c


@pytest.fixture
def logged(client):
    assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    return client


def test_health_is_public(client):
    assert client.get("/api/health").json() == {"ok": True, "fake": False}


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


def test_login_rate_limit_holds_under_concurrency(tmp_path):
    # a file DB: the in-memory test DB is one shared connection that cannot serve parallel requests
    with make_client(db_url=f"sqlite:///{tmp_path / 'api.db'}") as c:
        c.post("/api/auth/setup", json={"password": PASSWORD})
        c.post("/api/auth/logout")
        with ThreadPoolExecutor(max_workers=20) as pool:
            codes = list(
                pool.map(lambda _: c.post("/api/auth/login", json={"password": "errada!!"}).status_code, range(20))
            )
    assert codes.count(401) <= MAX_FAILURES
    assert codes.count(429) >= 20 - MAX_FAILURES


def test_rate_limiter_window_and_reset():
    now = [1000.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    for _ in range(MAX_FAILURES):
        assert limiter.try_acquire()
        limiter.release(success=False)
    assert not limiter.try_acquire()
    now[0] += FAILURE_WINDOW_S + 1  # failures expire
    assert limiter.try_acquire()
    limiter.release(success=False)
    assert limiter.try_acquire()
    limiter.release(success=True)  # a good login clears the failures
    for _ in range(MAX_FAILURES - 1):
        assert limiter.try_acquire()
    assert limiter.try_acquire()  # in-flight attempts count against the limit
    assert not limiter.try_acquire()


def test_password_length_limits(client):
    assert client.post("/api/auth/setup", json={"password": "x" * 1025}).status_code == 422
    assert client.post("/api/auth/setup", json={"password": "x" * 1024}).status_code == 200
    assert client.post("/api/auth/login", json={"password": "x" * 5000}).status_code == 422


def test_change_password(logged):
    assert logged.post("/api/auth/password", json={"current": "errada!!!", "new": "novasenha1"}).status_code == 401
    assert logged.post("/api/auth/password", json={"current": PASSWORD, "new": "curta"}).status_code == 422
    assert logged.post("/api/auth/password", json={"current": PASSWORD, "new": "x" * 1025}).status_code == 422
    assert logged.post("/api/auth/password", json={"current": PASSWORD, "new": "novasenha1"}).status_code == 200
    logged.post("/api/auth/logout")
    assert logged.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401
    assert logged.post("/api/auth/login", json={"password": "novasenha1"}).status_code == 200


def test_password_change_revokes_other_sessions(logged):
    other = TestClient(logged.app, headers=logged.headers)
    assert other.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert other.get("/api/status").status_code == 200
    assert logged.post("/api/auth/password", json={"current": PASSWORD, "new": "novasenha1"}).status_code == 200
    assert other.get("/api/status").status_code == 401
    assert logged.get("/api/status").status_code == 200


def test_health_reports_fake_mode(client, monkeypatch):
    assert client.get("/api/health").json()["fake"] is False
    monkeypatch.setenv("PSACC_FAKE", "1")
    assert client.get("/api/health").json()["fake"] is True


def test_change_password_requires_login_and_is_rate_limited(client):
    assert client.post("/api/auth/password", json={"current": "a", "new": "novasenha1"}).status_code == 401
    client.post("/api/auth/setup", json={"password": PASSWORD})
    for _ in range(MAX_FAILURES):
        assert client.post("/api/auth/password", json={"current": "errada!!!", "new": "novasenha1"}).status_code == 401
    assert client.post("/api/auth/password", json={"current": PASSWORD, "new": "novasenha1"}).status_code == 429


def test_fake_mode_uses_fake_gateway(monkeypatch):
    monkeypatch.setenv("PSACC_FAKE", "1")
    with make_client(gateway_factory=None) as c:
        c.post("/api/auth/setup", json={"password": PASSWORD})
        # (the first poll waits for the wakeup settle delay, so check the gateway rather than the status snapshot)
        assert c.get("/api/setup/state").json()["psa_connected"] is True
        assert c.post("/api/control/horn").json()["ok"] is True
        assert c.app.state.gateway.snapshot().vin.startswith("FAKE")


def test_csrf_header_required_for_state_changes():
    with make_client(csrf=False) as c:
        r = c.post("/api/auth/setup", json={"password": PASSWORD})
        assert r.status_code == 403
        assert CSRF_HEADER in r.json()["detail"]
        assert c.post("/api/auth/setup", json={"password": PASSWORD}, headers={CSRF_HEADER: "1"}).status_code == 200
        for path in ("/api/control/horn", "/api/control/charge-now", "/api/tariff/reset", "/api/auth/logout"):
            assert c.post(path).status_code == 403, path
        assert (
            c.put("/api/automation", json={"enabled": True, "offset_min": 10, "battery_limit": 80}).status_code == 403
        )
        assert c.get("/api/status").status_code == 200  # reads need no header
        assert c.post("/api/control/horn", headers={CSRF_HEADER: "1"}).json()["ok"] is True


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
        "psa_connected",
        "updated_at",
        "where",
        "place_name",
        "mileage_km",
        "position_age_min",
        "paused_reason",
    }
    assert body["paused_reason"] == "Posição do carro desconhecida" or body["paused_reason"] == "Defina o local Casa"
    assert body["where"] in ("unset", "unknown")  # no snapshot evaluated yet
    assert body["place_name"] is None
    assert body["period"] in ("vazio", "cheias", "ponta")
    assert body["automation"] == {"enabled": True, "offset_min": 10, "battery_limit": 80}
    assert body["mqtt_connected"] is True
    assert body["next_event"]["action"] in ("charge", "defer")
    assert datetime.fromisoformat(body["next_event"]["at"]).utcoffset() is not None
    assert datetime.fromisoformat(body["updated_at"]).utcoffset() is not None


def test_status_paused_reason(logged):
    engine = logged.app.state.poller.engine
    engine.last_where = "home"
    assert logged.get("/api/status").json()["paused_reason"] is None
    engine.last_where = "other"
    engine.last_place = Place(name="Lagoa", kind="other", lat=1.0, lon=1.0)
    assert logged.get("/api/status").json()["paused_reason"] == "Fora de Casa (Lagoa)"


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
    assert body == {
        "discord_webhook_set": True,
        "discord_info": True,
        "refresh_min": 10,
        "battery_capacity_kwh": 46,
    }
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


class SlowGateway(FakeGateway):
    """First poll blocks until released, like a wakeup + REST call still running when the runtime is replaced."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.polling = threading.Event()

    def snapshot(self, refresh=False):
        self.polling.set()
        self.release.wait(5)
        return self.snap


def test_rebuild_while_old_poll_in_flight(monkeypatch):
    from psacc import setup_flow

    monkeypatch.setattr(setup_flow, "activate_otp", lambda sms_code, pin: None)
    gateways = iter([SlowGateway(), FakeGateway()])
    with make_client(gateway_factory=lambda: next(gateways)) as c:
        c.post("/api/auth/setup", json={"password": PASSWORD})
        old_gateway, old_engine, old_poller = c.app.state.gateway, c.app.state.engine, c.app.state.poller
        assert old_gateway.polling.wait(5)
        threading.Timer(0.2, old_gateway.release.set).start()  # the old poll finishes after the swap
        assert c.post("/api/setup/otp", json={"sms_code": "1", "pin": "2"}).status_code == 200
        assert c.app.state.gateway is not old_gateway
        assert not old_poller._thread.is_alive()
        assert old_poller.latest() is None  # its late snapshot was dropped, not fed to the old engine
        old_engine.manual_charge()  # a closed engine never commands the car through the old gateway
        old_engine.tick()
        assert old_gateway.calls == []
        assert c.post("/api/control/horn").json()["ok"] is True
        assert ("horn",) in c.app.state.gateway.calls


def test_spa_fallback_does_not_shadow_api(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setenv("PSACC_STATIC_DIR", str(static))
    with make_client() as c:
        assert "spa" in c.get("/").text
        assert "spa" in c.get("/automacao").text
        assert c.get("/api/nao-existe").status_code == 404
        assert c.get("/api/health").json() == {"ok": True, "fake": False}


def test_setup_login_requires_package_name(logged, monkeypatch):
    from psacc import setup_flow

    seen = []
    monkeypatch.setattr(setup_flow, "start_login", lambda *a: seen.append(a) or setup_flow.LoginResult(ok=False))
    body = {"app_name": "MyPeugeot", "email": "a@b.pt", "password": "pw", "country": "PT"}
    r = logged.post("/api/setup/login", json=body)
    assert r.status_code == 422
    assert "Aplicação desconhecida" in r.text
    assert seen == []
    r = logged.post("/api/setup/login", json={**body, "app_name": "com.psa.mym.mypeugeot"})
    assert r.status_code == 200
    assert seen[0][0] == "com.psa.mym.mypeugeot"


def test_automation_and_tariff_changes_retick_engine(logged):
    ticks = []
    logged.app.state.engine.tick = lambda: ticks.append(1)
    body = {"enabled": True, "offset_min": 20, "battery_limit": 80}
    assert logged.put("/api/automation", json=body).status_code == 200
    assert len(ticks) == 1
    tariff = logged.get("/api/tariff").json()
    assert logged.put("/api/tariff", json=tariff).status_code == 200
    assert len(ticks) == 2
    assert logged.post("/api/tariff/reset").status_code == 200
    assert len(ticks) == 3


def test_put_tariff_without_vazio_is_422_pt(logged):
    flat = [{"start": 0, "end": 1440, "period": "cheias"}]
    body = {"name": "Simples", "seasons": [
        {"name": "Ano", "months": list(range(1, 13)), "days": {"weekday": flat, "saturday": flat, "sunday": flat}}
    ]}
    r = logged.put("/api/tariff", json=body)
    assert r.status_code == 422
    assert r.json()["detail"] == "A tarifa tem de ter pelo menos um intervalo de vazio"


def test_control_command_activity_is_tagged_control(logged):
    gateway = logged.app.state.gateway
    gateway.fail_next = 1
    logged.post("/api/control/horn")
    latest = logged.get("/api/activity").json()[0]
    assert (latest["kind"], latest["source"]) == ("error", "control")


PLACE = {"name": "Casa", "kind": "home", "lat": 10.0, "lon": 20.0}


def test_places_require_login(client):
    assert client.get("/api/places").status_code == 401


def test_places_crud(logged):
    assert logged.get("/api/places").json() == []
    home = logged.post("/api/places", json=PLACE).json()
    assert home["id"] and home["radius_m"] == 150 and home["price_eur_kwh"] is None
    other = logged.post(
        "/api/places", json={"name": "Ginásio", "kind": "other", "lat": 11, "lon": 20, "price_eur_kwh": 0.15}
    ).json()
    assert [p["id"] for p in logged.get("/api/places").json()] == [home["id"], other["id"]]
    r = logged.put(f"/api/places/{home['id']}", json={**PLACE, "name": "Casa nova", "radius_m": 300})
    assert r.status_code == 200 and r.json()["name"] == "Casa nova" and r.json()["id"] == home["id"]
    assert logged.put("/api/places/999", json=PLACE).status_code == 404
    assert logged.delete(f"/api/places/{other['id']}").status_code == 200
    assert logged.delete(f"/api/places/{other['id']}").status_code == 404
    assert len(logged.get("/api/places").json()) == 1


def test_places_validation_and_second_home_422_pt(logged):
    r = logged.post("/api/places", json={**PLACE, "radius_m": 5})
    assert r.status_code == 422 and "Raio" in r.json()["detail"]
    r = logged.post("/api/places", json={**PLACE, "price_eur_kwh": 0.2})
    assert r.status_code == 422 and "Preço" in r.json()["detail"]
    assert logged.post("/api/places", json=PLACE).status_code == 200
    r = logged.post("/api/places", json={**PLACE, "name": "Outra"})
    assert r.status_code == 422 and "casa" in r.json()["detail"]


def test_places_from_car(logged):
    r = logged.post("/api/places/from-car", json={"name": "Trabalho", "kind": "work"})
    assert r.status_code == 409 and r.json()["detail"] == "Posição do carro desconhecida"
    store.set_position_fix(store.PositionFix(lat=10.5, lon=20.5, at=datetime.now(timezone.utc), mileage_km=1.0))
    r = logged.post("/api/places/from-car", json={"name": "Trabalho", "kind": "work"})
    assert r.status_code == 200
    assert (r.json()["lat"], r.json()["lon"], r.json()["kind"]) == (10.5, 20.5, "work")


def test_places_from_car_rejects_stale_fix(logged, monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    store.set_position_fix(store.PositionFix(lat=10.5, lon=20.5, at=old, mileage_km=100.0))
    body = {"name": "Casa", "kind": "home"}
    for mileage in (None, 150.0):  # odometer unknown or moved: the stored fix no longer holds
        snap = dataclasses.replace(default_snapshot("V"), mileage_km=mileage)
        monkeypatch.setattr(logged.app.state.poller, "latest", lambda snap=snap: snap)
        r = logged.post("/api/places/from-car", json=body)
        assert r.status_code == 409 and r.json()["detail"] == "Posição do carro desatualizada"
    monkeypatch.setattr(
        logged.app.state.poller, "latest", lambda: dataclasses.replace(default_snapshot("V"), mileage_km=100.0)
    )
    assert logged.post("/api/places/from-car", json=body).status_code == 200  # odometer unchanged: still valid


def test_consumption_ignores_trips_with_unknown_energy(logged):
    from psacc.history.trips import Trip

    now = datetime.now(timezone.utc)
    for km, kwh in ((10.0, 2.0), (50.0, None)):
        store.save_trip(
            Trip(departed_at=now - timedelta(hours=2), arrived_at=now - timedelta(hours=1), from_kind="home",
                 to_kind="work", km=km, kwh=kwh, kwh_100km=None if kwh is None else kwh / km * 100)
        )
    c = logged.get("/api/consumption").json()["last30"]
    assert c["km"] == 10.0 and c["kwh"] == 2.0 and c["kwh_100km"] == pytest.approx(20.0)
    assert sorted(t["kwh"] is None for t in logged.get("/api/trips").json()) == [False, True]


def test_settings_capacity_range_and_keep(logged):
    base = {"discord_info": False, "refresh_min": 5}
    for bad in (9.9, 150.1):
        assert logged.put("/api/settings", json={**base, "battery_capacity_kwh": bad}).status_code == 422
    assert logged.put("/api/settings", json={**base, "battery_capacity_kwh": 50}).json()["battery_capacity_kwh"] == 50
    assert logged.put("/api/settings", json=base).json()["battery_capacity_kwh"] == 50  # omitted: kept


def test_battery_health_endpoint(logged):
    from datetime import date

    store.add_health_sample(date(2026, 11, 11), 95.0)
    store.add_health_sample(date(2026, 11, 12), 94.5)
    assert logged.get("/api/battery-health").json() == [
        {"day": "2026-11-11", "value": 95.0},
        {"day": "2026-11-12", "value": 94.5},
    ]


def _seed_history():
    from psacc.history.charges import ChargeSession
    from psacc.history.trips import Trip

    utc = timezone.utc
    store.save_place(Place(name="Casa", kind="home", lat=1, lon=1))

    def save(session, samples=()):  # as the engine does: costs are frozen when the session is stored
        s = store.save_charge_session(session)
        for at, soc in samples:
            store.add_charge_sample(s.id, at, soc, 20.0)
        return store.save_charge_session(store.refresh_session_cost(s))

    home = save(
        ChargeSession(
            started_at=datetime(2026, 10, 3, 2, 0, tzinfo=utc),
            ended_at=datetime(2026, 10, 3, 5, 0, tzinfo=utc),
            place_kind="home", soc_start=20, soc_end=70),
        [(datetime(2026, 10, 3, 3, 0, tzinfo=utc), 40)],
    )
    work = save(
        ChargeSession(
            started_at=datetime(2026, 10, 4, 10, 0, tzinfo=utc),
            ended_at=datetime(2026, 10, 4, 12, 0, tzinfo=utc),
            place_kind="work", soc_start=10, soc_end=30)
    )
    # 2026-11-01 01:00 UTC is November 01 00:00 Azores
    save(
        ChargeSession(
            started_at=datetime(2026, 11, 1, 1, 0, tzinfo=utc),
            ended_at=datetime(2026, 11, 1, 2, 0, tzinfo=utc),
            place_kind="work", soc_start=10, soc_end=20)
    )
    now = datetime.now(utc)
    for days, km, kwh in ((2, 100.0, 15.0), (40, 50.0, 10.0)):
        store.save_trip(
            Trip(departed_at=now - timedelta(days=days, hours=1), arrived_at=now - timedelta(days=days),
                 from_kind="home", to_kind="work", km=km, kwh=kwh, kwh_100km=kwh / km * 100)
        )
    return home, work


def test_history_requires_login(client):
    for path in ("/api/charges", "/api/charges/summary", "/api/charges/1/samples", "/api/trips", "/api/consumption"):
        assert client.get(path).status_code == 401, path
    assert client.put("/api/charges/1/price", json={"eur_kwh": 0.1}).status_code == 401


def test_charges_list_and_samples(logged):
    home, _ = _seed_history()
    rows = logged.get("/api/charges", params={"limit": 2}).json()
    assert len(rows) == 2 and rows[0]["started_at"] > rows[1]["started_at"]
    assert logged.get("/api/charges", params={"limit": 5, "offset": 2}).json()[0]["id"] == home.id
    h = logged.get("/api/charges", params={"limit": 5, "offset": 2}).json()[0]
    assert h["kwh"] == pytest.approx(23.0)
    samples = logged.get(f"/api/charges/{home.id}/samples").json()
    assert [s["soc"] for s in samples] == [40]
    assert rows[1]["cost_eur"] == 0.0  # work charge is free
    assert logged.get("/api/charges/999/samples").status_code == 404


def test_charge_price_put(logged):
    _, work = _seed_history()
    r = logged.put(f"/api/charges/{work.id}/price", json={"eur_kwh": 0.2})
    assert r.status_code == 200
    assert r.json()["price_override_eur_kwh"] == 0.2
    assert r.json()["cost_eur"] == pytest.approx(0.2 * 9.2)
    assert logged.put("/api/charges/999/price", json={"eur_kwh": 0.2}).status_code == 404
    assert logged.put(f"/api/charges/{work.id}/price", json={"eur_kwh": -1}).status_code == 422


def test_charges_summary_month_uses_azores_time(logged):
    _seed_history()
    oct_ = logged.get("/api/charges/summary", params={"month": "2026-10"}).json()
    assert set(oct_) == {"kwh", "cost_eur", "work_pct", "home_pct", "avg_home_eur_kwh", "unpriced", "partial"}
    assert oct_["kwh"] == pytest.approx(23.0 + 9.2)
    assert oct_["home_pct"] == pytest.approx(23.0 / 32.2 * 100)
    assert oct_["work_pct"] == pytest.approx(9.2 / 32.2 * 100)
    assert oct_["unpriced"] == 1 and oct_["partial"] == 0  # seeded: one session without a price
    nov = logged.get("/api/charges/summary", params={"month": "2026-11"}).json()
    assert nov["kwh"] == pytest.approx(4.6)  # 01:00 UTC 1 Nov = 00:00 Azores, November
    assert logged.get("/api/charges/summary", params={"month": "bad"}).status_code == 422


def test_trips_list(logged):
    _seed_history()
    rows = logged.get("/api/trips", params={"limit": 1}).json()
    assert len(rows) == 1 and rows[0]["km"] == 100.0
    assert logged.get("/api/trips", params={"limit": 5, "offset": 1}).json()[0]["km"] == 50.0


def test_consumption(logged):
    _seed_history()
    d = logged.get("/api/consumption").json()
    assert d["last30"] == {"km": 100.0, "kwh": 15.0, "kwh_100km": 15.0}
    assert sum(m["km"] for m in d["months"]) == 150.0
    assert all(set(m) == {"month", "km", "kwh", "kwh_100km"} for m in d["months"])


# ---- frozen charge history ---------------------------------------------------------------------------------


def _other_session(price=0.3):
    utc = timezone.utc
    store.save_place(Place(name="Posto", kind="other", lat=5, lon=5, price_eur_kwh=price))
    s = store.save_charge_session(
        ChargeSession(
            started_at=datetime(2026, 10, 5, 10, 0, tzinfo=utc),
            ended_at=datetime(2026, 10, 5, 12, 0, tzinfo=utc),
            place_kind="other", place_name="Posto", soc_start=20, soc_end=70)
    )
    return store.save_charge_session(store.refresh_session_cost(s))


def test_capacity_change_does_not_rewrite_finished_session(logged):
    s = _other_session()
    assert s.kwh == pytest.approx(23.0) and s.cost_eur == pytest.approx(6.9)
    cfg = store.get_config()
    cfg.battery_capacity_kwh = 60
    store.save_config(cfg)
    row = logged.get("/api/charges").json()[0]
    assert row["kwh"] == pytest.approx(23.0) and row["cost_eur"] == pytest.approx(6.9)
    logged.put(f"/api/charges/{s.id}/price", json={"eur_kwh": 0.5})
    assert logged.get("/api/charges").json()[0]["kwh"] == pytest.approx(23.0)  # a price edit keeps the frozen kWh


def test_deleting_or_renaming_other_place_keeps_cost(logged):
    s = _other_session()
    place = next(p for p in store.list_places() if p.kind == "other")
    store.save_place(place.model_copy(update={"name": "Outro nome", "price_eur_kwh": 0.9}))
    assert logged.get("/api/charges").json()[0]["cost_eur"] == pytest.approx(6.9)
    store.delete_place(place.id)
    assert logged.get("/api/charges").json()[0]["cost_eur"] == pytest.approx(6.9)
    assert store.refresh_session_cost(store.get_charge_session(s.id)).cost_eur == pytest.approx(6.9)  # price kept


def test_put_price_null_clears_override(logged):
    s = _other_session()
    assert logged.put(f"/api/charges/{s.id}/price", json={"eur_kwh": 0.5}).json()["cost_eur"] == pytest.approx(11.5)
    r = logged.put(f"/api/charges/{s.id}/price", json={"eur_kwh": None})
    assert r.status_code == 200
    assert r.json()["price_override_eur_kwh"] is None and r.json()["cost_eur"] == pytest.approx(6.9)


def test_reads_do_not_recompute_and_summary_reads_only_its_month(logged, monkeypatch):
    _seed_history()

    def boom(*a, **k):
        raise AssertionError("reads must not recompute")

    monkeypatch.setattr(store, "refresh_session_cost", boom)
    monkeypatch.setattr(store, "_priced", boom)
    monkeypatch.setattr(store, "charge_sessions", boom, raising=True)
    seen = []
    real = store.charge_sessions_between
    monkeypatch.setattr(store, "charge_sessions_between", lambda a, b: seen.append((a, b)) or real(a, b))
    assert logged.get("/api/charges/summary", params={"month": "2026-10"}).json()["kwh"] == pytest.approx(32.2)
    # Azores: 1 Oct 00:00 is UTC+0 (summer time), 1 Nov 00:00 is UTC-1
    first, last = datetime(2026, 10, 1, 0, tzinfo=timezone.utc), datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
    assert seen == [(first, last)]


def test_charges_list_does_not_recompute(logged, monkeypatch):
    _seed_history()
    monkeypatch.setattr(store, "refresh_session_cost", lambda c: (_ for _ in ()).throw(AssertionError("recompute")))
    monkeypatch.setattr(store, "_priced", lambda c: (_ for _ in ()).throw(AssertionError("recompute")))
    assert len(logged.get("/api/charges").json()) == 3


# ---- missing CSRF header, partial flag, Azores midnight -----------------------------------------------------


def test_put_price_requires_csrf_header(logged):
    s = _other_session()
    r = logged.put(f"/api/charges/{s.id}/price", json={"eur_kwh": 0.5}, headers={CSRF_HEADER: ""})
    assert r.status_code == 403


def test_charges_expose_partial_flag(logged):
    _seed_history()
    assert all(isinstance(r["partial"], bool) for r in logged.get("/api/charges").json())


def test_clearing_override_on_migrated_other_session_uses_current_place_price(logged):
    s = _other_session(price=0.3)
    # pre-migration row
    migrated = store.save_charge_session(s.model_copy(update={"place_price_eur_kwh": None, "cost_eur": None}))
    url = f"/api/charges/{migrated.id}/price"
    assert logged.put(url, json={"eur_kwh": 0.5}).json()["cost_eur"] == pytest.approx(11.5)
    r = logged.put(url, json={"eur_kwh": None}).json()
    assert r["cost_eur"] == pytest.approx(6.9) and r["place_price_eur_kwh"] == 0.3
