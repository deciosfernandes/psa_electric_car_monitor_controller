"""RemoteClient MQTT lifecycle: asynchronous connect at startup (F3) and request publishing (F4)."""
import json
import threading
from datetime import timedelta
from unittest import mock
from unittest.mock import MagicMock

import pytest

from psacc.car import Car, Cars
from psacc.psa import RemoteClient as rc_module
from psacc.psa.RemoteClient import MQTT_RESP_TOPIC
from tests.test_refresh_remote_token import get_remote_client


class FakePaho:
    """paho Client stand-in: CONNACK arrives on another thread `connack_after` s after loop_start (None: never)."""

    def __init__(self, connack_after: float | None):
        self.connack_after = connack_after
        self.connected = False
        self.published: list[tuple[str, dict]] = []
        self.events: list[str] = []
        self.on_connect = self.on_message = self.on_disconnect = None

    def enable_logger(self, logger=None):
        pass

    def username_pw_set(self, *_):
        pass

    def tls_set_context(self):
        pass

    def connect(self, *_):
        self.events.append("connect")

    def loop_start(self):
        if self.connack_after is not None:
            threading.Timer(self.connack_after, self._connack).start()

    def _connack(self):
        self.connected = True
        self.events.append("connack")
        self.on_connect(self, None, {}, 0)

    def is_connected(self):
        return self.connected

    def subscribe(self, topic):
        pass

    def publish(self, topic, payload):
        self.events.append("publish")
        self.published.append((topic, json.loads(payload)))

    def disconnect(self):
        pass


def start_with(paho: FakePaho, monkeypatch, timeout_s: float = 2.0) -> tuple[bool, rc_module.RemoteClient]:
    remote = get_remote_client()
    remote.account_info.get_mqtt_customer_id.return_value = "CID"
    remote.vehicles_list = Cars([Car("VIN", "vid", "Peugeot")])
    monkeypatch.setattr(rc_module, "CONNECT_TIMEOUT_S", timeout_s)
    monkeypatch.setattr(remote, "load_otp", lambda: True)
    # the real wakeup is rate limited process-wide (6 per 20 min): publish the same request without the limiter
    monkeypatch.setattr(remote, "wakeup", lambda vin: remote.publish(
        remote.mqtt_request(vin, {"action": "state"}, "/VehCharge/state")))
    with mock.patch.object(rc_module.mqtt, "Client", return_value=paho):
        started = remote.start()
    remote.update_thread.cancel()
    return started, remote


def test_start_waits_for_asynchronous_connack(monkeypatch):
    started, _ = start_with(FakePaho(connack_after=0.2), monkeypatch)
    assert started is True


def test_keepalive_wakeup_is_published_after_connack(monkeypatch):
    paho = FakePaho(connack_after=0.2)
    start_with(paho, monkeypatch)
    assert paho.events == ["connect", "connack", "publish"]


def test_start_reports_failure_when_broker_never_answers(monkeypatch):
    started, _ = start_with(FakePaho(connack_after=None), monkeypatch, timeout_s=0.2)
    assert started is False


def test_connect_timeout_is_bounded():
    assert 5 <= rc_module.CONNECT_TIMEOUT_S <= 15


def started_remote():
    remote = get_remote_client()
    remote.account_info.get_mqtt_customer_id.return_value = "CID"
    remote.mqtt_client = FakePaho(connack_after=None)
    return remote


def respond(remote, **data) -> None:
    msg = MagicMock()
    msg.topic = MQTT_RESP_TOPIC + "CID/VehCharge"
    msg.payload = json.dumps(data).encode()
    remote._on_mqtt_message(None, None, msg)


def correlation_ids(remote) -> list[str]:
    return [payload["correlation_id"] for _, payload in remote.mqtt_client.published]


def test_every_publish_has_a_unique_correlation_id():
    remote = started_remote()
    for _ in range(20):  # many requests within the same millisecond
        remote.veh_charge_request("VIN", 0, 10, "delayed")
    remote.lights("VIN", 10)
    ids = correlation_ids(remote)
    assert len(ids) == 21 and len(set(ids)) == 21


def test_expired_token_response_resends_last_request_once_with_a_new_id():
    remote = started_remote()
    remote.veh_charge_request("VIN", 0, 10, "delayed")
    first = correlation_ids(remote)[0]
    respond(remote, return_code="400", correlation_id=first)
    respond(remote, return_code="400", correlation_id=first)  # a second 400 for the same request: no resend
    ids = correlation_ids(remote)
    assert len(ids) == 2 and ids[1] != first
    assert remote.mqtt_client.published[1][1]["req_parameters"] == remote.mqtt_client.published[0][1]["req_parameters"]


def test_400_for_another_request_does_not_resend_last_request():
    remote = started_remote()
    remote.veh_charge_request("VIN", 0, 10, "delayed")
    respond(remote, return_code="400", correlation_id="someone-else")
    assert len(correlation_ids(remote)) == 1


def test_answered_request_is_never_resent():
    remote = started_remote()
    remote.veh_charge_request("VIN", 0, 10, "delayed")
    first = correlation_ids(remote)[0]
    respond(remote, return_code="0", correlation_id=first)
    respond(remote, return_code="400")  # a later 400 without correlation id must not replay an answered request
    assert len(correlation_ids(remote)) == 1


def test_stale_last_request_is_not_resent():
    remote = started_remote()
    remote.veh_charge_request("VIN", 0, 10, "delayed")
    remote.last_request.date -= timedelta(minutes=5)
    respond(remote, return_code="400", correlation_id=correlation_ids(remote)[0])
    assert len(correlation_ids(remote)) == 1


@pytest.mark.parametrize("code", ["502", "500"])
def test_error_response_logs_its_correlation_id_and_clears_last_request(code, caplog):
    remote = started_remote()
    remote.veh_charge_request("VIN", 0, 10, "delayed")
    first = correlation_ids(remote)[0]
    respond(remote, return_code=code, reason="[duplicate.message.key]", correlation_id=first)
    assert first in caplog.text and remote.last_request is None
