# PSACC rewrite — design

Date: 12/11/2026 · Status: draft for approval

## 1. Intent

Personal-use rewrite of psa_car_controller (single user, single Peugeot e-208, runs in Docker, used from a PC browser). The owner finds the current Dash UI unintuitive ("looks made by a programmer", black and white, things don't work properly) and is missing the one feature they need most: **automatic charging only during EDA (Electricidade dos Açores) off-peak hours ("vazio")**, driven by the car's deferred-charge mode.

Success = owner opens the dashboard on PC, sees battery/charge state at a glance, and never has to think about tariffs again: the car charges in vazio and stops before cheias/ponta, by itself, reliably, with a visible log of what was done and why.

## 2. Goals

1. **Tariff-driven charge automation** (core feature, new).
2. **New web UI** — light, card-based, left sidebar navigation (mockup approved: `.superpowers/brainstorm/*/content/dashboard-v2.html`).
3. Keep features: car status, trips (map + list), consumption stats, charge history with price, charging curve, remote controls (A/C preconditioning, lock/unlock, horn, lights, wakeup), charge limit (%).
4. Clean, tested codebase.

## 3. Non-goals

- Backwards compatibility of any kind: config files, `info.db`, REST API, Home Assistant add-on, Domoticz integration, multi-user. **Start from zero — no data migration.**
- CO2 / Ecomix — removed.
- ABRP — removed.
- Mobile-first design (must not break on a phone, but PC is the target).
- Publishing to PyPI / upstream.

## 4. Anti-goals (failure even if it "works")

- Car charging during cheias/ponta because a command silently failed.
- Automation state invisible — user can't tell what the app will do next or what it did.
- Tariff hours hard-coded — they must be editable in the UI because EDA may change them.
- A UI that still feels like a debug tool.

## 5. Constraints

- Runs as **one Docker image**, configured via a mounted data volume.
- Timezone: **`Atlantic/Azores`** for all scheduling and display. Store timestamps in UTC.
- Locale: Portuguese (pt-PT) UI text, 24h clock, DD/MM/YYYY, EUR, metric.
- PSA integration is reverse-engineered and fragile (OAuth via app credentials extracted from APK, OTP for remote token, MQTT with client certs). Must keep working.

## 6. Tariff model (decided with owner)

Owner has **EDA tri-horário, ciclo semanal**. Source: EDA leaflet "Ciclo Semanal" (Jul 2024), confirmed by owner against the official EDA chart.

Seasons are by **month**, not by DST: Winter = November–May, Summer = June–October. Holidays change nothing.

| Season | Day | Vazio | Cheias | Ponta |
|---|---|---|---|---|
| Winter | Mon–Fri | 00:00–07:00 | 07:00–18:30, 21:30–24:00 | 18:30–21:30 |
| Winter | Sat | 00:00–11:30, 13:30–18:00, 23:00–24:00 | 11:30–13:30, 18:00–23:00 | — |
| Summer | Mon–Fri | 00:00–07:00 | 07:00–10:30, 15:30–24:00 | 10:30–15:30 |
| Summer | Sat | 00:00–11:00, 14:30–19:30, 23:00–24:00 | 11:00–14:30, 19:30–23:00 | — |
| Both | Sun | 00:00–24:00 | — | — |

Requirements:
- Ship this as a built-in preset "EDA tri-horário semanal".
- Fully editable in the UI: seasons (month ranges), per-day-type periods (weekday / Saturday / Sunday), period type per slot. 15-min (or 30-min) granularity is enough. Validate that each day covers 24h with no overlaps.
- Each period type has a price (€/kWh), editable, used for charge cost.

## 7. Charge automation (decided with owner)

**Concept:** the app toggles the car's deferred-charge mode around vazio boundaries, with a single configurable **offset in minutes** (default 10) acting as safety margin:

- Vazio starts at T → at **T + offset** the app **disables deferred mode** (car charges).
- Vazio ends at T → at **T − offset** the app **enables deferred mode** (car stops / won't start).

Example, winter weekday, offset 10: charge at 00:10, deferred at 06:50. Saturday has three vazio windows → several toggles. Sunday → free all day. Vazio windows that touch across midnight (e.g. Sat 23:00–24:00 + Sun 00:00–24:00 + Mon 00:00–07:00) must be **merged** so no toggle happens at midnight.

Battery limit (owner): **default 80 %, configurable**. App watches SoC; on reaching the limit it enables deferred mode. If SoC is already ≥ limit when the next vazio window starts, the car stays deferred (builder's call).

Manual "Carregar agora" outside vazio (owner): **overrides the automation until the battery limit is reached** (or the cable is unplugged — builder's call). While active the dashboard shows "Carga manual ativa" with a cancel button; override state is persisted (survives restart) and logged. Afterwards normal automation resumes.

Failure notifications (owner): **Discord**, via a webhook URL configured in Definições. Sent when a command fails after retries (car may be charging outside vazio), when PSA auth/OTP breaks, and when MQTT stays disconnected. Builder's call: also optional info messages (charge started/finished) off by default.

Additional behaviour (proposed, owner did not object):
- Cable plugged in outside vazio → immediately ensure deferred mode.
- On app start / after downtime → compute the *current* desired state and enforce it (don't wait for the next boundary).
- Every command is verified against the next car status; retry with backoff; if still failing, show a visible error on the dashboard and in the activity log.
- Automation on/off switch.
- Dashboard shows the next scheduled event and an activity log ("00:10 Diferido desligado → carga iniciada").

Mechanism note (builder's call, see §10): the existing MQTT command is `/VehCharge` with `{"program":{"hour","minute"},"type":"delayed"|"immediate"}`. When enabling deferred mode, setting the program start hour to the **next vazio start + offset** gives a second line of defence: if the app is down at the next boundary, the car still starts by itself at the right time.

## 8. UI (decided with owner)

Approved direction: **light card layout ("B") + fixed left sidebar ("C")**.

Sidebar: Painel · Automação · Cargas · Viagens · Consumo · Controlo · Definições. Footer: last update time, MQTT connection state.

Painel (approved mockup):
- Header: car name, location, status pill (e.g. "A carregar · vazio").
- 3 cards: Battery (%, bar, limit, ETA) · Range (km, charge power) · Automation (on/off, tariff, next event, offset).
- Full-width "today" tariff strip (vazio green / cheias amber / ponta red) with current-time marker.
- Battery last-24h chart + activity log.
- Quick actions: Carregar agora, Climatizar, Trancar, Luzes, Buzina.

Other screens follow the same visual language (builder's design):
- **Automação**: on/off, offset, battery limit, tariff preset + weekly grid editor per season, prices per period, upcoming events (next 7 days), full activity log.
- **Cargas**: history table (start/end, kWh, % start→end, cost, € /kWh, editable price for public chargers), charging curve per session.
- **Viagens**: map + list, per-trip consumption/altitude.
- **Consumo**: stats over time (kWh/100 km, by month, vs temperature if weather key configured).
- **Controlo**: remote commands with clear feedback (pending → confirmed/failed).
- **Definições**: PSA account connection + OTP/SMS flow (replaces today's config pages), refresh interval, weather API key.
- Dark mode via `prefers-color-scheme` (nice-to-have, tokens from day one).

## 9. Architecture (builder's call, owner delegated language choice)

Decision: **keep Python for the PSA integration, rewrite everything else; new TypeScript frontend.** Rationale: the PSA auth/OTP/MQTT layer is reverse-engineered and is the riskiest part to port; nothing user-visible is gained by rewriting it. The pain is in the UI and app layer.

- **Backend**: Python 3.12, FastAPI (JSON API + serves built frontend), SQLite via SQLAlchemy/SQLModel with migrations from scratch, single asyncio process.
  - `psa/` (kept, cleaned): OAuth, OTP, RemoteClient (MQTT), generated `connected_car_api`, APK setup. Wrap behind a small `CarGateway` interface so the rest of the app (and tests) don't touch PSA internals.
  - `tariff/`: pure model + preset + "period at time t" + "next boundaries" — pure functions, heavily unit-tested (DST, month boundaries, midnight merge, Saturday multi-window).
  - `automation/`: scheduler loop that computes desired state, issues commands via `CarGateway`, verifies, retries, writes activity log.
  - `history/`: positions, trips, charges, cost from tariff, battery curve.
- **Frontend**: Vite + React + TypeScript + Tailwind, a chart lib, Leaflet for maps. Built into static files served by FastAPI. Live updates via SSE or polling.
- **Docker**: multi-stage build (node build → python runtime), one container, `/data` volume holds config, tokens, OTP, DB.
- Config: a single settings file / DB table instead of today's `config.json` + `charge_config.json` + `config.ini` + `otp.bin` sprawl (OTP/token secrets may stay in their own file under `/data`).

## 10. Left to the builder

- Exact libraries (chart, state management, ORM vs plain SQL), API shape, DB schema.
- Deferred-mode command details (program-hour trick in §7), retry/backoff values, verification timing (current code waits 60 s then re-reads status).
- Design of non-Painel screens within the approved visual language.
- Grid granularity for tariff editor.
- How much of current `psa/` code to refactor vs wrap.

## 11. Decisions after builder check

Owner:
- PSA credentials: **fresh setup** — owner doesn't run this project today (uses only the official mobile app). New login + OTP/SMS activation via the Definições screen; no import of old `config.json`/`otp.bin`.
- Manual charge override, battery limit default 80 %, Discord notifications — see §7.

Builder's calls (owner to check):
- Prices: €/kWh per period type (vazio/cheias/ponta) with an **effective-from date**; each charge stores the cost computed at the time, so price changes never rewrite history. Same price in summer and winter unless edited.
- Implementation plan starts with a **validation spike on the owner's real car**: toggle deferred mode during a live charge, confirm it stops; confirm program hour is honoured while the car sleeps. Current code already stops charges this way (`charge_control.py` → `charge_now(False)`), so expected to work; spike de-risks before the rest is built. Owner runs it (needs their account).
- Access protection: the UI can unlock doors, so add a **single password login** (set on first run), sessions via cookie. Container intended for LAN only.
- No git repo exists in the working folder; builder initialises one at the start of implementation.
