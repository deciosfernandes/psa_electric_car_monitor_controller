# PSA Electric Car Monitor & Controller

Personal web app to monitor and control a PSA/Stellantis electric car (tested with a Peugeot e-208) and
**charge automatically only during off-peak ("vazio") hours** of the EDA — Electricidade dos Açores tariff
(tri-hourly, weekly cycle).

> Based on [**psa_car_controller**](https://github.com/flobz/psa_car_controller) by
> [**flobz**](https://github.com/flobz), the original author. All of the PSA integration (OAuth login, OTP code,
> remote commands over MQTT) builds on that project's reverse-engineering work. This version is a rewrite for
> personal use, with a new interface, a new architecture and tariff-driven automation. Many thanks to the
> author and all contributors of the original project.

## Features

- **Dashboard (Painel)** — battery, range, charging state, today's tariff strip (vazio / cheias / ponta) with
  the next automatic event, last-24 h battery chart and activity log.
- **Charging automation** — drives the car's deferred-charging mode:
  - turns deferred charging off at the start of off-peak (+ offset, 10 min by default → `00:10`);
  - turns it back on before off-peak ends (− offset → `06:50`);
  - respects a battery limit (80 % by default);
  - merges back-to-back off-peak windows (e.g. Saturday 23:00 → Monday 07:00) with no commands at midnight;
  - verifies every command, retries, and alerts on failure.
- **Editable tariff** — built-in "EDA tri-horário semanal" preset (summer June–October, winter November–May),
  weekly grid in 15-minute blocks, and per-period prices with an effective-from date.
- **Controls** — "Charge now" (overrides the automation until the limit is reached or the cable is unplugged),
  climate control, locks, horn, lights and car wake-up.
- **Discord alerts** — command failure, expired PSA session, MQTT connection down; optionally charge
  start/stop.
- **Portuguese (pt-PT) interface**, light and dark theme, 24-hour clock, `Atlantic/Azores` timezone.

Charges, Trips and Consumption are shown as "Em breve" (coming soon).

## Important warnings

- **Use on your local network (LAN) only.** The app controls a real car (including unlocking it) and is
  protected by a single password. Do not expose it to the Internet.
- **Run the diagnostic script before trusting the automation** (see below). It is the only way to confirm that
  deferred mode stops an ongoing charge and which timezone the car uses for the programmed hour.
- The PSA API is not officially intended for this use and may change without notice.

## Installation (Docker)

Requirements: Docker with Docker Compose.

```bash
git clone https://github.com/deciosfernandes/psa_electric_car_monitor_controller.git
cd psa_electric_car_monitor_controller
docker compose up -d --build
```

Open `http://<server>:3208` (host port set in `docker-compose.yml`; the container listens on 5000).

### First run

1. Set the app password (first visit).
2. **Definições → Conta PSA** (Settings → PSA account): pick the app (MyPeugeot, MyCitroën, …), country and
   account credentials. If the automatic login fails, the app shows a link to finish the login manually and
   paste the code.
3. Request the SMS code and enter it together with your mobile app PIN.
4. **Automação** (Automation): check the offset (10 min), battery limit (80 %) and tariff hours, and fill in
   the off-peak / mid-peak / peak prices.
5. **Definições → Discord** (optional): paste the webhook URL and use "Enviar teste" (send test).
6. Run the diagnostic script (below).

### Data and backups

Everything lives in `./data` (mounted at `/data` in the container): database, configuration, PSA tokens, OTP
secret and certificates. The folder is created and restricted automatically so only the app can read it; the
app runs as a non-root user. Back up this folder and never share it.

### Updating

```bash
git pull
docker compose up -d --build
```

Data in `./data` is kept.

## Diagnostic script

Required **once**, with the car plugged in and the battery below the limit, ideally during winter time (when
the Azores are at UTC−1 the timezone test is conclusive). It takes 20–60 minutes because PSA rate-limits car
wake-up requests.

**Stop the app first** (`docker compose down`): the script and the app must not use the same PSA credentials
at the same time.

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -e '.[dev]'          # Linux/macOS: .venv/bin/pip
.venv/Scripts/python scripts/spike_deferred.py # Linux/macOS: .venv/bin/python
```

To share credentials with the Docker app, first point `PSACC_DATA_DIR` at the repository's `data` folder
(PowerShell: `$env:PSACC_DATA_DIR = "..\data"`).

The result is written to `spike-report.json` in the data folder, without tokens, VIN or location. Timezone
verdict:

| Result | Meaning |
|---|---|
| `local` | The car reads the hour in Azores local time — the automation can be used. |
| `utc` | The car reads the hour in UTC — do not use the automation until this is fixed. |
| `inconclusive` | The test was not conclusive (see `reason`) — run the script again. |

More details in [`backend/psacc/psa/NOTES.md`](backend/psacc/psa/NOTES.md).

## Development

Backend (Python 3.12, FastAPI):

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -e '.[dev]'
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check psacc tests
PSACC_FAKE=1 .venv/Scripts/python -m uvicorn psacc.main:app --port 8000
```

`PSACC_FAKE=1` uses an in-memory simulated car (demo mode, with a visible banner in the UI). Never use it in
production.

Frontend (Node 22, Vite + React + TypeScript + Tailwind):

```bash
cd frontend
npm ci
npm run dev     # http://localhost:5173, /api proxied to http://127.0.0.1:8000
npm test
npm run lint
npm run build
```

Architecture and developer commands are in [`CLAUDE.md`](CLAUDE.md); the full design is in
[`docs/superpowers/specs/`](docs/superpowers/specs/).

## Credits and license

- Original project: [flobz/psa_car_controller](https://github.com/flobz/psa_car_controller), by
  [flobz](https://github.com/flobz) and contributors.
- Tariff hours: "Ciclo Semanal" leaflet from [EDA — Electricidade dos Açores](https://www.eda.pt).

Licensed under **GPL-3.0**, like the original project — see [`LICENSE`](LICENSE).
