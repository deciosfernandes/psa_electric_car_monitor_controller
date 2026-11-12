import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.pool import StaticPool
from sqlmodel import Field as SQLField
from sqlmodel import Session, SQLModel, create_engine, select

from . import settings
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


class Activity(BaseModel):
    at: datetime
    kind: Literal["info", "action", "error"]
    message: str


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


class StateRow(SQLModel, table=True):
    key: str = SQLField(primary_key=True)
    data: str


class BatterySampleRow(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    at: datetime = SQLField(index=True)
    soc: float
    charging: bool


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


def _session() -> Session:
    if _engine is None:
        init_db()
    return Session(_engine)


def get_config() -> AppConfig:
    with _session() as s:
        row = s.get(ConfigRow, 1)
        if row:
            return AppConfig.model_validate_json(row.data)
    cfg = AppConfig()
    save_config(cfg)
    return cfg


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


def log_activity(kind: Literal["info", "action", "error"], message: str, at: datetime | None = None) -> None:
    at = at or datetime.now(timezone.utc)
    with _session() as s:
        s.add(ActivityRow(at=_utc(at), kind=kind, message=message))
        s.commit()


def recent_activity(limit: int = 50) -> list[Activity]:
    with _session() as s:
        rows = s.exec(select(ActivityRow).order_by(ActivityRow.at.desc(), ActivityRow.id.desc()).limit(limit)).all()
        return [Activity(at=_utc(r.at), kind=r.kind, message=r.message) for r in rows]


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
