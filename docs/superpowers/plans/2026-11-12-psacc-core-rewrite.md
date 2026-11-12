# PSACC Core Rewrite (Plan 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Dash app with a FastAPI backend + React frontend whose core is EDA-tariff-driven charge automation, delivering Painel, Automação, Controlo and Definições screens in one Docker image.

**Architecture:** Reverse-engineered PSA layer (`psa/`) is moved and decoupled, then hidden behind a `CarGateway` protocol. Pure tariff/schedule functions and a pure `decide()` function drive an engine that issues deferred/immediate charge commands, verifies them, retries, logs, and alerts Discord. FastAPI serves a JSON API and the built Vite/React SPA.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, SQLModel (SQLite), pydantic v2, requests, paho-mqtt <2, pytest · Node 22, Vite, React 18, TypeScript, Tailwind CSS v4, @tanstack/react-query, react-router, recharts, vitest · Docker (multi-stage).

**Spec:** `docs/superpowers/specs/2026-11-12-psacc-rewrite-design.md`

**PSA API reference (owner-supplied):** https://developer.groupe-psa.io/webapi/b2c/overview/about/ — OpenAPI `https://developer.groupe-psa.io/assets/openapi/api-b2c.yaml` (v4.17.2, 30/07/2024). Findings vs the old code, applied in Task 2/3:
- Old generated client (`psa/connected_car_api`, ~150 files) targets **v4.0 (2019)**. Since v4.10.1 the status `energy`, `preconditioning` and `service` objects are **deprecated**, replaced by `energies` (+ `extension.electric.charging.{type, schedule, chargingPowerLevel}` since v4.15.1). → Replace the generated client with small hand-written tolerant models.
- Spec defines `charging.nextDelayedTime` as a **duration until the next charge**, but old `RemoteClient.get_charge_hour()` parses it as the programmed **time of day**. Docs are known to contain errors, so the spike (Task 3) records the raw value to settle which one it is.
- `chargingMode` enum is `No | Slow | Quick` (not charge vs deferred), so deferred state can't be read from it.

**Plan 2 (later, separate):** history — Cargas (charge sessions, cost, curve), Viagens (map + list), Consumo. This plan only records the battery samples the Painel 24h chart needs.

## Global Constraints

- Single user, no backward compatibility, no data migration; CO2/Ecomix and ABRP removed.
- One Docker image; all state under `/data` (env `PSACC_DATA_DIR`, default `./data` locally).
- Scheduling and display timezone `Atlantic/Azores` (`zoneinfo`); store timestamps UTC.
- UI text pt-PT, 24h clock (`06h50` style in prose, `06:50` in inputs), dates DD/MM/AAAA, EUR, metric.
- Tariff seasons by month: Winter = Nov–May, Summer = Jun–Oct; holidays change nothing.
- Defaults: offset 10 min, battery limit 80 %, automation on, verify delay 60 s.
- Tariff fully editable; built-in preset "EDA tri-horário semanal" (values in Task 4).
- Manual "Carregar agora" overrides automation until battery limit or unplug; persisted; logged.
- Discord webhook alerts on: command failure after retries, PSA auth/OTP failure, MQTT down > 15 min. Info messages (charge start/end) off by default.
- Single-password login (set on first run), signed session cookie.
- Light card UI + fixed left sidebar: Painel · Automação · Cargas · Viagens · Consumo · Controlo · Definições (Cargas/Viagens/Consumo show "Em breve" until Plan 2). Reference mockup: `.superpowers/brainstorm/294-1794476547/content/dashboard-v2.html`.
- Python line length 120; ruff for lint+format (replaces prospector/autopep8). Generated `psa/connected_car_api/` excluded from lint.

## Review Focus

1. **Restart mid-period:** after downtime the engine must enforce the current desired state at once, not wait for the next boundary → test `test_engine_enforces_on_startup` (Task 7).
2. **Partial PSA status** (missing energy/charging fields, `None` level): engine must skip the tick and log, never crash or send commands → `test_decide_returns_none_on_unknown_soc` (Task 6), `test_snapshot_from_status_tolerates_missing_fields` (Task 2).
3. **Invalid tariff edit** (gap, overlap, unknown period, month in two seasons): API rejects with 422 and a pt-PT message, stored tariff unchanged → `test_put_tariff_rejects_gap` (Task 9).
4. **Vazio window shorter than 2 × offset:** no toggle pair generated, car stays deferred → `test_short_window_skipped` (Task 5).
5. **Discord unreachable / webhook unset:** engine keeps running, failure logged once → `test_notifier_swallows_errors` (Task 8).

---

### Task 0: Repository + backend skeleton, move PSA layer

**Files:**
- Create: `.gitignore` (add `.superpowers/`, `data/`, `node_modules/`, `dist/`, `.venv/`, `__pycache__/`)
- Create: `backend/pyproject.toml` (PEP 621, hatchling; deps: fastapi, uvicorn[standard], sqlmodel, pydantic>=2, requests, paho-mqtt>=1.5,<2, cryptography, pycryptodomex==3.23.0, oauth2-client, androguard, python-dateutil, playwright (marker `platform_machine != 'armv7l'`), itsdangerous; dev extra: pytest, ruff, httpx)
- Move: `psa_car_controller/psa/` → `backend/psacc/psa/`
- Move: `psa_car_controller/psacc/model/car.py`, `car_status.py` → `backend/psacc/car.py`, `backend/psacc/car_status.py`
- Move: `psa_car_controller/common/utils.py` (only `parse_hour`, `rate_limit`, `RateLimitException`) → `backend/psacc/utils.py`
- Create: `backend/psacc/psa_client.py` — slim `PSAClient` from `psacc/application/psa_client.py` with abrp, ecomix, weather, DB recording, refresh thread removed
- Modify: `backend/psacc/psa/setup/app_decoder.py` — drop `PSACarController`/`ChargeControl` imports; `InitialSetup.connect(code)` returns the `PSAClient` instead of saving `config.json` to CWD
- Test: `backend/tests/test_imports.py`, ported `backend/tests/test_sms_otp_code.py`, `test_refresh_remote_token.py`, `test_psa.py` (from `tests/`, imports rewritten)

