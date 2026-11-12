import dataclasses
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol

from psacc.psa.constants import DELAYED_CHARGE, IMMEDIATE_CHARGE, INPROGRESS
from psacc.psa.RemoteClient import parse_event_date
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

    def auth_ok(self) -> bool: ...  # False while PSA rejects our tokens (login/OTP must be redone)

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


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def merge_mqtt_event(last: CarSnapshot, data: dict) -> CarSnapshot | None:
    """Apply an MQTT vehicle event to the last REST snapshot; None when it is older or carries nothing usable.

    Only the fields whose MQTT meaning is known are taken (``soc_batt``, ``autonomy_zev``, ``cable_detected``);
    the charging status is not mapped from MQTT (its encoding is undocumented), except that an unplugged car
    cannot be charging.
    """
    at = parse_event_date(data.get("date"))
    if at < last.at:
        return None
    charging_state = data.get("charging_state") or {}
    changes: dict = {}
    if (soc := _number(charging_state.get("soc_batt"))) is not None:
        changes["soc"] = soc
    if (autonomy := _number(charging_state.get("autonomy_zev"))) is not None:
        changes["range_km"] = autonomy
    cable = charging_state.get("cable_detected")
    if cable in (0, 1):
        changes["plugged"] = bool(cable)
        if not cable:
            changes.update(charging=False, charge_status="Disconnected", charge_rate_kmh=0)
    if not changes:
        return None
    return dataclasses.replace(last, at=at, **changes)


class PsaGateway:
    """CarGateway over the PSA REST API (state) and MQTT (commands, pushed events).

    MQTT vehicle events are merged into the last REST snapshot without any network call (they arrive on the
    paho network thread) and handed to the subscribers, which must not block: they run under ``_lock`` so that
    a push merged onto an older snapshot is always handed over before ``snapshot()`` installs a newer one.
    """

    def __init__(self, client: PSAClient, vin: str):
        self.client = client
        self.vin = vin
        self._subscribers: list[Callable[[CarSnapshot], None]] = []
        self._last: CarSnapshot | None = None
        self._lock = threading.Lock()  # _last is shared by the paho thread and the poller/engine threads
        self._remote.on_event.append(self._on_mqtt_event)

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
        with self._lock:
            self._last = snap
        return snap

    def _on_mqtt_event(self, data: dict) -> None:
        if data.get("vin") != self.vin:
            return
        with self._lock:
            if self._last is None:
                return  # before the first REST snapshot there is nothing to merge into: the next poll catches up
            snap = merge_mqtt_event(self._last, data)
            if snap is None:
                return
            self._last = snap
            for cb in list(self._subscribers):
                try:
                    cb(snap)
                except Exception:
                    logger.exception("snapshot subscriber failed")

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

    def auth_ok(self) -> bool:
        return not (self.client.manager.auth_failed is True or self._remote.auth_failed is True)

    def subscribe(self, cb: Callable[[CarSnapshot], None]) -> None:
        self._subscribers.append(cb)
