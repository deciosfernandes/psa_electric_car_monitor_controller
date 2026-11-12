from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Body, FastAPI, Query, Request
from pydantic import BaseModel, ValidationError

from .. import store
from ..store import AppConfig
from ..tariff.model import Tariff
from ..tariff.presets import EDA_TRI_SEMANAL
from ..tariff.schedule import planned_events
from . import to_json, unprocessable

router = APIRouter(prefix="/api")


class AutomationIn(BaseModel):
    enabled: bool
    offset_min: int
    battery_limit: int
    battery_limit_enabled: bool = True


def _automation(cfg: AppConfig) -> dict:
    return {
        "enabled": cfg.automation_enabled,
        "battery_limit_enabled": cfg.battery_limit_enabled,
        "offset_min": cfg.offset_min,
        "battery_limit": cfg.battery_limit,
    }


def _tick_current(app: FastAPI) -> None:
    engine = app.state.engine  # resolved when the task runs: the runtime may have been rebuilt meanwhile
    if engine is not None:
        engine.tick()


def _retick(request: Request, background: BackgroundTasks) -> None:
    """Re-evaluate the automation after the response (the engine may talk to the car)."""
    background.add_task(_tick_current, request.app)


@router.get("/automation")
def get_automation() -> dict:
    return _automation(store.get_config())


@router.put("/automation")
def put_automation(body: AutomationIn, request: Request, background: BackgroundTasks) -> dict:
    current = store.get_config()
    try:  # AppConfig holds the valid ranges
        cfg = AppConfig.model_validate(
            {
                **current.model_dump(),
                "automation_enabled": body.enabled,
                "battery_limit_enabled": body.battery_limit_enabled,
                "offset_min": body.offset_min,
                "battery_limit": body.battery_limit,
            }
        )
    except ValidationError as exc:
        raise unprocessable(exc) from None
    store.save_config(cfg)
    _retick(request, background)
    return _automation(cfg)


@router.get("/tariff")
def get_tariff() -> dict:
    return to_json(store.get_tariff())


@router.put("/tariff")
def put_tariff(request: Request, background: BackgroundTasks, payload: dict = Body(...)) -> dict:
    try:
        tariff = Tariff.model_validate(payload)
    except ValidationError as exc:
        raise unprocessable(exc) from None
    store.save_tariff(tariff)
    _retick(request, background)
    return to_json(tariff)


@router.post("/tariff/reset")
def reset_tariff(request: Request, background: BackgroundTasks) -> dict:
    tariff = EDA_TRI_SEMANAL.model_copy(update={"prices": store.get_tariff().prices})
    store.save_tariff(tariff)
    _retick(request, background)
    return to_json(tariff)


@router.get("/tariff/events")
def tariff_events(days: int = Query(7, ge=1, le=31)) -> list:
    return to_json(planned_events(store.get_tariff(), store.get_config().offset_min, datetime.now(UTC), days=days))
