"""PSA account setup (login, OAuth code, SMS, OTP) and the general settings."""

import dataclasses
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import setup_flow, store
from ..psa.constants import BRAND
from ..psa.otp.otp import otp_path
from . import NO_PSA, start_car

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    app_name: str
    email: str
    password: str
    country: str

    @field_validator("app_name")
    @classmethod
    def _known_app(cls, v: str) -> str:
        if v not in BRAND:
            raise ValueError("Aplicação desconhecida")
        return v


class CodeIn(BaseModel):
    code: str


class OtpIn(BaseModel):
    sms_code: str
    pin: str


class SettingsIn(BaseModel):
    discord_webhook: str | None = None  # None keeps the current one, "" removes it
    discord_info: bool
    refresh_min: int = Field(ge=1, le=60)

    @field_validator("discord_webhook")
    @classmethod
    def _https(cls, v: str | None) -> str | None:
        if v and not v.startswith("https://"):
            raise ValueError("O webhook do Discord tem de começar por https://")
        return v


# ---- PSA account ---------------------------------------------------------------------------------------


@router.post("/setup/login")
def setup_login(body: LoginIn, request: Request) -> dict:
    result = setup_flow.start_login(body.app_name, body.email, body.password, body.country)
    if result.ok:
        start_car(request.app)
    return dataclasses.asdict(result)


@router.post("/setup/code")
def setup_code(body: CodeIn, request: Request) -> dict:
    try:
        setup_flow.finish_login(body.code)
    except RuntimeError:
        raise HTTPException(409, "Não há nenhum login em curso; recomece o login") from None
    except Exception as exc:
        logger.warning("finish_login failed: %s", type(exc).__name__)  # message may embed response bodies
        raise HTTPException(400, f"Não foi possível concluir o login ({type(exc).__name__})") from None
    start_car(request.app)
    return {"ok": True}


@router.post("/setup/sms")
def setup_sms() -> dict:
    if store.get_psa() is None:
        raise HTTPException(503, NO_PSA)
    try:
        setup_flow.request_sms()
    except Exception as exc:
        logger.warning("request_sms failed: %s", type(exc).__name__)
        raise HTTPException(502, f"Não foi possível pedir o SMS ({type(exc).__name__})") from None
    return {"ok": True}


@router.post("/setup/otp")
def setup_otp(body: OtpIn, request: Request) -> dict:
    try:
        setup_flow.activate_otp(body.sms_code, body.pin)
    except Exception as exc:
        logger.warning("activate_otp failed: %s", type(exc).__name__)
        raise HTTPException(400, f"Não foi possível ativar o OTP ({type(exc).__name__})") from None
    start_car(request.app)  # new PSAClient: auth flags reset, MQTT restarted with the new OTP
    return {"ok": True}


@router.get("/setup/state")
def setup_state(request: Request) -> dict:
    gateway = request.app.state.gateway
    return {
        "psa_connected": gateway is not None and gateway.auth_ok(),
        "otp_ready": otp_path().exists(),
        "vin": store.get_config().vin,
    }


# ---- settings -----------------------------------------------------------------------------------------


def _settings(cfg: store.AppConfig) -> dict:
    return {
        "discord_webhook_set": bool(cfg.discord_webhook),
        "discord_info": cfg.discord_info,
        "refresh_min": cfg.refresh_min,
    }


@router.get("/settings")
def get_settings() -> dict:
    return _settings(store.get_config())


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict:
    cfg = store.get_config()
    if body.discord_webhook is not None:
        cfg.discord_webhook = body.discord_webhook or None
    cfg.discord_info = body.discord_info
    cfg.refresh_min = body.refresh_min
    store.save_config(cfg)
    return _settings(cfg)


@router.post("/settings/discord-test")
def discord_test(request: Request) -> dict:
    if not store.get_config().discord_webhook:
        return {"ok": False, "message": "Webhook do Discord não definido"}
    ok = request.app.state.discord.test()
    return {"ok": ok, "message": "Mensagem de teste enviada" if ok else "Falha ao enviar a mensagem de teste"}
