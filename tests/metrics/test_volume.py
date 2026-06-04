from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from volume_flow.metrics.volume import (
    buy_fraction,
    buy_sell_ratio,
    cumulative_delta,
    relative_volume,
    total_buy_volume,
    total_sell_volume,
    total_volume,
    volume_delta,
    volume_imbalance,
)
from volume_flow.models import VolumeBar


def _bar(buy: float, sell: float) -> VolumeBar:
    return VolumeBar(
        open_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=0.0,
        high=0.0,
        low=0.0,
        close=0.0,
        total_volume=buy + sell,
        buy_volume=buy,
        sell_volume=sell,
    )


def test_total_buy_volume_sums_buy_across_window() -> None:
    bars = [_bar(10.0, 5.0), _bar(20.0, 1.0), _bar(0.0, 7.0)]
    assert total_buy_volume(bars) == 30.0


def test_total_sell_volume_sums_sell_across_window() -> None:
    bars = [_bar(10.0, 5.0), _bar(20.0, 1.0), _bar(0.0, 7.0)]
    assert total_sell_volume(bars) == 13.0


def test_total_volume_sums_total_across_window() -> None:
    bars = [_bar(10.0, 5.0), _bar(20.0, 1.0)]
    assert total_volume(bars) == 36.0


def test_total_buy_volume_empty_window_returns_zero() -> None:
    assert total_buy_volume([]) == 0.0


def test_volume_delta_positive_when_buys_exceed_sells() -> None:
    assert volume_delta(150.0, 50.0) == 100.0


def test_volume_delta_negative_when_sells_exceed_buys() -> None:
    assert volume_delta(40.0, 90.0) == -50.0


def test_cumulative_delta_accumulates_running_total() -> None:
    bars = [_bar(10.0, 0.0), _bar(0.0, 5.0), _bar(20.0, 0.0)]
    assert cumulative_delta(bars) == [10.0, 5.0, 25.0]


def test_cumulative_delta_single_bar_returns_its_delta() -> None:
    assert cumulative_delta([_bar(7.0, 2.0)]) == [5.0]


def test_cumulative_delta_empty_window_returns_empty() -> None:
    assert cumulative_delta([]) == []


def test_buy_fraction_balanced_returns_half() -> None:
    assert buy_fraction(100.0, 100.0) == 0.5


def test_buy_fraction_all_buys_returns_one() -> None:
    assert buy_fraction(100.0, 0.0) == 1.0


def test_buy_fraction_zero_volume_returns_zero() -> None:
    assert buy_fraction(0.0, 0.0) == 0.0


def test_buy_sell_ratio_returns_quotient() -> None:
    assert buy_sell_ratio(150.0, 50.0) == 3.0


def test_buy_sell_ratio_no_sells_returns_inf() -> None:
    assert buy_sell_ratio(100.0, 0.0) == math.inf


def test_buy_sell_ratio_no_activity_returns_zero() -> None:
    assert buy_sell_ratio(0.0, 0.0) == 0.0


def test_volume_imbalance_all_buys_returns_one() -> None:
    assert volume_imbalance(100.0, 0.0) == 1.0


def test_volume_imbalance_all_sells_returns_negative_one() -> None:
    assert volume_imbalance(0.0, 100.0) == -1.0


def test_volume_imbalance_balanced_returns_zero() -> None:
    assert volume_imbalance(100.0, 100.0) == 0.0


def test_volume_imbalance_zero_volume_returns_zero() -> None:
    assert volume_imbalance(0.0, 0.0) == 0.0


def test_volume_imbalance_known_value() -> None:
    assert volume_imbalance(150.0, 50.0) == 0.5


def test_relative_volume_above_average_returns_ratio() -> None:
    assert relative_volume(_bar(100.0, 100.0), [_bar(50.0, 50.0), _bar(50.0, 50.0)]) == 2.0


def test_relative_volume_equal_to_average_returns_one() -> None:
    assert relative_volume(_bar(50.0, 50.0), [_bar(50.0, 50.0), _bar(50.0, 50.0)]) == 1.0


def test_relative_volume_empty_prior_raises_value_error() -> None:
    with pytest.raises(ValueError):
        relative_volume(_bar(50.0, 50.0), [])


def test_relative_volume_zero_prior_average_returns_inf() -> None:
    assert relative_volume(_bar(25.0, 25.0), [_bar(0.0, 0.0)]) == math.inf
