"""Places (casa, trabalho, other) and the location of the car relative to them.

Pure helpers: the position fix and the places are passed in, nothing here touches the store or the car.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .gateway import CarSnapshot

STALE_FIX = timedelta(minutes=60)  # an older fix only counts while the odometer says the car has not moved
MOVED_KM = 0.1
_EARTH_RADIUS_M = 6_371_000

Where = Literal["home", "work", "other", "unknown", "unset"]  # "unset": no home place configured


class Place(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["home", "work", "other"]
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: int = Field(150, ge=25, le=2000)
    price_eur_kwh: float | None = Field(None, ge=0)  # only for kind "other"

    @model_validator(mode="after")
    def _price_only_for_other(self) -> "Place":
        if self.price_eur_kwh is not None and self.kind != "other":
            raise ValueError("Preço: só se aplica a locais do tipo outro")
        return self


class PositionFix(BaseModel):
    lat: float
    lon: float
    at: datetime
    mileage_km: float | None = None  # odometer when the fix was taken


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def place_at(lat: float, lon: float, places: list[Place]) -> Place | None:
    """The nearest place whose radius contains the point."""
    inside = [(distance_m(lat, lon, p.lat, p.lon), p) for p in places]
    inside = [(d, p) for d, p in inside if d <= p.radius_m]
    return min(inside, key=lambda dp: dp[0])[1] if inside else None


def update_fix(car: CarSnapshot, fix: PositionFix | None) -> PositionFix | None:
    """A new fix if the car reports a position newer than the stored one, else None (nothing to store)."""
    if car.latitude is None or car.longitude is None or car.position_at is None:
        return None
    if fix is not None and car.position_at <= fix.at:
        return None
    return PositionFix(lat=car.latitude, lon=car.longitude, at=car.position_at, mileage_km=car.mileage_km)


def locate(now: datetime, car: CarSnapshot, fix: PositionFix | None, places: list[Place]) -> tuple[Where, Place | None]:
    if not any(p.kind == "home" for p in places):
        return "unset", None
    if fix is None:
        return "unknown", None
    fix_at = fix.at if fix.at.tzinfo else fix.at.replace(tzinfo=timezone.utc)  # naive values are UTC
    if now - fix_at > STALE_FIX and (
        car.mileage_km is None or fix.mileage_km is None or abs(car.mileage_km - fix.mileage_km) > MOVED_KM
    ):
        return "unknown", None  # an old fix only holds while both odometers prove the car has not moved
    place = place_at(fix.lat, fix.lon, places)
    return (place.kind, place) if place else ("other", None)
