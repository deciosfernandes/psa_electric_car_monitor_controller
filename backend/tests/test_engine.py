import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from psacc import settings, store
from psacc.automation.engine import RETRY_DELAYS_S, Engine
from psacc.settings import TZ
from psacc.store import AppConfig
from psacc.tariff.model import Season, Slot, Tariff
from tests.fakes import FakeGateway, default_snapshot

ALERT_DEFER = (
    "⚠️ PSACC: não consegui ativar a carga diferida (Fora do vazio (ponta)). "
    "O carro pode estar a carregar fora do vazio."
)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    store.init_db("sqlite://")


class FakeNotifier:
    def __init__(self):
        self.alerts: list[str] = []
        self.infos: list[str] = []

    def alert(self, msg: str) -> None:
        self.alerts.append(msg)

    def info(self, msg: str) -> None:
        self.infos.append(msg)


class FakeTimer:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    """Records (delay_s, callback) instead of starting threads."""

    def __init__(self):
        self.scheduled: list[tuple[float, FakeTimer]] = []

    def __call__(self, delay_s, fn):
        timer = FakeTimer()
        self.scheduled.append((delay_s, timer))
        return timer

    @property
    def delays(self):
        return [d for d, _ in self.scheduled]


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self):
        return self.now


THU = datetime(2026, 11, 12, tzinfo=TZ)  # a Thursday, winter season


def thu(h, mi=0):
    return THU.replace(hour=h, minute=mi)


def snap(**kw):
    return dataclasses.replace(default_snapshot(), **kw)


def make(car=None, at=thu(19), apply_effect=False):
    gw = FakeGateway(
        car or snap(plugged=True, charging=True, charge_status="InProgress", soc=50.0), apply_effect=apply_effect
    )
    notifier, sched, clock = FakeNotifier(), FakeScheduler(), Clock(at)
    engine = Engine(gw, notifier, clock=clock, scheduler=sched)
    return engine, gw, notifier, sched, clock


def charge_calls(gw):
    return [c for c in gw.calls if c[0] == "set_charge_mode"]


def messages(kind=None):
    return [a.message for a in store.recent_activity(100) if kind is None or a.kind == kind]


def test_engine_enforces_on_startup():
    engine, gw, *_ = make()
    engine.tick()
    assert charge_calls(gw) == [("set_charge_mode", "defer", (0, 10))]
    last = store.get_last_command()
    assert (last.mode, last.verified, last.attempts) == ("defer", False, 1)


def test_no_repeat_command_when_already_verified():
    engine, gw, notifier, sched, _ = make(apply_effect=True)
    engine.tick()
    assert sched.delays == [60]
    engine.verify_now()
    assert store.get_last_command().verified is True
    assert "Fora do vazio (ponta) → diferido ligado" in messages("action")
    engine.tick()
    engine.tick()
    assert len(charge_calls(gw)) == 1
    assert notifier.alerts == []


def test_retry_then_alert_once():
    engine, gw, notifier, sched, _ = make()
    gw.fail_next = 3
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert len(charge_calls(gw)) == 3
    assert sched.delays == list(RETRY_DELAYS_S) == [60, 180, 600]
    assert notifier.alerts == [ALERT_DEFER]
    assert len(messages("error")) == 1
    # further ticks keep trying quietly: no second alert in the same episode
    gw.fail_next = 3
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert len(charge_calls(gw)) == 6
    assert notifier.alerts == [ALERT_DEFER]
    assert len(messages("error")) == 1


def test_alert_not_repeated_after_restart_mid_episode():
    engine, gw, notifier, *_ = make()
    gw.fail_next = 10
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert len(notifier.alerts) == 1
    notifier2 = FakeNotifier()
    restarted = Engine(gw, notifier2, clock=Clock(thu(19, 30)), scheduler=FakeScheduler())
    restarted.tick()
    for _ in range(3):
        restarted.verify_now()
    assert len(charge_calls(gw)) == 6
    assert notifier2.alerts == []


def test_success_after_retry_resets_episode():
    engine, gw, notifier, sched, _ = make(apply_effect=True)
    gw.fail_next = 1
    engine.tick()  # attempt 1 rejected
    engine.verify_now()  # still charging -> attempt 2 succeeds
    assert sched.delays == [60, 60]
    engine.verify_now()
    assert store.get_last_command().verified is True
    assert store.get_last_command().attempts == 2
    assert notifier.alerts == []

    # an exhausted episode alerts; a later success resets it so the next failure episode alerts again
    gw.apply_effect = False
    gw.snap = snap(plugged=True, charging=True, charge_status="InProgress", soc=50.0)
    gw.fail_next = 3
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert len(notifier.alerts) == 1
    gw.apply_effect = True
    engine.tick()
    engine.verify_now()
    assert store.get_last_command().verified is True
    assert store.get_last_command().alerted is False
    gw.apply_effect = False
    gw.snap = snap(plugged=True, charging=True, charge_status="InProgress", soc=50.0)
    gw.fail_next = 3
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert len(notifier.alerts) == 2


