"""Charge sessions: models and the detector that derives them from the snapshots the poller already gets.

Pure: the detector keeps its own small state and never touches the store or the car.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from ..gateway import CarSnapshot
from ..places import Place, Where

STOPS_TO_END = 2  # consecutive fresh "not charging" snapshots that end a session
MIN_SOC_RISE = 1.0  # % rise while plugged that reveals a charge the status did not flag


class ChargeSession(BaseModel):
    id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    place_kind: Where
    place_name: str | None = None
    soc_start: float
    soc_end: float | None = None
    kwh: float | None = None
    cost_eur: float | None = None
    price_override_eur_kwh: float | None = None
    place_price_eur_kwh: float | None = None  # the "other" place's EUR/kWh when the session was last priced
    partial: bool = False  # already charging when first seen: the true start is unknown


class ChargeSample(BaseModel):
    session_id: int | None = None
    at: datetime
    soc: float
    rate_kmh: float | None = None


@dataclass
class ChargeEvent:
    kind: Literal["start", "sample", "end"]
    session: ChargeSession
    sample: ChargeSample | None = None


def session_kwh(soc_start: float, soc_end: float, capacity: float) -> float:
    return max(0.0, soc_end - soc_start) / 100 * capacity


class ChargeDetector:
    def __init__(self, open_session: ChargeSession | None = None):
        self.current = open_session  # resumed after a restart: the charge is not "first seen" again
        self._seen = open_session is not None
        self._last_at: datetime | None = None
        self._last_status_at: datetime | None = None
        self._last_soc: float | None = None
        self._last_rate: float | None = None
        self._stops = 0

    def feed(self, snap: CarSnapshot, where: Where, place: Place | None) -> list[ChargeEvent]:
        at = snap.energy_at or snap.at
        newer_status = self._last_status_at is None or snap.at > self._last_status_at
        self._last_status_at = max(snap.at, self._last_status_at or snap.at)
        if self._last_at is not None and at <= self._last_at:
            if snap.plugged is False and self.current is not None and newer_status:
                return self._end(snap.soc, snap.at)  # an unplug is not held back by a stale electric block
            return []  # nothing new from the car
        prev_at, prev_soc, prev_rate = self._last_at, self._last_soc, self._last_rate
        self._last_at = at
        if snap.soc is not None:
            self._last_soc, self._last_rate = snap.soc, snap.charge_rate_kmh
        seen, self._seen = self._seen, self._seen or snap.soc is not None
        cur = self.current

        if snap.plugged is False:
            self._stops = 0
            if cur is None:
                return []
            return self._closing_sample(cur, at, snap) + self._end(snap.soc, at)
        if snap.soc is None:
            return []
        if cur is None:
            if snap.charging is True:
                return self._start(snap, at, where, place, partial=not seen)
            rose = (
                snap.plugged is True
                and snap.charging is None
                and prev_soc is not None
                and snap.soc - prev_soc >= MIN_SOC_RISE
            )
            if rose:
                events = self._start(snap, at, where, place, partial=False, since=(prev_at, prev_soc))
                events.insert(1, self._sample(events[0].session, prev_at, prev_soc, prev_rate))
                return events
            return []
        if snap.charging is False:
            self._stops += 1
            cur.soc_end = snap.soc
            events = self._closing_sample(cur, at, snap)  # soc gained since the last sample counts in the cost
            return events + self._end(snap.soc, at) if self._stops >= STOPS_TO_END else events
        if snap.charging is None:
            return []
        self._stops = 0
        cur.soc_end = snap.soc
        return [self._sample(cur, at, snap.soc, snap.charge_rate_kmh)]

    def _start(self, snap, at, where, place, partial, since=None) -> list[ChargeEvent]:
        start_at, start_soc = since if since else (at, snap.soc)
        self._stops = 0
        self.current = ChargeSession(
            started_at=start_at,
            place_kind=where,
            place_name=place.name if place else None,
            soc_start=start_soc,
            soc_end=snap.soc,
            partial=partial,
        )
        return [ChargeEvent("start", self.current), self._sample(self.current, at, snap.soc, snap.charge_rate_kmh)]

    def _closing_sample(self, session: ChargeSession, at: datetime, snap: CarSnapshot) -> list[ChargeEvent]:
        return [] if snap.soc is None else [self._sample(session, at, snap.soc, snap.charge_rate_kmh)]

    @staticmethod
    def _sample(session: ChargeSession, at: datetime, soc: float, rate: float | None) -> ChargeEvent:
        return ChargeEvent("sample", session, ChargeSample(at=at, soc=soc, rate_kmh=rate))

    def _end(self, soc: float | None, at: datetime) -> list[ChargeEvent]:
        s, self.current, self._stops = self.current, None, 0
        s.ended_at = at
        s.soc_end = soc if soc is not None else s.soc_end
        return [ChargeEvent("end", s)]
