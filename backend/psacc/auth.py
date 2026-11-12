"""Single-password authentication: scrypt hashing, login rate limiting and the session guard."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from typing import Callable

from fastapi import HTTPException, Request

from . import store

MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 1024
MAX_FAILURES = 5
FAILURE_WINDOW_S = 5 * 60
SESSION_KEY = "auth"
EPOCH_KEY = "epoch"

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
    """Allows at most ``MAX_FAILURES`` failed logins within ``FAILURE_WINDOW_S`` (single user: one global bucket).

    An attempt is reserved atomically with ``try_acquire()`` *before* the slow password check, and attempts still
    in flight count as failures, so a burst of parallel guesses can never exceed the limit.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: deque[float] = deque()
        self._in_flight = 0
        self._lock = threading.Lock()

    def _prune(self) -> None:
        cutoff = self._clock() - FAILURE_WINDOW_S
        while self._failures and self._failures[0] <= cutoff:
            self._failures.popleft()

    def try_acquire(self) -> bool:
        """Reserve one attempt; False when the limit is reached. Every True must be followed by ``release``."""
        with self._lock:
            self._prune()
            if len(self._failures) + self._in_flight >= MAX_FAILURES:
                return False
            self._in_flight += 1
            return True

    def release(self, success: bool) -> None:
        with self._lock:
            self._in_flight -= 1
            if success:
                self._failures.clear()
            else:
                self._failures.append(self._clock())


def is_logged_in(request: Request) -> bool:
    return (
        request.session.get(SESSION_KEY) is True and request.session.get(EPOCH_KEY) == store.get_config().session_epoch
    )


def start_session(request: Request, epoch: int) -> None:
    request.session.clear()
    request.session[SESSION_KEY] = True
    request.session[EPOCH_KEY] = epoch


def require_login(request: Request) -> None:
    if not is_logged_in(request):
        raise HTTPException(status_code=401, detail="Sessão inválida, inicie sessão")
