import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol

from psacc.psa.constants import DELAYED_CHARGE, IMMEDIATE_CHARGE, INPROGRESS
from psacc.psa.models import VehicleStatus
from psacc.psa_client import PSAClient
from psacc.utils import RateLimitException

logger = logging.getLogger(__name__)

HORN_COUNT = 2
LIGHTS_DURATION_S = 10


@dataclass(frozen=True)
class CarSnapshot:
    vin: str
    at: datetime  # UTC, status updated_at
    soc: float | None  # %
    range_km: float | None
    plugged: bool | None
    charging: bool | None  # status == "InProgress"
    charge_status: str | None  # raw: InProgress/Stopped/Finished/Disconnected/Failure
    charge_rate_kmh: int | None
    next_delayed_time: str | None  # raw PSA ISO-8601 duration, e.g. "PT22H30M"
    latitude: float | None
    longitude: float | None
    locked: bool | None
    precond_on: bool | None


class CarGateway(Protocol):
    def snapshot(self, refresh: bool = False) -> CarSnapshot: ...  # refresh=True -> wakeup + fetch

    def set_charge_mode(self, mode: Literal["charge", "defer"], program_hour: tuple[int, int]) -> bool: ...

    def precondition(self, on: bool) -> bool: ...

    def lock(self, locked: bool) -> bool: ...

    def horn(self) -> bool: ...

    def lights(self) -> bool: ...

    def mqtt_connected(self) -> bool: ...

    def subscribe(self, cb: Callable[[CarSnapshot], None]) -> None: ...  # MQTT-pushed snapshots


def parse_psa_datetime(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _locked(locked_states: list[str] | None) -> bool | None:
    states = [s.lower() for s in locked_states or []]
    if any(s.endswith("unlocked") for s in states):
        return False
    if any(s in ("locked", "superlocked") for s in states):
        return True
    return None


def snapshot_from_status(vin: str, status: VehicleStatus) -> CarSnapshot:
    """Pure conversion of a REST status; missing or odd fields become None."""
    electric = status.electric()
    charging = electric.charging_info() if electric else None
    position = status.last_position
    coordinates = list(position.geometry.coordinates or []) if position and position.geometry else []
    preconditioning = status.preconditioning or status.preconditionning
    air_conditioning = preconditioning.air_conditioning if preconditioning else None
    at = (parse_psa_datetime(status.updated_at) or parse_psa_datetime(electric.updated_at if electric else None)
          or parse_psa_datetime(status.created_at) or datetime.now(timezone.utc))
    charge_status = charging.status if charging else None
    return CarSnapshot(
        vin=vin,
        at=at,
        soc=electric.level if electric else None,
        range_km=electric.autonomy if electric else None,
        plugged=charging.plugged if charging else None,
        charging=None if charge_status is None else charge_status == INPROGRESS,
        charge_status=charge_status,
        charge_rate_kmh=charging.charging_rate if charging else None,
        next_delayed_time=charging.next_delayed_time if charging else None,
        latitude=coordinates[1] if len(coordinates) > 1 else None,
        longitude=coordinates[0] if len(coordinates) > 1 else None,
        locked=_locked(status.doors_state.locked_states) if status.doors_state else None,
        precond_on=air_conditioning.status == "Enabled" if air_conditioning and air_conditioning.status else None,
    )


class PsaGateway:
    def __init__(self, client: PSAClient, vin: str):
        self.client = client
        self.vin = vin
        self._subscribers: list[Callable[[CarSnapshot], None]] = []

    @property
    def _remote(self):
        return self.client.remote_client

    def snapshot(self, refresh: bool = False) -> CarSnapshot:
        if refresh:
            try:
                self._remote.wakeup(self.vin)
            except RateLimitException:
                logger.warning("wakeup rate limited, fetching without it")
        status = self.client.get_vehicle_info(self.vin)
        if status is None:
            raise RuntimeError("PSA status unavailable")
        snap = snapshot_from_status(self.vin, status)
        # mqtt is live while the rest api lags behind, so a newer mqtt lock state wins
        mqtt_lock, mqtt_at = self._remote.lock_state.get(self.vin), self._remote.lock_state_at.get(self.vin)
        if mqtt_lock is not None and mqtt_at is not None and mqtt_at > snap.at:
            snap = dataclasses.replace(snap, locked=mqtt_lock == "Locked")
        return snap

    def set_charge_mode(self, mode: Literal["charge", "defer"], program_hour: tuple[int, int]) -> bool:
        charge_type = IMMEDIATE_CHARGE if mode == "charge" else DELAYED_CHARGE
        return self._remote.veh_charge_request(self.vin, program_hour[0], program_hour[1], charge_type)

    def precondition(self, on: bool) -> bool:
        return self._remote.preconditioning(self.vin, on)

    def lock(self, locked: bool) -> bool:
        return self._remote.lock_door(self.vin, locked)

    def horn(self) -> bool:
        return self._remote.horn(self.vin, HORN_COUNT)

    def lights(self) -> bool:
        return self._remote.lights(self.vin, LIGHTS_DURATION_S)

    def mqtt_connected(self) -> bool:
        mqtt_client = self._remote.mqtt_client
        return mqtt_client is not None and mqtt_client.is_connected()

    def subscribe(self, cb: Callable[[CarSnapshot], None]) -> None:
        # wired to MQTT pushes in a later task
        self._subscribers.append(cb)
