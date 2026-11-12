import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from psacc.gateway import PsaGateway, snapshot_from_status
from psacc.psa.models import VehicleStatus
from tests.fakes import FakeGateway

DATA = Path(__file__).parent / "data"


def load(name: str) -> VehicleStatus:
    return VehicleStatus.model_validate(json.loads((DATA / name).read_text(encoding="utf-8")))


def test_snapshot_from_energies():
    snap = snapshot_from_status("VIN", load("status_v4_17.json"))
    assert snap.soc == 61.5
    assert snap.range_km == 131
    assert snap.plugged is True
    assert snap.charge_status == "InProgress"
    assert snap.charging is True
    assert snap.charge_rate_kmh == 20
    assert snap.next_delayed_time == "PT22H30M"
    assert (snap.latitude, snap.longitude) == (37.7412, -25.6687)
    assert snap.locked is True
    assert snap.precond_on is False
    assert snap.at == datetime(2024, 8, 1, 10, 15, 30, tzinfo=timezone.utc)


def test_snapshot_falls_back_to_legacy_energy():
    snap = snapshot_from_status("VIN", load("status_legacy.json"))
    assert snap.soc == 59
    assert snap.range_km == 122
    assert snap.plugged is False
    assert snap.charge_status == "Disconnected"
    assert snap.charging is False
    assert snap.next_delayed_time == "PT22H31M"
    assert snap.precond_on is False


def test_snapshot_from_status_tolerates_missing_fields():
    for raw in ({}, {"energies": []}, {"energies": [{"type": "Fuel", "level": 10}], "doorsState": {}}):
        snap = snapshot_from_status("VIN", VehicleStatus.model_validate(raw))
        assert snap.soc is None and snap.charging is None and snap.locked is None


def test_unknown_enum_kept():
    status = load("status_v4_17.json")
    raw = {"energies": [{"type": "Electric", "extension": {"electric": {"charging": {
        "chargingPowerLevel": "Level9", "status": "SomethingNew"}}}}]}
    charging = VehicleStatus.model_validate(raw).electric().charging_info()
    assert charging.charging_power_level == "Level9"
    assert snapshot_from_status("VIN", status).charge_status == "InProgress"
    assert snapshot_from_status("VIN", VehicleStatus.model_validate(raw)).charge_status == "SomethingNew"


def test_set_charge_mode_maps_to_mqtt():
    client = MagicMock()
    gateway = PsaGateway(client, "VIN1")
    result = gateway.set_charge_mode("defer", (0, 10))
    client.remote_client.veh_charge_request.assert_called_once_with("VIN1", 0, 10, "delayed")
    assert result is client.remote_client.veh_charge_request.return_value
    gateway.set_charge_mode("charge", (6, 50))
    client.remote_client.veh_charge_request.assert_called_with("VIN1", 6, 50, "immediate")


def test_newer_mqtt_lock_state_overrides_rest():
    client = MagicMock()
    client.get_vehicle_info.return_value = load("status_v4_17.json")  # Locked at 10:15:30
    client.remote_client.lock_state = {"VIN": "Unlocked"}
    client.remote_client.lock_state_at = {"VIN": datetime(2024, 8, 1, 11, 0, tzinfo=timezone.utc)}
    gateway = PsaGateway(client, "VIN")
    assert gateway.snapshot().locked is False
    client.remote_client.lock_state_at = {"VIN": datetime(2024, 8, 1, 10, 0, tzinfo=timezone.utc)}
    assert gateway.snapshot().locked is True


def test_snapshot_refresh_wakes_up():
    client = MagicMock()
    client.get_vehicle_info.return_value = load("status_v4_17.json")
    client.remote_client.lock_state = {}
    client.remote_client.lock_state_at = {}
    PsaGateway(client, "VIN").snapshot(refresh=True)
    client.remote_client.wakeup.assert_called_once_with("VIN")


def test_fake_gateway_effect_and_failures():
    fake = FakeGateway(apply_effect=True)
    received = []
    fake.subscribe(received.append)
    fake.fail_next = 1
    assert fake.set_charge_mode("charge", (1, 0)) is False
    assert fake.snapshot().charging is False
    assert fake.set_charge_mode("charge", (1, 0)) is True
    assert fake.snapshot().charging is True
    assert fake.calls == [("set_charge_mode", "charge", (1, 0))] * 2
    fake.emit(dataclasses.replace(fake.snap, soc=80.0))
    assert received[0].soc == 80.0 and fake.snapshot().soc == 80.0


def _mqtt_gateway():
    client = MagicMock()
    client.get_vehicle_info.return_value = load("status_v4_17.json")  # 61.5 %, plugged, InProgress at 10:15:30
    client.remote_client.lock_state = {}
    client.remote_client.lock_state_at = {}
    client.remote_client.on_event = []
    gateway = PsaGateway(client, "VIN")
    received = []
    gateway.subscribe(received.append)
    return gateway, client, received


