from datetime import UTC, datetime

from psacc.automation.decide import Decision, decide
from psacc.gateway import CarSnapshot
from psacc.settings import TZ
from psacc.store import AppConfig, Override
from psacc.tariff.presets import EDA_TRI_SEMANAL

CFG = AppConfig()
OVR = Override(started_at=datetime(2026, 11, 12, 18, 0, tzinfo=UTC), reason="manual")


def at(h, mi=0):
    return datetime(2026, 11, 12, h, mi, tzinfo=TZ)


def car(soc=50.0, plugged=True):
    return CarSnapshot(
        vin="V", at=datetime(2026, 11, 12, tzinfo=UTC), soc=soc, range_km=None, plugged=plugged, charging=None,
        charge_status=None, charge_rate_kmh=None, next_delayed_time=None, latitude=None, longitude=None,
        locked=None, precond_on=None,
    )  # fmt: skip


def run(now, c=None, cfg=CFG, override=None):
    return decide(now, cfg, EDA_TRI_SEMANAL, c or car(), override)


def test_disabled():
    assert run(at(1), cfg=AppConfig(automation_enabled=False)) == Decision(None, "Automação desligada")


def test_decide_returns_none_on_unknown_soc():
    assert run(at(1), car(soc=None)) == Decision(None, "Estado do carro desconhecido")
    assert run(at(1), car(plugged=None)).mode is None


def test_unplugged_clears_override():
    assert run(at(1), car(plugged=False), override=OVR) == Decision(None, "Cabo desligado", True)
    assert run(at(1), car(plugged=False)).clear_override is False


def test_override_until_limit():
    assert run(at(19), car(soc=80), override=OVR) == Decision(
        "defer", "Limite de 80 % atingido (carga manual terminada)", True
    )


def test_override_charges_in_ponta():
    assert run(at(19), override=OVR) == Decision("charge", "Carga manual ativa")


def test_limit_defers_in_vazio():
    assert run(at(1), car(soc=80)) == Decision("defer", "Limite de 80 % atingido")


def test_vazio_charges():
    assert run(at(1)) == Decision("charge", "Período de vazio")


def test_cheias_defers():
    d = run(at(12))
    assert d.mode == "defer" and "cheias" in d.reason
