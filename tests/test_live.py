from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from volume_flow.live import LiveWindow, interval_to_timedelta
from volume_flow.models import KlineEvent, VolumeBar

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MINUTE = timedelta(minutes=1)


def _bar(index: int, buy: float, sell: float) -> VolumeBar:
    return VolumeBar(
        open_time=_BASE + index * _MINUTE,
        open=0.0,
        high=0.0,
        low=0.0,
        close=0.0,
        total_volume=buy + sell,
        buy_volume=buy,
        sell_volume=sell,
    )


def _event(index: int, buy: float, sell: float, *, closed: bool = False) -> KlineEvent:
    return KlineEvent(bar=_bar(index, buy, sell), is_closed=closed)


def _open_times(window: LiveWindow) -> list[datetime]:
    return [bar.open_time for bar in window.bars]


def test_seed_keeps_only_the_last_capacity_bars() -> None:
    window = LiveWindow([_bar(0, 1, 1), _bar(1, 1, 1), _bar(2, 1, 1)], capacity=2, interval=_MINUTE)
    assert _open_times(window) == [_BASE + 1 * _MINUTE, _BASE + 2 * _MINUTE]


def test_capacity_below_one_raises_value_error() -> None:
    with pytest.raises(ValueError):
        LiveWindow([_bar(0, 1, 1)], capacity=0, interval=_MINUTE)


def test_new_window_does_not_need_reseed() -> None:
    window = LiveWindow([_bar(0, 1, 1)], capacity=4, interval=_MINUTE)
    assert window.needs_reseed is False


def test_apply_same_open_time_revises_forming_bar_in_place() -> None:
    window = LiveWindow([_bar(0, 10, 5), _bar(1, 10, 5)], capacity=4, interval=_MINUTE)
    window.apply(_event(1, 30, 5))
    assert len(window.bars) == 2
    assert window.bars[-1].buy_volume == 30.0
    assert window.bars[-1].open_time == _BASE + 1 * _MINUTE


def test_apply_next_interval_rolls_over_without_reseed() -> None:
    window = LiveWindow([_bar(0, 1, 1), _bar(1, 1, 1)], capacity=4, interval=_MINUTE)
    window.apply(_event(2, 7, 3))
    assert _open_times(window) == [_BASE, _BASE + 1 * _MINUTE, _BASE + 2 * _MINUTE]
    assert window.bars[-1].buy_volume == 7.0
    assert window.needs_reseed is False


def test_rollover_beyond_capacity_drops_oldest() -> None:
    window = LiveWindow([_bar(0, 1, 1), _bar(1, 1, 1)], capacity=2, interval=_MINUTE)
    window.apply(_event(2, 1, 1))
    assert _open_times(window) == [_BASE + 1 * _MINUTE, _BASE + 2 * _MINUTE]


def test_apply_older_event_is_ignored_as_stale() -> None:
    window = LiveWindow([_bar(0, 1, 1), _bar(1, 9, 9)], capacity=4, interval=_MINUTE)
    window.apply(_event(0, 100, 100))
    assert _open_times(window) == [_BASE, _BASE + 1 * _MINUTE]
    assert window.bars[-1].buy_volume == 9.0


def test_apply_skipping_a_bar_flags_reseed_and_still_appends() -> None:
    window = LiveWindow([_bar(0, 1, 1), _bar(1, 1, 1)], capacity=4, interval=_MINUTE)
    window.apply(_event(3, 4, 4))
    assert window.needs_reseed is True
    assert window.bars[-1].open_time == _BASE + 3 * _MINUTE


def test_apply_to_empty_seed_appends_first_bar() -> None:
    window = LiveWindow([], capacity=4, interval=_MINUTE)
    window.apply(_event(0, 2, 1))
    assert _open_times(window) == [_BASE]
    assert window.bars[0].buy_volume == 2.0


def test_bars_property_returns_a_copy() -> None:
    window = LiveWindow([_bar(0, 1, 1)], capacity=4, interval=_MINUTE)
    window.bars.append(_bar(1, 1, 1))
    assert len(window.bars) == 1


def test_interval_to_timedelta_supported_units() -> None:
    assert interval_to_timedelta("1m") == timedelta(minutes=1)
    assert interval_to_timedelta("15m") == timedelta(minutes=15)
    assert interval_to_timedelta("1h") == timedelta(hours=1)
    assert interval_to_timedelta("4h") == timedelta(hours=4)
    assert interval_to_timedelta("1d") == timedelta(days=1)


def test_interval_to_timedelta_rejects_unknown_unit() -> None:
    with pytest.raises(ValueError):
        interval_to_timedelta("1M")


def test_interval_to_timedelta_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        interval_to_timedelta("abc")
