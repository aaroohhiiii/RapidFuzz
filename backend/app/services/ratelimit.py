from __future__ import annotations

import time
from threading import Lock


class RateLimiter:
    def __init__(self, per_second: float = 1.0):
        self.per_second = per_second
        self._lock = Lock()
        self._next_allowed_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed_at:
                sleep_for = self._next_allowed_at - now
                time.sleep(sleep_for)
            self._next_allowed_at = max(now, self._next_allowed_at) + (1.0 / self.per_second)
