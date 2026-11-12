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
.venv/Scripts/python -m ruff check psacc --exclude psacc/psa/connected_car_api
PSACC_FAKE=1 .venv/Scripts/python -m uvicorn psacc.main:app --port 8000   # demo mode, fake in-memory car
python scripts/spike_deferred.py     # one-off real-car spike (car plugged in)
```

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
docker compose up -d --build
```

Env: `PSACC_DATA_DIR` (default `./data`), `PSACC_STATIC_DIR` (built SPA, set in the image), `PSACC_FAKE=1`
(dev only, never in the image). Python line length 120, ruff lint; generated `psa/connected_car_api` is excluded.

## Architecture (backend/psacc)

- `psa/`: PSA protocol layer. `rest.py` + `models.py` are small tolerant hand-written REST models (API v4.17,
  spec in `psa/api-b2c-v4.17.2.yaml`); `RemoteClient.py`/`mqtt_request.py` send remote commands over MQTT;
  `oauth.py`, `otp/`, `setup/` handle login, SMS OTP and app credential extraction.
- `gateway.py`: `PsaGateway`, the single facade the rest of the app uses to read state and send commands
  (`fake_gateway.py` is the in-memory stand-in for `PSACC_FAKE=1`). `setup_flow.py` is the shared login/OTP flow.
- `tariff/model.py`: seasons by month (Winter Nov-May, Summer Jun-Oct), day types, period slots, dated prices,
  built-in "EDA tri-horario semanal" preset; derives the charge/defer schedule.
- `automation/decide.py`: pure policy (state in, command out). `automation/engine.py` applies it, verifies the
  effect after a delay, retries, alerts once per failure episode; manual "Carregar agora" override is persisted.
- `poller.py`: background thread; refreshes state (REST + MQTT pushes), feeds the engine at planned events,
  raises health alerts. `notify.py`: Discord webhook alerts.
- `store.py`: SQLModel/SQLite persistence under the data dir (config, events, last command, status history).
- `api/`: FastAPI routers (`auth`, `status`, `automation`, `control`, `setup`). `main.py` builds the app
  (`create_app`, lazy `psacc.main:app`), serves the SPA, and enforces CSRF: mutating `/api` requests need the
  `X-PSACC` header. `auth.py`: single password, signed session cookie carrying a session epoch; changing the
  password bumps the epoch and invalidates existing sessions.

## Architecture (frontend/src)

`main.tsx`/`App.tsx` (router, react-query), `api.ts` (fetch wrapper adding `X-PSACC`), `useStatus.ts`,
`layout/` (Shell + fixed left Sidebar), `pages/` (Painel, Automacao, Controlo, Definicoes, Login, ComingSoon for
Cargas/Viagens/Consumo), `components/` (TariffEditor, SetupWizard, charts, lists), `lib/` (tariff and pill helpers),
`format.ts` (pt-PT formatting). Tests are vitest + Testing Library next to the code.
