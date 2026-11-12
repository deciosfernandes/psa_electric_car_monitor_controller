"""Tolerant models for the parts of the PSA connected car API (v4.17, see api-b2c-v4.17.2.yaml) we read.

Everything is optional and unknown keys are ignored: the API evolves, and enums may grow, so enum-like
values are kept as plain strings. Real cars also deviate from the spec (an e-208 returns `charging.schedule`
as `[]`), so every field is `Lenient`: a wrong-typed value becomes None instead of rejecting the whole status.
"""
from typing import Annotated, Any, TypeVar

from pydantic import (BaseModel, ConfigDict, Field, ValidationError, ValidatorFunctionWrapHandler, WrapValidator,
                      field_validator)

T = TypeVar("T")


def _none_if_invalid(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    try:
        return handler(value)
    except ValidationError:
        return None


Lenient = Annotated[T | None, WrapValidator(_none_if_invalid)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Charging(_Model):
    plugged: Lenient[bool] = None
    status: Lenient[str] = None  # Disconnected | InProgress | Failure | Stopped | Finished
    remaining_time: Lenient[str] = Field(None, alias="remainingTime")
    charging_rate: Lenient[int] = Field(None, alias="chargingRate")
    charging_power_level: Lenient[str] = Field(None, alias="chargingPowerLevel")
    charging_mode: Lenient[str] = Field(None, alias="chargingMode")
    next_delayed_time: Lenient[str] = Field(None, alias="nextDelayedTime")
    schedule: Any = None  # spec: object with programs; real cars: also []


class _ElectricExtension(_Model):
    charging: Lenient[Charging] = None


class _EnergyExtension(_Model):
    electric: Lenient[_ElectricExtension] = None


class Energy(_Model):
    type: Lenient[str] = None  # Fuel | Electric
    level: Lenient[float] = None
    autonomy: Lenient[float] = None
    updated_at: Lenient[str] = Field(None, alias="updatedAt")
    created_at: Lenient[str] = Field(None, alias="createdAt")
    charging: Lenient[Charging] = None  # legacy (deprecated `energy`) location
    extension: Lenient[_EnergyExtension] = None  # v4.15.1+ location: extension.electric.charging

    def charging_info(self) -> Charging | None:
        ext = self.extension.electric.charging if self.extension and self.extension.electric else None
        return ext or self.charging


class _Geometry(_Model):
    coordinates: Lenient[list[Lenient[float]]] = None


class _Position(_Model):
    geometry: Lenient[_Geometry] = None


class _DoorsState(_Model):
    locked_states: Lenient[list[str]] = Field(None, alias="lockedStates")


class _AirConditioning(_Model):
    status: Lenient[str] = None  # Enabled | Disabled | Finished | Failure


class _Preconditioning(_Model):
    air_conditioning: Lenient[_AirConditioning] = Field(None, alias="airConditioning")


class VehicleStatus(_Model):
    energies: Lenient[list[Lenient[Energy]]] = None
    energy: Lenient[list[Lenient[Energy]]] = None  # deprecated since v4.10.1
    last_position: Lenient[_Position] = Field(None, alias="lastPosition")
    doors_state: Lenient[_DoorsState] = Field(None, alias="doorsState")
    preconditioning: Lenient[_Preconditioning] = None
    preconditionning: Lenient[_Preconditioning] = None  # deprecated spelling
    updated_at: Lenient[str] = Field(None, alias="updatedAt")
    created_at: Lenient[str] = Field(None, alias="createdAt")

    @field_validator("energy", mode="before")
    @classmethod
    def _energy_as_list(cls, value):
        # the spec describes the deprecated `energy` as one object, real cars return a list
        return [value] if isinstance(value, dict) else value

    def electric(self) -> Energy | None:
        for energies in (self.energies, self.energy):
            for energy in energies or []:
                if energy is not None and energy.type == "Electric":
                    return energy
        return None


class VehicleRef(_Model):
    id: str
    vin: str
    brand: str | None = None
    label: str | None = None
