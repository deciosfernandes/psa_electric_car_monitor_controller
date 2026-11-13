from oauth2_client.credentials_manager import ServiceInformation

from psacc.psa.oauth import OpenIdCredentialManager


def _manager(refresh_token="r0"):
    m = OpenIdCredentialManager(ServiceInformation("https://a", "https://t", "cid", "cs", ["s"], True))
    m.refresh_token = refresh_token
    return m


def _fake_token_endpoint(m, response):
    m._token_request = lambda params, mandatory: m._process_token_response(dict(response), mandatory)


def test_silent_refresh_from_bearer_request_is_persisted():
    # the library refreshes on its own when a bearer request gets 401 "Token is invalid":
    # the rotated refresh token must reach the store like refresh_token_now()'s
    m = _manager()
    saved = []
    m.refresh_callbacks.append(lambda: saved.append(m.refresh_token))
    _fake_token_endpoint(m, {"access_token": "a1", "refresh_token": "r1"})

    m._refresh_token()

    assert saved == ["r1"]


def test_refresh_token_now_persists_once():
    m = _manager()
    saved = []
    m.refresh_callbacks.append(lambda: saved.append(m.refresh_token))
    _fake_token_endpoint(m, {"access_token": "a1", "refresh_token": "r1"})

    assert m.refresh_token_now() is True
    assert saved == ["r1"]


def test_response_without_refresh_token_keeps_the_old_one():
    # the library would set refresh_token to None, and that None would be saved: login lost for good
    m = _manager("r0")
    _fake_token_endpoint(m, {"access_token": "a1"})

    m._refresh_token()

    assert m.refresh_token == "r0"
    assert m.access_token == "a1"
