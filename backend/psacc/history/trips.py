"""Trips: the model and the detector that derives them from odometer changes in the snapshots the poller gets.

Pure: the detector keeps its own small state and never touches the store or the car. A trip in progress is not
persisted; after a restart the next snapshot simply becomes the new parked reference.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel

from ..gateway import CarSnapshot
from ..places import Place, Where

MIN_KM = 0.5  # shorter movements are odometer/GPS noise
MIN_STILL = timedelta(minutes=10)  # odometer unchanged this long, across two fresh snapshots, ends the trip


class Trip(BaseModel):
    id: int | None = None
    departed_at: datetime
    arrived_at: datetime
    from_kind: Where
    from_name: str | None = None
    to_kind: Where
    to_name: str | None = None
    from_lat: float | None = None
    from_lon: float | None = None
    to_lat: float | None = None
    to_lon: float | None = None
    km: float
    soc_start: float | None = None
    soc_end: float | None = None
    kwh: float
    kwh_100km: float


@dataclass
class _Point:
    snap: CarSnapshot
    where: Where
    place: Place | None

    @property
    def km(self) -> float:
        return self.snap.mileage_km  # type: ignore[return-value]  # only built with a known odometer


class TripDetector:
    def __init__(self, capacity_kwh: float):
        self.capacity = capacity_kwh
        self._last_at: datetime | None = None
        self._parked: _Point | None = None  # last snapshot seen without movement
        self._moving = False
        self._last_move: _Point | None = None  # last snapshot where the odometer had advanced

    def feed(self, snap: CarSnapshot, where: Where, place: Place | None) -> Trip | None:
        at = snap.at
        if snap.mileage_km is None or (self._last_at is not None and at <= self._last_at):
            return None  # unknown odometer is not movement; nothing new from the car
        self._last_at = at
        pt = _Point(snap, where, place)
        if self._parked is None:
            self._parked = pt
            return None
        if not self._moving:
            if pt.km > self._parked.km:
                self._moving, self._last_move = True, pt
            else:
                self._parked = pt  # also after an odometer reset or a non-increasing reading
            return None
        last = self._last_move
        if pt.km > last.km:
            self._last_move = pt
            return None
        if pt.km < last.km:  # odometer went backwards: abandon the trip
            self._moving, self._last_move, self._parked = False, None, pt
            return None
        if at - last.snap.at < MIN_STILL:
            return None
        trip = self._finish(self._parked, last, pt)
        self._moving, self._last_move, self._parked = False, None, pt
        return trip

    def _finish(self, start: _Point, moved: _Point, end: _Point) -> Trip | None:
        km = moved.km - start.km
        if km < MIN_KM:
            return None
        s0, s1 = start.snap.soc, end.snap.soc
        kwh = max(0.0, s0 - s1) / 100 * self.capacity if s0 is not None and s1 is not None else 0.0
        return Trip(
            departed_at=start.snap.at,
            arrived_at=moved.snap.at,
            from_kind=start.where,
            from_name=start.place.name if start.place else None,
            to_kind=end.where,
            to_name=end.place.name if end.place else None,
            from_lat=start.snap.latitude,
            from_lon=start.snap.longitude,
            to_lat=end.snap.latitude,
            to_lon=end.snap.longitude,
            km=km,
            soc_start=s0,
            soc_end=s1,
            kwh=kwh,
            kwh_100km=kwh / km * 100,
        )
