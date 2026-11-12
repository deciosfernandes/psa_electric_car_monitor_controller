import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from psacc import settings, store
from psacc.automation.engine import RETRY_DELAYS_S, SETTLE_S, Engine
from psacc.places import Place
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
    store.save_place(Place(name="Casa", kind="home", lat=HOME[0], lon=HOME[1]))  # the default snapshot is at Casa


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


def no_wait(seconds: float) -> bool:
    return False  # settle waits are instantaneous in tests; True would mean "engine closing"


THU = datetime(2026, 11, 12, tzinfo=TZ)  # a Thursday, winter season
HOME = (37.74, -25.67)
WORK = (37.80, -25.50)
OTHER = (37.70, -25.40)


def thu(h, mi=0):
    return THU.replace(hour=h, minute=mi)


def snap(**kw):
    return dataclasses.replace(default_snapshot(), **{"position_at": THU.astimezone(UTC), **kw})


def make(car=None, at=thu(19), apply_effect=False):
    gw = FakeGateway(
        car or snap(plugged=True, charging=True, charge_status="InProgress", soc=50.0), apply_effect=apply_effect
    )
    notifier, sched, clock = FakeNotifier(), FakeScheduler(), Clock(at)
    engine = Engine(gw, notifier, clock=clock, scheduler=sched, wait=no_wait)
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
    restarted = Engine(gw, notifier2, clock=Clock(thu(19, 30)), scheduler=FakeScheduler(), wait=no_wait)
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


