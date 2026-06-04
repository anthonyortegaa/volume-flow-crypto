from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SignalFlags:
    """Heuristic order-flow flags, not trading advice."""

    buy_imbalance: bool
    sell_imbalance: bool
    above_average_volume: bool


def evaluate_signals(
    imbalance: float,
    relative_volume: float,
    *,
    imbalance_threshold: float = 0.2,
    volume_threshold: float = 1.5,
) -> SignalFlags:
    """Derive heuristic flags from a precomputed imbalance and relative volume."""
    return SignalFlags(
        buy_imbalance=imbalance >= imbalance_threshold,
        sell_imbalance=imbalance <= -imbalance_threshold,
        above_average_volume=relative_volume >= volume_threshold,
    )
