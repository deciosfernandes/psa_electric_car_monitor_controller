from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request

from .. import store
from ..tariff.schedule import period_at, planned_events
from . import to_json

router = APIRouter(prefix="/api")


@router.get("/status")
def status(request: Request) -> dict:
    state = request.app.state
    now = datetime.now(UTC)
    cfg, tariff = store.get_config(), store.get_tariff()
    events = planned_events(tariff, cfg.offset_min, now, days=8)
    gateway = state.gateway
    return to_json(
        {
            "car": state.poller.latest() if state.poller is not None else None,
            "period": period_at(tariff, now),
            "next_event": events[0] if events else None,
            "automation": {
                "enabled": cfg.automation_enabled,
                "offset_min": cfg.offset_min,
                "battery_limit": cfg.battery_limit,
            },
            "override": store.get_override(),
            "last_command": store.get_last_command(),
            "mqtt_connected": gateway is not None and gateway.mqtt_connected(),
            "psa_connected": gateway is not None and gateway.auth_ok(),
            "updated_at": now,
        }
    )


@router.get("/battery")
def battery(hours: int = Query(24, ge=1, le=24 * 31)) -> list:
    return to_json(store.battery_samples(datetime.now(UTC) - timedelta(hours=hours)))


@router.get("/activity")
def activity(limit: int = Query(50, ge=1, le=500)) -> list:
    return to_json(store.recent_activity(limit))
