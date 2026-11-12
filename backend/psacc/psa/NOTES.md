# PSA API: open questions settled by the spike

`scripts/spike_deferred.py` must be run once, with the car plugged in and charging, before the automation is
trusted. It writes `DATA_DIR/spike-report.json` (tokens, VIN, ids and position stripped). What to look for:

1. **Defer stops an ongoing charge.** In `reads`, the entry `after defer` must show
   `charging.status` != `InProgress`; `after charge` must show `InProgress` again. If defer does not stop a
   charge, the engine's verification (`charging is False`) will fail and alert every evening.

2. **Meaning of `charging.nextDelayedTime`.** The v4.17 spec calls it a duration until the next charge
   (e.g. `PT22H30M`); the old code read it as the programmed time of day. Compare the value in `after defer`
   with the time the report was taken (`local_time`) and the programmed hour (`00:10` in that step): a
   duration counts down to the programmed hour, a time of day is constant.

3. **Timezone of the program hour** (`program_hour_timezone`). The engine sends the next vazio start as an
   (hour, minute) in **Azores local time**. The spike defers with a program hour ~2 min ahead in Azores time
   and watches for 6 min whether charging starts; if not, it repeats the probe with a program hour ~2 min
   ahead in UTC.
   - `verdict: "local"`: the car reads the hour in local time, as the engine assumes. Nothing to do.
   - `verdict: "utc"`: the car reads it in UTC; the engine would program the start one hour off in winter
     (Azores UTC-1). `Engine._program_hour` must convert to UTC before the automation is trusted.
   - `verdict: "inconclusive"`: neither probe started a charge within 6 min (check `timeline`; the car may
     need longer, or the battery may be at its limit). Rerun.
   - `verdict: "skipped ..."`: Azores was on UTC+0 (summer time), local time and UTC are the same; rerun
     between the end of October and the end of March.

4. **MQTT** (`mqtt_last_event`, `mqtt_charging_state`): the raw payload shape of the remote events, used to
   check the snapshot merge in `gateway.py`.
