import json
import logging
import os
import secrets
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Field as SQLField
from sqlmodel import Session, SQLModel, create_engine, select

from . import settings
from .history.charges import ChargeSample, ChargeSession, session_kwh
from .history.cost import session_cost
from .history.trips import Trip
from .places import Place, PositionFix
from .tariff.model import Tariff
from .tariff.presets import EDA_TRI_SEMANAL

logger = logging.getLogger(__name__)


class AppConfig(BaseModel):
    automation_enabled: bool = True
    offset_min: int = Field(10, ge=0, le=60)
    battery_limit: int = Field(80, ge=50, le=100)
    discord_webhook: str | None = None
    discord_info: bool = False
    refresh_min: int = 5
    password_hash: str | None = None
    session_secret: str = Field(default_factory=lambda: secrets.token_hex(32))
    vin: str | None = None
    battery_capacity_kwh: float = Field(46, ge=10, le=150)  # usable capacity
    session_epoch: int = 0  # bumped on password change: sessions issued before it stop being valid


ActivitySource = Literal["automation", "control"]


class Activity(BaseModel):
    at: datetime
    kind: Literal["info", "action", "error"]
    message: str
    source: ActivitySource = "automation"  # "control": a command sent from the Controlo page


class Override(BaseModel):
    started_at: datetime
    reason: str


class LastCommand(BaseModel):
    mode: Literal["charge", "defer"]
    at: datetime
    verified: bool
    attempts: int
    reason: str = ""
    alerted: bool = False  # failure alert already sent in this episode (reset on the next verified success)
    program_hour: tuple[int, int] | None = None  # (hour, minute) Azores local time sent with the command


class WorkEpisode(BaseModel):
    """The car is plugged in at Trabalho and the one "charge" of this plug-in episode was already sent."""

    charge_sent_at: datetime


class BatterySample(BaseModel):
    at: datetime
    soc: float
    charging: bool


class ConfigRow(SQLModel, table=True):
    id: int = SQLField(default=1, primary_key=True)
    data: str


class ActivityRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    at: datetime = SQLField(index=True)
    kind: str
    message: str
    source: str = "automation"


class StateRow(SQLModel, table=True):
    key: str = SQLField(primary_key=True)
    data: str


class BatterySampleRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    at: datetime = SQLField(index=True)
    soc: float
    charging: bool


class HealthSampleRow(SQLModel, table=True):
    day: date = SQLField(primary_key=True)  # Azores local day
    value: float


class PlaceRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    name: str
    kind: str
    lat: float
    lon: float
    radius_m: int = 150
    price_eur_kwh: float | None = None


class ChargeSessionRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    started_at: datetime = SQLField(index=True)
    ended_at: datetime | None = None
    place_kind: str
    place_name: str | None = None
    soc_start: float
    soc_end: float | None = None
    kwh: float | None = None
    cost_eur: float | None = None
    price_override_eur_kwh: float | None = None
    partial: bool = False


class ChargeSampleRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    session_id: int = SQLField(index=True)
    at: datetime
    soc: float
    rate_kmh: float | None = None


class TripRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    departed_at: datetime = SQLField(index=True)
    arrived_at: datetime
    from_kind: str
    from_name: str | None = None
    to_kind: str
    to_name: str | None = None
    from_lat: float | None = None
    from_lon: float | None = None
    to_lat: float | None = None
    to_lon: float | None = None
    km: float
    soc_start: float | None = None
    soc_end: float | None = None
    kwh: float | None = None
    kwh_100km: float | None = None


_engine = None


