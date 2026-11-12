import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import store
from ..auth import MIN_PASSWORD_LEN, SESSION_KEY, LoginRateLimiter, hash_password, is_logged_in, verify_password

router = APIRouter(prefix="/api/auth")

_setup_lock = threading.Lock()  # two concurrent first-run setups must not both set a password


class PasswordIn(BaseModel):
    password: str


@router.get("/state")
def auth_state(request: Request) -> dict:
    return {"configured": store.get_config().password_hash is not None, "logged_in": is_logged_in(request)}


@router.post("/setup")
def setup_password(body: PasswordIn, request: Request) -> dict:
    if len(body.password) < MIN_PASSWORD_LEN:
        raise HTTPException(422, f"A palavra-passe tem de ter pelo menos {MIN_PASSWORD_LEN} caracteres")
    with _setup_lock:
        cfg = store.get_config()
        if cfg.password_hash is not None:
            raise HTTPException(409, "A palavra-passe já está definida")
        cfg.password_hash = hash_password(body.password)
        store.save_config(cfg)
    request.session[SESSION_KEY] = True
    return {"ok": True}


@router.post("/login")
def login(body: PasswordIn, request: Request) -> dict:
    limiter: LoginRateLimiter = request.app.state.login_limiter
    if limiter.blocked():
        raise HTTPException(429, "Demasiadas tentativas falhadas. Tente novamente dentro de 5 minutos.")
    stored = store.get_config().password_hash
    if stored is None:
        raise HTTPException(409, "A palavra-passe ainda não foi definida")
    if not verify_password(body.password, stored):
        limiter.failure()
        raise HTTPException(401, "Palavra-passe incorreta")
    limiter.success()
    request.session.clear()
    request.session[SESSION_KEY] = True
    return {"ok": True}


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}