**Interfaces:**
- Produces: package `psacc`; `psacc.psa_client.PSAClient(refresh_token, client_id, client_secret, remote_refresh_token, customer_id, realm, country_code, brand=None)` with `.manager`, `.remote_client`, `.vehicles_list`, `.get_vehicles()`, `.get_vehicle_info(vin, cache=False)`, `.to_dict() -> dict`, `PSAClient.from_dict(d) -> PSAClient`; `psacc.car.Car`, `Cars`.

- [ ] **Step 1:** `git init`, commit the untouched tree as `chore: snapshot of upstream psa_car_controller` (so later moves keep history).
- [ ] **Step 2:** Do the moves with `git mv`; rewrite imports `psa_car_controller.psa` → `psacc.psa`, `psa_car_controller.psacc.model.car` → `psacc.car`, etc.
- [ ] **Step 3: Write `test_imports.py`**

```python
def test_core_modules_import():
    import psacc.psa.RemoteClient, psacc.psa.otp.otp, psacc.psa.oauth, psacc.psa_client, psacc.psa.setup.app_decoder  # noqa

def test_no_legacy_imports():
    import pathlib
    src = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path("psacc").rglob("*.py"))
    assert "psa_car_controller" not in src
    assert "abrp" not in src.lower() and "ecomix" not in src.lower()
```

- [ ] **Step 4:** In `backend/`: `python -m venv .venv && .venv/Scripts/pip install -e .[dev]` (Linux: `.venv/bin/pip`). Run `pytest -q`. Expected: all pass (ported tests included).
- [ ] **Step 5:** `ruff check psacc --exclude psacc/psa/connected_car_api` clean (add `[tool.ruff]` line-length 120 + exclude to pyproject).
- [ ] **Step 6: Commit** `refactor: move PSA layer into new psacc backend package`

---

### Task 1: Settings + persistence

**Files:**
- Create: `backend/psacc/settings.py`, `backend/psacc/store.py`
- Test: `backend/tests/test_store.py`

**Interfaces:**
- Produces:
  - `settings.DATA_DIR: Path` (env `PSACC_DATA_DIR`, default `./data`, created on import); `settings.TZ = ZoneInfo("Atlantic/Azores")`.
  - `store.init_db(url: str | None = None) -> None` (default `sqlite:///{DATA_DIR}/psacc.db`; `"sqlite://"` for tests).
  - `store.get_config() -> AppConfig` / `store.save_config(cfg: AppConfig) -> None`. `AppConfig` (pydantic): `automation_enabled: bool = True`, `offset_min: int = 10` (0–60), `battery_limit: int = 80` (50–100), `discord_webhook: str | None = None`, `discord_info: bool = False`, `refresh_min: int = 5`, `password_hash: str | None = None`, `session_secret: str` (random on first run), `vin: str | None = None`. Stored as one JSON row.
  - `store.get_psa() -> dict | None` / `store.save_psa(d: dict)` — PSAClient.to_dict() blob (file `DATA_DIR/psa.json`, mode 0600).
  - `store.log_activity(kind: Literal["info","action","error"], message: str, at: datetime | None = None) -> None`; `store.recent_activity(limit: int = 50) -> list[Activity]` (newest first; `Activity(at: datetime, kind, message)`).
  - `store.get_override() -> Override | None` / `store.set_override(o: Override | None)`; `Override(started_at: datetime, reason: str)`.
  - `store.get_last_command() -> LastCommand | None` / `store.set_last_command(c)`; `LastCommand(mode: Literal["charge","defer"], at: datetime, verified: bool, attempts: int)`.
  - `store.add_battery_sample(at: datetime, soc: float, charging: bool)`; `store.battery_samples(since: datetime) -> list[BatterySample]`.
  - OTP keeps using `psa/otp/otp.py` `save_otp/load_otp`; point its `CONFIG_NAME` to `DATA_DIR / "otp.bin"`.

