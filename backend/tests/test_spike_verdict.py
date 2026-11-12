"""R1: the timezone verdict of the spike is a pure function over a timeline of reads."""
from datetime import UTC, datetime, timedelta

from psacc.settings import TZ
from psacc.spike_report import (
    Observation,
    WakeupBudget,
    observation_from,
    probe_outcome,
    probe_target,
    timezone_verdict,
)

P = datetime(2026, 11, 12, 12, 3, tzinfo=UTC)  # program hour 11h03 Azores (UTC-1 in winter)
M = timedelta(minutes=1)


def woken(at: datetime, charging: bool) -> Observation:
    """A read 20 s after a wakeup sent at `at`: the snapshot is newer than the wakeup, so it is evidence."""
    return Observation(window_start=at - timedelta(seconds=30), at=at + timedelta(seconds=10), charging=charging)


def stale(read_at: datetime, snapshot_at: datetime, charging: bool) -> Observation:
    return Observation(window_start=read_at - timedelta(seconds=60), at=snapshot_at, charging=charging)


PRE = woken(P - timedelta(seconds=45), False)  # the woken read just before the program hour


def test_start_at_program_hour_is_started():
    obs = [PRE, woken(P + 2 * M, False), woken(P + 5 * M, True)]
    assert probe_outcome(obs, P)[0] == "started"


def test_start_without_a_fresh_pre_hour_read_is_inconclusive():
    """A car that starts as soon as the command arrives must never pass for one that started at the hour."""
    started = [woken(P + 2 * M, True)]
    outcome, reason = probe_outcome(started, P)
    assert outcome == "inconclusive" and "before the program hour" in reason
    # pre-hour read stale, or taken too long before the hour: no evidence either
    stale_pre = stale(P - timedelta(seconds=30), P - 10 * M, False)
    assert probe_outcome([stale_pre] + started, P)[0] == "inconclusive"
    assert probe_outcome([woken(P - 5 * M, False)] + started, P)[0] == "inconclusive"
    # not_started also needs it
    assert probe_outcome([woken(P + 2 * M, False), woken(P + 8 * M, False)], P)[0] == "inconclusive"


def test_charging_at_the_pre_hour_read_is_inconclusive():
    obs = [woken(P - timedelta(seconds=45), True), woken(P + 2 * M, True)]
    outcome, reason = probe_outcome(obs, P)
    assert outcome == "inconclusive" and "before" in reason


def test_charging_seen_before_program_hour_is_inconclusive():
    obs = [woken(P - 2 * M, True), woken(P + 2 * M, True)]
    outcome, reason = probe_outcome(obs, P)
    assert outcome == "inconclusive" and "before" in reason


def test_start_after_the_window_is_inconclusive_not_started():
    obs = [PRE, woken(P + 2 * M, False), woken(P + 8 * M, False), woken(P + 60 * M, True)]
    assert probe_outcome(obs, P)[0] == "inconclusive"


def test_no_start_needs_a_fresh_read_after_the_window():
    assert probe_outcome([PRE, woken(P + 2 * M, False), woken(P + 8 * M, False)], P)[0] == "not_started"
    assert probe_outcome([PRE, woken(P + 2 * M, False)], P)[0] == "inconclusive"
    assert probe_outcome([], P)[0] == "inconclusive"


def test_stale_reads_are_not_evidence():
    # charging=True from a snapshot taken before the program hour but read after it: stale, ignored
    obs = [woken(P + 2 * M, False), stale(P + 4 * M, P - 10 * M, True), stale(P + 9 * M, P - 10 * M, False)]
    outcome, _ = probe_outcome(obs, P)
    assert outcome == "inconclusive"  # neither a start nor a fresh read after the window
    # a stale charging=True never makes "started"
    assert probe_outcome([stale(P + 5 * M, P + 5 * M - 2 * M, True)], P)[0] == "inconclusive"
    # unknown charging / missing timestamp are ignored
    assert probe_outcome([Observation(P, P + 5 * M, None), Observation(P, None, True)], P)[0] == "inconclusive"


def test_timezone_verdict():
    started, not_started, inc = ("started", ""), ("not_started", ""), ("inconclusive", "x")
    assert timezone_verdict(started, None, baseline_ok=True, azores_offset_h=-1)[0] == "local"
    assert timezone_verdict(not_started, started, baseline_ok=True, azores_offset_h=-1)[0] == "utc"
    assert timezone_verdict(not_started, not_started, baseline_ok=True, azores_offset_h=-1)[0] == "inconclusive"
    assert timezone_verdict(not_started, inc, baseline_ok=True, azores_offset_h=-1)[0] == "inconclusive"
    assert timezone_verdict(not_started, None, baseline_ok=True, azores_offset_h=-1)[0] == "inconclusive"
    assert timezone_verdict(inc, started, baseline_ok=True, azores_offset_h=-1)[0] == "inconclusive"
    # no confirmed "not charging" before probing, or Azores on UTC+0: never conclusive
    assert timezone_verdict(started, None, baseline_ok=False, azores_offset_h=-1)[0] == "inconclusive"
    assert timezone_verdict(started, None, baseline_ok=True, azores_offset_h=0)[0] == "inconclusive"


def test_probe_target_is_the_program_hour_as_an_instant():
    now = datetime(2026, 11, 12, 12, 0, 30, tzinfo=UTC)  # 11h00:30 Azores
    local = probe_target(now, TZ)
    assert local == datetime(2026, 11, 12, 12, 4, tzinfo=UTC)  # 11h04 Azores, >= 3 min ahead
    assert (local.astimezone(TZ).hour, local.astimezone(TZ).minute) == (11, 4)
    assert probe_target(now, UTC) == local  # same instant, read as 12:04 on a UTC clock


def test_wakeup_budget_sliding_window():
    t0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    budget = WakeupBudget(limit=2, period=timedelta(minutes=20))
    assert budget.take(t0) and budget.take(t0 + M)
    assert not budget.take(t0 + 2 * M)  # refused calls are not counted
    assert budget.available(t0 + 2 * M) == 0
    assert budget.free_at(t0 + 2 * M, 1) == t0 + 20 * M
    assert budget.free_at(t0 + 2 * M, 2) == t0 + 21 * M
    assert budget.take(t0 + 20 * M)


def test_freshness_is_judged_on_the_electric_energy_timestamp():
    """The top-level updatedAt can be fresh while the energy block (source of `charging`) is old."""
    import json
    from pathlib import Path

    from psacc.gateway import snapshot_from_status
    from psacc.psa.models import VehicleStatus

    raw = json.loads((Path(__file__).parent / "data" / "status_v4_17.json").read_text(encoding="utf-8"))
    now = datetime(2026, 11, 12, 12, 5, tzinfo=UTC)
    raw["updatedAt"] = now.isoformat()
    raw["energies"][0]["createdAt"] = (now - 30 * M).isoformat()
    snap = snapshot_from_status("VIN", VehicleStatus.model_validate(raw))
    assert snap.at == now and snap.energy_at == now - 30 * M
    assert not observation_from(snap, window_start=now - timedelta(seconds=30)).fresh
    raw["energies"][0]["updatedAt"] = now.isoformat()  # an updatedAt on the block wins over its createdAt
    snap = snapshot_from_status("VIN", VehicleStatus.model_validate(raw))
    assert observation_from(snap, window_start=now - timedelta(seconds=30)).fresh
    del raw["energies"][0]["updatedAt"], raw["energies"][0]["createdAt"]
    snap = snapshot_from_status("VIN", VehicleStatus.model_validate(raw))
    assert snap.energy_at is None and not observation_from(snap, window_start=now - M).fresh
