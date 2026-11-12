#!/usr/bin/env python3
"""Interactive spike: does "defer" stop an ongoing charge, and what is charging.nextDelayedTime?

Run from backend/:  python scripts/spike_deferred.py
Car must be plugged in and charging. Writes DATA_DIR/spike-report.json (tokens, VIN, ids, position stripped).
Share that file with the developer afterwards.
"""
import json
import sys
import time
from datetime import datetime
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psacc import settings, setup_flow, store  # noqa: E402
from psacc.gateway import PsaGateway  # noqa: E402
from psacc.psa.RemoteClient import MQTT_EVENT_TOPIC  # noqa: E402
from psacc.psa.constants import BRAND  # noqa: E402
from psacc.spike_report import find_keys, sanitize  # noqa: E402

WAIT_S = 90
PROGRAM_HOUR = (0, 10)
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


def raw_read(gw: PsaGateway, label: str) -> dict:
    car = gw.client.vehicles_list.get_car_by_vin(gw.vin)
    raw = gw.client.rest.get(f"/user/vehicles/{car.vehicle_id}/status")
    entry = {"label": label, "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
             "charging_fields": find_keys(raw, {"nextDelayedTime", "schedule", "chargingMode", "status", "plugged"}),
             "raw_status": raw}
    print(f"[{label}] nextDelayedTime/schedule: "
          f"{json.dumps(find_keys(raw, {'nextDelayedTime', 'schedule'}), indent=None)}")
    return entry


def show(gw: PsaGateway, label: str) -> None:
    snap = gw.snapshot(refresh=True)
    print(f"[{label}] soc={snap.soc} plugged={snap.plugged} charging={snap.charging} "
          f"status={snap.charge_status} next_delayed_time={snap.next_delayed_time}")


def login() -> None:
    if store.get_psa():
        print("Using stored PSA login.")
        return
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
    show(gw, "start")
    input("Plug the car in and confirm it is charging, then press Enter. ")
    show(gw, "charging baseline")
    report["reads"].append(raw_read(gw, "before defer"))

    print(f"Sending defer, program hour {PROGRAM_HOUR[0]:02d}:{PROGRAM_HOUR[1]:02d}")
    print("  accepted:", gw.set_charge_mode("defer", PROGRAM_HOUR))
    wait(WAIT_S)
    show(gw, "after defer (expect charging False)")
    report["reads"].append(raw_read(gw, "after defer"))

    print("Sending charge (immediate)")
    print("  accepted:", gw.set_charge_mode("charge", PROGRAM_HOUR))
    wait(WAIT_S)
    show(gw, "after charge (expect charging True)")
    report["reads"].append(raw_read(gw, "after charge"))


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
            path = settings.DATA_DIR / "spike-report.json"
            path.write_text(json.dumps(sanitize(report, secrets), indent=2, ensure_ascii=False), encoding="utf-8")
            print("Report written to", path)
            gw.client.remote_client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
