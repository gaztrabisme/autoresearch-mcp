"""One in-process lane for all sessions and a memory gate before every run."""

import math
import re
import subprocess
import threading
import time
from collections.abc import Callable

_RUN_LOCK = threading.RLock()


def parse_memory_free_pct(output: str) -> float:
    match = re.search(r"System-wide memory free percentage:\s*(\d+(?:\.\d+)?)%", output)
    if not match or not 0 <= float(match[1]) <= 100:
        raise ValueError("memory_pressure did not report a valid free percentage")
    return float(match[1])


def read_memory_free_pct() -> float:
    result = subprocess.run(["memory_pressure"], capture_output=True, text=True, check=True)
    return parse_memory_free_pct(result.stdout)


class RunQueue:
    def __init__(
        self,
        memory_reader: Callable[[], float] = read_memory_free_pct,
        *,
        clock=time,
        poll_interval_s: float = 1.0,
    ):
        if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive and finite")
        self.memory_reader = memory_reader
        self.clock = clock
        self.poll_interval_s = poll_interval_s
        self.lock = _RUN_LOCK

    def run(self, operation: Callable[[], dict], memory_free_min_pct: float) -> dict:
        if not 0 <= memory_free_min_pct <= 100:
            raise ValueError("memory_free_min_pct must be in [0, 100]")
        with self.lock:
            while True:
                free = self.memory_reader()
                if not math.isfinite(free) or not 0 <= free <= 100:
                    raise ValueError("invalid memory free percentage")
                if free >= memory_free_min_pct:
                    break
                self.clock.sleep(self.poll_interval_s)
            result = operation()
            return {**result, "memory_free_pct": free}
