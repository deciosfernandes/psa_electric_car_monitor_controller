"""Poller: refreshes the car state, feeds the automation engine and raises health alerts.

The background thread wakes at ``min(next refresh, next planned charge/defer event + 5 s)`` and for every
MQTT-pushed snapshot. Pushed snapshots are queued by the subscriber (which runs on the paho network thread)
and handed to ``Engine.on_status`` on the poller thread only, because the engine holds its lock across
REST calls. Pushes still queued when a poll fetched a REST snapshot are discarded (see ``_discard_pushed``).

Every poll reads PSA's REST state; only some polls also wake the car (``refresh=True``), because each wakeup
costs the 12 V battery and is rate-limited by PSA. A wakeup happens only while the cable is plugged in or its
state is unknown, and then only when the poll is a follow-up ``FOLLOW_UP_DELAY`` after a planned event (catches
the car self-starting on its own program), the cable was just plugged in, a planned event happened in the last
``WAKE_AFTER_EVENT``, a command verification is pending, or the snapshot (and the last wakeup) is older than
``STALE_AFTER``. A wakeup only makes PSA's REST state fresh some seconds later, so it waits ``settle_s``
(interruptible) and fetches again before feeding the engine.
"""

import logging
import queue
import threading
from datetime import datetime, timedelta
from typing import Callable

from oauth2_client.credentials_manager import OAuthError

from . import store
from .automation.engine import Engine, Notifier, utcnow
from .gateway import CarGateway, CarSnapshot
from .psa.RemoteClient import RemoteException
from .tariff.schedule import planned_events

logger = logging.getLogger(__name__)

ALERT_AUTH = "🔑 PSACC: sessão PSA expirada, refaça o login em Definições"
ALERT_MQTT = "📡 PSACC: ligação MQTT à PSA em baixo há mais de 15 min; os comandos ao carro podem falhar."
MQTT_DOWN_ALERT_AFTER = timedelta(minutes=15)
EVENT_DELAY = timedelta(seconds=5)  # wake just after a planned event so decide() already sees the new mode
SETTLE_S = 20  # wait after a wakeup before re-fetching the REST state
WAKE_AFTER_EVENT = timedelta(minutes=15)
FOLLOW_UP_DELAY = timedelta(minutes=3)  # one extra wakeup this long after each planned event
STALE_AFTER = timedelta(minutes=60)
MAX_SLEEP_S = 60  # re-plan at least this often (config/tariff changes, clock jumps)
ERROR_BACKOFF_S = 5
AUTH_ERRORS = (RemoteException, OAuthError)


def psa_token_saver(client) -> Callable[[], None]:
    """Persist the PSA credentials whenever their serialized form changed (tokens rotate at runtime)."""
    last = store.get_psa()

    def save() -> None:
        nonlocal last
        current = client.to_dict()
        if current != last:
            store.save_psa(current)
            last = current

    return save


