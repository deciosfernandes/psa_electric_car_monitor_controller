# PSA API: open questions settled by the spike

`scripts/spike_deferred.py` was executed on a real Peugeot e-208 (Azores, UTC-1). The questions are now settled:

1. **Defer stops an ongoing charge: CONFIRMED.**
   When `defer` was sent, the car immediately stopped charging (`status: Stopped`, `charging: False`, `chargingRate: 0`). The engine's evening deferral verification (`charging is False`) is sound.

2. **Meaning of `charging.nextDelayedTime`: Programmed time of day.**
   When program hour `00:10` was sent, the field returned `PT10M` (10 minutes after midnight) regardless of the local time of the read. It is the programmed time-of-day formatted as an ISO-8601 duration (`PT[H]H[M]M`), NOT a countdown/duration-until-charge.

3. **Timezone of the program hour: LOCAL TIME (Atlantic/Azores).**
   - Spike verdict: `"local"` (`local probe: charging seen at 2026-11-12T15:18:58+00:00`).
   - The car started charging within the expected window of the programmed local time (14:14 Azores).
   - `Engine._program_hour` sends local time directly; NO UTC conversion needed.

4. **MQTT payload findings:**
   - `cable_detected` in MQTT `charging_state` returned `0` even while the car was plugged in (`plugged: true` in REST). The gateway decision to trust `plugged` and `charging` only from REST is confirmed.
   - `soc_batt` and `autonomy_zev` in MQTT events are accurate and safe to merge into snapshots.
