from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from .. import store
from ..places import Place
from . import to_json, unprocessable

router = APIRouter(prefix="/api/places")


class FromCar(BaseModel):
    name: str
    kind: Literal["home", "work", "other"]


def _save(place: Place) -> dict:
    try:
        return to_json(store.save_place(place))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("")
def list_places() -> list:
    return to_json(store.list_places())


@router.post("")
def create_place(body: Place) -> dict:
    return _save(body.model_copy(update={"id": None}))


@router.put("/{place_id}")
def update_place(place_id: int, body: Place) -> dict:
    if not any(p.id == place_id for p in store.list_places()):
        raise HTTPException(status_code=404, detail="Local não encontrado")
    return _save(body.model_copy(update={"id": place_id}))


@router.delete("/{place_id}")
def delete_place(place_id: int) -> dict:
    if not store.delete_place(place_id):
        raise HTTPException(status_code=404, detail="Local não encontrado")
    return {"ok": True}


@router.post("/from-car")
def place_from_car(body: FromCar) -> dict:
    fix = store.get_position_fix()
    if fix is None:
        raise HTTPException(status_code=409, detail="Posição do carro desconhecida")
    try:
        place = Place(name=body.name, kind=body.kind, lat=fix.lat, lon=fix.lon)
    except ValidationError as exc:
        raise unprocessable(exc) from None
    return _save(place)
