# PSACC Plan 2 — Locais, Cargas, Viagens, Consumo — design

Date: 13/11/2026 · Status: draft for approval · Builds on `2026-11-12-psacc-rewrite-design.md` (Plan 1, merged).

## 1. Intent

Owner (single user, Azores) charges **almost always at work, for free**, and rarely at home (EDA tri-horário).
Plan 1's automation is location-blind: during the day (cheias/ponta) it puts the car in deferred mode wherever
it is plugged — **it blocks the free charging at work**. Plan 2 fixes that first, then fills the three "Em breve"
screens. What the owner wants to see: **kWh spent and % battery lost** per trip, charge history with cost, and on
the Painel the **cable state** and **battery useful-life (SoH)**.

Success = the automation only acts at home; the owner can see how much energy/money each charge and trip cost,
monthly consumption, and never has to think about the work charger.

## 2. Goals

1. **Locais (places)** — named places with position + radius: "Casa", "Trabalho", optional others with a
   price €/kWh. **Automation acts only when the car is at Casa.**
2. **Cargas** — charge sessions with place, start/end, % before→after, kWh, duration, cost, charging curve;
   monthly totals.
3. **Viagens** — trips with departure/arrival (time, place name or coordinates), distance, % lost, kWh,
   kWh/100 km; small map with start/end points only.
4. **Consumo** — kWh/100 km for the last 30 days and per month.
5. **Painel additions** — cable card (plugged/unplugged + place + age), "Vida útil da bateria" card,
   **current odometer (km)** (owner confirmed it appears in the real REST payload, see `spike-report.json`),
   and a quick **automation on/off switch** in the Automação card.

## 3. Non-goals

- Route tracking / drawing the path on the map (owner: start and end are enough). No frequent wake-ups while
  driving.
- Altitude profiles (Plan 1 spec mentioned altitude; dropped with route tracking).
- Temperature / weather correlation (owner declined).
- Data retention limits (owner: keep forever; single user).
- Import of legacy psa_car_controller data.

## 4. Anti-goals

- Automation sending commands outside Casa (the current bug).
- Automation acting on an unknown/stale position as if it were Casa.
- Showing invented numbers (e.g. SoH estimated without data) — show "não disponível".
- Extra car wake-ups that drain the 12 V battery just to record trips.

## 5. Constraints

- Everything from Plan 1 global constraints (pt-PT UI, Azores TZ, UTC storage, single Docker image, `/data`,
  `X-PSACC` header, REST is the only source of `plugged`/`charging`, lenient models).
- Wake policy (Plan 1 ruling 18 + fixes) stays; Plan 2 must not add wakeups beyond it. Trip/charge detection
  uses whatever snapshots the poller already gets (REST polls + MQTT pushes for soc/autonomy).
- Settled PSA facts in `backend/psacc/psa/NOTES.md` apply (local-time program hour, `nextDelayedTime` = time of
  day, MQTT `cable_detected` unreliable).

## 6. Decided with the owner

| Topic | Decision |
|---|---|
| Scope | All three screens + location fix, no temperature |
| Location fix | Part of Plan 2, delivered first |
| Trips | Start/end only, no route |
| Retention | Forever |
| SoH label | "Vida útil da bateria" (not "SoH") |
| Painel | Cable card, battery useful-life card, current odometer (km), automation quick switch |
| Work charging | Free (0 €) |

Approved mockups: `.superpowers/brainstorm/694-1794503810/content/plano2-ecras.html` and `plano2-painel-v2.html`.

## 7. Behaviour (builder's calls marked ⓑ)

### 7.1 Locais
- Place = name, lat, lon, radius (m, default 150 ⓑ), kind: `home` | `work` | `other`, price €/kWh (`other`
  only; `work` = 0 €; `home` = tariff). Exactly one `home` required for automation; at most one `work` ⓑ.
- Set via Definições → Locais: "usar posição atual do carro" or click on a small map (Leaflet + OSM tiles ⓑ).
- `place_at(lat, lon)` → nearest place whose radius contains the point, else None.

