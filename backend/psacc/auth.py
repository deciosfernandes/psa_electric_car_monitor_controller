"""Single-password authentication: scrypt hashing, login rate limiting and the session guard."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from typing import Callable

from fastapi import HTTPException, Request

MIN_PASSWORD_LEN = 8
MAX_FAILURES = 5
FAILURE_WINDOW_S = 5 * 60
SESSION_KEY = "auth"

# scrypt cost: 2**14 * 8 * 128 B = 16 MiB of memory per hash
_N, _R, _P = 2**14, 8, 1
_DKLEN = 32


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=_DKLEN)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${_scrypt(password, salt, _N, _R, _P).hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        computed = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p))
        return hmac.compare_digest(computed, bytes.fromhex(digest))
    except ValueError:
        return False


class LoginRateLimiter:
    """Blocks logins after ``MAX_FAILURES`` failures within ``FAILURE_WINDOW_S`` (single user: one global bucket)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self) -> None:
        cutoff = self._clock() - FAILURE_WINDOW_S
        while self._failures and self._failures[0] <= cutoff:
            self._failures.popleft()

    def blocked(self) -> bool:
        with self._lock:
            self._prune()
            return len(self._failures) >= MAX_FAILURES

    def failure(self) -> None:
        with self._lock:
            self._failures.append(self._clock())

    def success(self) -> None:
        with self._lock:
            self._failures.clear()


def is_logged_in(request: Request) -> bool:
    return request.session.get(SESSION_KEY) is True


def require_login(request: Request) -> None:
    if not is_logged_in(request):
        raise HTTPException(status_code=401, detail="Sessão inválida, inicie sessão")
