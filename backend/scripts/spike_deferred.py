#!/usr/bin/env python3
"""Interactive spike: does "defer" stop an ongoing charge, what is charging.nextDelayedTime, and in which
timezone does the car read the program hour (Azores local time or UTC)?

Mandatory once before trusting the automation (see psa/NOTES.md for what to look for).
Run from backend/:  python scripts/spike_deferred.py
Car must be plugged in and charging. Writes DATA_DIR/spike-report.json (tokens, VIN, ids, position stripped).
Share that file with the developer afterwards.
"""
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psacc import settings, setup_flow, store  # noqa: E402
from psacc.gateway import PsaGateway  # noqa: E402
from psacc.psa.RemoteClient import MQTT_EVENT_TOPIC  # noqa: E402
from psacc.psa.constants import BRAND  # noqa: E402
from psacc.settings import TZ  # noqa: E402
from psacc.spike_report import (  # noqa: E402
    NOT_PLUGGED,
    PROBE_WINDOW,
    Observation,
    WakeupBudget,
    find_keys,
    observation_from,
    plug_precheck,
    probe_outcome,
    probe_target,
    sanitize,
    timezone_verdict,
)
from psacc.utils import RateLimitException  # noqa: E402

WAIT_S = 90
PROGRAM_HOUR = (0, 10)
SETTLE_S = 20  # after a wakeup, wait this long before reading (the car needs time to report)
WOKEN_TOLERANCE = timedelta(seconds=30)  # clock skew allowed between this PC and the car's timestamps
PLAIN_MAX_AGE = timedelta(seconds=60)  # a plain REST read is evidence only if the snapshot is this recent
POLL_S = 30  # plain REST reads (no wakeup) between the scheduled wakeups
PROBE_LEAD_MIN = 5  # program hour this far ahead: room for the pre-hour read
# wakeups relative to the program hour, per probe: just before it (must show "not charging"), during the
# window, and just after it (proves "not started")
PROBE_WAKES = (-timedelta(seconds=45), timedelta(minutes=2), PROBE_WINDOW + timedelta(seconds=30))
# the script's own count: RemoteClient.wakeup allows 6 per 20 min and RemoteClient.start()'s keepalive used one
budget = WakeupBudget(limit=5)
last_mqtt: dict = {"event": None, "precond_state": None, "charging_state": None}


def capture_mqtt(remote) -> None:
    """Wrap the MQTT handler so the last raw event payload is kept for the report."""
    original = remote.mqtt_client.on_message

    def wrapper(client, userdata, msg):
        if msg.topic.startswith(MQTT_EVENT_TOPIC):
            try:
                data = json.loads(msg.payload)
                last_mqtt["event"] = data
                for key in ("precond_state", "charging_state"):
                    if data.get(key) is not None:
                        last_mqtt[key] = data[key]
            except ValueError:
                pass
        original(client, userdata, msg)

    remote.mqtt_client.on_message = wrapper


def wait(seconds: int) -> None:
    for left in range(seconds, 0, -10):
        print(f"  ... {left}s", end="\r", flush=True)
        time.sleep(min(10, left))
    print(" " * 20, end="\r")


