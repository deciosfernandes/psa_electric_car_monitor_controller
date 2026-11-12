import dataclasses
import threading
from datetime import datetime, timedelta

import pytest

from psacc import settings, store
from psacc.poller import ALERT_AUTH, ALERT_MQTT, Poller, psa_token_saver
from psacc.psa.RemoteClient import RemoteException
from psacc.settings import TZ
from tests.fakes import FakeGateway


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    store.init_db("sqlite://")


class FakeNotifier:
    def __init__(self):
        self.alerts: list[str] = []

    def alert(self, msg: str) -> None:
        self.alerts.append(msg)

    def info(self, msg: str) -> None:
        pass


class RecordingEngine:
    def __init__(self):
        self.statuses = []
        self.threads = []
        self.received = threading.Event()
        self.pending = False

    def verification_pending(self) -> bool:
        return self.pending

    def on_status(self, snap) -> None:
        self.statuses.append(snap)
        self.threads.append(threading.current_thread())
        self.received.set()


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class RecordingGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.refreshes: list[bool] = []
        self.raise_next: Exception | None = None

    def snapshot(self, refresh: bool = False):
        self.refreshes.append(refresh)
        if self.raise_next is not None:
            raise self.raise_next
        return self.snap


THU_0648 = datetime(2026, 11, 12, 6, 48, tzinfo=TZ)  # Thursday, winter: weekday vazio ends 07h00


def make(clock: Clock, gateway=None, waits=None, **kw):
    gateway = gateway or RecordingGateway()
    engine, notifier = RecordingEngine(), FakeNotifier()
    wait = (lambda s: waits.append(s) or False) if waits is not None else (lambda s: False)
    poller = Poller(gateway, engine, notifier, clock=clock, wait=wait, **kw)
    return poller, gateway, engine, notifier


def test_next_wake_is_planned_event():
    clock = Clock(THU_0648)
    poller, *_ = make(clock)
    poller.run_once()
    # the 06h50 defer (07h00 - 10 min offset) + 5 s comes before the 06h53 refresh
    assert poller.next_wake() == datetime(2026, 11, 12, 6, 50, 5, tzinfo=TZ)
    clock.now = datetime(2026, 11, 12, 6, 50, 5, tzinfo=TZ)
    poller.run_once()
    assert poller.next_wake() == datetime(2026, 11, 12, 6, 53, tzinfo=TZ)  # the refresh is still due at 06h53


def test_refresh_every_refresh_min_with_settled_refetch():
    clock = Clock(THU_0648)
    waits = []
    poller, gateway, engine, _ = make(clock, waits=waits)
    poller.run_once()
    assert gateway.refreshes == [True, False]  # wakeup, wait for the car, re-fetch the fresh state
    assert waits == [20]
    clock.advance(minutes=2)
    poller.run_once()
    clock.advance(minutes=3)
    poller.run_once()
    assert gateway.refreshes == [True, False, False, True, False]
    assert len(engine.statuses) == 3 and poller.latest() is gateway.snap


def test_auth_error_alerts_once():
    clock = Clock(THU_0648)
    poller, gateway, engine, notifier = make(clock)
    gateway.raise_next = RemoteException("otp")
    for _ in range(3):
        poller.run_once()
        clock.advance(minutes=5)
    assert notifier.alerts == [ALERT_AUTH]
    assert ALERT_AUTH == "🔑 PSACC: sessão PSA expirada, refaça o login em Definições"
    assert engine.statuses == [] and poller.latest() is None
    gateway.raise_next = None
    poller.run_once()  # recovered: the episode ends
    gateway.auth_up = False  # token refresh failed without an exception
    poller.run_once()
    poller.run_once()
    assert notifier.alerts == [ALERT_AUTH, ALERT_AUTH]


def test_other_errors_do_not_alert_and_do_not_raise():
    clock = Clock(THU_0648)
    poller, gateway, engine, notifier = make(clock)
    gateway.raise_next = RuntimeError("PSA status unavailable")
    poller.run_once()
    assert notifier.alerts == [] and engine.statuses == []


def test_mqtt_down_15_min_alerts_once():
    clock = Clock(THU_0648)
    poller, gateway, _, notifier = make(clock)
    gateway.mqtt_up = False
    for _ in range(3):  # 0, 7, 14 min
        poller.run_once()
        clock.advance(minutes=7)
    assert notifier.alerts == []
    clock.now = THU_0648 + timedelta(minutes=15, seconds=1)
    poller.run_once()
    clock.advance(minutes=10)
    poller.run_once()
    assert notifier.alerts == [ALERT_MQTT]
    gateway.mqtt_up = True
    poller.run_once()
    gateway.mqtt_up = False
    poller.run_once()
    clock.advance(minutes=16)
    poller.run_once()
    assert notifier.alerts == [ALERT_MQTT, ALERT_MQTT]


