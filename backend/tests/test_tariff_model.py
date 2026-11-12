from datetime import date

import pytest
from pydantic import ValidationError

from psacc.tariff.model import Price, Season, Slot, Tariff
from psacc.tariff.presets import EDA_TRI_SEMANAL


def minutes(tariff: Tariff, season: str, day: str) -> dict[str, int]:
    s = next(x for x in tariff.seasons if x.name == season)
    out: dict[str, int] = {}
    for slot in s.days[day]:
        out[slot.period] = out.get(slot.period, 0) + slot.end - slot.start
    return out


def day(*slots):
    return [Slot(start=a, end=b, period=p) for a, b, p in slots]


def full_days(weekday):
    flat = day((0, 1440, "vazio"))
    return {"weekday": weekday, "saturday": flat, "sunday": flat}


def make(seasons):
    return Tariff(name="t", seasons=seasons)


def test_eda_preset_valid():
    Tariff.model_validate(EDA_TRI_SEMANAL.model_dump())
    assert EDA_TRI_SEMANAL.name == "EDA tri-horário semanal"
    assert EDA_TRI_SEMANAL.prices == []
    assert EDA_TRI_SEMANAL.season_for(11).name == "Inverno"
    assert EDA_TRI_SEMANAL.season_for(6).name == "Verão"


def test_eda_hour_totals():
    assert minutes(EDA_TRI_SEMANAL, "Inverno", "weekday") == {"vazio": 420, "cheias": 840, "ponta": 180}
    assert minutes(EDA_TRI_SEMANAL, "Verão", "weekday") == {"vazio": 420, "cheias": 720, "ponta": 300}
    for s in ("Inverno", "Verão"):
        assert minutes(EDA_TRI_SEMANAL, s, "saturday") == {"vazio": 1020, "cheias": 420}
        assert minutes(EDA_TRI_SEMANAL, s, "sunday") == {"vazio": 1440}


def test_rejects_gap():
    wk = day((0, 420, "vazio"), (420, 1440, "cheias"))
    sat = day((0, 690, "vazio"), (810, 1440, "vazio"))
    flat = day((0, 1440, "vazio"))
    with pytest.raises(ValidationError, match="Inverno / sábado: falta o intervalo 11:30–13:30"):
        make([Season(name="Inverno", months=list(range(1, 13)), days={"weekday": wk, "saturday": sat, "sunday": flat})])


def test_rejects_overlap():
    wk = day((0, 600, "vazio"), (540, 1440, "cheias"))
    with pytest.raises(ValidationError, match="sobrepõe"):
        make([Season(name="Inverno", months=list(range(1, 13)), days=full_days(wk))])


def test_rejects_month_twice():
    a = Season(name="A", months=[1, 2, 3, 4, 5, 6, 7], days=full_days(day((0, 1440, "vazio"))))
    b = Season(name="B", months=[7, 8, 9, 10, 11, 12], days=full_days(day((0, 1440, "vazio"))))
    with pytest.raises(ValidationError, match="mês 7"):
        make([a, b])


def test_rejects_month_missing():
    a = Season(name="A", months=list(range(1, 12)), days=full_days(day((0, 1440, "vazio"))))
    with pytest.raises(ValidationError, match="mês 12"):
        make([a])


def test_rejects_non_15_min():
    wk = day((0, 425, "vazio"), (425, 1440, "cheias"))
    with pytest.raises(ValidationError, match="15"):
        make([Season(name="Inverno", months=list(range(1, 13)), days=full_days(wk))])


def test_rejects_missing_day_type():
    s = Season(name="Inverno", months=list(range(1, 13)), days={"weekday": day((0, 1440, "vazio"))})
    with pytest.raises(ValidationError, match="Inverno: faltam os dias"):
        make([s])


def test_price_at_picks_latest_valid_from():
    t = EDA_TRI_SEMANAL.model_copy(
        update={
            "prices": [
                Price(period="vazio", eur_kwh=0.10, valid_from=date(2025, 1, 1)),
                Price(period="vazio", eur_kwh=0.12, valid_from=date(2026, 1, 1)),
                Price(period="cheias", eur_kwh=0.20, valid_from=date(2025, 1, 1)),
            ]
        }
    )
    assert t.price_at("vazio", date(2025, 6, 1)) == 0.10
    assert t.price_at("vazio", date(2026, 1, 1)) == 0.12
    assert t.price_at("vazio", date(2024, 12, 31)) is None
    assert t.price_at("ponta", date(2026, 1, 1)) is None
