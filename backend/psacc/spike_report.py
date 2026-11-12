"""Helpers for the spike report: strip anything personal before the owner shares it."""
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

# substring match, or exact key for the short ones that would otherwise hit unrelated names
SENSITIVE_KEY = re.compile(r"token|secret|password|customer|position|latitude|longitude|geometry|coordinates"
                           r"|location|gps|address|latlng|vin_?number"
                           r"|^(vin|lat|lon|lng)$|[a-z_]vin$", re.I)


def probe_target(now: datetime, tz: tzinfo, minutes: int = 3) -> datetime:
    """The instant at which a car reading the program hour on a `tz` clock starts: at least `minutes` after
    `now`, rounded up to a whole minute of `tz` (send ``(target.astimezone(tz).hour, ….minute)``)."""
    target = (now + timedelta(minutes=minutes)).astimezone(tz)
    if target.second or target.microsecond:
        target = target.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return target.astimezone(now.tzinfo)


# --- timezone probe verdict (pure; the script only collects the reads) ---

PROBE_WINDOW = timedelta(minutes=8)  # a start counts only if seen between the program hour and this much later
PRE_HOUR = timedelta(minutes=2)  # a fresh not-charging read must exist this close before the program hour


@dataclass(frozen=True)
class Observation:
    """One status read during a probe.

    ``window_start``: the read is evidence only if the snapshot is at least this recent (wakeup time minus
    a small clock tolerance for a woken read; read time minus a minute for a plain REST read).
    ``at``: snapshot timestamp (UTC); ``charging``: snapshot.charging.
    """
    window_start: datetime
    at: datetime | None
    charging: bool | None

    @property
    def fresh(self) -> bool:
        return self.at is not None and self.charging is not None and self.at >= self.window_start


def observation_from(snap, window_start: datetime) -> Observation:
    """Freshness is judged on the electric energy block's timestamp (where `charging` comes from), never on
    the status' top-level updatedAt, which can be recent while the energy block is old."""
    return Observation(window_start=window_start, at=snap.energy_at, charging=snap.charging)


def probe_outcome(observations: list[Observation], expected_at: datetime,
                  window: timedelta = PROBE_WINDOW) -> tuple[str, str]:
    """("started" | "not_started" | "inconclusive", reason) for one probe whose start is expected at
    `expected_at`. Only fresh reads count. Any charging read before `expected_at` is inconclusive, and a fresh
    not-charging read within ``PRE_HOUR`` before it is required (else a car that starts on command arrival
    would pass for one that started at the hour). The first fresh charging read must then fall inside
    [expected_at, expected_at + window]; "not_started" needs a fresh not-charging read after the window."""
    fresh = sorted((o for o in observations if o.fresh), key=lambda o: o.at)
    if any(o.charging and o.at < expected_at for o in fresh):
        first = next(o for o in fresh if o.charging)
        return "inconclusive", f"charging seen before the program hour ({first.at.isoformat()})"
    if not any(expected_at - PRE_HOUR <= o.at < expected_at for o in fresh):
        return "inconclusive", "no fresh not-charging read just before the program hour"
    for o in fresh:
        if o.charging:
            if o.at < expected_at:
                return "inconclusive", f"charging seen before the program hour ({o.at.isoformat()})"
            if o.at <= expected_at + window:
                return "started", f"charging seen at {o.at.isoformat()}"
            return "inconclusive", f"charging seen only after the window ({o.at.isoformat()})"
    if any(o.at >= expected_at + window for o in fresh):
        return "not_started", "fresh not-charging read after the window"
    return "inconclusive", "no fresh read after the window"


def timezone_verdict(local: tuple[str, str] | None, utc: tuple[str, str] | None, *, baseline_ok: bool,
                     azores_offset_h: float) -> tuple[str, str]:
    """("local" | "utc" | "inconclusive", reason). Conservative: anything but a clean start of exactly one
    probe, after a confirmed not-charging baseline while Azores differs from UTC, is "inconclusive"."""
    if azores_offset_h == 0:
        return "inconclusive", "Azores on UTC+0 (summer time): local time and UTC are the same; rerun in winter"
    if not baseline_ok:
        return "inconclusive", "could not confirm with fresh data that the car stopped charging before probing"
    if local is None:
        return "inconclusive", "local probe not run"
    if local[0] == "started":
        return "local", f"local probe: {local[1]}"
    if local[0] != "not_started":
        return "inconclusive", f"local probe: {local[1]}"
    if utc is None:
        return "inconclusive", "local probe did not start and the UTC probe was not run"
    if utc[0] == "started":
        return "utc", f"local probe did not start; UTC probe: {utc[1]}"
    return "inconclusive", f"neither probe started (UTC probe: {utc[1]})"


class WakeupBudget:
    """Sliding-window count of the wakeups the script sends (RemoteClient.wakeup allows 6 per 20 min)."""

    def __init__(self, limit: int = 5, period: timedelta = timedelta(minutes=20)):
        self.limit, self.period = limit, period
        self._sent: deque[datetime] = deque()

    def _prune(self, now: datetime) -> None:
        while self._sent and self._sent[0] + self.period <= now:
            self._sent.popleft()

    def available(self, now: datetime) -> int:
        self._prune(now)
        return self.limit - len(self._sent)

    def take(self, now: datetime) -> bool:
        if self.available(now) <= 0:
            return False
        self._sent.append(now)
        return True

    def free_at(self, now: datetime, n: int) -> datetime:
        """Earliest instant at which `n` wakeups are available."""
        self._prune(now)
        missing = n - (self.limit - len(self._sent))
        return now if missing <= 0 else self._sent[missing - 1] + self.period


def _replace(text: str, secrets: dict[str, str]) -> str:
    for value, placeholder in secrets.items():
        if value:
            text = text.replace(value, placeholder)
    return text


def sanitize(obj, secrets: dict[str, str]):
    """Redact sensitive keys (whole subtree) and replace known ids by placeholders in keys and values."""
    if isinstance(obj, dict):
        return {_replace(str(k), secrets): "REDACTED" if SENSITIVE_KEY.search(str(k)) else sanitize(v, secrets)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, secrets) for v in obj]
    if isinstance(obj, str):
        return _replace(obj, secrets)
    return obj


def find_keys(obj, names: set[str], path: str = "") -> dict:
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names:
                found[f"{path}/{k}"] = v
            found.update(find_keys(v, names, f"{path}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.update(find_keys(v, names, f"{path}[{i}]"))
    return found
