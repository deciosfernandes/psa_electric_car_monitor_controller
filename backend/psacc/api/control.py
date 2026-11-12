import logging
from typing import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import store
from ..automation.engine import Engine
from ..gateway import CarGateway
from . import require_engine, require_gateway

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/control")


class PreconditionIn(BaseModel):
    on: bool


class LockIn(BaseModel):
    locked: bool


def _result(ok: bool, message: str) -> dict:
    return {"ok": ok, "message": message}


def _command(name: str, done: str, send: Callable[[], bool]) -> dict:
    """Send one car command; the outcome is logged in the activity log."""
    try:
        ok = bool(send())
    except Exception as exc:
        logger.warning("%s command failed: %s", name, type(exc).__name__)
        ok = False
    message = done if ok else f"O comando falhou: {name}"
    store.log_activity("action" if ok else "error", message)
    return _result(ok, message)


@router.post("/charge-now")
def charge_now(engine: Engine = Depends(require_engine)) -> dict:
    before = store.get_last_command()
    engine.manual_charge()
    if store.get_override() is not None:
        return _result(True, "Carga manual iniciada")
    if store.get_last_command() != before:  # automation off: the engine sent "charge" without an override
        return _result(True, "Carga pedida (automação desligada)")
    return _result(False, "Carga manual não iniciada (ver atividade)")  # e.g. cable unplugged


@router.post("/cancel-override")
def cancel_override(engine: Engine = Depends(require_engine)) -> dict:
    engine.cancel_override()
    return _result(True, "Carga manual cancelada")


@router.post("/precondition")
def precondition(body: PreconditionIn, gateway: CarGateway = Depends(require_gateway)) -> dict:
    done = "Pré-climatização ligada" if body.on else "Pré-climatização desligada"
    return _command("pré-climatização", done, lambda: gateway.precondition(body.on))


@router.post("/lock")
def lock(body: LockIn, gateway: CarGateway = Depends(require_gateway)) -> dict:
    done = "Carro trancado" if body.locked else "Carro destrancado"
    return _command("trancar" if body.locked else "destrancar", done, lambda: gateway.lock(body.locked))


@router.post("/horn")
def horn(gateway: CarGateway = Depends(require_gateway)) -> dict:
    return _command("buzina", "Buzina acionada", gateway.horn)


@router.post("/lights")
def lights(gateway: CarGateway = Depends(require_gateway)) -> dict:
    return _command("luzes", "Luzes acionadas", gateway.lights)


@router.post("/wakeup")
def wakeup(gateway: CarGateway = Depends(require_gateway)) -> dict:
    return _command("acordar o carro", "Carro acordado", lambda: gateway.snapshot(refresh=True) is not None)
