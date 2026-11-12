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


def car(soc=50.0, plugged=True, charging=None):
    return CarSnapshot(
        vin="V", at=datetime(2026, 11, 12, tzinfo=UTC), soc=soc, range_km=None, plugged=plugged, charging=charging,
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
        "defer", "Limite de 80 % atingido (carga manual terminada)", True, limit=80
    )


def test_override_charges_in_ponta():
    assert run(at(19), override=OVR) == Decision("charge", "Carga manual ativa")


def test_limit_defers_in_vazio():
    assert run(at(1), car(soc=80)) == Decision("defer", "Limite de 80 % atingido", limit=80)
    # at Casa the limit defers even when the car is not charging (keeps the program set), as in Plan 1
    assert run(at(1), car(soc=80, charging=False)) == Decision("defer", "Limite de 80 % atingido", limit=80)


def test_vazio_charges():
    assert run(at(1)) == Decision("charge", "Período de vazio")


def test_cheias_defers():
    d = run(at(12))
    assert d.mode == "defer" and "cheias" in d.reason


def run_at(where, now=None, c=None, override=None, work_charge_sent=False, cfg=CFG):
    return decide(now or at(19), cfg, EDA_TRI_SEMANAL, c or car(), override, where, work_charge_sent)


LIMIT_DEFER = Decision("defer", "Limite de 80 % atingido", limit=80)


def test_decide_no_home_place():
    assert run_at("unset", at(1)) == Decision(None, "Defina o local Casa")


def test_decide_unknown_position():
    assert run_at("unknown", at(1)) == Decision(None, "Posição do carro desconhecida")


def test_decide_work_charge_once():
    assert run_at("work") == Decision("charge", "Carga no Trabalho")
    # tariff ignored (work charging is free), but only below the battery limit
    assert run_at("work", at(12), car(soc=70)) == Decision("charge", "Carga no Trabalho")
    assert run_at("work", work_charge_sent=True) == Decision(None, "No Trabalho (carga já pedida)")
    assert run_at("work", override=OVR) == Decision("charge", "Carga no Trabalho")


def test_decide_other_place_no_command():
    assert run_at("other", at(1)) == Decision(None, "Fora de Casa (local desconhecido)")
    # outside Casa no tariff defer: in ponta below the limit nothing is sent
    assert run_at("other", at(19), car(soc=79, charging=True)) == Decision(None, "Fora de Casa (local desconhecido)")


def test_decide_limit_enforced_everywhere_while_charging():
    for where in ("work", "other", "unknown", "unset"):
        for charging in (True, None):  # None: may be charging
            c = car(soc=80, charging=charging)
            assert run_at(where, at(12), c) == LIMIT_DEFER, (where, charging)
            assert run_at(where, at(12), c, work_charge_sent=True) == LIMIT_DEFER, (where, charging)


def test_decide_limit_outside_home_not_charging_sends_nothing():
    for where in ("work", "other", "unknown", "unset"):
        assert run_at(where, c=car(soc=85, charging=False)) == Decision(None, "Limite de 80 % atingido"), where
        assert run_at(where, c=car(soc=85, charging=False), override=OVR) == Decision(
            None, "Limite de 80 % atingido", True
        ), where


def test_decide_limit_outside_home_ends_manual_override():
    for where in ("work", "other", "unknown", "unset"):
        d = run_at(where, c=car(soc=80, charging=True), override=OVR, work_charge_sent=True)
        assert d == Decision("defer", "Limite de 80 % atingido", True, limit=80), where


def test_decide_limit_enforced_even_when_automation_off():
    off = AppConfig(automation_enabled=False)
    # limit applies everywhere while charging, even with the tariff automation switch off
    for where in ("home", "work", "other", "unknown", "unset"):
        assert run_at(where, c=car(soc=95, charging=True), cfg=off) == LIMIT_DEFER, where
    # below the limit, the automation switch being off results in no action
    for where in ("home", "work", "other"):
        assert run_at(where, c=car(soc=70, charging=True), cfg=off) == Decision(None, "Automação desligada"), where


def test_decide_limit_disabled_when_limit_switch_off():
    no_limit = AppConfig(battery_limit_enabled=False)
    # at Casa in vazio it charges up to 100%
    assert run(at(1), car(soc=95), cfg=no_limit) == Decision("charge", "Período de vazio")
    # at Casa in ponta it defers for tariff, not for battery limit (limit is None)
    d = run(at(19), car(soc=95), cfg=no_limit)
    assert d.mode == "defer" and d.limit is None and "ponta" in d.reason
    # at work work charge is sent even above 80%
    assert run_at("work", c=car(soc=95), cfg=no_limit) == Decision("charge", "Carga no Trabalho")
    # when both switches are off, nothing is enforced
    both_off = AppConfig(automation_enabled=False, battery_limit_enabled=False)
    for where in ("home", "work", "other"):
        assert run_at(where, c=car(soc=95, charging=True), cfg=both_off) == Decision(None, "Automação desligada"), where


def test_decide_other_place_manual_override_charges():
    assert run_at("other", override=OVR) == Decision("charge", "Carga manual ativa")


def test_decide_location_checked_after_unplugged_and_unknown_state():
    assert run_at("work", c=car(plugged=False), override=OVR) == Decision(None, "Cabo desligado", True)
    assert run_at("unset", c=car(soc=None)) == Decision(None, "Estado do carro desconhecido")


def test_decide_manual_override_charges_everywhere():
    for where in ("unset", "unknown", "other"):
        assert run_at(where, at(12), car(soc=75), override=OVR) == Decision("charge", "Carga manual ativa"), where
    assert run_at("work", override=OVR, work_charge_sent=True) == Decision("charge", "Carga manual ativa")
    assert run_at("unknown", c=car(plugged=False), override=OVR) == Decision(None, "Cabo desligado", True)


def test_decide_other_place_reason_includes_name():
    d = decide(at(1), CFG, EDA_TRI_SEMANAL, car(), None, "other", False, "Ginásio")
    assert d == Decision(None, "Fora de Casa (Ginásio)")
