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