def failure(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def mqtt_charging_state() -> dict | None:
    """The last MQTT charging_state, recorded next to REST `plugged` to learn what cable_detected means."""
    return last_mqtt["charging_state"]


def raw_read(gw: PsaGateway, label: str) -> dict:
    """Never raises: a failed read is recorded in the returned entry."""
    try:
        car = gw.client.vehicles_list.get_car_by_vin(gw.vin)
        raw = gw.client.rest.get(f"/user/vehicles/{car.vehicle_id}/status")
    except Exception as exc:
        print(f"[{label}] raw read failed: {failure(exc)}")
        return {"label": label, "error": failure(exc)}
    entry = {"label": label, "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
             "charging_fields": find_keys(raw, {"nextDelayedTime", "schedule", "chargingMode", "status", "plugged"}),
             "raw_status": raw}
    print(f"[{label}] nextDelayedTime/schedule: "
          f"{json.dumps(find_keys(raw, {'nextDelayedTime', 'schedule'}), indent=None)}")
    return entry


def wakeup(gw: PsaGateway, reserve: int = 0) -> bool:
    """Send a wakeup if the budget allows (keeping `reserve` wakeups for later), then let the car settle.
    False when no wakeup was sent."""
    now = datetime.now(UTC)
    if budget.available(now) <= reserve or not budget.take(now):
        print("  (wakeup budget exhausted: plain read)")
        return False
    try:
        sent = gw.client.remote_client.wakeup(gw.vin)
    except RateLimitException:
        print("  (wakeup rate limited: plain read)")
        return False
    except Exception as exc:
        print(f"  (wakeup failed: {type(exc).__name__}; plain read)")
        return False
    if not sent:
        print("  (wakeup not sent: plain read)")
        return False
    wait(SETTLE_S)
    return True


def observe(gw: PsaGateway, wake: bool, reserve: int = 0) -> tuple[Observation, dict]:
    """One read; with `wake`, a wakeup + settle first. Returns the observation and its report entry.
    Freshness is judged on the electric energy block's timestamp; a failed read is never evidence."""
    started = datetime.now(UTC)
    woken = wake and wakeup(gw, reserve)
    window_start = started - WOKEN_TOLERANCE if woken else datetime.now(UTC) - PLAIN_MAX_AGE
    entry = {"azores_time": datetime.now(TZ).strftime("%H:%M:%S"), "woken": woken}
    try:
        snap = gw.snapshot()
    except Exception as exc:  # transient PSA/network failure: no evidence, keep going
        entry["error"] = failure(exc)
        print(f"  {entry['azores_time']} Azores: read failed ({entry['error']})")
        return Observation(window_start=window_start, at=None, charging=None), entry
    obs = observation_from(snap, window_start)
    energy_at = snap.energy_at.astimezone(TZ).strftime("%H:%M:%S") if snap.energy_at else None
    entry.update({"energy_at_azores": energy_at, "fresh": obs.fresh, "charging": snap.charging,
                  "status": snap.charge_status, "plugged": snap.plugged})
    print(f"  {entry['azores_time']} Azores: charging={snap.charging} status={snap.charge_status} "
          f"energy_at={energy_at} {'fresh' if obs.fresh else 'STALE'}{' (woken)' if woken else ''}")
    return obs, entry


def show(gw: PsaGateway, label: str, report: dict, wake: bool = True) -> None:
    """Wake (optionally), read and print; never raises: the read or its failure is added to report["reads"]."""
    if wake:
        wakeup(gw)
    try:
        snap = gw.snapshot()
    except Exception as exc:
        print(f"[{label}] read failed: {failure(exc)}")
        report["reads"].append({"label": label, "error": failure(exc)})
        return
    print(f"[{label}] soc={snap.soc} plugged={snap.plugged} charging={snap.charging} "
          f"status={snap.charge_status} next_delayed_time={snap.next_delayed_time}")
    report["reads"].append({"label": label, "soc": snap.soc, "plugged": snap.plugged, "charging": snap.charging,
                            "status": snap.charge_status, "next_delayed_time": snap.next_delayed_time,
                            "energy_at": snap.energy_at.isoformat() if snap.energy_at else None,
                            "mqtt_charging_state": mqtt_charging_state()})


def login() -> None:
    if store.get_psa():
        try:
            client = setup_flow._stored_client()
            if client.manager.ensure_access_token():
                print("Using stored PSA login.")
                return
        except Exception:
            pass
        print("Stored PSA login expired. Logging in again...")
    packages = sorted(BRAND)
    app = input(f"App package {packages} [com.psa.mym.mypeugeot]: ").strip() or "com.psa.mym.mypeugeot"
    country = input("Country code [PT]: ").strip().upper() or "PT"
    result = setup_flow.start_login(app, input("Email: ").strip(), getpass("Password: "), country)
    if not result.ok:
        print("Automatic login failed:", result.error)
        print("Open this URL, log in, and copy the code from the redirect:\n", result.manual_url)
        setup_flow.finish_login(input("Code: ").strip())
    print("Logged in; VIN stored.")


def otp() -> None:
    from psacc.psa.otp.otp import load_otp
    if load_otp() is not None and input("OTP already configured. Reuse it? [Y/n] ").strip().lower() != "n":
        return
    setup_flow.request_sms()
    setup_flow.activate_otp(input("SMS code: ").strip(), getpass("PSA app PIN: "))


def run(gw: PsaGateway, report: dict) -> None:
    # RemoteClient.start() has just sent its keepalive wakeup: a second identical wakeup seconds later is
    # avoidable (and a suspect for PSA's "502 duplicate.message.key"), so the first read only waits for it
    wait(SETTLE_S)
    show(gw, "start", report, wake=False)
    input("Plug the car in and confirm it is charging, then press Enter. ")
    show(gw, "charging baseline", report)
    report["reads"].append(raw_read(gw, "before defer"))

    print(f"Sending defer, program hour {PROGRAM_HOUR[0]:02d}:{PROGRAM_HOUR[1]:02d}")
    print("  accepted:", gw.set_charge_mode("defer", PROGRAM_HOUR))
    wait(WAIT_S)
    show(gw, "after defer (expect charging False)", report)
    report["reads"].append(raw_read(gw, "after defer"))

    print("Sending charge (immediate)")
    print("  accepted:", gw.set_charge_mode("charge", PROGRAM_HOUR))
    wait(WAIT_S)
    show(gw, "after charge (expect charging True)", report)
    report["reads"].append(raw_read(gw, "after charge"))

    report["program_hour_timezone"] = tz_test = timezone_test(gw)
    print(f"Timezone verdict: {tz_test['verdict']} ({tz_test['reason']})")
    if tz_test["verdict"] == "inconclusive":
        print("Inconclusive: do not trust the automation yet; rerun the spike (winter time, car plugged in, "
              "battery below its limit).")


def wait_for_budget(n: int) -> None:
    seconds = int((budget.free_at(datetime.now(UTC), n) - datetime.now(UTC)).total_seconds()) + 1
    if seconds > 0:
        print(f"Waiting {seconds}s for the wakeup budget ({n} wakeups needed).")
        wait(seconds)


def confirm_not_charging(gw: PsaGateway, test: dict) -> bool:
    """Defer with a program hour ~12 h away (far both in local time and in UTC), then confirm with a fresh
    read that the car is not charging. False when that cannot be confirmed."""
    wait_for_budget(1 + len(PROBE_WAKES))  # one confirmation wakeup + the probe's own wakeups
    park = (datetime.now(TZ) + timedelta(hours=12)).replace(second=0, microsecond=0)
    print(f"Deferring to {park:%H:%M} Azores so that the car is not charging before the probe")
    accepted = gw.set_charge_mode("defer", (park.hour, park.minute))
    print("  accepted:", accepted)
    wait(WAIT_S)
    for attempt in range(3):  # a retry wakes only if the probe's wakeups stay available
        obs, entry = observe(gw, wake=True, reserve=len(PROBE_WAKES) if attempt else 0)
        test["baseline"].append(entry)
        if obs.fresh:
            return accepted and obs.charging is False
        wait(POLL_S)
    return False


def plug_check(gw: PsaGateway, test: dict) -> bool:
    """The timezone probes need the car plugged in, proven by a fresh REST read (MQTT cable_detected is not
    trusted, only recorded). Otherwise the verdict is set to "inconclusive" and False is returned."""
    started = datetime.now(UTC)
    woken = wakeup(gw)
    window_start = started - WOKEN_TOLERANCE if woken else datetime.now(UTC) - PLAIN_MAX_AGE
    entry = {"azores_time": datetime.now(TZ).strftime("%H:%M:%S"), "woken": woken,
             "mqtt_charging_state": mqtt_charging_state()}
    snap = None
    try:
        snap = gw.snapshot()
        entry.update(plugged=snap.plugged, charging=snap.charging, status=snap.charge_status,
                     energy_at=snap.energy_at.isoformat() if snap.energy_at else None)
    except Exception as exc:
        entry["error"] = failure(exc)
    test["plug_check"] = entry
    ok, reason = plug_precheck(snap, window_start)
    if ok:
        return True
    test["verdict"], test["reason"] = "inconclusive", reason
    if reason == NOT_PLUGGED:
        print("\nO carro não está ligado à tomada. Ligue o carro à tomada e volte a correr o spike.")
    else:
        print(f"\nNão foi possível confirmar que o carro está ligado à tomada ({reason}). "
              "Ligue o carro à tomada e volte a correr o spike.")
    return False


def probe(gw: PsaGateway, tz, label: str) -> dict:
    """Defer with a program hour ~5 min ahead on a `tz` clock; watch whether charging starts at that instant."""
    expected = probe_target(datetime.now(UTC), tz, PROBE_LEAD_MIN)
    hour = expected.astimezone(tz)
    print(f"[{label}] sending defer, program hour {hour:%H:%M} ({tz}) = {expected.astimezone(TZ):%H:%M} Azores")
    result = {"tz": str(tz), "program_hour": f"{hour:%H:%M}", "expected_azores": f"{expected.astimezone(TZ):%H:%M}",
              "accepted": gw.set_charge_mode("defer", (hour.hour, hour.minute)), "timeline": []}
    observations: list[Observation] = []
    wakes = [expected + offset for offset in PROBE_WAKES]
    deadline = expected + PROBE_WINDOW + timedelta(minutes=3)
    while result["accepted"] and datetime.now(UTC) < deadline:
        wake = bool(wakes) and datetime.now(UTC) >= wakes[0]
        if wake:
            wakes.pop(0)
        obs, entry = observe(gw, wake)
        observations.append(obs)
        result["timeline"].append(entry)
        if obs.fresh and (obs.charging or obs.at >= expected + PROBE_WINDOW):
            break  # first fresh start (judged below), or fresh proof that nothing started within the window
        until_wake = (wakes[0] - datetime.now(UTC)).total_seconds() if wakes else POLL_S
        wait(max(1, min(POLL_S, int(until_wake))))
    if result["accepted"]:
        result["outcome"], result["reason"] = probe_outcome(observations, expected)
    else:
        result["outcome"], result["reason"] = "inconclusive", "defer command not sent"
    print(f"[{label}] {result['outcome']}: {result['reason']}")
    return result


def timezone_test(gw: PsaGateway) -> dict:
    """Is the program hour read in Azores local time or in UTC? (only distinguishable while they differ)

    Local probe: program hour = Azores now + ~5 min; a fresh not-charging read just before it is required,
    and a start counts only between that instant and 8 min later.
    Only if it provably did not start, UTC probe: program hour = UTC now + ~5 min (a car reading local time
    would start that one an hour later; a car reading UTC would start the local probe ~23 h later). Each probe
    is preceded by a fresh "not charging" confirmation. Anything unclear gives "inconclusive".
    """
    offset_h = datetime.now(TZ).utcoffset().total_seconds() / 3600
    test = {"azores_utc_offset_h": offset_h, "baseline": [], "probes": []}
    local = utc = None
    baseline_ok = True
    if offset_h != 0:
        if not plug_check(gw, test):
            return test
        print("\nTimezone test: the car stops charging now and must start again at the programmed time.")
        baseline_ok = confirm_not_charging(gw, test)
        if baseline_ok:
            result = probe(gw, TZ, "local")
            test["probes"].append(result)
            local = (result["outcome"], result["reason"])
            if local[0] == "not_started":
                baseline_ok = confirm_not_charging(gw, test)
                if baseline_ok:
                    result = probe(gw, UTC, "utc")
                    test["probes"].append(result)
                    utc = (result["outcome"], result["reason"])
    test["verdict"], test["reason"] = timezone_verdict(local, utc, baseline_ok=baseline_ok, azores_offset_h=offset_h)
    return test


def main() -> int:
    login()
    otp()
    gw = setup_flow.load_gateway()
    if gw is None or gw.client.remote_client.mqtt_client is None:
        print("No gateway or MQTT not started: login/OTP incomplete.")
        return 1
    capture_mqtt(gw.client.remote_client)
    car = gw.client.vehicles_list.get_car_by_vin(gw.vin)
    secrets = {gw.vin: "VIN", car.vehicle_id: "VEHICLE_ID", gw.client.customer_id: "CUSTOMER_ID"}
    report = {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "reads": []}
    try:
        run(gw, report)
    finally:
        try:
            choice = ""
            while choice not in ("charge", "defer"):
                choice = input('\nLeave the car in "charge" (immediate) or "defer"? ').strip().lower()
            print("  accepted:", gw.set_charge_mode(choice, PROGRAM_HOUR))
            report["left_in"] = choice
        finally:
            report["mqtt_last_event"] = last_mqtt["event"]
            report["mqtt_precond_state"] = last_mqtt["precond_state"]
            report["mqtt_charging_state"] = last_mqtt["charging_state"]
            try:  # PSA rotates the tokens: keep the latest ones for the app and the next run
                store.save_psa(gw.client.to_dict())
            except Exception as exc:
                print("Could not save the PSA tokens:", type(exc).__name__)
            path = settings.DATA_DIR / "spike-report.json"
            path.write_text(json.dumps(sanitize(report, secrets), indent=2, ensure_ascii=False), encoding="utf-8")
            print("Report written to", path)
            gw.client.remote_client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
