"""Client-side rate limiting for the AODP API.

AODP allows about 180 requests per minute and 300 per 5 minutes per IP
(see AGENTS.md). The original client only waits 0.15 s between batches, which
is about 400 requests per minute: a full sweep of ~8,700 items (~90 price
batches plus the same again for history on a cold start) runs into HTTP 429
and then into long backoffs. On a server the IP may also be shared with other
tools, so we stay below the limit with some headroom instead of reacting to 429s.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class RateLimiter:
    """Blocks until a request fits into every sliding window."""

    def __init__(
        self,
        windows: tuple[tuple[int, float], ...] = ((170, 60.0), (285, 300.0)),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.windows = windows
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def wait_time(self, now: float) -> float:
        """Seconds until the next request may be sent (0 if it may go now)."""
        longest = max(w for _, w in self.windows)
        while self._stamps and now - self._stamps[0] >= longest:
            self._stamps.popleft()
        wait = 0.0
        for limit, window in self.windows:
            in_window = [t for t in self._stamps if now - t < window]
            if len(in_window) >= limit:
                wait = max(wait, window - (now - in_window[-limit]))
        return wait

    def acquire(self) -> float:
        """Waits if needed, records the request and returns the seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                wait = self.wait_time(now)
                if wait <= 0:
                    self._stamps.append(now)
                    return waited
            self._sleep(wait)
            waited += wait