def test_mqtt_event_merges_into_last_snapshot():
    gateway, client, received = _mqtt_gateway()
    on_event = client.remote_client.on_event[0]
    on_event({"vin": "VIN", "date": "2024-08-01T11:00:00Z", "charging_state": {"soc_batt": 70, "autonomy_zev": 150}})
    assert received == []  # nothing to merge into before the first REST snapshot
    rest = gateway.snapshot()
    on_event({"vin": "OTHER", "date": "2024-08-01T11:00:00Z", "charging_state": {"soc_batt": 70}})
    on_event({"vin": "VIN", "date": "2024-08-01T10:00:00Z", "charging_state": {"soc_batt": 70}})  # older than REST
    on_event({"vin": "VIN", "date": "2024-08-01T11:00:00Z", "charging_state": {"rate": 0}})  # nothing usable
    assert received == []
    on_event({"vin": "VIN", "date": "2024-08-01T11:00:00Z", "charging_state": {"soc_batt": 70, "autonomy_zev": 150}})
    assert received[-1] == dataclasses.replace(rest, at=datetime(2024, 8, 1, 11, tzinfo=timezone.utc), soc=70.0,
                                               range_km=150)
    on_event({"vin": "VIN", "date": "2024-08-01T11:05:00Z", "charging_state": {"cable_detected": 0}})
    unplugged = received[-1]
    assert (unplugged.soc, unplugged.plugged, unplugged.charging, unplugged.charge_status) == (
        70.0, False, False, "Disconnected")


def test_mqtt_event_subscriber_errors_are_contained():
    gateway, client, received = _mqtt_gateway()
    gateway.subscribe(lambda snap: 1 / 0)
    gateway.snapshot()
    client.remote_client.on_event[0]({"vin": "VIN", "date": "2024-08-01T11:00:00Z",
                                      "charging_state": {"soc_batt": 70}})
    assert received[-1].soc == 70.0


def test_auth_ok_reflects_token_refresh_failures():
    client = MagicMock()
    client.manager.auth_failed = False
    client.remote_client.auth_failed = False
    gateway = PsaGateway(client, "VIN")
    assert gateway.auth_ok() is True
    client.remote_client.auth_failed = True
    assert gateway.auth_ok() is False
    client.remote_client.auth_failed = False
    client.manager.auth_failed = True
    assert gateway.auth_ok() is False


def test_schedule_as_list_does_not_reject_status():
    """Real e-208 payload: `charging.schedule` is `[]`, which used to reject the whole VehicleStatus."""
    snap = snapshot_from_status("VIN", load("status_schedule_list.json"))
    assert (snap.soc, snap.plugged, snap.charging, snap.charge_status) == (61.5, True, True, "InProgress")
    assert snap.next_delayed_time == "PT22H30M"


def test_snapshot_through_rest_parses_schedule_list():
    from psacc.psa.rest import PsaRest
    rest = PsaRest(MagicMock(), "cid", "realm")
    rest.get = MagicMock(return_value=json.loads((DATA / "status_schedule_list.json").read_text(encoding="utf-8")))
    assert rest.status("vid").electric().charging_info().plugged is True


def test_wrong_typed_leaves_become_none_without_rejecting_status():
    raw = json.loads((DATA / "status_v4_17.json").read_text(encoding="utf-8"))
    charging = raw["energies"][0]["extension"]["electric"]["charging"]
    charging.update(chargingRate="fast", plugged={"x": 1}, schedule="weird", nextDelayedTime=12)
    raw["doorsState"] = ["Locked"]
    raw["lastPosition"] = {"geometry": {"coordinates": "nowhere"}}
    raw["preconditioning"] = {"airConditioning": 3}
    raw["updatedAt"] = 5
    raw["energies"].insert(0, "garbage")
    snap = snapshot_from_status("VIN", VehicleStatus.model_validate(raw))
    assert (snap.soc, snap.charge_status, snap.charging) == (61.5, "InProgress", True)
    assert snap.plugged is None and snap.charge_rate_kmh is None and snap.next_delayed_time is None
    assert snap.locked is None and snap.latitude is None and snap.precond_on is None
    assert snap.energy_at == datetime(2024, 8, 1, 10, 15, 30, tzinfo=timezone.utc)


def test_wrong_typed_soc_becomes_none():
    raw = {"energies": [{"type": "Electric", "level": "n/a", "autonomy": [], "extension": {"electric": "x"}}]}
    snap = snapshot_from_status("VIN", VehicleStatus.model_validate(raw))
    assert snap.soc is None and snap.range_km is None and snap.plugged is None
