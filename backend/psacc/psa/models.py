"""Tolerant models for the parts of the PSA connected car API (v4.17, see api-b2c-v4.17.2.yaml) we read.

Everything is optional and unknown keys are ignored: the API evolves, and enums may grow, so enum-like
values are kept as plain strings.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Charging(_Model):
    plugged: bool | None = None
    status: str | None = None  # Disconnected | InProgress | Failure | Stopped | Finished
    remaining_time: str | None = Field(None, alias="remainingTime")
    charging_rate: int | None = Field(None, alias="chargingRate")
    charging_power_level: str | None = Field(None, alias="chargingPowerLevel")
    charging_mode: str | None = Field(None, alias="chargingMode")
    next_delayed_time: str | None = Field(None, alias="nextDelayedTime")
    schedule: dict | None = None


class _ElectricExtension(_Model):
    charging: Charging | None = None


class _EnergyExtension(_Model):
    electric: _ElectricExtension | None = None


class Energy(_Model):
    type: str | None = None  # Fuel | Electric
    level: float | None = None
    autonomy: float | None = None
    updated_at: str | None = Field(None, alias="updatedAt")
    created_at: str | None = Field(None, alias="createdAt")
    charging: Charging | None = None  # legacy (deprecated `energy`) location
    extension: _EnergyExtension | None = None  # v4.15.1+ location: extension.electric.charging

    def charging_info(self) -> Charging | None:
        ext = self.extension.electric.charging if self.extension and self.extension.electric else None
        return ext or self.charging


class _Geometry(_Model):
    coordinates: list[float | None] | None = None


class _Position(_Model):
    geometry: _Geometry | None = None


class _DoorsState(_Model):
    locked_states: list[str] | None = Field(None, alias="lockedStates")


class _AirConditioning(_Model):
    status: str | None = None  # Enabled | Disabled | Finished | Failure


class _Preconditioning(_Model):
    air_conditioning: _AirConditioning | None = Field(None, alias="airConditioning")


class VehicleStatus(_Model):
    energies: list[Energy] | None = None
    energy: list[Energy] | None = None  # deprecated since v4.10.1
    last_position: _Position | None = Field(None, alias="lastPosition")
    doors_state: _DoorsState | None = Field(None, alias="doorsState")
    preconditioning: _Preconditioning | None = None
    preconditionning: _Preconditioning | None = None  # deprecated spelling
    updated_at: str | None = Field(None, alias="updatedAt")
    created_at: str | None = Field(None, alias="createdAt")

    @field_validator("energy", mode="before")
    @classmethod
    def _energy_as_list(cls, value):
        # the spec describes the deprecated `energy` as one object, real cars return a list
        return [value] if isinstance(value, dict) else value

    def electric(self) -> Energy | None:
        for energies in (self.energies, self.energy):
            for energy in energies or []:
                if energy.type == "Electric":
                    return energy
        return None


class VehicleRef(_Model):
    id: str
    vin: str
    brand: str | None = None
    label: str | None = None
