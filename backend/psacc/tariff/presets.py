from .model import Season, Slot, Tariff


def _day(*slots: tuple[str, str, str]) -> list[Slot]:
    def minutes(t: str) -> int:
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    return [Slot(start=minutes(a), end=minutes(b), period=p) for a, b, p in slots]  # type: ignore[arg-type]


_SUNDAY = _day(("00:00", "24:00", "vazio"))

EDA_TRI_SEMANAL = Tariff(
    name="EDA tri-horário semanal",
    seasons=[
        Season(
            name="Inverno",
            months=[11, 12, 1, 2, 3, 4, 5],
            days={
                "weekday": _day(
                    ("00:00", "07:00", "vazio"),
                    ("07:00", "18:30", "cheias"),
                    ("18:30", "21:30", "ponta"),
                    ("21:30", "24:00", "cheias"),
                ),
                "saturday": _day(
                    ("00:00", "11:30", "vazio"),
                    ("11:30", "13:30", "cheias"),
                    ("13:30", "18:00", "vazio"),
                    ("18:00", "23:00", "cheias"),
                    ("23:00", "24:00", "vazio"),
                ),
                "sunday": _SUNDAY,
            },
        ),
        Season(
            name="Verão",
            months=[6, 7, 8, 9, 10],
            days={
                "weekday": _day(
                    ("00:00", "07:00", "vazio"),
                    ("07:00", "10:30", "cheias"),
                    ("10:30", "15:30", "ponta"),
                    ("15:30", "24:00", "cheias"),
                ),
                "saturday": _day(
                    ("00:00", "11:00", "vazio"),
                    ("11:00", "14:30", "cheias"),
                    ("14:30", "19:30", "vazio"),
                    ("19:30", "23:00", "cheias"),
                    ("23:00", "24:00", "vazio"),
                ),
                "sunday": _SUNDAY,
            },
        ),
    ],
)
