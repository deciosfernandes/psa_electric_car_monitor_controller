from unittest.mock import MagicMock

import pytest

from psacc import settings, setup_flow, store
from psacc.psa.setup.headless_oauth import HeadlessOAuthError


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(setup_flow, "_pending", None)
    store.init_db("sqlite://")


def fake_setup(monkeypatch):
    client = MagicMock()
    client.to_dict.return_value = {"refresh_token": "r", "vehicles": []}
    client.get_vehicles.return_value = []
    car = MagicMock(vin="VF3TEST")
    client.vehicles_list = [car]
    setup = MagicMock()
    setup.psacc.manager.generate_redirect_url.return_value = "https://auth.example/authorize"
    setup.psacc.manager.redirect_uri = "mymap://oauth2redirect/pt"
    setup.connect.return_value = client
    monkeypatch.setattr(setup_flow, "InitialSetup", MagicMock(return_value=setup))
    return setup


def test_start_login_headless_success_saves_psa(monkeypatch):
    setup = fake_setup(monkeypatch)
    headless = MagicMock(return_value="CODE123")
    monkeypatch.setattr(setup_flow, "get_oauth_code_headless", headless)
    result = setup_flow.start_login("com.psa.mym.mypeugeot", "a@b.pt", "pw", "PT")
    assert result == setup_flow.LoginResult(ok=True, manual_url=None, error=None)
    headless.assert_called_once_with("https://auth.example/authorize", "a@b.pt", "pw", "mymap")
    setup.connect.assert_called_once_with("CODE123")
    assert store.get_psa() == {"refresh_token": "r", "vehicles": []}
    assert store.get_config().vin == "VF3TEST"


def test_start_login_headless_failure_returns_manual_url(monkeypatch):
    setup = fake_setup(monkeypatch)
    monkeypatch.setattr(setup_flow, "get_oauth_code_headless",
                        MagicMock(side_effect=HeadlessOAuthError("boom", "u", "<html>", [])))
    result = setup_flow.start_login("com.psa.mym.mypeugeot", "a@b.pt", "pw", "PT")
    assert result.ok is False
    assert result.manual_url == "https://auth.example/authorize"
    assert "HeadlessOAuthError" in result.error
    assert "boom" not in result.error
    assert store.get_psa() is None
    # the user finishes manually with the code from the redirect
    monkeypatch.setattr(setup_flow, "_pending", setup)
    setup_flow.finish_login("MANUAL")
    setup.connect.assert_called_once_with("MANUAL")
    assert store.get_psa() is not None


def test_finish_login_without_start_raises():
    with pytest.raises(RuntimeError):
        setup_flow.finish_login("x")


def test_load_gateway_none_without_config():
    assert setup_flow.load_gateway() is None
    store.save_psa({"refresh_token": "r"})  # psa but no vin
    assert setup_flow.load_gateway() is None


def test_request_sms_on_stored_client_refreshes_access_token_first(monkeypatch):
    """R8: a client rebuilt from the stored dict has a refresh token but no access token (no session)."""
    from psacc.psa.oauth import OpenIdCredentialManager

    store.save_psa({"refresh_token": "stored-refresh", "client_id": "cid", "client_secret": "cs",
                    "remote_refresh_token": None, "customer_id": "AP-1", "realm": "clientsB2CPeugeot",
                    "country_code": "PT", "brand": None, "vehicles": []})
    refreshes, posts = [], []

    def fake_refresh(self):  # stands in for the token endpoint: never contact PSA
        refreshes.append(self.refresh_token)
        self._access_token = "fresh-access"

    def fake_post(self, url, **kwargs):
        posts.append((url, self.headers.get("Authorization")))
        return MagicMock(ok=True, status_code=202)

    monkeypatch.setattr(OpenIdCredentialManager, "_refresh_token", fake_refresh)
    monkeypatch.setattr("requests.Session.post", fake_post)
    setup_flow.request_sms()
    assert refreshes == ["stored-refresh"]
    assert len(posts) == 1 and "smsCode" in posts[0][0] and posts[0][1] == "Bearer fresh-access"


def test_request_sms_without_access_token_and_failed_refresh_raises(monkeypatch):
    from psacc.psa.oauth import OpenIdCredentialManager
    from psacc.psa.RemoteClient import RemoteException

    store.save_psa({"refresh_token": "stored-refresh", "client_id": "cid", "client_secret": "cs",
                    "remote_refresh_token": None, "customer_id": "AP-1", "realm": "clientsB2CPeugeot",
                    "country_code": "PT", "brand": None, "vehicles": []})
    monkeypatch.setattr(OpenIdCredentialManager, "refresh_token_now", lambda self: False)
    post = MagicMock()
    monkeypatch.setattr("requests.Session.post", post)
    with pytest.raises(RemoteException, match="no PSA access token"):
        setup_flow.request_sms()
    post.assert_not_called()