- [ ] **Step 1: Write tests** — `test_config_defaults` (fresh DB → `offset_min == 10`, `battery_limit == 80`, `automation_enabled is True`, non-empty `session_secret`), `test_config_roundtrip`, `test_activity_newest_first`, `test_override_set_and_clear`, `test_battery_samples_since` (3 samples, query cutoff returns last 2). Use fixture `init_db("sqlite://")`.
- [ ] **Step 2:** `pytest tests/test_store.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement with SQLModel tables `ConfigRow(id=1, json)`, `ActivityRow`, `StateRow(key, json)` for override/last_command, `BatterySampleRow`.
- [ ] **Step 4:** `pytest tests/test_store.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: settings and sqlite store`

---

### Task 2: PSA REST client (official spec) + CarGateway (+ fake)

**Files:**
- Create: `backend/psacc/psa/rest.py`, `backend/psacc/psa/models.py`, `backend/psacc/gateway.py`, `backend/tests/fakes.py`, `backend/tests/data/status_v4_17.json` (example status built from the spec's `/user/vehicles/{id}/status` response, with `energies`), `backend/tests/data/status_legacy.json` (the old `ELECTRIC_CAR_STATUS` as JSON, with `energy`)
- Copy: the OpenAPI file to `backend/psacc/psa/api-b2c-v4.17.2.yaml` (reference only)
- Delete: `backend/psacc/psa/connected_car_api/`; `Oauth2PSACCApiConfig`, `OauthAPIClient` in `psa/oauth.py`; `car_status.py`; generated-model usage in `psa_client.py`, `car.py`, `RemoteClient._fix_not_updated_api`
- Test: `backend/tests/test_gateway.py`, `backend/tests/test_psa_rest.py`

**Interfaces:**
- Consumes: `PSAClient` (Task 0).
- Produces (REST):
  - `PsaRest(manager: OpenIdCredentialManager, client_id: str, realm: str, base_url: str = "https://api.groupe-psa.com/connectedcar/v4")` with `get(path: str, **params) -> dict` — query `client_id`, headers `x-introspect-realm: realm`, `Authorization: Bearer <manager.access_token>`, `Accept: application/hal+json`; on 401 calls `manager.refresh_token_now()` once and retries; timeout 20 s.
  - `PsaRest.vehicles() -> list[VehicleRef]` (`/user/vehicles`, reads `_embedded.vehicles[].{id, vin, brand, label}`); `PsaRest.status(vehicle_id: str) -> VehicleStatus` (`/user/vehicles/{id}/status`).
  - `models.py`: pydantic v2 models, **all fields optional, `extra="ignore"`**, covering only what the gateway reads: `energies[]` (type `Electric`, `level`, `autonomy`, `charging.{plugged, status, chargingRate, chargingMode, nextDelayedTime, remainingTime, schedule}`), deprecated `energy[]` as fallback, `lastPosition.geometry.coordinates`, `doorsState.lockedStates`, `preconditioning`/`preconditionning` (`airConditioning.status`), `updatedAt`. Unknown enum values kept as plain strings (spec says enums may grow).
  - `PSAClient.get_vehicles()` / `get_vehicle_info()` reimplemented on `PsaRest`.
- Produces (gateway):

```python
@dataclass(frozen=True)
class CarSnapshot:
    vin: str
    at: datetime                 # UTC, status updated_at
    soc: float | None            # %
    range_km: float | None
    plugged: bool | None
    charging: bool | None        # status == "InProgress"
    charge_status: str | None    # raw: InProgress/Stopped/Finished/Disconnected/Failure
    charge_rate_kmh: int | None
    next_delayed_time: str | None  # raw PSA ISO-8601 duration, e.g. "PT22H30M"
    latitude: float | None
    longitude: float | None
    locked: bool | None
    precond_on: bool | None

class CarGateway(Protocol):
    def snapshot(self, refresh: bool = False) -> CarSnapshot: ...   # refresh=True → wakeup + fetch
    def set_charge_mode(self, mode: Literal["charge", "defer"], program_hour: tuple[int, int]) -> bool: ...
    def precondition(self, on: bool) -> bool: ...
    def lock(self, locked: bool) -> bool: ...
    def horn(self) -> bool: ...
    def lights(self) -> bool: ...
    def mqtt_connected(self) -> bool: ...
