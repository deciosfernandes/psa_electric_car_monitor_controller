from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import store
from . import TZ, to_json

router = APIRouter(prefix="/api")

_ALL = 1_000_000  # history is kept forever and is small (one car): aggregate in Python


class Price(BaseModel):
    eur_kwh: float = Field(ge=0, le=10)


def _month(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m")


def _l100(km: float, kwh: float) -> float | None:
    return kwh / km * 100 if km > 0 else None


@router.get("/charges")
def charges(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> list:
    return to_json([store.recompute_session_cost(c) for c in store.charge_sessions(limit, offset)])


@router.get("/charges/summary")
def charges_summary(month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")) -> dict:
    month = month or _month(datetime.now(UTC))
    rows = [
        store.recompute_session_cost(c) for c in store.charge_sessions(_ALL) if _month(c.started_at) == month
    ]
    kwh = sum(c.kwh or 0.0 for c in rows)
    home = [c for c in rows if c.place_kind == "home"]
    home_kwh = sum(c.kwh or 0.0 for c in home)
    work_kwh = sum(c.kwh or 0.0 for c in rows if c.place_kind == "work")
    priced = [c for c in home if c.cost_eur is not None and c.kwh]  # unknown price is not 0 EUR
    priced_kwh = sum(c.kwh for c in priced)
    return {
        "kwh": kwh,
        "cost_eur": sum(c.cost_eur or 0.0 for c in rows),
        "work_pct": work_kwh / kwh * 100 if kwh > 0 else 0.0,
        "home_pct": home_kwh / kwh * 100 if kwh > 0 else 0.0,
        "avg_home_eur_kwh": sum(c.cost_eur for c in priced) / priced_kwh if priced_kwh > 0 else None,
        "unpriced": sum(1 for c in rows if c.cost_eur is None),  # counted as 0 EUR in cost_eur
        "partial": sum(1 for c in rows if c.partial),
    }


@router.get("/charges/{session_id}/samples")
def charge_samples(session_id: int) -> list:
    if store.get_charge_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Carga não encontrada")
    return to_json(store.charge_samples(session_id))


@router.put("/charges/{session_id}/price")
def set_price(session_id: int, body: Price) -> dict:
    c = store.set_session_price(session_id, body.eur_kwh)
    if c is None:
        raise HTTPException(status_code=404, detail="Carga não encontrada")
    return to_json(c)


@router.get("/trips")
def trips(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> list:
    return to_json(store.trips(limit, offset))


@router.get("/consumption")
def consumption() -> dict:
    all_trips = store.trips(_ALL)
    since = datetime.now(UTC) - timedelta(days=30)
    recent = [t for t in all_trips if t.departed_at.replace(tzinfo=t.departed_at.tzinfo or UTC) >= since]
    km, kwh = sum(t.km for t in recent), sum(t.kwh for t in recent)
    by_month: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for t in all_trips:
        acc = by_month[_month(t.departed_at.replace(tzinfo=t.departed_at.tzinfo or UTC))]
        acc[0] += t.km
        acc[1] += t.kwh
    return {
        "last30": {"km": km, "kwh": kwh, "kwh_100km": _l100(km, kwh)},
        "months": [
            {"month": m, "km": v[0], "kwh": v[1], "kwh_100km": _l100(v[0], v[1])} for m, v in sorted(by_month.items())
        ],
    }
