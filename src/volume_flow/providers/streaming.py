from __future__ import annotations

from datetime import datetime, timezone

from volume_flow.models import KlineEvent, VolumeBar
from volume_flow.providers.errors import ProviderError

_KLINE_KEY = "k"
_OPEN_TIME = "t"
_OPEN = "o"
_HIGH = "h"
_LOW = "l"
_CLOSE = "c"
_VOLUME = "v"
_TAKER_BUY_VOLUME = "V"
_IS_CLOSED = "x"

_REQUIRED_KLINE_FIELDS = (
    _OPEN_TIME,
    _OPEN,
    _HIGH,
    _LOW,
    _CLOSE,
    _VOLUME,
    _TAKER_BUY_VOLUME,
    _IS_CLOSED,
)


def _parse_kline_event(message: object) -> KlineEvent:
    """Parse a Binance kline-stream message into a KlineEvent.

    The stream's "k" object carries taker buy base volume ("V"), so the buy/sell split matches
    a REST VolumeBar exactly. The "x" flag marks whether the bar has closed.

    Example:
        >>> event = _parse_kline_event(
        ...     {"k": {"t": 1780584600000, "o": "1", "h": "2", "l": "1", "c": "2",
        ...            "v": "10", "V": "6", "x": False}}
        ... )
        >>> event.bar.buy_volume, event.bar.sell_volume, event.is_closed
        (6.0, 4.0, False)
    """
    if not isinstance(message, dict):
        raise ProviderError(f"Expected a kline-stream object, got {type(message).__name__}")
    kline = message.get(_KLINE_KEY)
    if not isinstance(kline, dict):
        raise ProviderError(f"Kline-stream message missing a {_KLINE_KEY!r} object: {message!r}")
    missing = [field for field in _REQUIRED_KLINE_FIELDS if field not in kline]
    if missing:
        raise ProviderError(f"Kline payload missing fields {missing}: {kline!r}")
    try:
        total_volume = float(kline[_VOLUME])
        buy_volume = float(kline[_TAKER_BUY_VOLUME])
        bar = VolumeBar(
            open_time=datetime.fromtimestamp(int(kline[_OPEN_TIME]) / 1000, tz=timezone.utc),
            open=float(kline[_OPEN]),
            high=float(kline[_HIGH]),
            low=float(kline[_LOW]),
            close=float(kline[_CLOSE]),
            total_volume=total_volume,
            buy_volume=buy_volume,
            sell_volume=total_volume - buy_volume,
        )
    except (TypeError, ValueError) as exc:
        raise ProviderError(f"Malformed kline payload {kline!r}: {exc}") from exc
    return KlineEvent(bar=bar, is_closed=bool(kline[_IS_CLOSED]))