```

  - `PsaGateway(client: PSAClient, vin: str)`: `set_charge_mode("charge", h)` → `remote_client.veh_charge_request(vin, h[0], h[1], IMMEDIATE_CHARGE)`; `"defer"` → same with `DELAYED_CHARGE`. Returns the `publish` result.
  - `snapshot_from_status(vin, status: VehicleStatus) -> CarSnapshot` (pure, never raises; prefers `energies` Electric entry, falls back to deprecated `energy`; missing → `None`). MQTT lock/precond state from `RemoteClient` overrides REST when newer.
  - `tests/fakes.py: FakeGateway` with settable `snap: CarSnapshot`, records `calls: list[tuple]`, `fail_next: int` (number of upcoming commands returning False / not taking effect), and `apply_effect: bool` (when True, `set_charge_mode` updates `snap.charging` to match, simulating the car).

- [ ] **Step 1: Write tests** — `test_snapshot_from_energies` (`status_v4_17.json` → `soc`, `range_km`, `plugged`, `charge_status` equal fixture values), `test_snapshot_falls_back_to_legacy_energy` (`status_legacy.json` → same fields from `energy`), `test_snapshot_from_status_tolerates_missing_fields` (`{}` and `{"energies": []}` → `soc is None`, no exception), `test_unknown_enum_kept` (`chargingPowerLevel: "Level9"` parses), `test_rest_refreshes_once_on_401` (mocked session: 401 then 200 → one `refresh_token_now` call, dict returned), `test_set_charge_mode_maps_to_mqtt` (PsaGateway with MagicMock client: `"defer", (0,10)` → `veh_charge_request(vin, 0, 10, "delayed")`).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement; delete the generated client and fix ported tests that used `ApiClient()._ApiClient__deserialize` to load the JSON fixtures instead. **Step 4:** `pytest -q` → all PASS; `test_no_legacy_imports` extended to assert `"connected_car_api" not in src`.
- [ ] **Step 5: Commit** `feat: PSA REST client from official v4.17 spec and CarGateway`

---

### Task 3: Setup CLI + deferred-mode spike (owner runs on real car)

**Files:**
- Create: `backend/psacc/setup_flow.py`, `backend/scripts/spike_deferred.py`
- Test: `backend/tests/test_setup_flow.py`

**Interfaces:**
- Consumes: `InitialSetup`, `get_oauth_code_headless`, `RemoteClient.get_sms_otp_code`, `new_otp_session`, `store.save_psa`.
- Produces (reused by the API in Task 10):
  - `setup_flow.start_login(app_name: str, email: str, password: str, country: str) -> LoginResult` — `LoginResult(ok: bool, manual_url: str | None, error: str | None)`; tries headless OAuth, on failure returns `manual_url` for the user to finish.
  - `setup_flow.finish_login(code: str) -> None` — completes OAuth, `store.save_psa(client.to_dict())`, sets `cfg.vin` to the first vehicle.
  - `setup_flow.request_sms() -> None`; `setup_flow.activate_otp(sms_code: str, pin: str) -> None`.
  - `setup_flow.load_gateway() -> PsaGateway | None` — from stored PSA dict + `cfg.vin`; starts MQTT.

- [ ] **Step 1: Write tests** with patched `InitialSetup`/headless: `test_start_login_headless_success_saves_psa`, `test_start_login_headless_failure_returns_manual_url`, `test_load_gateway_none_without_config`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Write `scripts/spike_deferred.py`** — interactive CLI (`input()`/`getpass`): login (or reuse stored), SMS + PIN, then:
  1. print snapshot; ask owner to plug in and confirm charging;
  2. `set_charge_mode("defer", next 00:10)`; wait 90 s; print snapshot → expect `charging False`;
  3. `set_charge_mode("charge", …)`; wait 90 s → expect `charging True`;
  4. dump raw REST status + last MQTT `charging_state` JSON to `DATA_DIR/spike-report.json`, including raw `charging.nextDelayedTime` and `charging.schedule` **before and after** sending `defer` with program hour 00:10, plus local time of the read (settles whether `nextDelayedTime` is time-of-day, as old code assumes, or duration-until-charge, as the official spec says).
- [x] **Step 6: Owner checkpoint.** Spike successfully completed on a real e-208 in Azores. Results documented in `backend/psacc/psa/NOTES.md`:
  - `defer` stops ongoing charge (confirmed).
  - `charging.nextDelayedTime` is time-of-day formatted as an ISO-8601 duration string (e.g. `PT10M` for 00:10).
  - Timezone verdict: `local` (car reads program hour in Azores local time, no UTC conversion needed).
  - MQTT `cable_detected` is 0 despite being plugged; confirmed REST-only source for `plugged`/`charging`.
- [x] **Step 7: Commit** `feat: setup flow and deferred-charge spike script`

---

### Task 4: Tariff model + EDA preset

**Files:**
- Create: `backend/psacc/tariff/__init__.py`, `model.py`, `presets.py`
- Test: `backend/tests/test_tariff_model.py`

**Interfaces:**
- Produces (pydantic v2 models, JSON-serialisable):

```python
PeriodType = Literal["vazio", "cheias", "ponta"]
DayType = Literal["weekday", "saturday", "sunday"]
class Slot(BaseModel): start: int; end: int; period: PeriodType   # minutes of local day, 0..1440, start < end
class Season(BaseModel): name: str; months: list[int]; days: dict[DayType, list[Slot]]
class Price(BaseModel): period: PeriodType; eur_kwh: float; valid_from: date
class Tariff(BaseModel): name: str; seasons: list[Season]; prices: list[Price] = []
```

  - `Tariff.validate_full()` (model validator): months 1–12 each in exactly one season; every season has all 3 day types; each day's slots sorted, contiguous, cover 0→1440, no overlap; minutes multiple of 15. Errors raised in pt-PT, e.g. `"Inverno / sábado: falta o intervalo 11:30–13:30"`.
  - `Tariff.season_for(month: int) -> Season`; `Tariff.price_at(period, on: date) -> float | None` (latest `valid_from <= on`).
  - `presets.EDA_TRI_SEMANAL: Tariff` named `"EDA tri-horário semanal"`, prices empty (owner fills):
    - Inverno, months [11,12,1,2,3,4,5]: weekday vazio 00:00–07:00, cheias 07:00–18:30, ponta 18:30–21:30, cheias 21:30–24:00; saturday vazio 00:00–11:30, cheias 11:30–13:30, vazio 13:30–18:00, cheias 18:00–23:00, vazio 23:00–24:00; sunday vazio 00:00–24:00.
    - Verão, months [6,7,8,9,10]: weekday vazio 00:00–07:00, cheias 07:00–10:30, ponta 10:30–15:30, cheias 15:30–24:00; saturday vazio 00:00–11:00, cheias 11:00–14:30, vazio 14:30–19:30, cheias 19:30–23:00, vazio 23:00–24:00; sunday vazio 00:00–24:00.

- [ ] **Step 1: Write tests** — `test_eda_preset_valid`; `test_eda_hour_totals` (winter weekday: ponta 180, cheias 840, vazio 420 min; summer weekday: ponta 300, cheias 720, vazio 420; saturday both: cheias 420, vazio 1020; sunday vazio 1440 — the leaflet totals); `test_rejects_gap` (message contains `"falta"`), `test_rejects_overlap`, `test_rejects_month_twice`, `test_rejects_non_15_min`; `test_price_at_picks_latest_valid_from`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: tariff model with EDA weekly tri-horário preset`

---

### Task 5: Tariff schedule (windows, events, desired mode)

**Files:**
- Create: `backend/psacc/tariff/schedule.py`
- Test: `backend/tests/test_schedule.py`

**Interfaces:**
- Consumes: `Tariff` (Task 4), `settings.TZ`.
- Produces:
  - `period_at(t: Tariff, at: datetime) -> PeriodType` (aware `at`, converted to TZ; weekday Mon–Fri → `"weekday"`).
  - `vazio_windows(t: Tariff, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]` — aware TZ datetimes, adjacent/touching windows **merged across midnight and season changes**, clipped to `[start, end]` only at the outer edges (a window in progress at `start` is returned with its true start).
  - `@dataclass Event(at: datetime, action: Literal["charge","defer"])`
  - `planned_events(t: Tariff, offset_min: int, start: datetime, days: int = 7) -> list[Event]` — per window `(s, e)`: `Event(s+offset, "charge")`, `Event(e-offset, "defer")`; skip window if `e - s <= 2*offset`; only events `>= start`; sorted.
  - `desired_mode(t: Tariff, offset_min: int, at: datetime) -> Literal["charge","defer"]` — `"charge"` iff some window has `s+offset <= at < e-offset`.
  - `next_vazio_start(t, offset_min, at) -> datetime` — next `s+offset` strictly after `at` (used as deferred program hour).
