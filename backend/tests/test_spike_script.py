"""F2: the spike never aborts on a failed read, and only probes the timezone with a fresh REST plugged=True."""
import dataclasses
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from psacc.spike_report import NOT_PLUGGED, plug_precheck
from tests.fakes import FakeGateway

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spike_deferred.py"
MQTT_EVENT = json.loads((Path(__file__).parent / "data" / "mqtt_event_e208.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 12, 13, 40, tzinfo=UTC)


@pytest.fixture
def spike(monkeypatch):
    spec = importlib.util.spec_from_file_location("spike_deferred", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "wait", lambda seconds: None)
    return module


def gateway(**snap) -> MagicMock:
    gw = MagicMock()
    gw.snapshot.return_value = dataclasses.replace(FakeGateway().snap, energy_at=datetime.now(UTC), **snap)
    return gw


def snap(plugged, energy_at=NOW):
    return dataclasses.replace(FakeGateway().snap, plugged=plugged, energy_at=energy_at)


def test_plug_precheck_needs_a_fresh_rest_plugged_true():
    window = NOW - timedelta(seconds=30)
    assert plug_precheck(snap(True), window) == (True, None)
    assert plug_precheck(snap(False), window) == (False, NOT_PLUGGED)
    assert NOT_PLUGGED == "carro não está ligado à tomada"
    for stale_or_unknown in (snap(True, energy_at=NOW - timedelta(hours=1)), snap(True, energy_at=None),
                             snap(None), None):
        ok, reason = plug_precheck(stale_or_unknown, window)
        assert not ok and reason != NOT_PLUGGED


def test_show_records_a_failed_read_and_never_raises(spike):
    gw = gateway()
    gw.snapshot.side_effect = RuntimeError("PSA status unavailable")
    report = {"reads": []}
    spike.show(gw, "start", report)
    assert report["reads"] == [{"label": "start", "error": "RuntimeError: PSA status unavailable"}]


def test_show_records_rest_plugged_next_to_mqtt_cable_detected(spike):
    spike.last_mqtt["charging_state"] = MQTT_EVENT["charging_state"]
    report = {"reads": []}
    spike.show(gateway(plugged=True), "start", report)
    entry = report["reads"][0]
    assert entry["plugged"] is True and entry["mqtt_charging_state"]["cable_detected"] == 0


def test_raw_read_records_a_failure_and_never_raises(spike):
    gw = gateway()
    gw.client.rest.get.side_effect = ValueError("bad json")
    entry = spike.raw_read(gw, "before defer")
    assert entry == {"label": "before defer", "error": "ValueError: bad json"}


def test_observe_records_a_failed_read(spike):
    gw = gateway()
    gw.snapshot.side_effect = RuntimeError("PSA status unavailable")
    obs, entry = spike.observe(gw, wake=False)
    assert not obs.fresh and entry["error"].startswith("RuntimeError")


def test_unplugged_car_skips_the_timezone_probes(spike, capsys):
    gw = gateway(plugged=False)
    spike.last_mqtt["charging_state"] = MQTT_EVENT["charging_state"]
    test = {"baseline": [], "probes": []}
    assert spike.plug_check(gw, test) is False
    assert (test["verdict"], test["reason"]) == ("inconclusive", NOT_PLUGGED)
    assert test["plug_check"]["plugged"] is False and test["plug_check"]["mqtt_charging_state"]["cable_detected"] == 0
    gw.set_charge_mode.assert_not_called()
    assert "ligue o carro" in capsys.readouterr().out.lower()


def test_mqtt_cable_detected_0_does_not_block_a_rest_plugged_car(spike):
    spike.last_mqtt["charging_state"] = MQTT_EVENT["charging_state"]  # cable_detected = 0, as on the real car
    test = {"baseline": [], "probes": []}
    assert spike.plug_check(gateway(plugged=True), test) is True
    assert "verdict" not in test


def test_failed_plug_read_is_inconclusive_not_a_crash(spike):
    gw = gateway()
    gw.snapshot.side_effect = RuntimeError("PSA status unavailable")
    test = {"baseline": [], "probes": []}
    assert spike.plug_check(gw, test) is False
    assert test["verdict"] == "inconclusive" and test["reason"] != NOT_PLUGGED
