import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Field as SQLField
from sqlmodel import Session, SQLModel, create_engine, select

from . import settings
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


class PlaceRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    name: str
    kind: str
    lat: float
    lon: float
    radius_m: int = 150
    price_eur_kwh: float | None = None


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
