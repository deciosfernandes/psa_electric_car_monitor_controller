"""Absolute locations under the data dir for the downloaded APK and the extracted client certificate."""
import os
from pathlib import Path

from psacc import settings


def apk_dir() -> Path:
    path = settings.DATA_DIR.resolve() / "apk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def certs_dir() -> Path:
    path = settings.DATA_DIR.resolve() / "certs"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def cert_paths() -> tuple[str, str]:
    """(public.pem, private.pem) as absolute paths, the form `requests` takes for a client cert."""
    certs = certs_dir()
    return str(certs / "public.pem"), str(certs / "private.pem")