def test_plug_in_during_cheias_defers():
    engine, gw, *_ = make(car=snap(plugged=False, charging=False, charge_status="Disconnected"), at=thu(21))
    engine.on_status(gw.snap)
    assert charge_calls(gw) == []
    gw.snap = snap(plugged=True, charging=True, charge_status="InProgress")
    engine.on_status(gw.snap)
    assert charge_calls(gw) == [("set_charge_mode", "defer", (0, 10))]
    assert "Cabo ligado" in messages()
    gw.snap = snap(plugged=False, charging=False, charge_status="Disconnected")
    engine.on_status(gw.snap)
    assert "Cabo desligado" in messages()
    assert len(store.battery_samples(datetime(2000, 1, 1, tzinfo=UTC))) == 3


def test_manual_charge_then_limit():
    engine, gw, notifier, sched, clock = make(car=snap(plugged=True, charging=False, soc=70.0), apply_effect=True)
    engine.manual_charge()
    assert store.get_override().reason == "manual"
    assert "Carga manual iniciada" in messages()
    assert charge_calls(gw) == [("set_charge_mode", "charge", (0, 10))]
    engine.verify_now()
    assert store.get_last_command().verified is True
    assert "Carga manual ativa → diferido desligado" in messages("action")

    clock.now = thu(20)
    gw.snap = dataclasses.replace(gw.snap, soc=80.0)
    engine.tick()
    assert charge_calls(gw)[-1] == ("set_charge_mode", "defer", (0, 10))
    assert store.get_override() is None
    engine.verify_now()
    assert store.get_last_command().verified is True


def test_cancel_override_returns_to_automation():
    engine, gw, *_ = make(car=snap(plugged=True, charging=False, soc=70.0), apply_effect=True)
    engine.manual_charge()
    engine.verify_now()
    engine.cancel_override()
    assert store.get_override() is None
    assert charge_calls(gw)[-1] == ("set_charge_mode", "defer", (0, 10))


def test_gateway_exception_counts_as_failure():
    engine, gw, notifier, sched, _ = make()

    def boom(mode, hour):
        gw.calls.append(("set_charge_mode", mode, hour))
        raise RuntimeError("PSA down")

    gw.set_charge_mode = boom
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert len(charge_calls(gw)) == 3
    assert notifier.alerts == [ALERT_DEFER]


def test_snapshot_exception_never_propagates():
    engine, gw, notifier, *_ = make()

    def boom(refresh=False):
        raise RuntimeError("PSA down")

    engine.tick()
    gw.snapshot = boom
    engine.tick()
    for _ in range(6):  # each accepted send needs a verification step and a retry step
        engine.verify_now()
    assert len(charge_calls(gw)) == 3
    assert len(notifier.alerts) == 1


def test_new_mode_cancels_pending_round():
    engine, gw, notifier, sched, clock = make(car=snap(plugged=True, charging=False, soc=50.0))
    engine.tick()  # defer sent, verification pending
    pending = sched.scheduled[-1][1]
    clock.now = datetime(2026, 11, 13, 1, 0, tzinfo=TZ)  # vazio
    engine.tick()
    assert pending.cancelled
    assert charge_calls(gw)[-1] == ("set_charge_mode", "charge", (0, 10))


def test_charge_resent_when_car_not_charging_long_after_command():
    engine, gw, notifier, sched, clock = make(
        car=snap(plugged=True, charging=False, soc=50.0), at=datetime(2026, 11, 13, 1, 0, tzinfo=TZ)
    )
    store.set_last_command(
        store.LastCommand(mode="charge", at=clock.now - timedelta(seconds=120), verified=True, attempts=1)
    )
    engine.tick()
    assert charge_calls(gw) == []
    clock.now += timedelta(seconds=61)
    engine.tick()
    assert charge_calls(gw) == [("set_charge_mode", "charge", (0, 10))]


def test_finished_counts_as_charge_success_and_needs_no_resend():
    car = snap(plugged=True, charging=False, charge_status="Finished", soc=50.0)
    engine, gw, notifier, *_ = make(car=car, at=datetime(2026, 11, 13, 1, 0, tzinfo=TZ))
    engine.tick()
    engine.verify_now()
    assert store.get_last_command().verified is True
    engine.tick()
    assert len(charge_calls(gw)) == 1


