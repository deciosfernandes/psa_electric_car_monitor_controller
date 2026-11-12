"""HTTP API: shared helpers (JSON encoding, pt-PT validation errors) and the car runtime wiring.

The car runtime lives on ``app.state``: ``gateway`` (None while no PSA account is configured), ``engine``,
``poller`` and the shared ``notifier``. ``start_car``/``stop_car`` (re)build and tear it down.
"""

import dataclasses
import logging
import threading
from datetime import date, datetime
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError

from ..automation.engine import Engine
from ..gateway import CarGateway, PsaGateway
from ..poller import Poller, psa_token_saver
from ..settings import TZ

logger = logging.getLogger(__name__)

NO_PSA = "Conta PSA não configurada"

GatewayFactory = Callable[[], CarGateway | None]

_FIELD_LABELS = {
    "enabled": "Automação",
    "offset_min": "Desvio",
    "battery_limit": "Limite de bateria",
    "password": "Palavra-passe",
    "refresh_min": "Intervalo de atualização",
    "discord_webhook": "Webhook do Discord",
    "name": "Nome",
    "seasons": "Estações",
    "months": "Meses",
    "days": "Dias",
    "start": "Início",
    "end": "Fim",
    "period": "Período",
    "prices": "Preços",
    "eur_kwh": "Preço (EUR/kWh)",
    "valid_from": "Data de início",
}


# ---- JSON ---------------------------------------------------------------------------------------------


def to_json(obj: Any) -> Any:
    """JSON-ready value; datetimes become ISO-8601 in the display timezone (always with an offset)."""
    if isinstance(obj, datetime):
        return obj.astimezone(TZ).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, BaseModel):
        return to_json(obj.model_dump())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_json(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    return obj


# ---- validation errors in pt-PT -----------------------------------------------------------------------


def _error_text(err: dict) -> str:
    ctx = err.get("ctx") or {}
    kind = err.get("type", "")
    if kind == "value_error" and "error" in ctx:
        return str(ctx["error"])
    field = next((str(p) for p in reversed(err.get("loc", ())) if isinstance(p, str) and p != "body"), "")
    label = _FIELD_LABELS.get(field, field or "Pedido")
    if kind == "missing":
        return f"{label}: valor em falta"
    if kind in ("greater_than_equal", "greater_than"):
        return f"{label}: tem de ser pelo menos {ctx.get('ge', ctx.get('gt'))}"
    if kind in ("less_than_equal", "less_than"):
        return f"{label}: tem de ser no máximo {ctx.get('le', ctx.get('lt'))}"
    if kind.startswith("int_"):
        return f"{label}: tem de ser um número inteiro"
    if kind.startswith("float_"):
        return f"{label}: tem de ser um número"
    if kind.startswith("bool_"):
        return f"{label}: tem de ser verdadeiro ou falso"
    if kind == "string_too_short":
        return f"{label}: demasiado curto (mínimo {ctx.get('min_length')} caracteres)"
    if kind == "json_invalid":
        return "Pedido inválido: JSON mal formado"
    return f"{label}: valor inválido"


def pt_detail(errors: list[dict]) -> str:
    """One pt-PT sentence for a list of pydantic errors (``ValidationError.errors()``)."""
    return "; ".join(dict.fromkeys(_error_text(e) for e in errors)) or "Pedido inválido"


def unprocessable(exc: ValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=pt_detail(exc.errors()))


# ---- car runtime ----------------------------------------------------------------------------------------


def _close_gateway(gateway: CarGateway | None) -> None:
    if isinstance(gateway, PsaGateway):
        try:
            gateway.client.remote_client.stop()  # MQTT of a replaced client must not keep running
        except Exception:
            logger.exception("could not stop MQTT")


def stop_car(app: FastAPI) -> None:
    state = app.state
    if state.poller is not None:
        state.poller.stop()
    if state.engine is not None:
        state.engine.close()
    _close_gateway(state.gateway)
    state.gateway = state.engine = state.poller = None


def start_car(app: FastAPI) -> None:
    """(Re)build gateway, engine and poller from the stored PSA account; a fresh PSAClient resets auth flags."""
    state = app.state
    with state.car_lock:
        stop_car(app)
        try:
            gateway = state.gateway_factory()
        except Exception:
            logger.exception("could not load the PSA gateway")
            gateway = None
        if gateway is None:
            logger.info("no PSA account configured, car features disabled")
            return
        save_tokens = psa_token_saver(gateway.client) if isinstance(gateway, PsaGateway) else None
        engine = Engine(gateway, state.notifier)
        poller = Poller(gateway, engine, state.notifier, save_tokens=save_tokens)
        state.gateway, state.engine, state.poller = gateway, engine, poller
        poller.start()


def init_state(app: FastAPI, gateway_factory: GatewayFactory, notifier) -> None:
    app.state.gateway_factory = gateway_factory
    app.state.notifier = notifier
    app.state.car_lock = threading.Lock()
    app.state.gateway = app.state.engine = app.state.poller = None


def require_gateway(request: Request) -> CarGateway:
    gateway = request.app.state.gateway
    if gateway is None:
        raise HTTPException(status_code=503, detail=NO_PSA)
    return gateway


def require_engine(request: Request) -> Engine:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(status_code=503, detail=NO_PSA)
    return engine
