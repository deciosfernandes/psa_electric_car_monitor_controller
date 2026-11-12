"""Tariff model: seasons by month, day types with period slots, and dated prices."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, model_validator

PeriodType = Literal["vazio", "cheias", "ponta"]
DayType = Literal["weekday", "saturday", "sunday"]

DAY_TYPES: tuple[DayType, ...] = ("weekday", "saturday", "sunday")
DAY_LABELS = {"weekday": "dias úteis", "saturday": "sábado", "sunday": "domingo"}
MINUTES_PER_DAY = 1440
GRANULARITY = 15


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _span(start: int, end: int) -> str:
    return f"{hhmm(start)}–{hhmm(end)}"


class Slot(BaseModel):
    start: int  # minutes of local day, 0..1440
    end: int
    period: PeriodType


class Season(BaseModel):
    name: str
    months: list[int]
    days: dict[DayType, list[Slot]]


class Price(BaseModel):
    period: PeriodType
    eur_kwh: float
    valid_from: date


def _check_day(label: str, slots: list[Slot]) -> None:
    cursor = 0
    for slot in slots:
        if not (0 <= slot.start < slot.end <= MINUTES_PER_DAY):
            raise ValueError(f"{label}: intervalo inválido {_span(slot.start, slot.end)}")
        if slot.start % GRANULARITY or slot.end % GRANULARITY:
            raise ValueError(f"{label}: os intervalos têm de ser múltiplos de {GRANULARITY} minutos")
        if slot.start < cursor:
            raise ValueError(f"{label}: o intervalo {_span(slot.start, slot.end)} sobrepõe-se ao anterior")
        if slot.start > cursor:
            raise ValueError(f"{label}: falta o intervalo {_span(cursor, slot.start)}")
        cursor = slot.end
    if cursor < MINUTES_PER_DAY:
        raise ValueError(f"{label}: falta o intervalo {_span(cursor, MINUTES_PER_DAY)}")


class Tariff(BaseModel):
    name: str
    seasons: list[Season]
    prices: list[Price] = []

    @model_validator(mode="after")
    def validate_full(self) -> "Tariff":
        owner: dict[int, str] = {}
        for season in self.seasons:
            for month in season.months:
                if not 1 <= month <= 12:
                    raise ValueError(f"{season.name}: mês {month} inválido")
                if month in owner:
                    raise ValueError(f"O mês {month} está em mais de uma estação ({owner[month]} e {season.name})")
                owner[month] = season.name
        for month in range(1, 13):
            if month not in owner:
                raise ValueError(f"O mês {month} não pertence a nenhuma estação")
        for season in self.seasons:
            missing = [DAY_LABELS[d] for d in DAY_TYPES if d not in season.days]
            if missing:
                raise ValueError(f"{season.name}: faltam os dias {', '.join(missing)}")
            for d in DAY_TYPES:
                _check_day(f"{season.name} / {DAY_LABELS[d]}", season.days[d])
        periods = {slot.period for season in self.seasons for slots in season.days.values() for slot in slots}
        if "vazio" not in periods:
            raise ValueError("A tarifa tem de ter pelo menos um intervalo de vazio")  # nothing to charge in
        return self

    def season_for(self, month: int) -> Season:
        for season in self.seasons:
            if month in season.months:
                return season
        raise ValueError(f"O mês {month} não pertence a nenhuma estação")

    def price_at(self, period: PeriodType, on: date) -> float | None:
        valid = [p for p in self.prices if p.period == period and p.valid_from <= on]
        return max(valid, key=lambda p: p.valid_from).eur_kwh if valid else None
