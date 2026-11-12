def test_core_modules_import():
    import psacc.psa.RemoteClient, psacc.psa.otp.otp, psacc.psa.oauth, psacc.psa_client, psacc.psa.setup.app_decoder  # noqa


def test_no_legacy_imports():
    import pathlib
    src = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path("psacc").rglob("*.py"))
    assert "psa_car_controller" not in src
    assert "abrp" not in src.lower() and "ecomix" not in src.lower()