class Poller:
    def __init__(
        self,
        gateway: CarGateway,
        engine: Engine,
        notifier: Notifier,
        clock: Callable[[], datetime] = utcnow,
        wait: Callable[[float], bool] | None = None,
        save_tokens: Callable[[], None] | None = None,
        settle_s: float = SETTLE_S,
    ):
        self.gateway = gateway
        self.engine = engine
        self.notifier = notifier
        self._clock = clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._wait = wait or self._stop.wait  # returns True when the poller is stopping
        self._save_tokens = save_tokens
        self.settle_s = settle_s
        self._pushed: queue.Queue[CarSnapshot] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._latest: CarSnapshot | None = None
        self._last_refresh: datetime | None = None  # last interval poll (refresh_min)
        self._last_wakeup: datetime | None = None
        self._followed_up: set[datetime] = set()  # planned events whose follow-up wakeup was done (or skipped)
        self._force_wake = False  # cable just plugged in: poll and wake now
        self._auth_alerted = False
        self._mqtt_down_since: datetime | None = None
        self._mqtt_alerted = False
        gateway.subscribe(self._on_push)

    def latest(self) -> CarSnapshot | None:
        return self._latest

    # ---- one poll -------------------------------------------------------------------------------------

    def run_once(self) -> None:
        now = self._clock()
        poll_due = self._refresh_due(now)
        follow_up = self._follow_up_due(now)
        forced, self._force_wake = self._force_wake, False
        if poll_due:
            self._last_refresh = now
        if follow_up is not None:
            self._followed_up.add(follow_up)
        urgent = follow_up is not None or forced
        refresh = (poll_due or urgent) and self._wake_wanted(now, urgent)
        if refresh:
            self._last_wakeup = now  # even on failure: never hammer the rate-limited wakeup
        snap, auth_error = None, False
        try:
            snap = self._fetch(refresh)
        except AUTH_ERRORS:
            logger.exception("PSA authentication failed")
            auth_error = True
        except Exception:
            logger.exception("car snapshot failed")
        if self._stop.is_set():
            return  # stopped (replaced) while fetching: its gateway, engine and tokens are no longer current
        self._guard(self._check_auth, auth_error)
        self._guard(self._check_mqtt, now)
        if snap is not None:
            self._discard_pushed()
            self._guard(self._handle, snap)
        if self._save_tokens is not None and not self._stop.is_set():
            self._guard(self._save_tokens)

    def next_wake(self) -> datetime:
        now = self._clock()
        if self._last_refresh is None or self._force_wake or self._follow_up_due(now) is not None:
            return now
        wake = self._last_refresh + timedelta(minutes=max(store.get_config().refresh_min, 1))
        for ev in self._events(now):
            candidates = [ev.at + EVENT_DELAY]
            if ev.at not in self._followed_up:
                candidates.append(ev.at + FOLLOW_UP_DELAY)
            wake = min([wake, *(at for at in candidates if at > now)])
        return wake

    def _events(self, now: datetime) -> list:
        try:
            return planned_events(store.get_tariff(), store.get_config().offset_min, now - WAKE_AFTER_EVENT, days=2)
        except Exception:
            logger.exception("planned events unavailable")
            return []

    def _follow_up_due(self, now: datetime) -> datetime | None:
        """The planned event (its time) whose follow-up wakeup is due now and was not done yet."""
        self._followed_up = {t for t in self._followed_up if now - t < timedelta(days=1)}
        for ev in self._events(now):
            if ev.at + FOLLOW_UP_DELAY <= now < ev.at + WAKE_AFTER_EVENT and ev.at not in self._followed_up:
                return ev.at
        return None

    def _wake_wanted(self, now: datetime, urgent: bool) -> bool:
        latest = self._latest
        if latest is not None and latest.plugged is False and now - latest.at < STALE_AFTER:
            return False  # unplugged: the car cannot charge, nothing to enforce (older than that: unknown)
        if urgent or self.engine.verification_pending():
            return True
        if any(ev.at <= now < ev.at + WAKE_AFTER_EVENT for ev in self._events(now)):
            return True
        snapshot_stale = latest is None or now - latest.at >= STALE_AFTER
        return snapshot_stale and (self._last_wakeup is None or now - self._last_wakeup >= STALE_AFTER)

    def _refresh_due(self, now: datetime) -> bool:
        if self._last_refresh is None:
            return True
        return now - self._last_refresh >= timedelta(minutes=max(store.get_config().refresh_min, 1))

    def _fetch(self, refresh: bool) -> CarSnapshot | None:
        snap = self.gateway.snapshot(refresh=refresh)
        if refresh and self.settle_s > 0:
            if self._wait(self.settle_s):
                return None  # stopping
            snap = self.gateway.snapshot(refresh=False)
        return snap

    def _handle(self, snap: CarSnapshot) -> None:
        if self._stop.is_set():
            return
        previous, self._latest = self._latest, snap
        if previous is not None and previous.plugged is False and snap.plugged is True:
            self._force_wake = True  # plugged back in: get the real state and let the engine act at once
        self.engine.on_status(snap)

    @staticmethod
    def _guard(fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception:
            logger.exception("%s failed", getattr(fn, "__name__", fn))

    # ---- health alerts: one per episode -------------------------------------------------------------

    def _check_auth(self, auth_error: bool) -> None:
        if not auth_error and self.gateway.auth_ok():
            self._auth_alerted = False
            return
        if not self._auth_alerted:
            self._auth_alerted = True
            self.notifier.alert(ALERT_AUTH)

    def _check_mqtt(self, now: datetime) -> None:
        if self.gateway.mqtt_connected():
            self._mqtt_down_since, self._mqtt_alerted = None, False
            return
        if self._mqtt_down_since is None:
            self._mqtt_down_since = now
        if not self._mqtt_alerted and now - self._mqtt_down_since >= MQTT_DOWN_ALERT_AFTER:
            self._mqtt_alerted = True
            self.notifier.alert(ALERT_MQTT)

    # ---- background thread ----------------------------------------------------------------------------

    def _on_push(self, snap: CarSnapshot) -> None:  # paho network thread: queue only, never block
        self._pushed.put(snap)
        self._wake.set()

    def _discard_pushed(self) -> None:
        """Drop the pushes queued so far, before handing a fresh REST snapshot to the engine.

        Those merged before the fetch are based on an older REST snapshot (stale charging state), so the fresh
        REST snapshot must be the last state the engine sees. A newer MQTT value they carried (e.g. soc), or a
        rare push merged onto the fresh snapshot in the meantime, is picked up again by the next push or poll.
        """
        dropped = 0
        while True:
            try:
                self._pushed.get_nowait()
            except queue.Empty:
                break
            dropped += 1
        if dropped:
            logger.debug("discarded %d MQTT push(es) superseded by a REST snapshot", dropped)

    def _drain_pushed(self) -> None:
        while True:
            try:
                snap = self._pushed.get_nowait()
            except queue.Empty:
                return
            self._guard(self._handle, snap)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._wake.clear()  # before draining: a push arriving later wakes the next wait at once
                self._drain_pushed()
                delay = (self.next_wake() - self._clock()).total_seconds()
                if delay <= 0:
                    self.run_once()
                    continue
                self._wake.wait(min(delay, MAX_SLEEP_S))
            except Exception:
                logger.exception("poller iteration failed")
                self._stop.wait(ERROR_BACKOFF_S)