def test_token_saver_persists_rotated_tokens(monkeypatch):
    saved = []
    monkeypatch.setattr(store, "save_psa", saved.append)

    class Client:
        tokens = {"refresh_token": "a"}

        def to_dict(self):
            return dict(self.tokens)

    monkeypatch.setattr(store, "get_psa", lambda: {"refresh_token": "a"})
    client = Client()
    save = psa_token_saver(client)
    save()
    assert saved == []
    client.tokens = {"refresh_token": "b"}
    save()
    save()
    assert saved == [{"refresh_token": "b"}]


def test_run_once_saves_tokens():
    calls = []
    poller, *_ = make(Clock(THU_0648), save_tokens=lambda: calls.append(1))
    poller.run_once()
    assert calls == [1]


def test_mqtt_push_handled_on_poller_thread_and_stop_is_prompt():
    clock = Clock(THU_0648)  # frozen: after the first run the poller just waits
    poller, gateway, engine, _ = make(clock)
    poller.start()
    try:
        assert engine.received.wait(2)
        engine.received.clear()
        pushed = dataclasses.replace(gateway.snap, soc=77.0)
        gateway.emit(pushed)
        assert engine.received.wait(2)
        assert engine.statuses[-1] is pushed and poller.latest() is pushed
        assert engine.threads[-1] is poller._thread
    finally:
        poller.stop()
    assert not poller._thread.is_alive()


def test_push_during_settle_wait_does_not_override_fresh_rest_snapshot():
    clock = Clock(THU_0648)
    gateway = RecordingGateway()
    stale_push = dataclasses.replace(gateway.snap, soc=60.0, charging=False)  # merged onto the pre-wakeup state
    fresh = dataclasses.replace(gateway.snap, soc=61.0, charging=True, charge_status="InProgress")

    def wait(_s):
        gateway.emit(stale_push)  # MQTT push arriving while the car wakes up
        gateway.snap = fresh  # the re-fetch after the wait returns the post-wakeup REST state
        return False

    engine, notifier = RecordingEngine(), FakeNotifier()
    poller = Poller(gateway, engine, notifier, clock=clock, wait=wait)
    poller.run_once()
    poller._drain_pushed()  # what the next loop iteration does
    assert engine.statuses == [fresh]
    assert poller.latest() is fresh


def test_failing_engine_does_not_skip_token_saving():
    calls = []
    poller, _, engine, _ = make(Clock(THU_0648), save_tokens=lambda: calls.append(1))

    def boom(snap):
        raise RuntimeError("engine")

    engine.on_status = boom
    poller.run_once()
    assert calls == [1]


def test_poller_stopped_during_fetch_does_not_act():
    # replaced while a poll was in flight: no engine feed, no alerts, no token save over the new account
    clock = Clock(THU_0648)
    saved = []
    gateway = RecordingGateway()
    gateway.auth_up = False
    poller, _, engine, notifier = make(clock, gateway=gateway, settle_s=0, save_tokens=lambda: saved.append(1))
    real_snapshot = gateway.snapshot

    def slow_snapshot(refresh=False):
        poller.stop()  # the API swaps the runtime while this REST call is still running
        return real_snapshot(refresh)

    gateway.snapshot = slow_snapshot
    poller.run_once()
    assert engine.statuses == []
    assert saved == []
    assert notifier.alerts == []
    assert poller.latest() is None


THU_1200 = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)  # no planned event between 06h50 and 00h10


def at_clock(gateway, clock, **kw):
    gateway.snap = dataclasses.replace(gateway.snap, at=clock.now, **kw)


def test_no_wakeup_when_unplugged():
    clock = Clock(THU_1200)
    poller, gateway, *_ = make(clock)
    at_clock(gateway, clock, plugged=False, charging=False, charge_status="Disconnected")
    gateway.snap = dataclasses.replace(gateway.snap, at=clock.now - timedelta(hours=5))  # stale
    poller.run_once()  # startup: state unknown -> wake once
    for _ in range(3):
        clock.advance(minutes=5)
        poller.run_once()
    assert gateway.refreshes == [True, False, False, False, False]



def test_stale_unplugged_is_unknown_hourly_wakeup():
    """R3: an "unplugged" snapshot older than 60 min no longer blocks the hourly staleness wakeup."""
    clock = Clock(THU_1200)
    poller, gateway, *_ = make(clock)
    at_clock(gateway, clock, plugged=False, charging=False, charge_status="Disconnected")
    poller.run_once()  # 12h00: startup wake; the car reports unplugged at 12h00
    for _ in range(11):  # 12h05 .. 12h55: fresh unplugged -> no wakeup
        clock.advance(minutes=5)
        poller.run_once()
    assert gateway.refreshes == [True, False] + [False] * 11
    clock.advance(minutes=5)  # 13h00: "unplugged" is 60 min old -> unknown -> hourly wakeup
    poller.run_once()
    assert gateway.refreshes[-2:] == [True, False]

