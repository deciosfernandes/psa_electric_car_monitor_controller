# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PSA Car Controller (PSACC): Python app that controls and reads data from PSA/Stellantis cars (Peugeot, Citroën, Opel, Vauxhall, DS) via the Connected Car v4 REST API plus PSA's MQTT remote-control broker, and serves a Dash/Flask dashboard + JSON API. Python >=3.11, managed with Poetry. Distributed as a PyPI package, Docker image (`flobz/psa_car_controller`) and Home Assistant add-on (psacc-ha).

## Commands

```bash
poetry install                       # install deps incl. dev tools
pre-commit run -a                    # quality gate used in CI: autopep8 + prospector (--without-tool dodgy)
prospector                           # lint only (config: .prospector.yaml)
python -m unittest                   # run all tests (CI uses: coverage run -m unittest)
python -m unittest tests.test_unit.TestUnit.test_car   # single test
TEST_ONLINE=1 python -m unittest tests.test_unit       # enable tests that hit real external services
DEBUG_LEVEL=10 python -m unittest ...                  # verbose logging in tests
psa-car-controller -f config.json -c charge_config.json -r   # run app (or: python -m psa_car_controller)
```

Useful run flags (`psacc/application/car_controller.py:parse_args`): `--web-conf` (start without config, set up via web UI), `--offline`, `--remote-disable` (no MQTT), `-R <min>` refresh interval, `-r` record to DB, `-l/-p` listen addr/port (default 127.0.0.1:5000), `-b` base path.

## Code style

- Max line length 120; autopep8 aggressive level 3 (config in `pyproject.toml`).
- `psa_car_controller/psa/connected_car_api/` is **generated OpenAPI (swagger) client code** — excluded from autopep8, prospector and coverage. Don't hand-refactor it; API spec in `api_spec.md`.
- `tests/` excluded from prospector.

## Architecture

Layers under `psa_car_controller/`:

- `psa/` — low-level PSA integration:
  - `connected_car_api/` generated REST client (`VehiclesApi`, models like `Status`).
  - `oauth.py` OpenID credential manager / token refresh for the REST API.
  - `RemoteClient.py` MQTT client (paho) for remote commands (charge, preconditioning, lock, horn, lights, wakeup); handles remote token refresh and OTP. Incoming MQTT messages update car state.
  - `otp/` reimplementation of PSA's OTP (stored in `otp.bin`) needed for remote-control tokens; SMS code flow.
  - `setup/` first-time setup: downloads the official Android APK, extracts client id/secret/certs (`apk_parser`, `app_decoder`, `github`), and `headless_oauth` (Playwright) login.
- `psacc/` — application layer:
  - `application/car_controller.py` `PSACarController` — **Singleton** (via `psacc/utils/utils.Singleton`) holding args, `PSAClient`, `ChargeControls`, config. Web views access app state through it. Saves config every 30 s via timer.
  - `application/psa_client.py` `PSAClient` — facade over REST API + `RemoteClient`; loads/saves `config.json` (credentials, vehicles), refresh thread, records positions/charges to DB.
  - `application/charge_control.py` threshold/stop-hour charge logic (`charge_config.json`); `charging.py` charge session detection + pricing; `abrp.py` ABetterRoutePlanner push; `ecomix.py` CO2 intensity; `trip_parser.py`/`battery_charge_curve.py` derived analytics.
  - `model/` plain domain objects (`Car`, `Cars`, `Charge`, `Trip`, ...).
  - `repository/db.py` `Database` — static-method SQLite access (`info.db`), schema init/migrations in `init_db`, callbacks on commit to refresh the dashboard. `config_repository.py` reads/writes `config.ini` (electricity prices, night hours, API keys). `trips.py` builds trips from recorded positions.
- `web/` — Dash app on Flask. `app.py` builds the server (`config_flask`), with `MyProxyFix` handling HA ingress / `X-Forwarded-Prefix` base paths. `view/api.py` = JSON REST API (documented in `docs/psacc_api.md`), `view/views.py` dashboard callbacks, `view/config_*.py` web-based setup/OAuth flow, `figures.py` plotly figures.

Entry: `__main__.main()` → `PSACarController().load_app()` (load config, check DB, refresh token, start MQTT/charge control) → web server thread.

Runtime files created in CWD: `config.json`, `charge_config.json`, `config.ini`, `otp.bin`, `info.db`.

## Tests

Plain `unittest`. `tests/utils.py` provides fixtures: in-memory DB via `get_new_test_db()` (`Database.get_db(force_new_conn=True, db_file="")`), sample cars, `get_rc()` mocked `RemoteClient`. Sample API payloads in `tests/data/`. Network-dependent tests are gated by `TEST_ONLINE=1`.

## Dev notes (docs/Develop.md)

Inspect app↔PSA traffic with mitmproxy using the client cert from the APK (`assets/MWPMYMA1.pfx` → `openssl pkcs12 -in MWPMYMA1.pfx -out MWPMYMA1.pem -nodes`, no password), then `mitmproxy --set client_certs=MWPMYMA1.pem`.
