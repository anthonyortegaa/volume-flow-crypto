from __future__ import annotations

import math
from collections.abc import Sequence

from volume_flow.models import VolumeBar


def total_buy_volume(bars: Sequence[VolumeBar]) -> float:
    return sum((bar.buy_volume for bar in bars), 0.0)


def total_sell_volume(bars: Sequence[VolumeBar]) -> float:
    return sum((bar.sell_volume for bar in bars), 0.0)


def total_volume(bars: Sequence[VolumeBar]) -> float:
    return sum((bar.total_volume for bar in bars), 0.0)


def volume_delta(buy_volume: float, sell_volume: float) -> float:
    return buy_volume - sell_volume


def cumulative_delta(bars: Sequence[VolumeBar]) -> list[float]:
    """Running buy-minus-sell total, one entry per bar."""
    running = 0.0
    series: list[float] = []
    for bar in bars:
        running += bar.buy_volume - bar.sell_volume
        series.append(running)
    return series


def buy_fraction(buy_volume: float, sell_volume: float) -> float:
    """Buy share of total volume in [0, 1]; 0.0 when there is no volume."""
    total = buy_volume + sell_volume
    if total == 0.0:
        return 0.0
    return buy_volume / total


def buy_sell_ratio(buy_volume: float, sell_volume: float) -> float:
    """Buy-to-sell volume ratio; math.inf when there are buys but no sells."""
    if sell_volume == 0.0:
        return math.inf if buy_volume > 0.0 else 0.0
    return buy_volume / sell_volume


def volume_imbalance(buy_volume: float, sell_volume: float) -> float:
    """Order-flow imbalance in [-1, 1]: +1 all buys, -1 all sells, 0.0 when flat."""
    total = buy_volume + sell_volume
    if total == 0.0:
        return 0.0
    return (buy_volume - sell_volume) / total


def relative_volume(current: VolumeBar, prior_bars: Sequence[VolumeBar]) -> float:
    """Current bar volume over the mean volume of prior_bars; ValueError if it is empty."""
    if not prior_bars:
        raise ValueError("relative_volume requires at least one prior bar")
    prior_average = total_volume(prior_bars) / len(prior_bars)
    if prior_average == 0.0:
        return math.inf if current.total_volume > 0.0 else 0.0
    return current.total_volume / prior_average
