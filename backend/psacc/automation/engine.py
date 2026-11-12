"""Automation engine: applies `decide()` to the car, verifies the effect, retries and alerts on failure.

A *round* is one command being enforced: up to ``MAX_ATTEMPTS`` sends, each followed by a verification.
Failed attempts wait ``RETRY_DELAYS_S[attempt - 1]`` before the car is re-checked and, if still wrong,
the command is re-sent; after the last attempt the owner is alerted once per failure episode (an episode
ends with the next verified command, and survives restarts via ``LastCommand.alerted``).
"""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal, Protocol

from .. import store
from ..gateway import CarGateway, CarSnapshot
from ..settings import TZ
from ..store import LastCommand, Override
from ..tariff.schedule import next_vazio_start
from .decide import Decision, decide

logger = logging.getLogger(__name__)

Mode = Literal["charge", "defer"]

RETRY_DELAYS_S = (60, 180, 600)  # wait after failed attempt 1, 2, 3 before re-checking the car
MAX_ATTEMPTS = len(RETRY_DELAYS_S)
FALLBACK_PROGRAM_HOUR = (0, 0)


class Notifier(Protocol):
    def alert(self, msg: str) -> None: ...

    def info(self, msg: str) -> None: ...


class Cancellable(Protocol):
    def cancel(self) -> None: ...


Scheduler = Callable[[float, Callable[[], None]], Cancellable]


def utcnow() -> datetime:
    return datetime.now(UTC)


def start_timer(delay_s: float, fn: Callable[[], None]) -> Cancellable:
    timer = threading.Timer(delay_s, fn)
    timer.daemon = True
    timer.start()
    return timer


def _deferred_label(mode: Mode) -> str:
    return "diferido desligado" if mode == "charge" else "diferido ligado"


def _verb(mode: Mode) -> str:
    return "desativar" if mode == "charge" else "ativar"


def _applied(mode: Mode, car: CarSnapshot) -> bool:
    if mode == "defer":
        return car.charging is False
    # a full battery cannot be charging, so "Finished" also means immediate charging was accepted
    return car.charging is True or car.charge_status == "Finished"


@dataclass
class _Round:
    mode: Mode
    reason: str
    alerted: bool
    manual: bool = False  # started by manual_charge() with automation off: not cancelled by "Automação desligada"
    program_hour: tuple[int, int] | None = None
    attempts: int = 0
    phase: Literal["verify", "retry"] = "verify"
    sent_at: datetime | None = None
    timer: Cancellable | None = None