- Implementation note: build windows by walking local calendar days from `start.date()-1` to `end.date()+1`, constructing wall-clock datetimes with `datetime.combine(day, time) .replace(tzinfo=TZ)` (minute 1440 = next day 00:00), then merge where `prev_end >= next_start`.

- [ ] **Step 1: Write tests** (EDA preset, offset 10, all times `Atlantic/Azores`):
  - `test_winter_weekday_events` — from Thu 12/11/2026 00:00, `days=2`: first four events `[charge Thu 00:10, defer Thu 06:50, charge Fri 00:10, defer Fri 06:50]` (weekday vazio is only 00:00–07:00, so no merge across weekday midnights).
  - `test_weekend_merge` — window starting Sat 14/11/2026 23:00 merges through Sunday into Mon 16/11 07:00 → single pair `charge Sat 23:10`, `defer Mon 06:50`; no event at Sun/Mon 00:00.
  - `test_saturday_multiple_windows` — events dated Sat 14/11/2026 (Fri vazio ended 07:00, so Sat's first window starts 00:00): `charge 00:10, defer 11:20, charge 13:40, defer 17:50, charge 23:10`.
  - `test_season_switch` — Sat 30/05/2026 23:00 → Mon 01/06/2026 07:00 merged (31/05/2026 is a Sunday); first June weekday uses summer: `period_at(Mon 01/06 11:00) == "ponta"`.
  - `test_dst_weekend` — Sat 28/03/2026 23:00 → Mon 30/03 07:00 window (Azores DST starts Sun 29/03 00:00→01:00): one pair `charge Sat 23:10`, `defer Mon 06:50`, and UTC length is 31 h.
  - `test_short_window_skipped` — custom tariff with 20-min vazio, offset 10 → no events; `desired_mode` always `"defer"`.
  - `test_desired_mode_samples` — Thu 12/11/2026 00:05 → defer; 00:10 → charge; 06:49 → charge; 06:50 → defer; 19:00 → defer.
  - `test_next_vazio_start` — Thu 12/11/2026 07:00 → Fri 13/11 00:10.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: tariff schedule with merged vazio windows and offset events`
---

### Task 6: `decide()` — pure automation policy

**Files:**
- Create: `backend/psacc/automation/__init__.py`, `decide.py`
- Test: `backend/tests/test_decide.py`

**Interfaces:**
- Consumes: `CarSnapshot`, `AppConfig`, `Override`, `Tariff`, `desired_mode`.
- Produces:

```python
@dataclass(frozen=True)
class Decision:
    mode: Literal["charge", "defer"] | None   # None = do nothing
    reason: str                               # pt-PT, shown in activity log
    clear_override: bool = False

def decide(now: datetime, cfg: AppConfig, tariff: Tariff, car: CarSnapshot, override: Override | None) -> Decision
```

  Rules, first match wins:
  1. `not cfg.automation_enabled` → `None`, "Automação desligada".
  2. `car.soc is None` or `car.plugged is None` → `None`, "Estado do carro desconhecido".
  3. `car.plugged is False` → `None`, "Cabo desligado", `clear_override = override is not None`.
  4. override and `soc >= battery_limit` → `"defer"`, "Limite de {limit} % atingido (carga manual terminada)", `clear_override=True`.
  5. override → `"charge"`, "Carga manual ativa".
  6. `soc >= battery_limit` → `"defer"`, "Limite de {limit} % atingido".
  7. else `desired_mode(...)` → `"charge"` "Período de vazio" / `"defer"` "Fora do vazio ({period})".

- [ ] **Step 1: Write tests** — one per rule: `test_disabled`, `test_decide_returns_none_on_unknown_soc`, `test_unplugged_clears_override`, `test_override_until_limit`, `test_override_charges_in_ponta` (Thu 19:00, soc 50 → charge), `test_limit_defers_in_vazio` (Thu 01:00, soc 80 → defer), `test_vazio_charges`, `test_cheias_defers` (reason contains "cheias").
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: pure charge automation policy`

---

### Task 7: Engine — act, verify, retry, alert

**Files:**
- Create: `backend/psacc/automation/engine.py`
- Test: `backend/tests/test_engine.py`

**Interfaces:**
- Consumes: `decide`, `next_vazio_start`, `CarGateway`, store fns, `Notifier` (Task 8 — use a `Protocol` with `alert(msg: str) -> None`, `info(msg: str) -> None`; pass a stub in tests).
- Produces: `class Engine(gateway: CarGateway, notifier: Notifier, clock: Callable[[], datetime] = utcnow, verify_delay_s: int = 60)` with
  - `tick() -> None` — one evaluation (called by poller every refresh and at each planned event).
  - `on_status(snap: CarSnapshot) -> None` — store battery sample, log plug/unplug transitions, then `tick()`.
  - `manual_charge() -> None` — `set_override(Override(now, "manual"))`, log, `tick()`.
  - `cancel_override() -> None`.
- Behaviour (decides the semantics the spike validated):
  - Command needed when: `decision.mode != last_command.mode`, **or** `mode == "defer" and car.charging`, **or** `mode == "charge" and not car.charging and car.charge_status != "Finished"` and last command older than `verify_delay_s * 3`.
  - Program hour sent with every command = `next_vazio_start(...)` in TZ (for "charge" too, so the car's own program stays aligned).
  - After sending: schedule verification after `verify_delay_s` (`threading.Timer`; tests call `engine.verify_now()` directly) → `snapshot(refresh=True)`; success iff `charging == (mode == "charge")`. On success `last_command.verified=True`, log action "{reason} → diferido ligado/desligado".
  - On failure: retry, max 3 attempts, delays 1 min, 3 min, 10 min. After the 3rd failure: `log_activity("error", …)`, `notifier.alert("⚠️ PSACC: não consegui {ativar/desativar} a carga diferida ({reason}). O carro pode estar a carregar fora do vazio.")` — **once per failure episode** (reset on next success).
  - Exceptions from gateway → treated as failed attempt, logged, never propagate.
- [ ] **Step 1: Write tests** using `FakeGateway` + fake clock + `init_db("sqlite://")`:
  - `test_engine_enforces_on_startup` — empty state, Thu 19:00, plugged, charging, soc 50 → one call `("defer", (0,10))` on first `tick()`.
  - `test_no_repeat_command_when_already_verified`.
  - `test_retry_then_alert_once` — `fail_next=3` → 3 `set_charge_mode` calls across `verify_now()`s, exactly 1 `alert`, error row in activity.
  - `test_success_after_retry_resets_episode`.
  - `test_plug_in_during_cheias_defers` — snapshot transitions `plugged False → True, charging True` at Thu 21:00 → defer sent, activity contains "Cabo ligado".
  - `test_manual_charge_then_limit` — manual at 19:00 soc 70 → charge; soc 80 → defer + override cleared.
  - `test_gateway_exception_counts_as_failure`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: automation engine with verification, retries and alerts`

---

### Task 8: Discord notifier

**Files:**
- Create: `backend/psacc/notify.py`
- Test: `backend/tests/test_notify.py`

**Interfaces:**
- Produces: `class DiscordNotifier(get_cfg: Callable[[], AppConfig])` with `alert(msg)` (always, if webhook set), `info(msg)` (only if `cfg.discord_info`), `test() -> bool` (sends "✅ PSACC ligado ao Discord"). POST `{"content": msg}` with `requests.post(url, json=…, timeout=10)`.
- [ ] **Step 1: Write tests** (patch `requests.post`): `test_alert_posts_content`, `test_info_suppressed_by_default`, `test_no_webhook_noop`, `test_notifier_swallows_errors` (post raises `ConnectionError` → no exception, one log line).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: discord webhook notifier`

---

### Task 9: Poller + scheduler loop + health alerts

**Files:**
- Create: `backend/psacc/poller.py`
- Test: `backend/tests/test_poller.py`

**Interfaces:**
- Consumes: `CarGateway`, `Engine`, `planned_events`, store, notifier.
- Produces: `class Poller(gateway, engine, notifier, clock=utcnow)`:
  - `run_once() -> None` — `snapshot()` (refresh=True every `cfg.refresh_min`), `engine.on_status(snap)`; on PSA auth error (`RemoteException`/token refresh False) → alert "🔑 PSACC: sessão PSA expirada, refaça o login em Definições" once per episode; MQTT disconnected > 15 min → alert once per episode.
  - `start() / stop()` — background thread: sleep until `min(next refresh, next planned event + 5 s)`, then `run_once()`.
  - `latest() -> CarSnapshot | None` for the API.
  - Hook RemoteClient MQTT event messages → `engine.on_status` via a callback list on `RemoteClient` (add `on_event: list[Callable]` in `RemoteClient._on_mqtt_message`; gateway exposes `subscribe(cb)`).
- [ ] **Step 1: Write tests** — `test_next_wake_is_planned_event` (Thu 06:48, refresh 5 min → next wake 06:50:05, the planned defer + 5 s, because it precedes the 06:53 refresh), `test_auth_error_alerts_once`, `test_mqtt_down_15_min_alerts_once`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: poller with event-aligned wakeups and health alerts`

---

### Task 10: HTTP API + auth

**Files:**
- Create: `backend/psacc/main.py`, `backend/psacc/auth.py`, `backend/psacc/api/__init__.py`, `api/auth.py`, `api/status.py`, `api/automation.py`, `api/control.py`, `api/setup.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: everything above. App state on `app.state`: `gateway | None`, `engine`, `poller`, `notifier`.
- Produces (all JSON, prefix `/api`, all except auth + `/api/health` require session):
  - `GET /api/auth/state` → `{"configured": bool, "logged_in": bool}`; `POST /api/auth/setup {password}` (only when no password; min 8 chars; `hashlib.scrypt`); `POST /api/auth/login {password}`; `POST /api/auth/logout`. Session via Starlette `SessionMiddleware(secret_key=cfg.session_secret, same_site="lax", https_only=False)`. Login rate limit: 5 failures / 5 min → 429.
  - `GET /api/status` → `{car: CarSnapshot|null, period: PeriodType, next_event: Event|null, automation: {enabled, offset_min, battery_limit}, override: Override|null, last_command, mqtt_connected: bool, updated_at}`.
  - `GET /api/battery?hours=24` → `[{at, soc, charging}]`.
  - `GET /api/activity?limit=50`.
  - `GET /api/automation` / `PUT /api/automation {enabled, offset_min, battery_limit}` (validation ranges from Task 1) → triggers `engine.tick()`.
  - `GET /api/tariff` / `PUT /api/tariff Tariff` (422 with pt-PT `detail` on validation failure) / `POST /api/tariff/reset` (EDA preset, keeps prices) / `GET /api/tariff/events?days=7`.
  - `POST /api/control/charge-now`, `POST /api/control/cancel-override`, `POST /api/control/precondition {on}`, `/lock {locked}`, `/horn`, `/lights`, `/wakeup` → `{ok: bool, message: str}`.
  - `GET/PUT /api/settings {discord_webhook, discord_info, refresh_min}`; `POST /api/settings/discord-test`.
  - `POST /api/setup/login {app_name, email, password, country}` → `LoginResult`; `POST /api/setup/code {code}`; `POST /api/setup/sms`; `POST /api/setup/otp {sms_code, pin}`; `GET /api/setup/state` → `{psa_connected, otp_ready, vin}`. Successful OTP → build gateway, start poller.
  - `GET /api/health` → `{"ok": true}` (Docker healthcheck).
  - Static: `StaticFiles(directory=PSACC_STATIC_DIR, html=True)` mounted at `/` with SPA fallback to `index.html`.
- [ ] **Step 1: Write tests** (`fastapi.testclient.TestClient`, FakeGateway injected, `sqlite://`): `test_requires_login`, `test_first_run_setup_then_login`, `test_login_rate_limited`, `test_status_shape`, `test_put_tariff_rejects_gap` (422, `detail` contains "falta", GET returns previous tariff), `test_put_automation_out_of_range_422`, `test_charge_now_sets_override`, `test_tariff_events_7_days`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Manual: `uvicorn psacc.main:app --reload` → `GET http://127.0.0.1:8000/api/health` returns `{"ok":true}`.
- [ ] **Step 6: Commit** `feat: FastAPI API with password auth`

---

### Task 11: Frontend scaffold, design tokens, shell, login

**Files:**
- Create: `frontend/` via `npm create vite@latest frontend -- --template react-ts`; add `tailwindcss @tailwindcss/vite @tanstack/react-query react-router recharts`; dev: `vitest @testing-library/react jsdom`.
- Create: `frontend/src/styles.css` (tokens), `src/api.ts`, `src/format.ts`, `src/layout/Shell.tsx`, `src/layout/Sidebar.tsx`, `src/pages/Login.tsx`, `src/pages/ComingSoon.tsx`, `src/App.tsx`
- Modify: `frontend/vite.config.ts` (proxy `/api` → `http://127.0.0.1:8000`)
- Test: `frontend/src/format.test.ts`

**Interfaces:**
- Produces:
  - Tokens as CSS variables on `:root` (light) and `prefers-color-scheme: dark`: `--bg #f4f6fa`, `--card #fff`, `--text #1d2433`, `--muted #64748b`, `--primary #2563eb`, `--vazio #22c55e`, `--cheias #fbbf24`, `--ponta #ef4444`, radius 12 px, card shadow `0 1px 3px #0001` (from mockup).
  - `api.ts`: `apiGet<T>(path)`, `apiSend<T>(method, path, body?)`; on 401 redirect to `/login`. Types mirroring Task 10 responses (`Status`, `CarSnapshot`, `Tariff`, `Activity`, `Event`).
  - `format.ts`: `fmtHour(d: Date|string): string` → `"06h50"` in `Atlantic/Azores`; `fmtDate(d)` → `"12/11/2026"`; `fmtPct(n)` → `"78 %"`; `fmtKm(n)` → `"312 km"`; `fmtEur(n)` → `"0,12 €"` (`Intl` pt-PT).
  - Routes: `/` Painel, `/automacao`, `/cargas`, `/viagens`, `/consumo` (ComingSoon), `/controlo`, `/definicoes`, `/login`. Sidebar footer: "Atualizado há N min" + MQTT dot from `/api/status` (react-query, refetch 30 s).
  - Login page: first-run "Defina uma palavra-passe" vs "Entrar".
- [ ] **Step 1: Write `format.test.ts`** — `fmtHour("2026-11-12T07:50:00Z") === "06h50"`; `fmtDate("2026-11-12T12:00:00Z") === "12/11/2026"`; `fmtEur(0.1234) === "0,12 €"`.
- [ ] **Step 2:** `npm run test` → FAIL. **Step 3:** implement. **Step 4:** PASS; `npm run build` succeeds; `npm run dev` shows sidebar + login against running backend.
- [ ] **Step 5: Commit** `feat: frontend shell, tokens and login`

---

### Task 12: Painel

**Files:**
- Create: `frontend/src/pages/Painel.tsx`, `src/components/TariffStrip.tsx`, `src/components/BatteryChart.tsx`, `src/components/ActivityList.tsx`, `src/components/StatusPill.tsx`, `src/lib/tariff.ts`
- Test: `frontend/src/lib/tariff.test.ts`

**Interfaces:**
- Consumes: `/api/status`, `/api/battery`, `/api/activity`, `/api/tariff`, control endpoints.
- Produces: `lib/tariff.ts: daySegments(t: Tariff, day: Date): {start:number,end:number,period:PeriodType}[]` (same day-type/season rules as backend; used by TariffStrip and Automação preview).
- Layout exactly as mockup `dashboard-v2.html`: header (car name, status pill), 3 cards (Bateria / Autonomia / Automação with next event + offset), full-width today strip with legend and a current-time marker, 24h battery area chart + activity list, quick actions (Carregar agora · Climatizar · Trancar · Luzes · Buzina). When override active, Automação card shows "Carga manual ativa" + "Cancelar". Buttons show pending spinner, then toast with API `message`. Buzina/Trancar ask inline confirm (no `window.confirm`).
- Status pill states: "A carregar · vazio" (green), "Em diferido" (blue), "Ligado · sem carregar" (grey), "Desligado da tomada" (grey), "Erro de automação" (red, when last activity is error and unresolved).
- [ ] **Step 1: Write `tariff.test.ts`** — EDA preset Thursday 12/11/2026 → 4 segments `[0–420 vazio, 420–1110 cheias, 1110–1290 ponta, 1290–1440 cheias]`; Sunday → single vazio segment.
- [ ] **Step 2–4:** FAIL → implement → PASS; visually check against mockup in browser.
- [ ] **Step 5: Commit** `feat: painel page`

---

### Task 13: Automação page (settings + weekly grid editor)

**Files:**
- Create: `frontend/src/pages/Automacao.tsx`, `src/components/TariffEditor.tsx`, `src/components/EventsList.tsx`
- Test: `frontend/src/components/TariffEditor.test.tsx`

**Interfaces:**
- Consumes: `/api/automation`, `/api/tariff`, `/api/tariff/reset`, `/api/tariff/events`, `/api/activity`.
- Behaviour:
  - Top card: automation switch, offset (minutes, number input 0–60), battery limit (slider 50–100, default 80) — save button, inline errors from 422.
  - Tariff card: season tabs (Inverno / Verão + editable month chips), 3 rows (Dias úteis, Sábado, Domingo) × 96 columns (15-min cells), coloured by period; click-drag paints the selected period (palette: vazio/cheias/ponta). Shows hour labels every 2 h. "Repor EDA" button (inline confirm). Save → `PUT /api/tariff`; show backend `detail` on 422.
  - Prices card: table per period with `€/kWh` and "em vigor desde" date; add new price row.
  - Upcoming events (7 days) grouped by day: "qui 12/11 · 00h10 Desligar diferido (carregar)", "06h50 Ligar diferido".
  - Full activity log (100 rows).
- Grid ↔ model conversion: `slotsToCells(slots): PeriodType[96]`, `cellsToSlots(cells): Slot[]` (merge consecutive equal cells) in `TariffEditor.tsx` (exported).
- [ ] **Step 1: Write tests** — `cellsToSlots(slotsToCells(edaWinterSaturday))` deep-equals input; painting cells 44–53 as "cheias" on a weekday yields slots with a cheias block 660–810.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: automação page with weekly tariff grid editor`

---

### Task 14: Controlo + Definições pages

**Files:**
- Create: `frontend/src/pages/Controlo.tsx`, `src/pages/Definicoes.tsx`, `src/components/SetupWizard.tsx`

**Interfaces:**
- Consumes: control, settings, setup endpoints (Task 10).
- Controlo: cards for Carga (Carregar agora / Cancelar carga manual), Climatização (on/off + current state), Fechaduras (state + Trancar/Destrancar with inline confirm for Destrancar), Buzina, Luzes, Acordar carro. Each shows pending → "Confirmado"/"Falhou: …".
- Definições: (1) Conta PSA — `SetupWizard` 3 steps: credentials (app select defaults "MyPeugeot", country "PT") → if `manual_url`, show link + paste-code field → SMS button + SMS code + PIN → done shows VIN. (2) Discord — webhook URL, "Enviar teste", toggle info messages. (3) Atualização — refresh minutes. (4) Palavra-passe — change.
- [ ] **Step 1:** Implement; manual check with FakeGateway mode: start backend with `PSACC_FAKE=1` (add to `main.py`: uses `FakeGateway` with a plausible snapshot, for UI dev) and walk every button.
- [ ] **Step 2:** `npm run build` passes `tsc` with no errors.
- [ ] **Step 3: Commit** `feat: controlo and definições pages`

---

### Task 15: Docker, cleanup, docs

**Files:**
- Create: `Dockerfile` (stage 1 `node:22-alpine` → `npm ci && npm run build`; stage 2 `python:3.12-slim-bookworm` → `pip install ./backend`, `playwright install --with-deps --only-shell chromium` (skip on armv7), copy `frontend/dist` → `/app/static`; env `PSACC_DATA_DIR=/data PSACC_STATIC_DIR=/app/static TZ=Atlantic/Azores`; `VOLUME /data`; `EXPOSE 5000`; `HEALTHCHECK curl -f http://localhost:5000/api/health`; `CMD uvicorn psacc.main:app --host 0.0.0.0 --port 5000`)
- Replace: `docker-compose.yml` (one service, `./data:/data`, port `5000:5000`, `restart: unless-stopped`)
- Delete: `psa_car_controller/`, `tests/`, `docker_files/`, `pyproject.toml`, `poetry.lock`, `.prospector.yaml`, `.pre-commit-config.yaml`, `.coveragerc`, `.eslintrc.yml`, `config.ini`, `psa-car-controller.service`, `api_spec.md` (superseded by `backend/psacc/psa/api-b2c-v4.17.2.yaml`), old `docs/` except `docs/superpowers/`, `FAQ.md`, `.github/workflows/release.yml`
- Replace: `.github/workflows/test.yml` (backend `pytest` + `ruff`, frontend `npm ci && npm test && npm run build`)
- Rewrite: `README.md` (pt-PT: what it does, docker compose up, first-run steps), `CLAUDE.md` (new commands + architecture)

- [ ] **Step 1:** `docker build -t psacc .` succeeds; `docker run -p 5000:5000 -v ./data:/data psacc` → `curl localhost:5000/api/health` → `{"ok":true}`; browser shows login.
- [ ] **Step 2:** Full test run: `cd backend && pytest -q` all pass; `cd frontend && npm test && npm run build` pass.
- [ ] **Step 3: Owner acceptance:** owner completes setup in Definições, sets EDA prices, leaves car plugged overnight; next morning the activity log shows `00h10` charge and `06h50` defer, verified.
- [ ] **Step 4: Commit** `chore: docker image, remove legacy app, update docs`