### 7.2 Automation only at home
- New rule in `decide()` before the tariff rules: if no `home` place configured → no command ("Defina a
  localização de Casa"); if car position unknown or position older than 60 min ⓑ → no command ("Posição
  desconhecida"); if not at home → no command ("Fora de Casa (<place or 'local desconhecido'>)").
- Manual "Carregar agora" keeps working anywhere (owner-initiated).
- Leaving home with a stored defer command: at Trabalho the app sends "charge" once per plug-in episode (owner
  decision, §11). At other places nothing is sent; if `last_command` is defer the Painel shows a hint "Programa
  diferido ativo no carro" so the owner can press "Carregar agora".

### 7.3 Charge sessions
- Detected from REST snapshots: a session starts when `charging` becomes True (or soc rises while plugged) and
  ends when charging stops / cable unplugged / soc stops rising for N polls ⓑ. Records: place (from position at
  start), start/end time, soc start/end, kWh = Δsoc × usable capacity ⓑ (capacity from REST battery capacity if
  available, else configurable default for e-208, e.g. 46 kWh usable ⓑ), samples for the curve (soc + rate).
- Cost: home → Σ over the session of kWh per tariff period × price valid that day (prices with `valid_from`);
  work → 0; other place with price → kWh × price; unknown place → null, editable ("definir preço").
- Owner can edit a session's price (€/kWh) afterwards.

### 7.4 Trips
- Detected when mileage increases between snapshots with the car unplugged ⓑ: departure = last snapshot before
  movement (time, position, soc, mileage), arrival = first stable snapshot after (no mileage change for N
  minutes ⓑ). Distance from odometer; kWh = Δsoc × capacity; kWh/100 km. Places resolved by `place_at`.
- Coarse by design (depends on poll cadence) — the UI says times are approximate ⓑ.
- Needs odometer + position in `CarSnapshot` (REST `timed.odometer` / spec `VehicleOdometer`, `lastPosition`)
  — models must be extended leniently.

### 7.5 Consumo
- kWh/100 km over trips: last 30 days card + monthly bars; total km and kWh.

### 7.6 Vida útil da bateria
- Read REST `energies[].extension.electric.battery.health` (`capacity`, `resistance` per spec) leniently.
  Display capacity health % if present; else "não disponível". Keep history (one sample/day ⓑ) for a future
  trend. Verify with the owner's real payload before building the card UI around it.

## 8. Architecture (builder)

- Backend: `places.py` (model + `place_at`), store tables (places, charge_sessions + samples, trips, battery
  health samples), `history/` package (session + trip detectors fed by `Engine.on_status`/poller snapshots,
  pure and unit-tested with recorded snapshot sequences), cost calculator reusing `tariff.schedule.period_at` +
  `Tariff.price_at`, API routes `/api/places`, `/api/charges`, `/api/trips`, `/api/consumption`,
  `/api/battery-health`, automation toggle already exists (`PUT /api/automation`).
- Frontend: pages Cargas, Viagens, Consumo replacing ComingSoon; Definições → Locais; Painel cards + switch;
  Leaflet for maps (CSP/offline: OSM tile fetch from browser is acceptable on LAN ⓑ).

## 9. Left to the builder
Detection thresholds, capacity default, schema, charts, map lib details, pagination, all ⓑ items above.

## 10. Facts from the owner's real payload + builder-check answers

Real REST status (owner's `data/spike-report.json`, 12/11/2026):
- `odometer: {createdAt, mileage: 37255.7}` at the top level of the status → current km for the Painel and trip
  distance.
- `energies[0].extension.electric.battery.health: {createdAt, resistance: 95}` (legacy `energy[0].battery.health`
  too) — **only `resistance`, no `capacity`**. The Painel card "Vida útil da bateria" shows this value as reported
  by PSA (e.g. "95 %") with a note "valor reportado pela PSA"; history kept daily. If absent → "não disponível".
- `lastPosition` is present (redacted in the report); its own timestamp field must be parsed leniently
  (spec: `properties.updatedAt`) — position age = that timestamp if present, else the status timestamp.

Builder-check answers (controller calls, owner may override):
- Position cadence: plain REST polls every `refresh_min` (5 min) return the last state the car reported; the
  60-min staleness rule uses the position's own timestamp. While parked and asleep the position doesn't change,
  so "at Casa" stays valid as long as the last reported position is Casa and the car hasn't moved (odometer
  unchanged since the position was taken) — i.e. staleness is only applied if the odometer advanced after the
  position timestamp. This keeps automation working overnight without extra wake-ups.
- Trips are approximate; very short round trips between polls may be missed — accepted (owner wants start/end
  only, no wake-ups while driving).
- Upgrade with no Casa defined: automation pauses with a Painel banner "Defina o local Casa" + one-click
  "usar posição atual do carro"; activity log entry. Manual commands keep working.
- Sessions first seen already in progress (or app started mid-charge): soc_start = first observed soc, flagged
  `partial`; kWh/cost shown with "≈". 

## 11. Owner decision — work charging
**Option A (owner):** when the car is first seen plugged at the `work` place (fresh REST `plugged=True` and
position inside the work radius) the app sends **"charge" once** for that plug-in episode — the only exception to
"act only at Casa". Rules (builder implements, tests required):
- One command per plug-in episode at work (episode = from plugged at work until unplugged or left the place);
  reuse engine verification/retry/alert (failure alert text: "não consegui iniciar a carga no Trabalho").
- Respects the automation switch (off → nothing sent) but **not** the battery limit (work charging is free and
  the owner wants it) — ⓑ unless owner later asks otherwise; activity log "Carga no Trabalho iniciada".
- Never sends "defer" outside Casa.

## 12. Owner decision — limit everywhere (12/11/2026)

Decision (overrides §7.2, §11 and controller ruling 6 where they conflict):
"Embora as automações da hora só aconteçam em casa, o limite dos 80% deve acontecer SEMPRE que o carro está em carga independentemente do sítio ou fonte."

- The battery limit (`AppConfig.battery_limit`, default 80 %) is enforced at **every** `where` (home, work, other,
  unknown, unset), whatever started the charge (tariff automation, work charge, manual "Carregar agora", a public
  charger, or the car itself).
- The tariff/time rules remain Casa-only.
- A dedicated limit switch (`AppConfig.battery_limit_enabled`, default True) controls limit enforcement. The general
  automation switch (`AppConfig.automation_enabled`) controls tariff and location charging automations. Turning off
  the automation switch does not disable the battery limit.
- Outside Casa, "defer" is sent **only** to enforce the battery limit, and only while the car is (or may be)
  charging (`car.charging is not False`). Once stopped (`car.charging is False`), no command is sent (`None`).
- Failure alert text for the limit anywhere: "⚠️ PSACC: não consegui parar a carga no limite de N %."
- At Trabalho, the work charge episode remains marked after a limit stop (no second work charge in the same episode).