def test_no_vazio_window_soon_falls_back_to_midnight():
    flat = [Slot(start=0, end=1440, period="cheias")]
    summer = [Slot(start=0, end=420, period="vazio"), Slot(start=420, end=1440, period="cheias")]
    store.save_tariff(
        Tariff(
            name="Vazio só no verão",
            seasons=[
                Season(name="Inverno", months=[11, 12, 1, 2, 3, 4, 5],
                       days={"weekday": flat, "saturday": flat, "sunday": flat}),
                Season(name="Verão", months=[6, 7, 8, 9, 10],
                       days={"weekday": summer, "saturday": summer, "sunday": summer}),
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

    engine = Engine(gw, FakeNotifier(), clock=Clock(thu(19)), scheduler=flaky, wait=no_wait)
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


def test_closed_engine_is_inert():
    engine, gw, _, sched, _ = make()
    engine.close()
    engine.tick()
    engine.on_status(snap(plugged=True, charging=True, charge_status="InProgress", soc=50.0))
    engine.manual_charge()
    engine.verify_now()
    assert gw.calls == []
    assert sched.scheduled == []
    assert store.get_override() is None


def test_verified_defer_resent_when_program_hour_changes():
    engine, gw, notifier, sched, clock = make(car=snap(plugged=True, charging=False, soc=50.0), apply_effect=True)
    engine.tick()
    engine.verify_now()
    last = store.get_last_command()
    assert (last.mode, last.verified, last.program_hour) == ("defer", True, (0, 10))
    engine.tick()
    assert len(charge_calls(gw)) == 1  # same program hour: nothing to do
    store.save_config(AppConfig(offset_min=25))  # next vazio start moves to 00h25
    engine.tick()
    assert charge_calls(gw)[-1] == ("set_charge_mode", "defer", (0, 25))
    engine.verify_now()
    assert store.get_last_command().program_hour == (0, 25)


def test_defer_without_stored_program_hour_is_resent():
    engine, gw, *_ = make(car=snap(plugged=True, charging=False, soc=50.0))
    store.set_last_command(store.LastCommand(mode="defer", at=thu(18), verified=True, attempts=1))
    engine.tick()
    assert charge_calls(gw) == [("set_charge_mode", "defer", (0, 10))]


def test_info_message_on_verified_success():
    engine, gw, notifier, sched, clock = make(car=snap(plugged=True, charging=True, soc=50.0), apply_effect=True)
    engine.tick()
    assert notifier.infos == []  # only once verified
    engine.verify_now()
    assert notifier.infos == ["⏸ Carga diferida (Fora do vazio (ponta))"]
    clock.now = datetime(2026, 11, 13, 1, 0, tzinfo=TZ)  # vazio
    engine.tick()
    engine.verify_now()
    assert notifier.infos[-1] == "🔌 Carga iniciada (Período de vazio)"


def test_no_info_message_when_not_verified():
    engine, gw, notifier, *_ = make()
    gw.fail_next = 3
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert notifier.infos == []


def test_charge_failure_alert_text():
    engine, gw, notifier, *_ = make(
        car=snap(plugged=True, charging=False, soc=50.0), at=datetime(2026, 11, 13, 1, 0, tzinfo=TZ)
    )
    gw.fail_next = 3
    engine.tick()
    for _ in range(3):
        engine.verify_now()
    assert notifier.alerts == ["⚠️ PSACC: não consegui iniciar a carga no vazio (Período de vazio)."]


def test_replug_forces_defer_even_after_verified_defer():
    engine, gw, notifier, sched, clock = make(car=snap(plugged=True, charging=False, soc=50.0), apply_effect=True)
    engine.on_status(gw.snap)
    engine.verify_now()
    assert store.get_last_command().verified is True
    gw.snap = snap(plugged=False, charging=False, charge_status="Disconnected")
    engine.on_status(gw.snap)
    assert len(charge_calls(gw)) == 1  # unplugged: nothing sent
    clock.now = thu(21)
    gw.snap = snap(plugged=True, charging=False, charge_status="Stopped", soc=50.0)
    engine.on_status(gw.snap)  # plugged back in outside vazio, not (yet) charging
    assert charge_calls(gw) == [("set_charge_mode", "defer", (0, 10))] * 2
    engine.verify_now()
    engine.on_status(gw.snap)
    assert len(charge_calls(gw)) == 2  # the forced command is one-off


class SettlingGateway(FakeGateway):
    """The woken read still shows the old state; PSA's REST state is fresh only after the settle wait."""

    def __init__(self, car):
        super().__init__(car)
        self.reads: list[bool] = []

    def snapshot(self, refresh: bool = False):
        self.reads.append(refresh)
        return self.snap


def test_verification_wakes_settles_and_rereads_before_judging():
    gw = SettlingGateway(snap(plugged=True, charging=True, charge_status="InProgress"))
    notifier, sched, waits = FakeNotifier(), FakeScheduler(), []

    def wait(seconds):
        waits.append(seconds)
        gw.snap = dataclasses.replace(gw.snap, charging=False, charge_status="Stopped")  # the car caught up
        return False

    engine = Engine(gw, notifier, clock=Clock(thu(19)), scheduler=sched, wait=wait)
    engine.tick()
    gw.reads.clear()
    engine.verify_now()
    assert waits == [SETTLE_S] and SETTLE_S == 20
    assert gw.reads == [True, False]  # wakeup read, then the settled re-read that is judged
    assert store.get_last_command().verified is True
    assert len(charge_calls(gw)) == 1


def test_verification_abandoned_when_closing_during_settle():
    gw = SettlingGateway(snap(plugged=True, charging=True, charge_status="InProgress"))
    engine = Engine(gw, FakeNotifier(), clock=Clock(thu(19)), scheduler=FakeScheduler(), wait=lambda s: True)
    engine.tick()
    gw.reads.clear()
    engine.verify_now()
    assert gw.reads == [True]  # no judgement on the unsettled read, no retry
    assert store.get_last_command().verified is False
    assert len(charge_calls(gw)) == 1


def test_replug_flag_cleared_when_evaluation_returns_early():
    """R6: a re-plug seen while nothing can be decided (state unknown) does not force a resend later."""
    engine, gw, notifier, sched, clock = make(car=snap(plugged=True, charging=False, soc=50.0), apply_effect=True)
    engine.on_status(gw.snap)
    engine.verify_now()
    gw.snap = snap(plugged=False, charging=False, charge_status="Disconnected")
    engine.on_status(gw.snap)
    gw.snap = snap(plugged=True, charging=False, charge_status="Stopped", soc=None)
    engine.on_status(gw.snap)  # plugged back in, battery unknown: decide() returns no mode
    assert len(charge_calls(gw)) == 1
    gw.snap = snap(plugged=True, charging=False, charge_status="Stopped", soc=50.0)
    engine.on_status(gw.snap)  # same vazio program, verified, not charging: nothing to send
    assert len(charge_calls(gw)) == 1


# ---- location (Plan 2) -------------------------------------------------------------------------------------


def at_place(latlon, minutes=0, **kw):
    pos_at = (THU + timedelta(minutes=minutes)).astimezone(UTC)
    return snap(latitude=latlon[0], longitude=latlon[1], position_at=pos_at, **kw)


def add_work():
    store.save_place(Place(name="Trabalho", kind="work", lat=WORK[0], lon=WORK[1]))


def test_engine_persists_fix_and_exposes_where():
    engine, gw, *_ = make(car=at_place(HOME, plugged=True, charging=False))
    assert engine.last_where is None
    engine.on_status(gw.snap)
    assert store.get_position_fix().at == THU.astimezone(UTC)
    assert store.get_position_fix().mileage_km == gw.snap.mileage_km
    assert engine.last_where == "home" and engine.last_place.name == "Casa"
    gw.snap = at_place(OTHER, minutes=30, plugged=True, charging=False)
    engine.tick()
    assert (engine.last_where, engine.last_place) == ("other", None)
    assert (store.get_position_fix().lat, store.get_position_fix().lon) == OTHER


def test_work_charge_once_per_episode():
    add_work()
    engine, gw, notifier, sched, clock = make(car=at_place(WORK, plugged=True, charging=False), apply_effect=True)
    engine.on_status(gw.snap)
    assert charge_calls(gw) == [("set_charge_mode", "charge", (0, 10))]
    assert "Carga no Trabalho iniciada" in messages("info")
    engine.on_status(gw.snap)  # same episode: nothing more, the round is being verified
    engine.verify_now()
    assert store.get_last_command().verified is True
    gw.snap = dataclasses.replace(gw.snap, charging=False, charge_status="Stopped")  # e.g. stopped from the car
    clock.now = thu(21)
    engine.on_status(gw.snap)
    engine.tick()
    assert len(charge_calls(gw)) == 1
    gw.snap = dataclasses.replace(gw.snap, plugged=False, charge_status="Disconnected")
    engine.on_status(gw.snap)
    gw.snap = dataclasses.replace(gw.snap, plugged=True, charge_status="Stopped")
    engine.on_status(gw.snap)  # new plug-in episode
    assert charge_calls(gw) == [("set_charge_mode", "charge", (0, 10))] * 2


def test_work_episode_survives_restart():
    add_work()
    engine, gw, *_ = make(car=at_place(WORK, plugged=True, charging=False))
    engine.on_status(gw.snap)
    restarted = Engine(gw, FakeNotifier(), clock=Clock(thu(19, 5)), scheduler=FakeScheduler(), wait=no_wait)
    restarted.on_status(gw.snap)
    assert len(charge_calls(gw)) == 1


def test_work_charge_ignores_verified_charge_and_override():
    add_work()
    engine, gw, *_ = make(car=at_place(WORK, plugged=True, charging=True, charge_status="InProgress"))
    store.set_override(store.Override(started_at=thu(18), reason="manual"))
    # a recent verified charge (e.g. from Casa) would stop the Plan 1 rules from re-sending
    store.set_last_command(store.LastCommand(mode="charge", at=thu(18, 59), verified=True, attempts=1))
    engine.on_status(gw.snap)
    assert charge_calls(gw) == [("set_charge_mode", "charge", (0, 10))]


def test_work_charge_failure_alert_text():
    add_work()
    engine, gw, notifier, *_ = make(car=at_place(WORK, plugged=True, charging=False))
    gw.fail_next = 3
    engine.on_status(gw.snap)
    for _ in range(3):
        engine.verify_now()
    assert len(charge_calls(gw)) == 3
    assert notifier.alerts == ["⚠️ PSACC: não consegui iniciar a carga no Trabalho."]
    assert messages("error") == ["Não consegui iniciar a carga no Trabalho após 3 tentativas (Carga no Trabalho)"]


def test_no_defer_outside_home():
    add_work()
    for latlon, soc in ((OTHER, 50.0), (OTHER, 95.0), (WORK, 95.0)):
        store.set_position_fix(None)
        store.set_work_episode(None)
        engine, gw, *_ = make(car=at_place(latlon, plugged=True, charging=True, charge_status="InProgress", soc=soc))
        engine.on_status(gw.snap)
        engine.verify_now()
        engine.tick()
        assert [c for c in charge_calls(gw) if c[1] == "defer"] == []


def test_no_command_without_home_place_or_position():
    for p in store.list_places():
        store.delete_place(p.id)
    engine, gw, *_ = make()
    engine.tick()
    assert engine.last_where == "unset" and charge_calls(gw) == []
    store.save_place(Place(name="Casa", kind="home", lat=HOME[0], lon=HOME[1]))
    gw.snap = snap(latitude=None, longitude=None, position_at=None)
    store.set_position_fix(None)
    engine.tick()
    assert engine.last_where == "unknown" and charge_calls(gw) == []


def test_pending_defer_dropped_when_car_no_longer_at_home():
    engine, gw, notifier, sched, _ = make(car=at_place(HOME, plugged=True, charging=True, charge_status="InProgress"))
    gw.fail_next = 1
    engine.on_status(gw.snap)
    assert charge_calls(gw) == [("set_charge_mode", "defer", (0, 10))]
    gw.snap = at_place(OTHER, minutes=5, plugged=True, charging=True, charge_status="InProgress")
    engine.on_status(gw.snap)
    assert not engine.verification_pending()
    engine.verify_now()
    assert len(charge_calls(gw)) == 1
    assert notifier.alerts == []


def test_defer_retry_never_sent_once_car_is_not_known_at_home():
    engine, gw, *_ = make(car=at_place(HOME, plugged=True, charging=True, charge_status="InProgress"))
    gw.fail_next = 1
    engine.on_status(gw.snap)
    engine.last_where = "unknown"  # e.g. located elsewhere by an evaluation that then failed
    engine.verify_now()
    assert len(charge_calls(gw)) == 1 and not engine.verification_pending()


def test_work_episode_kept_when_home_place_recreated():
    add_work()
    engine, gw, *_ = make(car=at_place(WORK, plugged=True, charging=False))
    engine.on_status(gw.snap)
    home = next(p for p in store.list_places() if p.kind == "home")
    store.delete_place(home.id)
    engine.tick()
    assert engine.last_where == "unset" and store.get_work_episode() is not None
    store.save_place(Place(name="Casa", kind="home", lat=HOME[0], lon=HOME[1]))
    engine.tick()
    assert len(charge_calls(gw)) == 1


def test_manual_charge_at_work_after_episode_charge():
    add_work()
    engine, gw, notifier, sched, clock = make(car=at_place(WORK, plugged=True, charging=False), apply_effect=True)
    engine.on_status(gw.snap)
    engine.verify_now()
    gw.snap = dataclasses.replace(gw.snap, charging=False, charge_status="Stopped")  # charge stopped later
    clock.now = thu(20)
    engine.manual_charge()
    assert [c[1] for c in charge_calls(gw)] == ["charge", "charge"]
    engine.on_status(gw.snap)  # the manual round is being verified: no third command
    assert len(charge_calls(gw)) == 2


def test_engine_records_one_health_sample_per_day():
    engine, gw, *_ = make(car=snap(battery_health=93.0))
    engine.on_status(gw.snap)
    gw.snap = snap(battery_health=92.5)
    engine.on_status(gw.snap)
    assert store.health_samples() == [(thu(19).astimezone(TZ).date(), 92.5)]


def test_engine_records_charge_session_with_location():
    first = snap(plugged=True, charging=True, charge_status="InProgress", soc=50.0, energy_at=thu(19))
    engine, *_ = make(first)
    engine.on_status(first)
    sessions = store.charge_sessions(10)
    assert len(sessions) == 1 and sessions[0].place_kind == engine.last_where
    assert sessions[0].partial is True and len(store.charge_samples(sessions[0].id)) == 1
    engine.on_status(dataclasses.replace(first, energy_at=thu(19, 10), soc=52.0))
    assert len(store.charge_samples(sessions[0].id)) == 2
    assert store.charge_sessions(10)[0].soc_end == 52.0


def test_engine_charge_cost_covers_whole_session_and_keeps_price_override():
    from psacc.history.charges import session_kwh

    first = snap(plugged=True, charging=True, charge_status="InProgress", soc=50.0, energy_at=thu(19))
    engine, *_ = make(first)
    engine.on_status(first)
    sid = store.charge_sessions(10)[0].id
    store.set_session_price(sid, 0.5)
    engine.on_status(dataclasses.replace(first, energy_at=thu(19, 10), soc=52.0))
    assert store.get_charge_session(sid).price_override_eur_kwh == 0.5
    for i, mins in enumerate((20, 30)):  # charge finished between polls: soc gained on the first stop snapshot
        engine.on_status(dataclasses.replace(first, energy_at=thu(19, mins), soc=55.0, charging=False))
    done = store.get_charge_session(sid)
    assert done.ended_at is not None and done.soc_end == 55.0
    assert done.kwh == session_kwh(50, 55, 46) and done.cost_eur == pytest.approx(done.kwh * 0.5)
    assert store.charge_samples(sid)[-1].soc == 55.0


def test_engine_records_trip_and_survives_trip_store_failure(monkeypatch):
    base = snap(plugged=False, charging=False, soc=80.0, mileage_km=1000.0)
    engine, *_ = make(base)
    engine.on_status(base)
    engine.on_status(dataclasses.replace(base, at=thu(19, 20), mileage_km=1010.0, soc=75.0))
    engine.on_status(dataclasses.replace(base, at=thu(19, 40), mileage_km=1010.0, soc=75.0))
    assert [t.km for t in store.trips(10)] == [10.0]

    def boom(_):
        raise RuntimeError("db")

    monkeypatch.setattr(store, "save_trip", boom)
    engine.on_status(dataclasses.replace(base, at=thu(20, 0), mileage_km=1020.0))
    engine.on_status(dataclasses.replace(base, at=thu(20, 20), mileage_km=1020.0))  # logged, not raised
