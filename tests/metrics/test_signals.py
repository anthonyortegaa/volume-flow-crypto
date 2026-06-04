from __future__ import annotations

from volume_flow.metrics.signals import SignalFlags, evaluate_signals


def test_evaluate_signals_strong_buy_imbalance_sets_buy_flag() -> None:
    flags = evaluate_signals(imbalance=0.5, relative_volume=1.0)
    assert flags.buy_imbalance is True
    assert flags.sell_imbalance is False


def test_evaluate_signals_strong_sell_imbalance_sets_sell_flag() -> None:
    flags = evaluate_signals(imbalance=-0.5, relative_volume=1.0)
    assert flags.sell_imbalance is True
    assert flags.buy_imbalance is False


def test_evaluate_signals_high_relative_volume_sets_volume_flag() -> None:
    flags = evaluate_signals(imbalance=0.0, relative_volume=2.0)
    assert flags.above_average_volume is True


def test_evaluate_signals_quiet_balanced_market_sets_no_flags() -> None:
    flags = evaluate_signals(imbalance=0.0, relative_volume=1.0)
    assert flags == SignalFlags(
        buy_imbalance=False, sell_imbalance=False, above_average_volume=False
    )


def test_evaluate_signals_respects_custom_thresholds() -> None:
    flags = evaluate_signals(
        imbalance=0.1, relative_volume=1.2, imbalance_threshold=0.05, volume_threshold=1.1
    )
    assert flags.buy_imbalance is True
    assert flags.above_average_volume is True
