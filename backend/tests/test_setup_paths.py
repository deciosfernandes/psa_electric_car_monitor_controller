"""App credential extraction writes the APK and the client certificate under the data dir, never the CWD."""
import bz2
import datetime
import os
import stat

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from psacc import settings
from psacc.psa.setup import apk_parser, app_decoder, github, paths


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.chdir(tmp_path)  # anything written to the CWD would land here
    return tmp_path / "data"


def _pfx(password: bytes) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"t", key, cert, None,
                                                 serialization.BestAvailableEncryption(password))


def test_certs_written_under_data_dir(data_dir, tmp_path):
    apk_parser.save_key_to_pem(_pfx(b"pw"), b"pw")
    public, private = paths.cert_paths()
    assert public == str((data_dir / "certs" / "public.pem").resolve())
    assert os.path.isabs(public) and os.path.isabs(private)
    assert b"BEGIN CERTIFICATE" in open(public, "rb").read()
    assert b"PRIVATE KEY" in open(private, "rb").read()
    assert not (tmp_path / "certs").exists()
    if os.name == "posix":
        assert stat.S_IMODE((data_dir / "certs").stat().st_mode) == 0o700
        assert stat.S_IMODE(os.stat(private).st_mode) == 0o600


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield self.body


def test_apk_downloaded_under_data_dir(data_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(github, "get_github_sha_from_file", lambda *a: "different")
    urls = []
    monkeypatch.setattr(github.requests, "get", lambda url, **kw: urls.append(url) or _Resp(bz2.compress(b"APK")))
    local = github.urlretrieve_from_github("u", "r", "", "mypeugeot.apk", dest_dir=paths.apk_dir())
    assert urls == ["https://github.com/u/r/raw/main/mypeugeot.apk.bz2"]
    assert local == data_dir.resolve() / "apk" / "mypeugeot.apk"
    assert local.read_bytes() == b"APK"
    assert not (tmp_path / "mypeugeot.apk").exists() and not (tmp_path / "mypeugeot.apk.bz2").exists()


def test_get_content_from_apk_uses_absolute_path(data_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr(app_decoder, "urlretrieve_from_github",
                        lambda user, repo, directory, filename, dest_dir: dest_dir / filename)

    class FakeParser:
        def __init__(self, filename, country):
            seen["filename"] = filename

        def retrieve_content_from_apk(self):
            pass

    monkeypatch.setattr(app_decoder, "ApkParser", FakeParser)
    app_decoder.get_content_from_apk("mypeugeot.apk", "PT")
    assert seen["filename"] == str(data_dir.resolve() / "apk" / "mypeugeot.apk")
