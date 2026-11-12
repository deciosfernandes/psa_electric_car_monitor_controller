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
   (hour, minute) in **Azores local time**. Only testable while Azores differs from UTC (winter, UTC-1).
   Before each probe the spike defers to ~12 h ahead and confirms with a fresh read that the car is not
   charging (`baseline`). Local probe: program hour = Azores now + ~5 min. If it provably did not start, UTC
   probe: program hour = UTC now + ~5 min (a car reading local time would start it an hour later; a car
   reading UTC would start the local probe ~23 h later). A read is **fresh** when the electric energy block's
   timestamp (the source of `charging`, not the status' top-level `updatedAt`) is newer than the read window;
   each wakeup is followed by a 20 s settle; wakeups are budgeted below the 6 per 20 min limit, plain REST
   reads in between; a failed read is never evidence. A probe needs a fresh not-charging read in the 2 min
   before its program hour (woken at hour - 45 s); a start then counts only if a fresh read shows charging
   between the program hour and 8 min later. Each `timeline` entry records the Azores time, whether the read
   was woken, the energy block time and whether it was fresh.
   - `verdict: "local"`: the local probe started at its hour. The car reads local time, as the engine
     assumes. Nothing to do.
   - `verdict: "utc"`: the local probe provably did not start and the UTC probe started at its hour.
     `Engine._program_hour` must convert to UTC before the automation is trusted.
   - `verdict: "inconclusive"` (see `reason`): no confirmed stop before probing, a charge seen before the
     program hour or after the window, no fresh read, Azores on UTC+0, or neither probe started (battery at
     its limit, cable unplugged). **Do not trust the automation; rerun the spike.**

4. **MQTT** (`mqtt_last_event`, `mqtt_charging_state`): the raw payload shape of the remote events, used to
   check the snapshot merge in `gateway.py`.
