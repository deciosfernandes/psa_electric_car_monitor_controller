"""Cost of a charge session: home follows the tariff period of each part of the charge."""

from ..settings import TZ
from ..tariff.model import Tariff
from ..tariff.schedule import period_at
from .charges import ChargeSample, ChargeSession, session_kwh


def _home_cost(session: ChargeSession, samples: list[ChargeSample], tariff: Tariff, capacity: float) -> float | None:
    if len(samples) >= 2:
        parts = [(a.at + (b.at - a.at) / 2, session_kwh(a.soc, b.soc, capacity)) for a, b in zip(samples, samples[1:])]
    else:  # no curve to split: the whole charge at the period it started in
        parts = [(session.started_at, session.kwh or 0.0)]
    raw = sum(kwh for _, kwh in parts)
    if raw == 0 and session.kwh:  # no rise visible in the samples: price the whole charge at its start period
        parts = [(session.started_at, session.kwh)]
    elif raw > 0 and session.kwh is not None:  # SoC dips must not inflate the cost beyond the session's kWh
        parts = [(mid, kwh * session.kwh / raw) for mid, kwh in parts]
    total = 0.0
    for mid, kwh in parts:
        price = tariff.price_at(period_at(tariff, mid), mid.astimezone(TZ).date())
        if price is None:
            return None
        total += kwh * price
    return total


def session_cost(
    session: ChargeSession,
    samples: list[ChargeSample],
    tariff: Tariff,
    capacity: float,
    place_price: float | None = None,
) -> float | None:
    """EUR for the session, or None when the price is unknown. ``place_price`` is the place's EUR/kWh (other)."""
    kwh = session.kwh
    if kwh is None and session.soc_end is not None:
        kwh = session_kwh(session.soc_start, session.soc_end, capacity)
    if session.price_override_eur_kwh is not None:
        return None if kwh is None else kwh * session.price_override_eur_kwh
    if session.place_kind == "work":
        return 0.0
    if kwh is None:
        return None
    if session.place_kind == "home":
        return _home_cost(session.model_copy(update={"kwh": kwh}), samples, tariff, capacity)
    if session.place_kind == "other" and place_price is not None:
        return kwh * place_price
    return None
