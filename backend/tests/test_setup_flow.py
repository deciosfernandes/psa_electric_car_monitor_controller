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
    assert "boom" in result.error
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
