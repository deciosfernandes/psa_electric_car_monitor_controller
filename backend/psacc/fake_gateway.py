import dataclasses
from datetime import datetime, timezone
from typing import Callable, Literal

from psacc.gateway import CarSnapshot


def default_snapshot(vin: str = "FAKEVIN") -> CarSnapshot:
    return CarSnapshot(vin=vin, at=datetime.now(timezone.utc), soc=50.0, range_km=100.0, plugged=True,
                       charging=False, charge_status="Stopped", charge_rate_kmh=0, next_delayed_time="PT22H30M",
                       latitude=37.74, longitude=-25.67, locked=True, precond_on=False)


class FakeGateway:
    """In-memory CarGateway for tests and the PSACC_FAKE dev mode.

    `fail_next` commands return False and have no effect; with `apply_effect` a successful
    `set_charge_mode` changes `snap.charging` like the car would.
    """

    def __init__(self, snap: CarSnapshot | None = None, apply_effect: bool = False):
        self.snap = snap or default_snapshot()
        self.apply_effect = apply_effect
        self.fail_next = 0
        self.mqtt_up = True
        self.calls: list[tuple] = []
        self._subscribers: list[Callable[[CarSnapshot], None]] = []

    def _command(self, *call) -> bool:
        self.calls.append(call)
        if self.fail_next > 0:
            self.fail_next -= 1
            return False
        return True

    def snapshot(self, refresh: bool = False) -> CarSnapshot:
        return self.snap

    def set_charge_mode(self, mode: Literal["charge", "defer"], program_hour: tuple[int, int]) -> bool:
        if not self._command("set_charge_mode", mode, program_hour):
            return False
        if self.apply_effect:
            charging = mode == "charge"
            self.snap = dataclasses.replace(self.snap, charging=charging,
                                            charge_status="InProgress" if charging else "Stopped")
        return True

    def precondition(self, on: bool) -> bool:
        return self._command("precondition", on)

    def lock(self, locked: bool) -> bool:
        return self._command("lock", locked)

    def horn(self) -> bool:
        return self._command("horn")

    def lights(self) -> bool:
        return self._command("lights")

    def mqtt_connected(self) -> bool:
        return self.mqtt_up

    def subscribe(self, cb: Callable[[CarSnapshot], None]) -> None:
        self._subscribers.append(cb)

    def emit(self, snap: CarSnapshot) -> None:
        """Simulate an MQTT-pushed snapshot."""
        self.snap = snap
        for cb in self._subscribers:
            cb(snap)
