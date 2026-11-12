"""Pure charge-automation policy: given the current state, what should the car be told to do?"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..gateway import CarSnapshot
from ..places import Where
from ..store import AppConfig, Override
from ..tariff.model import Tariff
from ..tariff.schedule import desired_mode, period_at


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
) -> Decision:
    """Plan 1 tariff rules apply only at Casa; never "defer" anywhere else."""
    if not cfg.automation_enabled:
        return Decision(None, "Automação desligada")
    if car.soc is None or car.plugged is None:
        return Decision(None, "Estado do carro desconhecido")
    if car.plugged is False:
        return Decision(None, "Cabo desligado", clear_override=override is not None)
    if where == "unset":
        return Decision(None, "Defina o local Casa")
    if where == "unknown":
        return Decision(None, "Posição do carro desconhecida")
    if where == "work":  # free charging: once per plug-in episode, whatever the override, limit or tariff
        return (
            Decision(None, "No Trabalho (carga já pedida)")
            if work_charge_sent
            else Decision("charge", "Carga no Trabalho")
        )
    if where == "other":
        return Decision("charge", "Carga manual ativa") if override else Decision(None, "Fora de Casa")
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