def test_unplugged_abandons_round_without_alert():
    engine, gw, notifier, sched, _ = make()
    gw.fail_next = 3
    engine.tick()
    gw.snap = snap(plugged=False, charging=False, charge_status="Disconnected")
    engine.verify_now()
    engine.verify_now()
    assert len(charge_calls(gw)) == 1
    assert notifier.alerts == []


def test_no_vazio_tariff_falls_back_to_midnight():
    flat = [Slot(start=0, end=1440, period="cheias")]
    store.save_tariff(
        Tariff(
            name="Simples",
            seasons=[
                Season(name="Ano", months=list(range(1, 13)), days={"weekday": flat, "saturday": flat, "sunday": flat})
            ],
        )
    )
    engine, gw, *_ = make()
    engine.tick()
    assert charge_calls(gw) == [("set_charge_mode", "defer", (0, 0))]


def test_automation_off_does_nothing():
    store.save_config(AppConfig(automation_enabled=False))
    engine, gw, *_ = make()
    engine.tick()
    assert charge_calls(gw) == []


def test_unknown_state_does_not_cancel_round():
    engine, gw, notifier, *_ = make()
    gw.fail_next = 3
    known = gw.snap
    unknown = snap(plugged=None, soc=None, charging=True, charge_status="InProgress")
    engine.tick()
    for _ in range(3):
        engine.on_status(unknown)  # e.g. a partial MQTT push between polls
        engine.on_status(known)
        engine.tick()
        engine.verify_now()
    assert len(charge_calls(gw)) == 3
    assert notifier.alerts == [ALERT_DEFER]


def test_scheduler_failure_does_not_block_later_ticks():
    gw = FakeGateway(snap(plugged=True, charging=True, charge_status="InProgress"))
    sched = FakeScheduler()
    failing = {"left": 1}

    def flaky(delay_s, fn):
        if failing["left"]:
            failing["left"] -= 1
            raise RuntimeError("cannot start timer")
        return sched(delay_s, fn)

    engine = Engine(gw, FakeNotifier(), clock=Clock(thu(19)), scheduler=flaky)
    engine.tick()
    engine.tick()
    assert len(charge_calls(gw)) == 2
    assert sched.delays == [RETRY_DELAYS_S[0]]


def test_store_failure_mid_step_does_not_block_later_ticks(monkeypatch):
    engine, gw, notifier, sched, _ = make()
    gw.fail_next = 1
    engine.tick()
    real = store.set_last_command
    calls = {"n": 0}

    def flaky(c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        real(c)

    monkeypatch.setattr(store, "set_last_command", flaky)
    engine.verify_now()  # re-send raises while persisting
    engine.tick()
    assert len(charge_calls(gw)) == 3
    assert sched.delays[-1] == 60


def test_unplug_clears_override_even_with_automation_off():
    engine, gw, *_ = make(car=snap(plugged=True, charging=False, soc=70.0))
    store.set_override(store.Override(started_at=thu(18), reason="manual"))
    store.save_config(AppConfig(automation_enabled=False))
    engine.on_status(snap(plugged=False, charging=False, charge_status="Disconnected"))
    assert store.get_override() is None
    assert "Carga manual cancelada (cabo desligado)" in messages()


def test_manual_charge_with_automation_off_sends_once_without_override():
    store.save_config(AppConfig(automation_enabled=False))
    engine, gw, notifier, sched, _ = make(car=snap(plugged=True, charging=False, soc=70.0))
    engine.manual_charge()
    assert store.get_override() is None
    assert "Carga manual iniciada (automação desligada)" in messages()
    assert charge_calls(gw) == [("set_charge_mode", "charge", (0, 10))]
    engine.tick()  # "Automação desligada" must not cancel the manual round
    engine.verify_now()  # not applied -> retry later
    engine.verify_now()  # attempt 2
    assert len(charge_calls(gw)) == 2
    gw.snap = dataclasses.replace(gw.snap, charging=True, charge_status="InProgress")
    engine.verify_now()
    assert store.get_last_command().verified is True
    assert "Carga manual → diferido desligado" in messages("action")
    engine.tick()
    assert len(charge_calls(gw)) == 2


def test_manual_charge_refused_when_unplugged():
    engine, gw, *_ = make(car=snap(plugged=False, charging=False, charge_status="Disconnected"))
    engine.manual_charge()
    assert store.get_override() is None
    assert "Carga manual ignorada: cabo desligado" in messages()
    assert charge_calls(gw) == []


def test_close_cancels_round_in_flight():
    engine, gw, _, sched, _ = make()
    engine.tick()
    timer = sched.scheduled[-1][1]
    engine.close()
    assert timer.cancelled
    engine.verify_now()  # nothing left to verify
    assert len(charge_calls(gw)) == 1