class Engine:
    def __init__(
        self,
        gateway: CarGateway,
        notifier: Notifier,
        clock: Callable[[], datetime] = utcnow,
        verify_delay_s: int = 60,
        scheduler: Scheduler = start_timer,
    ):
        self.gateway = gateway
        self.notifier = notifier
        self._clock = clock
        self.verify_delay_s = verify_delay_s
        self._schedule = scheduler
        self._lock = threading.RLock()  # tick() runs from the poller, MQTT callbacks, API and timer threads
        self._round: _Round | None = None
        self._plugged: bool | None = None
        self._replugged = False  # cable just plugged back in: (re)send the command even if one was verified
        self._closed = False  # set by close(): every entry point becomes a no-op

    # ---- public entry points: never raise -------------------------------------------------------------

    def tick(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                car = self._snapshot(refresh=False)
                if car is not None:
                    self._evaluate(car)
            except Exception:
                logger.exception("automation tick failed")

    def on_status(self, snap: CarSnapshot) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._record_status(snap)
                self._evaluate(snap)
            except Exception:
                logger.exception("automation status handling failed")

    def manual_charge(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                car = self._snapshot(refresh=False)
                if car is not None and car.plugged is False:
                    store.log_activity("info", "Carga manual ignorada: cabo desligado", at=self._clock())
                    return
                if not store.get_config().automation_enabled:
                    # no override: nothing would ever end it; just send "charge" once, verified and retried
                    store.log_activity("info", "Carga manual iniciada (automação desligada)", at=self._clock())
                    self._cancel_round()
                    self._start_round(Decision("charge", "Carga manual"), store.get_last_command(), manual=True)
                    return
                store.set_override(Override(started_at=self._clock(), reason="manual"))
                store.log_activity("info", "Carga manual iniciada", at=self._clock())
                if car is not None:
                    self._evaluate(car)
                    return
            except Exception:
                logger.exception("could not start manual charge")
                return
            self.tick()

    def cancel_override(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                if store.get_override() is None:
                    return
                store.set_override(None)
                store.log_activity("info", "Carga manual cancelada", at=self._clock())
            except Exception:
                logger.exception("could not cancel manual charge")
                return
            self.tick()

    def close(self) -> None:
        """Shut this engine down for good (it is being replaced): cancel the round in flight and ignore every
        later call, so a replaced engine never commands the car through its old gateway."""
        with self._lock:
            self._closed = True
            self._cancel_round()

    def verify_now(self) -> None:
        """Advance the round in flight: verify the last command, re-send it or give up (timer callback)."""
        with self._lock:
            if self._closed:
                return
            try:
                if self._round is not None:
                    self._step(self._round)
            except Exception:
                logger.exception("automation verification failed")
                self._cancel_round()  # the next tick starts afresh (the last command is still unverified)

    # ---- internals (called with the lock held) ---------------------------------------------------------

    def _snapshot(self, refresh: bool) -> CarSnapshot | None:
        try:
            return self.gateway.snapshot(refresh=refresh)
        except Exception:
            logger.exception("car snapshot failed")
            return None

    def _record_status(self, snap: CarSnapshot) -> None:
        if snap.soc is not None:
            store.add_battery_sample(snap.at, snap.soc, bool(snap.charging))
        if snap.plugged is None:
            return
        if self._plugged is not None and snap.plugged != self._plugged:
            store.log_activity("info", "Cabo ligado" if snap.plugged else "Cabo desligado", at=self._clock())
            self._replugged = snap.plugged
        self._plugged = snap.plugged

    def _evaluate(self, car: CarSnapshot) -> None:
        now = self._clock()
        cfg = store.get_config()
        override = store.get_override()
        if car.plugged is False and override is not None:
            store.set_override(None)
            store.log_activity("info", "Carga manual cancelada (cabo desligado)", at=now)
            override = None
        decision = decide(now, cfg, store.get_tariff(), car, override)
        if decision.clear_override and override is not None:
            store.set_override(None)
        if self._round is not None and self._round.timer is None:
            # a round always has an armed timer between steps; without one it died mid-step
            logger.warning("dropping %s round left without a timer", self._round.mode)
            self._round = None
        if decision.mode is None:
            # unknown car state keeps the round going; only unplugging or switching automation off ends it
            if self._round is not None and (
                car.plugged is False or (not cfg.automation_enabled and not self._round.manual)
            ):
                self._cancel_round()
            return
        if self._round is not None:
            if self._round.mode == decision.mode:
                return  # already being enforced
            self._cancel_round()
        last = store.get_last_command()
        if self._replugged or self._needs_command(decision.mode, car, last, now):
            self._replugged = False
            self._start_round(decision, last)

    def verification_pending(self) -> bool:
        """A command is in flight (sent, not yet verified or given up). Read without the lock: advisory only."""
        return self._round is not None

    def _needs_command(self, mode: Mode, car: CarSnapshot, last: LastCommand | None, now: datetime) -> bool:
        if last is None or last.mode != mode or not last.verified:
            return True
        if mode == "defer":
            # a stale program hour (tariff/offset edited, or the next vazio start moved) would let the car
            # start on its own at the wrong time
            return bool(car.charging) or last.program_hour != self._program_hour(now)
        stale = now - last.at > timedelta(seconds=self.verify_delay_s * 3)
        return not car.charging and car.charge_status != "Finished" and stale

    def _start_round(self, decision: Decision, last: LastCommand | None, manual: bool = False) -> None:
        # the alert flag is cleared on every verified success, so carrying it over keeps one alert per episode
        self._round = _Round(
            mode=decision.mode, reason=decision.reason, alerted=bool(last and last.alerted), manual=manual
        )
        try:
            self._attempt(self._round)
        except Exception:
            self._cancel_round()  # never leave a round without an armed timer: it would block every later tick
            raise

    def _cancel_round(self) -> None:
        if self._round is not None and self._round.timer is not None:
            self._round.timer.cancel()
        self._round = None

    def _program_hour(self, now: datetime) -> tuple[int, int]:
        try:
            start = next_vazio_start(store.get_tariff(), store.get_config().offset_min, now).astimezone(TZ)
        except ValueError:
            logger.warning("tariff has no usable vazio window, programming %02d:%02d", *FALLBACK_PROGRAM_HOUR)
            return FALLBACK_PROGRAM_HOUR
        return start.hour, start.minute

    def _attempt(self, r: _Round) -> None:
        r.attempts += 1
        r.sent_at = self._clock()
        r.program_hour = self._program_hour(r.sent_at)
        try:
            ok = self.gateway.set_charge_mode(r.mode, r.program_hour)
        except Exception:
            logger.exception("set_charge_mode(%s) raised", r.mode)
            ok = False
        self._persist(r, verified=False)
        if ok:
            r.phase = "verify"
            self._arm(r, self.verify_delay_s)
        else:
            logger.warning("set_charge_mode(%s) attempt %d failed", r.mode, r.attempts)
            self._retry_later(r)

    def _retry_later(self, r: _Round) -> None:
        r.phase = "retry"
        self._arm(r, RETRY_DELAYS_S[r.attempts - 1])

    def _arm(self, r: _Round, delay_s: float) -> None:
        def fire() -> None:
            with self._lock:
                if self._round is r:  # ignore timers of a cancelled or replaced round
                    self.verify_now()

        r.timer = self._schedule(delay_s, fire)

    def _step(self, r: _Round) -> None:
        r.timer = None
        car = self._snapshot(refresh=r.phase == "verify")
        if car is not None and car.plugged is False:
            logger.info("car unplugged, dropping pending %s command", r.mode)
            self._round = None
            return
        if car is not None and _applied(r.mode, car):
            self._succeed(r)
        elif r.phase == "verify":
            logger.warning("%s not applied after attempt %d", r.mode, r.attempts)
            self._retry_later(r)
        elif r.attempts < MAX_ATTEMPTS:
            self._attempt(r)
        else:
            self._give_up(r)

    def _succeed(self, r: _Round) -> None:
        self._round = None
        r.alerted = False
        self._persist(r, verified=True)
        store.log_activity("action", f"{r.reason} → {_deferred_label(r.mode)}", at=self._clock())
        text = "🔌 Carga iniciada" if r.mode == "charge" else "⏸ Carga diferida"
        try:
            self.notifier.info(f"{text} ({r.reason})")  # the notifier sends it only if info messages are on
        except Exception:
            logger.exception("info message could not be sent")

    def _give_up(self, r: _Round) -> None:
        self._round = None
        if r.alerted:
            logger.warning("%s still failing after %d attempts (already alerted)", r.mode, r.attempts)
            return
        store.log_activity(
            "error",
            f"Não consegui {_verb(r.mode)} a carga diferida após {r.attempts} tentativas ({r.reason})",
            at=self._clock(),
        )
        r.alerted = True
        self._persist(r, verified=False)
        if r.mode == "charge":
            text = f"⚠️ PSACC: não consegui iniciar a carga no vazio ({r.reason})."
        else:
            text = (
                f"⚠️ PSACC: não consegui {_verb(r.mode)} a carga diferida ({r.reason}). "
                "O carro pode estar a carregar fora do vazio."
            )
        try:
            self.notifier.alert(text)
        except Exception:
            logger.exception("failure alert could not be sent")

    def _persist(self, r: _Round, verified: bool) -> None:
        store.set_last_command(
            LastCommand(
                mode=r.mode,
                at=r.sent_at,
                verified=verified,
                attempts=r.attempts,
                reason=r.reason,
                alerted=r.alerted,
                program_hour=r.program_hour,
            )
        )
