from functools import wraps
from threading import Semaphore, Timer

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


class RateLimitException(Exception):
    def __init__(self, func_name):
        super().__init__(f"Rate limit exceeded for {func_name}")


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