def test_no_wakeup_when_idle_and_fresh_then_hourly():
    clock = Clock(THU_1200)
    poller, gateway, *_ = make(clock)
    at_clock(gateway, clock)
    poller.run_once()
    for _ in range(11):  # 12h05 .. 12h55: snapshot (12h00) younger than 60 min
        clock.advance(minutes=5)
        poller.run_once()
    assert gateway.refreshes == [True, False] + [False] * 11
    clock.advance(minutes=5)  # 13h00: snapshot 60 min old
    poller.run_once()
    assert gateway.refreshes[-2:] == [True, False]


def test_wakeup_while_verification_pending():
    clock = Clock(THU_1200)
    poller, gateway, engine, _ = make(clock)
    at_clock(gateway, clock)
    poller.run_once()
    engine.pending = True
    clock.advance(minutes=5)
    poller.run_once()
    assert gateway.refreshes == [True, False, True, False]


def test_wakeup_only_within_15_min_after_event():
    clock = Clock(datetime(2026, 11, 12, 6, 55, tzinfo=TZ))
    poller, gateway, *_ = make(clock)
    at_clock(gateway, clock)
    poller.run_once()
    clock.advance(minutes=5)  # 07h00: 10 min after the 06h50 defer
    poller.run_once()
    clock.advance(minutes=5)  # 07h05: 15 min after, snapshot fresh
    poller.run_once()
    assert gateway.refreshes == [True, False, True, False, False]


def test_extra_wakeup_3_min_after_each_event():
    store.save_config(store.AppConfig(refresh_min=30))
    clock = Clock(datetime(2026, 11, 12, 6, 40, tzinfo=TZ))
    poller, gateway, *_ = make(clock)
    at_clock(gateway, clock)
    poller.run_once()
    assert poller.next_wake() == datetime(2026, 11, 12, 6, 50, 5, tzinfo=TZ)
    clock.now = poller.next_wake()
    poller.run_once()  # the event itself: plain fetch, the engine verifies its own command
    assert poller.next_wake() == datetime(2026, 11, 12, 6, 53, tzinfo=TZ)
    clock.now = poller.next_wake()
    poller.run_once()
    assert gateway.refreshes == [True, False, False, True, False]
    assert poller.next_wake() == datetime(2026, 11, 12, 7, 10, tzinfo=TZ)  # follow-up done: next refresh


def test_no_follow_up_wakeup_when_unplugged():
    store.save_config(store.AppConfig(refresh_min=30))
    clock = Clock(datetime(2026, 11, 12, 6, 40, tzinfo=TZ))
    poller, gateway, *_ = make(clock)
    at_clock(gateway, clock, plugged=False, charging=False, charge_status="Disconnected")
    poller.run_once()
    clock.now = datetime(2026, 11, 12, 6, 53, tzinfo=TZ)
    poller.run_once()
    assert gateway.refreshes == [True, False, False]


def test_plug_in_push_triggers_immediate_wakeup():
    clock = Clock(THU_1200)
    poller, gateway, engine, _ = make(clock)
    at_clock(gateway, clock, plugged=False, charging=False, charge_status="Disconnected")
    poller.run_once()
    clock.advance(minutes=1)
    assert poller.next_wake() > clock.now
    plugged = dataclasses.replace(gateway.snap, plugged=True, charge_status="Stopped", at=clock.now)
    gateway.emit(plugged)
    poller._drain_pushed()
    assert engine.statuses[-1] is plugged
    assert poller.next_wake() == clock.now
    gateway.snap = plugged
    poller.run_once()
    assert gateway.refreshes == [True, False, True, False]


def test_mqtt_pushes_do_not_keep_rest_unplugged_fresh():
    """MQTT pushes advance `at` but never refresh plugged (REST only): its freshness is judged on energy_at."""
    clock = Clock(THU_1200)
    poller, gateway, *_ = make(clock)
    at_clock(gateway, clock, plugged=False, charging=False, charge_status="Disconnected", energy_at=clock.now)
    poller.run_once()  # 12h00: startup wake; REST reports unplugged at 12h00
    for _ in range(12):  # 12h05 .. 13h00: an MQTT soc push every 5 min, then a plain REST poll still at 12h00
        clock.advance(minutes=5)
        gateway.emit(dataclasses.replace(gateway.snap, at=clock.now))
        poller._drain_pushed()
        poller.run_once()
    assert gateway.refreshes[-2:] == [True, False]  # 13h00: "unplugged" is 60 min old -> unknown -> wakeup
