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


def run_at(where, now=None, c=None, override=None, work_charge_sent=False):
    return decide(now or at(19), CFG, EDA_TRI_SEMANAL, c or car(), override, where, work_charge_sent)


def test_decide_no_home_place():
    assert run_at("unset", at(1)) == Decision(None, "Defina o local Casa")


def test_decide_unknown_position():
    assert run_at("unknown", at(1)) == Decision(None, "Posição do carro desconhecida")


def test_decide_work_charge_once():
    assert run_at("work") == Decision("charge", "Carga no Trabalho")
    # battery limit and tariff ignored: work charging is free
    assert run_at("work", at(12), car(soc=95)) == Decision("charge", "Carga no Trabalho")
    assert run_at("work", work_charge_sent=True) == Decision(None, "No Trabalho (carga já pedida)")
    assert run_at("work", override=OVR) == Decision("charge", "Carga no Trabalho")


def test_decide_other_place_no_command():
    assert run_at("other", at(1)) == Decision(None, "Fora de Casa (local desconhecido)")
    # never defer outside Casa
    assert run_at("other", at(1), car(soc=90)) == Decision(None, "Fora de Casa (local desconhecido)")


def test_decide_other_place_manual_override_charges():
    assert run_at("other", override=OVR) == Decision("charge", "Carga manual ativa")


def test_decide_location_checked_after_unplugged_and_unknown_state():
    assert run_at("work", c=car(plugged=False), override=OVR) == Decision(None, "Cabo desligado", True)
    assert run_at("unset", c=car(soc=None)) == Decision(None, "Estado do carro desconhecido")


def test_decide_manual_override_charges_everywhere():
    for where in ("unset", "unknown", "other"):
        assert run_at(where, at(12), car(soc=95), override=OVR) == Decision("charge", "Carga manual ativa"), where
    assert run_at("work", override=OVR, work_charge_sent=True) == Decision("charge", "Carga manual ativa")
    assert run_at("unknown", c=car(plugged=False), override=OVR) == Decision(None, "Cabo desligado", True)


def test_decide_other_place_reason_includes_name():
    d = decide(at(1), CFG, EDA_TRI_SEMANAL, car(), None, "other", False, "Ginásio")
    assert d == Decision(None, "Fora de Casa (Ginásio)")
