from functools import wraps
from threading import Semaphore, Timer
from typing import List

TIMEOUT_IN_S = 10


def rate_limit(limit, every):
    def limit_decorator(func):
        semaphore = Semaphore(limit)

        @wraps(func)
        def wrapper(*args, **kwargs):
            if semaphore.acquire(blocking=False):  # pylint: disable=consider-using-with
                try:
                    return func(*args, **kwargs)
                finally:  # don't catch but ensure semaphore release
                    timer = Timer(every, semaphore.release)
                    timer.daemon = True
                    timer.start()
            else:
                raise RateLimitException(func.__name__)

        return wrapper

    return limit_decorator


def parse_hour(s):
    s = s[2:]
    separators = ("H", "M", "S")
    res: List[int] = []
    for sep in separators:
        if sep in s:
            n, s = s.split(sep)
        else:
            n = 0
        res.append(int(n))
        if s.isnumeric():
            res.append(int(s))
            break
    if len(res) == 2:
        res.append(0)
    return res


class RateLimitException(Exception):
    def __init__(self, func_name):
        super().__init__(f"Rate limit exceeded for {func_name}")


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
