"""PSA login / OTP setup steps shared by the CLI spike and the API."""
import logging
from dataclasses import dataclass

from psacc import store
from psacc.gateway import PsaGateway
from psacc.psa.otp.otp import load_otp, new_otp_session
from psacc.psa.setup.app_decoder import InitialSetup
from psacc.psa.setup.headless_oauth import HeadlessOAuthError, get_oauth_code_headless
from psacc.psa_client import PSAClient

logger = logging.getLogger(__name__)

_pending: InitialSetup | None = None  # login started, waiting for the OAuth code


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    manual_url: str | None = None
    error: str | None = None


def start_login(app_name: str, email: str, password: str, country: str) -> LoginResult:
    """Try the headless OAuth login; on failure hand back the URL for the user to finish by hand."""
    global _pending
    try:
        _pending = InitialSetup(app_name, email, password, country)
    except Exception as ex:  # message may embed response bodies, keep only the type
        logger.error("InitialSetup failed: %s", type(ex).__name__)
        return LoginResult(ok=False, error=f"login setup failed ({type(ex).__name__})")
    manager = _pending.psacc.manager
    auth_url = manager.generate_redirect_url()
    scheme = manager.redirect_uri.split("://")[0]
    try:
        code = get_oauth_code_headless(auth_url, email, password, scheme)
        finish_login(code)
    except HeadlessOAuthError as ex:
        logger.warning("headless oauth failed: %s", ex)
        return LoginResult(ok=False, manual_url=auth_url, error=str(ex))
    except Exception as ex:
        logger.warning("headless oauth failed: %s", type(ex).__name__)
        return LoginResult(ok=False, manual_url=auth_url, error=f"{type(ex).__name__}: {ex}")
    return LoginResult(ok=True)


def finish_login(code: str) -> None:
    global _pending
    if _pending is None:
        raise RuntimeError("no login in progress")
    client = _pending.connect(code)
    store.save_psa(client.to_dict())
    cfg = store.get_config()
    cfg.vin = client.vehicles_list[0].vin
    store.save_config(cfg)
    _pending = None


def _stored_client() -> PSAClient:
    data = store.get_psa()
    if data is None:
        raise RuntimeError("PSA account not configured")
    return PSAClient.from_dict(data)


def request_sms() -> None:
    _stored_client().remote_client.get_sms_otp_code()


def activate_otp(sms_code: str, pin: str) -> None:
    new_otp_session(sms_code, pin, load_otp())


def load_gateway() -> PsaGateway | None:
    data, cfg = store.get_psa(), store.get_config()
    if data is None or not cfg.vin:
        return None
    client = PSAClient.from_dict(data)
    if not client.remote_client.start():
        logger.warning("MQTT did not connect at startup")
    return PsaGateway(client, cfg.vin)
