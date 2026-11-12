# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

psacc: single-user app that controls a Peugeot e-208 through PSA's reverse-engineered Connected Car v4 REST API
and MQTT remote broker, and automates charging around the EDA (Azores) tri-hourly tariff: start at the
beginning of the off-peak period (+offset), defer before it ends. `backend/` (Python package `psacc`, FastAPI) +
`frontend/` (Vite/React/TS). One Docker image; all state in `/data`. UI text is pt-PT (24h, DD/MM/AAAA, EUR);
scheduling timezone `Atlantic/Azores`, timestamps stored in UTC. The legacy Flask/Dash app was removed.

## Commands

Backend (from `backend/`, venv at `backend/.venv`; Windows paths shown, use `.venv/bin/` elsewhere):

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest tests/test_decide.py::test_name -q     # single test
.venv/Scripts/python -m ruff check psacc tests
PSACC_FAKE=1 .venv/Scripts/python -m uvicorn psacc.main:app --port 8000   # demo mode, fake in-memory car
.venv/Scripts/python scripts/spike_deferred.py     # real-car diagnostic (stop the app first; car plugged in)
```

The spike has been run on the owner's car; settled facts are in `psa/NOTES.md` and must be respected:
defer stops an ongoing charge; `charging.nextDelayedTime` is the programmed time of day as an ISO-8601
duration (`PT10M` = 00:10), not a countdown; the car reads the program hour in **Azores local time** (no UTC
conversion); MQTT `charging_state.cable_detected` is unreliable (0 while plugged), so `plugged`/`charging`
come from REST only, while MQTT `soc_batt`/`autonomy_zev` are fine to merge.

Dependencies: `pyproject.toml` lists them unpinned; `backend/requirements.lock` pins the versions the image
installs (`pip install -c requirements.lock ./backend`). After changing dependencies, regenerate it from a
working venv: `.venv/Scripts/python -m pip freeze --exclude-editable > requirements.lock`, drop Windows-only
packages (`colorama`, `win32_setctime`) and re-add the two header comment lines.

Frontend (from `frontend/`):

```bash
npm ci
npm test            # vitest
npm run lint        # oxlint
npm run build       # tsc -b && vite build -> dist/
npm run dev         # vite; /api proxied to http://127.0.0.1:8000
```

Docker (repo root):

```bash
docker build -t psacc .
docker run -p 5000:5000 -v ./data:/data psacc      # GET /api/health -> {"ok": true, "fake": false}
docker compose up -d --build                        # compose publishes host port 3208 -> container 5000
```

Env: `PSACC_DATA_DIR` (default `./data`), `PSACC_STATIC_DIR` (built SPA, set in the image), `PSACC_FAKE=1`
(dev only, never in the image). Python line length 120, ruff lint.

Image runtime: the entrypoint (root) fixes `/data` ownership, sets it to 0700, `umask 077`, `HOME=/home/psacc`,
`cd /data`, then drops to `psacc` (uid 10001). Secrets under `/data` (`psa.json`, `otp.bin`, `psacc.db`,
`certs/`) are owner-only; the APK downloaded during setup goes to `/data/apk/`.

## Architecture (backend/psacc)

- `psa/`: PSA protocol layer. `rest.py` (one retry on 401 after token refresh, one on 5xx) + `models.py`:
  hand-written REST models (API v4.17, spec in `psa/api-b2c-v4.17.2.yaml`) that are deliberately lenient — a
  wrong-typed leaf becomes `None` instead of rejecting the whole status (the real API differs from the spec,
  e.g. `charging.schedule` arrives as a list). Downstream code must treat `None` as unknown, never as
  unplugged/not charging. `RemoteClient.py`/`mqtt_request.py` send remote commands over MQTT (paho-mqtt 1.x;
  `start()` waits up to 15 s for CONNACK; only the pending request is re-sent once after a 400, with a new
  correlation id). `oauth.py` (`ensure_access_token()` before first use of a stored client), `otp/`, `setup/`
  handle login, SMS OTP and app credential extraction (APK and client certificate under the data dir,
  `setup/paths.py`). `psa_client.PSAClient` auto-persists rotated OAuth/remote tokens to the store via
  refresh/update callbacks.
- `gateway.py`: `PsaGateway`, the single facade the rest of the app uses to read state and send commands
  (`fake_gateway.py` is the in-memory stand-in for `PSACC_FAKE=1`). `CarSnapshot.energy_at` is the electric
  block timestamp (freshness of `charging`), `at` the status timestamp; `merge_mqtt_event` only merges
  `soc_batt`/`autonomy_zev`. `setup_flow.py` is the shared login/OTP flow (saves tokens after SMS request and
  gateway start).
- `tariff/`: `model.py` (seasons by month, Winter Nov-May / Summer Jun-Oct, day types, period slots, dated
  prices, validation), `presets.py` (built-in "EDA tri-horario semanal"), `schedule.py` (period at a time,
  desired mode, next vazio start, planned charge/defer events).
- `places.py`: places (kind home/work/other, radius 25–2000 m, default 150 m; price only for other), pure helpers.
  `update_fix()` stores a newer position fix; `locate()` returns home/work/other/unknown/unset. A fix older than
  60 min counts only if both odometers are known and unchanged, else "unknown".
- `automation/decide.py`: pure policy (state in, command out). `automation/engine.py` applies it, stores the
  program hour sent with each command (re-sends defer when the next vazio start changes), verifies the effect
  after a ~20 s settle following a wakeup, retries (60/180/600 s), alerts once per failure episode; manual
  "Carregar agora" override is persisted and cleared on unplug. `Engine.close()` is terminal. The battery limit
  (default 80%) applies everywhere while charging (independent of the tariff automation switch; controlled by its own
  limit switch); tariff rules apply only at Casa; at Trabalho "charge" is sent once per plug-in episode below the limit;
  outside Casa "defer" is sent only to enforce the battery limit; "Carregar agora" works everywhere. The engine locates
  the car once per snapshot (`engine.last_where`/`last_place`).
- `history/`: `charges.py` (ChargeDetector: sessions, `partial` when already charging on first sight), `cost.py`
  (home split per tariff period; work 0 €; other = place price; unknown = null), `trips.py` (TripDetector from
  the odometer; MQTT-only updates ignored; kWh from the SoC drop, approximate). Fed from `Engine.on_status` with
  that evaluation's place; failures are logged only; no extra wake-ups. History is kept forever. Usable capacity
  `AppConfig.battery_capacity_kwh` defaults to 46 kWh. Charge kWh and cost are frozen when the session ends (capacity
  and the "other" place price in force then, stored on the row); reads never recompute, only a price override edit
  does; changing the capacity later does not rewrite history (like trips).
- `poller.py`: background thread; reads state (REST + MQTT pushes, MQTT handled off the paho thread), feeds the
  engine at planned events, persists rotated tokens, raises health alerts. It wakes the car only when plugged
  in/unknown and near a planned event (plus one follow-up 3 min after each), while a verification is pending,
  right after plug-in, or when the state is older than 60 min (a REST "unplugged" older than 60 min counts as
  unknown). `notify.py`: Discord webhook alerts and (opt-in) info messages, sent via a background queue.
- `store.py`: SQLModel/SQLite persistence under the data dir (config, events, last command, status history,
  places, position fix, charge sessions, trips).
- `api/`: FastAPI routers (`auth`, `status`, `automation`, `control`, `setup`, `places`, `history`); `/api/status`
  adds `where`, `place_name`, `mileage_km`, `position_age_min` and reports `mqtt_connected` and `psa_connected`
  (gateway auth OK). `/api/places` (+ `from-car`), `/api/charges`, `/api/trips`, `/api/consumption`,
  `/api/battery-health` (daily series; `car.battery_health` in status). `main.py` builds the app
  (`create_app`, lazy `psacc.main:app`), serves the SPA, and enforces CSRF: mutating `/api` requests need the
  `X-PSACC` header. `auth.py`: single password, signed session cookie carrying a session epoch; changing the
  password bumps the epoch and invalidates existing sessions.

## Architecture (frontend/src)

`main.tsx`/`App.tsx` (router, react-query), `api.ts` (fetch wrapper adding `X-PSACC`), `useStatus.ts`,
`layout/` (Shell + fixed left Sidebar), `pages/` (Painel, Automacao, Controlo, Definicoes, Login, Cargas, Viagens,
Consumo), `components/` (TariffEditor, SetupWizard, PlacesEditor (Definicoes → Locais), MiniMap, charts, lists),
`lib/` (tariff and pill helpers), `format.ts` (pt-PT formatting). MiniMap uses plain leaflet on purpose: react-leaflet
is not used because its Hippocratic License conflicts with this project's GPL-3.0. Tests are vitest + Testing Library next to the code.
