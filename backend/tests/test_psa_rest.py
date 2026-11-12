from unittest.mock import MagicMock

from psacc.psa.rest import PsaRest


def response(status_code, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def make_rest(*responses):
    manager = MagicMock()
    manager.access_token = "tok"
    session = MagicMock()
    session.get.side_effect = list(responses)
    return PsaRest(manager, "cid", "realm", session=session), manager, session


def test_rest_refreshes_once_on_401():
    rest, manager, session = make_rest(response(401), response(200, {"ok": 1}))
    assert rest.get("/user/vehicles", x="y") == {"ok": 1}
    manager.refresh_token_now.assert_called_once_with()
    assert session.get.call_count == 2
    _, kwargs = session.get.call_args
    assert kwargs["params"] == {"client_id": "cid", "x": "y"}
    assert kwargs["headers"] == {"x-introspect-realm": "realm", "Authorization": "Bearer tok",
                                 "Accept": "application/hal+json"}
    assert kwargs["timeout"] == 20


def test_rest_does_not_retry_twice():
    rest, manager, session = make_rest(response(401), response(401))
    rest.get("/x")
    manager.refresh_token_now.assert_called_once_with()
    assert session.get.call_count == 2


def test_vehicles_and_status():
    rest, _, _ = make_rest(
        response(200, {"_embedded": {"vehicles": [{"id": "vid", "vin": "VIN", "brand": "Peugeot", "label": "e-208",
                                                   "extra": 1}]}}),
        response(200, {"energies": [{"type": "Electric", "level": 40}]}))
    ref = rest.vehicles()[0]
    assert (ref.id, ref.vin, ref.brand, ref.label) == ("vid", "VIN", "Peugeot", "e-208")
    assert rest.status("vid").electric().level == 40
