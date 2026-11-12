from datetime import UTC, date, datetime

import pytest

from psacc import settings, store
from psacc.history.charges import ChargeSample, ChargeSession
from psacc.history.cost import session_cost
from psacc.settings import TZ
from psacc.tariff.model import Price
from psacc.tariff.presets import EDA_TRI_SEMANAL

TARIFF = EDA_TRI_SEMANAL.model_copy(
    update={
        "prices": [
            Price(period="vazio", eur_kwh=0.10, valid_from=date(2026, 1, 1)),
            Price(period="cheias", eur_kwh=0.20, valid_from=date(2026, 1, 1)),
        ]
    }
)


def local(h, m):  # Thursday 12/11/2026, winter weekday
    return datetime(2026, 11, 12, h, m, tzinfo=TZ).astimezone(UTC)


def make(kind="home", **kw):
    base = dict(started_at=local(0, 10), ended_at=local(7, 30), place_kind=kind, soc_start=20, soc_end=70, kwh=23.0)
    return ChargeSession(**{**base, **kw})


SAMPLES = [
    ChargeSample(session_id=1, at=local(0, 10), soc=20, rate_kmh=30),
    ChargeSample(session_id=1, at=local(7, 0), soc=60, rate_kmh=30),
    ChargeSample(session_id=1, at=local(7, 30), soc=70, rate_kmh=30),
]


def test_home_cost_split_by_period():
    # 40 % (18.4 kWh) at vazio 0.10 + 10 % (4.6 kWh) at cheias 0.20
    assert session_cost(make(), SAMPLES, TARIFF, 46) == pytest.approx(18.4 * 0.10 + 4.6 * 0.20)


def test_home_without_price_is_none():
    assert session_cost(make(), SAMPLES, EDA_TRI_SEMANAL, 46) is None


def test_work_cost_zero():
    assert session_cost(make("work"), SAMPLES, TARIFF, 46) == 0.0


def test_other_no_price_is_none():
    assert session_cost(make("other"), SAMPLES, TARIFF, 46) is None
    assert session_cost(make("other"), SAMPLES, TARIFF, 46, place_price=0.3) == pytest.approx(23 * 0.3)


def test_price_override():
    other = make("other", price_override_eur_kwh=0.5)
    assert session_cost(other, SAMPLES, TARIFF, 46, place_price=0.3) == pytest.approx(11.5)
    assert session_cost(make("work", price_override_eur_kwh=0.5), SAMPLES, TARIFF, 46) == pytest.approx(11.5)


def test_unknown_place_is_none():
    assert session_cost(make("unknown"), SAMPLES, TARIFF, 46) is None


def test_store_roundtrip_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    store.init_db("sqlite://")
    s = store.save_charge_session(make("other", place_name="Posto"))
    assert s.id is not None
    for sm in SAMPLES:
        store.add_charge_sample(s.id, sm.at, sm.soc, sm.rate_kmh)
    assert [x.soc for x in store.charge_samples(s.id)] == [20, 60, 70]
    assert [x.id for x in store.charge_sessions(10, 0)] == [s.id]
    out = store.set_session_price(s.id, 0.4)
    assert out.price_override_eur_kwh == 0.4 and out.cost_eur == pytest.approx(9.2)
    assert store.charge_sessions(10, 0)[0].cost_eur == pytest.approx(9.2)
    assert store.set_session_price(999, 0.4) is None


def test_closing_sample_makes_cost_cover_session_kwh():
    from psacc.history.charges import session_kwh

    samples = SAMPLES + [ChargeSample(session_id=1, at=local(8, 0), soc=75, rate_kmh=0)]
    s = make(soc_end=75, kwh=session_kwh(20, 75, 46), ended_at=local(8, 0))
    # 18.4 kWh vazio + (4.6 + 2.3) kWh cheias
    assert session_cost(s, samples, TARIFF, 46) == pytest.approx(18.4 * 0.10 + 6.9 * 0.20)
