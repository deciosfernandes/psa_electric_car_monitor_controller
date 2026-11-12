"""Pure charge-automation policy: given the current state, what should the car be told to do?"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..gateway import CarSnapshot
from ..places import Where
from ..store import AppConfig, Override
from ..tariff.model import Tariff
from ..tariff.schedule import desired_mode, period_at


_NOT_HOME = {
    "unset": "Defina o local Casa",
    "unknown": "Posição do carro desconhecida",
    "work": "No Trabalho (carga já pedida)",
    "other": "Fora de Casa",
}


def not_home_reason(where: Where, place_name: str | None = None) -> str:
    """Why the automation is paused when the car is not at Casa (pt-PT)."""
    if where == "other":
        return f"Fora de Casa ({place_name or 'local desconhecido'})"
    return _NOT_HOME[where]


@dataclass(frozen=True)
class Decision:
    mode: Literal["charge", "defer"] | None  # None = do nothing
    reason: str  # pt-PT, shown in the activity log
    clear_override: bool = False


def decide(
    now: datetime,
    cfg: AppConfig,
    tariff: Tariff,
    car: CarSnapshot,
    override: Override | None,
    where: Where = "home",
    work_charge_sent: bool = False,
    place_name: str | None = None,
) -> Decision:
    """Plan 1 tariff rules apply only at Casa; never "defer" anywhere else."""
    if not cfg.automation_enabled:
        return Decision(None, "Automação desligada")
    if car.soc is None or car.plugged is None:
        return Decision(None, "Estado do carro desconhecido")
    if car.plugged is False:
        return Decision(None, "Cabo desligado", clear_override=override is not None)
    if where != "home":  # never "defer" here; "Carregar agora" keeps working anywhere
        if where == "work" and not work_charge_sent:  # free charging: once per plug-in episode
            return Decision("charge", "Carga no Trabalho")
        if override:
            return Decision("charge", "Carga manual ativa")
        return Decision(None, not_home_reason(where, place_name))
    at_limit = car.soc >= cfg.battery_limit
    if override and at_limit:
        return Decision("defer", f"Limite de {cfg.battery_limit} % atingido (carga manual terminada)", True)
    if override:
        return Decision("charge", "Carga manual ativa")
    if at_limit:
        return Decision("defer", f"Limite de {cfg.battery_limit} % atingido")
    if desired_mode(tariff, cfg.offset_min, now) == "charge":
        return Decision("charge", "Período de vazio")
    return Decision("defer", f"Fora do vazio ({period_at(tariff, now)})")
