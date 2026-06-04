from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from volume_flow.models import KlineEvent, VolumeBar

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def interval_to_timedelta(interval: str) -> timedelta:
    """Convert a Binance interval string like "1m", "4h", or "1d" to a timedelta.

    Supports minute/hour/day/week units. The irregular month interval ("1M") is not supported.

    Example:
        >>> interval_to_timedelta("15m")
        datetime.timedelta(seconds=900)
    """
    unit = interval[-1:]
    amount = interval[:-1]
    if unit not in _UNIT_SECONDS or not amount.isdigit():
        raise ValueError(f"Unsupported interval: {interval!r}")
    return timedelta(seconds=int(amount) * _UNIT_SECONDS[unit])


class LiveWindow:
    """A rolling window of VolumeBars seeded from history and updated by live kline events.

    Holds at most `capacity` bars; the last is the forming bar. Each event either revises the
    forming bar in place (same open time), rolls over to a new bar (later open time), or is
    ignored (older open time — a stale or duplicate update). An event that opens more than one
    interval past the forming bar means bars were missed; the window appends it but raises
    `needs_reseed` so the caller can refetch a fresh history.
    """

    def __init__(self, seed: Sequence[VolumeBar], capacity: int, interval: timedelta) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._interval = interval
        self._bars: list[VolumeBar] = list(seed)[-capacity:]
        self._needs_reseed = False

    @property
    def bars(self) -> list[VolumeBar]:
        return list(self._bars)

    @property
    def needs_reseed(self) -> bool:
        return self._needs_reseed

    def apply(self, event: KlineEvent) -> None:
        bar = event.bar
        if not self._bars:
            self._append(bar)
            return
        last_time = self._bars[-1].open_time
        if bar.open_time == last_time:
            self._bars[-1] = bar
        elif bar.open_time > last_time:
            if bar.open_time > last_time + self._interval:
                self._needs_reseed = True
            self._append(bar)

    def _append(self, bar: VolumeBar) -> None:
        self._bars.append(bar)
        if len(self._bars) > self._capacity:
            self._bars = self._bars[-self._capacity :]
