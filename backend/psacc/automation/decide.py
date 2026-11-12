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
    limit: int | None = None  # set on a "defer" that enforces the battery limit (this %): allowed anywhere


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
    """The battery limit is enforced everywhere (owner decision 12/11/2026); the tariff rules apply only at Casa.

    Outside Casa a "defer" is sent only to enforce the limit, and only while the car is (or may be) charging.
    The battery limit applies whenever battery_limit_enabled is True, even with the tariff automation switch off."""
    if car.soc is None or car.plugged is None:
        return Decision(None, "Estado do carro desconhecido")
    if car.plugged is False:
        return Decision(None, "Cabo desligado", clear_override=override is not None)
    limit = cfg.battery_limit
    if cfg.battery_limit_enabled and car.soc >= limit:  # whatever started the charge (tariff, Trabalho, manual, etc.)
        reason = f"Limite de {limit} % atingido"
        if where == "home":  # Plan 1: defer even when not charging (keeps the program hour set)
            if override:
                return Decision("defer", f"{reason} (carga manual terminada)", True, limit=limit)
            return Decision("defer", reason, limit=limit)
        if car.charging is not False:
            return Decision("defer", reason, clear_override=override is not None, limit=limit)
        return Decision(None, reason, clear_override=override is not None)  # already stopped: nothing to send
    if not cfg.automation_enabled:
        if override:
            return Decision("charge", "Carga manual ativa")
        return Decision(None, "Automação desligada")
    if where != "home":  # below the limit no "defer" here; "Carregar agora" keeps working anywhere
        if where == "work" and not work_charge_sent:  # free charging: once per plug-in episode
            return Decision("charge", "Carga no Trabalho")
        if override:
            return Decision("charge", "Carga manual ativa")
        return Decision(None, not_home_reason(where, place_name))
    if override:
        return Decision("charge", "Carga manual ativa")
    if desired_mode(tariff, cfg.offset_min, now) == "charge":
        return Decision("charge", "Período de vazio")
    return Decision("defer", f"Fora do vazio ({period_at(tariff, now)})")
