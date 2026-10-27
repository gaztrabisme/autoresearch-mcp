"""Selection uses an absolute gain, with a strict, pre-recorded threshold."""

import math
from statistics import median


def mad(values: list[float]) -> float:
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("MAD requires nonempty, finite measurements")
    center = median(values)
    return float(median(abs(value - center) for value in values))


def keep_threshold(values: list[float], noise_k: float = 2.0, min_gain: float = 0.0) -> float:
    if not all(math.isfinite(value) and value >= 0 for value in (noise_k, min_gain)):
        raise ValueError("noise_k and min_gain must be finite and nonnegative")
    return max(noise_k * mad(values), min_gain)


def improves(candidate: float, best: float, threshold: float, direction: str) -> bool:
    if direction not in {"min", "max"}:
        raise ValueError("direction must be min or max")
    if not all(math.isfinite(value) for value in (candidate, best, threshold)) or threshold < 0:
        raise ValueError("scores and threshold must be finite; threshold must be nonnegative")
    gain = best - candidate if direction == "min" else candidate - best
    return gain > threshold
