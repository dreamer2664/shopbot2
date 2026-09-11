"""Tiny retry helper with exponential backoff. Sleep is injectable for tests."""
from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from ..providers.base import PermanentError, TransientError

T = TypeVar("T")


def retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    jitter: bool = True,
) -> T:
    """Run fn, retrying on TransientError only. PermanentError raises immediately.

    Returns fn's result. Raises the last TransientError if attempts run out.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            delay = base_delay * (2 ** i)
            if jitter:
                delay *= 0.5 + random.random()
            sleep(delay)
    assert last is not None
    raise last