def _utc(dt: datetime) -> datetime:
    """Normalise to aware UTC (naive values are assumed to be UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def init_db(url: str | None = None) -> None:
    global _engine
    url = url or f"sqlite:///{settings.DATA_DIR / 'psacc.db'}"
    kwargs = {"connect_args": {"check_same_thread": False}}
    if url in ("sqlite://", "sqlite:///:memory:"):
        kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    SQLModel.metadata.create_all(_engine)
    _add_activity_source(_engine)
    _restrict_db_file(url)
    _ensure_config()


def _add_activity_source(engine) -> None:
    """Databases created before activity entries had a source get the column (existing rows: automation)."""
    if "source" in {c["name"] for c in inspect(engine).get_columns("activityrow")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE activityrow ADD COLUMN source VARCHAR NOT NULL DEFAULT 'automation'"))


def _restrict_db_file(url: str) -> None:
    """The database holds the password hash and the session secret: owner-only (best effort; on Windows chmod
    only toggles the read-only flag, and 0o600 keeps the file writable)."""
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url == "sqlite:///:memory:":
        return
    try:
        os.chmod(url[len(prefix):], 0o600)
    except OSError as exc:
        logger.warning("could not restrict the database permissions: %s", type(exc).__name__)


def _ensure_config() -> None:
    """Create the config row (with its random session secret) if absent; a concurrent creator wins cleanly."""
    with _session() as s:
        if s.get(ConfigRow, 1) is not None:
            return
        s.add(ConfigRow(id=1, data=AppConfig().model_dump_json()))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()  # another process/thread inserted it first: keep theirs


def _session() -> Session:
    if _engine is None:
        init_db()
    return Session(_engine)


def get_config() -> AppConfig:
    for _ in range(2):
        with _session() as s:
            row = s.get(ConfigRow, 1)
            if row:
                return AppConfig.model_validate_json(row.data)
        _ensure_config()  # only if the row was deleted behind our back
    raise RuntimeError("config row could not be created")


def save_config(cfg: AppConfig) -> None:
    with _session() as s:
        s.merge(ConfigRow(id=1, data=cfg.model_dump_json()))
        s.commit()


def _psa_path():
    return settings.DATA_DIR / "psa.json"


def get_psa() -> dict | None:
    try:
        return json.loads(_psa_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        logger.warning("psa.json is corrupt, ignoring it")
        return None


def save_psa(d: dict) -> None:
    path = _psa_path()
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def log_activity(
    kind: Literal["info", "action", "error"],
    message: str,
    at: datetime | None = None,
    source: ActivitySource = "automation",
) -> None:
    at = at or datetime.now(timezone.utc)
    with _session() as s:
        s.add(ActivityRow(at=_utc(at), kind=kind, message=message, source=source))
        s.commit()


def recent_activity(limit: int = 50) -> list[Activity]:
    with _session() as s:
        rows = s.exec(select(ActivityRow).order_by(ActivityRow.at.desc(), ActivityRow.id.desc()).limit(limit)).all()
        return [Activity(at=_utc(r.at), kind=r.kind, message=r.message, source=r.source) for r in rows]


def _get_state(key: str, model):
    with _session() as s:
        row = s.get(StateRow, key)
        return model.model_validate_json(row.data) if row else None


def _set_state(key: str, value: BaseModel | None) -> None:
    with _session() as s:
        if value is None:
            row = s.get(StateRow, key)
            if row:
                s.delete(row)
        else:
            s.merge(StateRow(key=key, data=value.model_dump_json()))
        s.commit()


def get_override() -> Override | None:
    return _get_state("override", Override)


def set_override(o: Override | None) -> None:
    _set_state("override", o)


def get_last_command() -> LastCommand | None:
    return _get_state("last_command", LastCommand)


def set_last_command(c: LastCommand | None) -> None:
    _set_state("last_command", c)


def get_tariff() -> Tariff:
    return _get_state("tariff", Tariff) or EDA_TRI_SEMANAL


def save_tariff(t: Tariff) -> None:
    _set_state("tariff", t)


def add_battery_sample(at: datetime, soc: float, charging: bool) -> None:
    with _session() as s:
        s.add(BatterySampleRow(at=_utc(at), soc=soc, charging=charging))
        s.commit()


def battery_samples(since: datetime) -> list[BatterySample]:
    with _session() as s:
        q = select(BatterySampleRow).where(BatterySampleRow.at >= _utc(since)).order_by(BatterySampleRow.at)
        rows = s.exec(q).all()
        return [BatterySample(at=_utc(r.at), soc=r.soc, charging=r.charging) for r in rows]


def add_health_sample(day: date, value: float) -> None:
    """One battery-health sample per (Azores) day: a later value for the same day replaces it."""
    with _session() as s:
        s.merge(HealthSampleRow(day=day, value=value))
        s.commit()


def health_samples() -> list[tuple[date, float]]:
    with _session() as s:
        return [(r.day, r.value) for r in s.exec(select(HealthSampleRow).order_by(HealthSampleRow.day)).all()]


def _place(row: PlaceRow) -> Place:
    return Place(**row.model_dump())


def list_places() -> list[Place]:
    with _session() as s:
        return [_place(r) for r in s.exec(select(PlaceRow).order_by(PlaceRow.id)).all()]


def save_place(p: Place) -> Place:
    """Insert (no id) or update; at most one home and one work place."""
    with _session() as s:
        if p.kind in ("home", "work"):
            clash = s.exec(select(PlaceRow).where(PlaceRow.kind == p.kind, PlaceRow.id != p.id)).first()
            if clash is not None:
                raise ValueError("Já existe um local de casa" if p.kind == "home" else "Já existe um local de trabalho")
        row = PlaceRow(**p.model_dump())
        row = s.merge(row)
        s.commit()
        s.refresh(row)  # commit expires the instance
        return _place(row)


def delete_place(place_id: int) -> bool:
    with _session() as s:
        row = s.get(PlaceRow, place_id)
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def get_position_fix() -> PositionFix | None:
    return _get_state("position_fix", PositionFix)


def set_position_fix(fix: PositionFix | None) -> None:
    _set_state("position_fix", fix)


def get_work_episode() -> WorkEpisode | None:
    return _get_state("work_episode", WorkEpisode)


def set_work_episode(e: WorkEpisode | None) -> None:
    _set_state("work_episode", e)


def _charge_session(row: ChargeSessionRow) -> ChargeSession:
    d = row.model_dump()
    d["started_at"] = _utc(row.started_at)
    d["ended_at"] = _utc(row.ended_at) if row.ended_at else None
    return ChargeSession(**d)


def save_charge_session(c: ChargeSession) -> ChargeSession:
    """Insert (no id) or update."""
    with _session() as s:
        row = s.merge(ChargeSessionRow(**c.model_dump()))
        s.commit()
        s.refresh(row)
        return _charge_session(row)


def open_charge_session() -> ChargeSession | None:
    with _session() as s:
        q = select(ChargeSessionRow).where(ChargeSessionRow.ended_at.is_(None))
        row = s.exec(q.order_by(ChargeSessionRow.started_at.desc())).first()
        return _charge_session(row) if row else None


def charge_sessions(limit: int, offset: int = 0) -> list[ChargeSession]:
    """Newest first."""
    with _session() as s:
        q = select(ChargeSessionRow).order_by(ChargeSessionRow.started_at.desc()).offset(offset).limit(limit)
        return [_charge_session(r) for r in s.exec(q).all()]


def get_charge_session(session_id: int) -> ChargeSession | None:
    with _session() as s:
        row = s.get(ChargeSessionRow, session_id)
        return _charge_session(row) if row else None


def add_charge_sample(session_id: int, at: datetime, soc: float, rate_kmh: float | None) -> None:
    with _session() as s:
        same = select(ChargeSampleRow).where(ChargeSampleRow.session_id == session_id, ChargeSampleRow.at == _utc(at))
        if s.exec(same).first() is not None:
            return  # e.g. the first snapshot after a restart repeats the last stored one
        s.add(ChargeSampleRow(session_id=session_id, at=_utc(at), soc=soc, rate_kmh=rate_kmh))
        s.commit()


def charge_samples(session_id: int) -> list[ChargeSample]:
    with _session() as s:
        q = select(ChargeSampleRow).where(ChargeSampleRow.session_id == session_id).order_by(ChargeSampleRow.at)
        return [ChargeSample(**{**r.model_dump(), "at": _utc(r.at)}) for r in s.exec(q).all()]


def recompute_session_cost(c: ChargeSession) -> ChargeSession:
    """The session with kwh and cost filled in from its soc, samples, the tariff and the place's price."""
    cap = get_config().battery_capacity_kwh
    if c.soc_end is not None:
        c = c.model_copy(update={"kwh": session_kwh(c.soc_start, c.soc_end, cap)})
    price = next((p.price_eur_kwh for p in list_places() if p.name == c.place_name and p.kind == "other"), None)
    samples = charge_samples(c.id) if c.id is not None else []
    return c.model_copy(update={"cost_eur": session_cost(c, samples, get_tariff(), cap, price)})


def set_session_price(session_id: int, eur_kwh: float | None) -> ChargeSession | None:
    """Set (or clear, with None) the session's EUR/kWh override and recompute its cost."""
    with _session() as s:
        row = s.get(ChargeSessionRow, session_id)
        if row is None:
            return None
        row.price_override_eur_kwh = eur_kwh
        s.add(row)
        s.commit()
        s.refresh(row)
        c = _charge_session(row)
    return save_charge_session(recompute_session_cost(c))


def _trip(row: TripRow) -> Trip:
    return Trip(**{**row.model_dump(), "departed_at": _utc(row.departed_at), "arrived_at": _utc(row.arrived_at)})


def save_trip(t: Trip) -> Trip:
    with _session() as s:
        row = s.merge(TripRow(**t.model_dump()))
        s.commit()
        s.refresh(row)
        return _trip(row)


def trips(limit: int, offset: int = 0) -> list[Trip]:
    """Newest first."""
    with _session() as s:
        q = select(TripRow).order_by(TripRow.departed_at.desc()).offset(offset).limit(limit)
        return [_trip(r) for r in s.exec(q).all()]
