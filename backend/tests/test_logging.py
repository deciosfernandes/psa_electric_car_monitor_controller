"""Tokens never reach the logs; MQTT payloads only at DEBUG; the app configures root logging."""
import logging
from unittest.mock import MagicMock

from psacc.psa.RemoteClient import RemoteCredentials
from tests.utils import get_rc

SECRET_ACCESS = "secret-remote-access-token"
SECRET_REFRESH = "secret-remote-refresh-token"


def _client(response: dict):
    rc = get_rc()
    rc.manager = MagicMock()
    rc.manager.refresh_token_now.return_value = True
    res = MagicMock()
    res.json.return_value = response
    rc.manager.post.return_value = res
    rc.remoteCredentials = RemoteCredentials("old-refresh")
    rc.mqtt_client = MagicMock()
    rc.account_info.get_mqtt_customer_id.return_value = "AP-1"
    return rc


def test_tokens_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    rc = _client({"access_token": SECRET_ACCESS, "refresh_token": SECRET_REFRESH})
    assert rc._refresh_remote_token(force=True)
    rc.veh_charge_request("VIN", 0, 10, "delayed")
    rc2 = _client({"error": "invalid_grant", "refresh_token": SECRET_REFRESH})
    rc2._refresh_remote_token(force=True)
    assert SECRET_ACCESS not in caplog.text and SECRET_REFRESH not in caplog.text


def test_mqtt_payload_logged_at_debug_only(caplog):
    caplog.set_level(logging.INFO)
    rc = get_rc()
    msg = MagicMock(topic="psa/RemoteServices/events/MPHRTServices/VIN", payload=b'{"vin": "VIN", "x": 1}')
    rc._on_mqtt_message(None, None, msg)
    assert '"x": 1' not in caplog.text


def test_configure_logging_sets_root_info():
    from psacc.main import configure_logging

    root = logging.getLogger()
    saved = root.level, list(root.handlers)
    try:
        root.handlers.clear()
        configure_logging()
        assert root.level == logging.INFO
        assert root.handlers
    finally:
        root.setLevel(saved[0])
        root.handlers[:] = saved[1]
