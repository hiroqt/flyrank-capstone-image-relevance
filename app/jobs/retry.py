"""Retry policy and client-side rate limiting for AI calls."""
import random
import threading
import time
from collections.abc import Callable


def backoff_seconds(attempt: int, base: float, cap: float, *, retry_after: float | None = None,
                    rand: Callable[[], float] = random.random) -> float:
    """Exponential backoff with jitter: attempt 1 -> ~base, 2 -> ~2*base, ... capped.

    Jitter spreads retries so a burst of failures doesn't retry in lockstep. A provider's
    retry-after hint (429) is honoured as a floor.
    """
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    delay = ceiling / 2 + rand() * ceiling / 2
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


class RateLimiter:
    """Spaces calls to at most `rpm` per minute (free-tier quotas). rpm <= 0 disables it."""

    def __init__(self, rpm: int, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.interval == 0:
            return
        with self._lock:
            now = self._clock()
            wait = self._next_slot - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
            self._next_slot = max(now, self._next_slot) + self.interval
