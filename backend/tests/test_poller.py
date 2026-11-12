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
