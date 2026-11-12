from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request

from .. import store
from ..automation.decide import not_home_reason
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
    car = state.poller.latest() if state.poller is not None else None
    engine = state.poller.engine if state.poller is not None else None
    fix = store.get_position_fix()
    pos_at = (car.position_at if car is not None else None) or (fix.at if fix is not None else None)
    if pos_at is not None and pos_at.tzinfo is None:
        pos_at = pos_at.replace(tzinfo=UTC)
    where = (engine.last_where if engine is not None else None) or "unknown"
    place_name = engine.last_place.name if engine is not None and engine.last_place else None
    return to_json(
        {
            "car": car,
            "where": where,
            "place_name": place_name,
            "paused_reason": None if where == "home" else not_home_reason(where, place_name),
            "mileage_km": car.mileage_km if car is not None else None,
            "position_age_min": max(0, int((now - pos_at).total_seconds() // 60)) if pos_at is not None else None,
            "period": period_at(tariff, now),
            "next_event": events[0] if events else None,
            "automation": {
                "enabled": cfg.automation_enabled,
                "battery_limit_enabled": cfg.battery_limit_enabled,
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


@router.get("/battery-health")
def battery_health() -> list:
    return to_json([{"day": d, "value": v} for d, v in store.health_samples()])


@router.get("/activity")
def activity(limit: int = Query(50, ge=1, le=500)) -> list:
    return to_json(store.recent_activity(limit))
