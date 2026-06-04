from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from volume_flow.models import Symbol, VolumeBar
from volume_flow.providers.errors import ProviderError

# data-api.binance.vision is Binance's public market-data domain. Unlike api.binance.com it
# is not geo-restricted, and it serves the same global market data without an API key.
_BASE_URL = "https://data-api.binance.vision/api/v3"

_KLINE_FIELD_COUNT = 12
_OPEN_TIME = 0
_OPEN = 1
_HIGH = 2
_LOW = 3
_CLOSE = 4
_VOLUME = 5
_TAKER_BUY_VOLUME = 9

_REQUEST_ERRORS: tuple[type[BaseException], ...] = (requests.RequestException, ValueError)


class BinanceProvider:

    def __init__(
        self,
        base_url: str = _BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    def get_volume_bars(
        self, symbol: Symbol, interval: str, limit: int = 500
    ) -> list[VolumeBar]:
        
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        try:
            response = self._session.get(
                f"{self._base_url}/klines", params=params, timeout=self._timeout
            )
            response.raise_for_status()
            payload = response.json()
        except _REQUEST_ERRORS as exc:
            raise ProviderError(
                f"Binance kline request failed for {symbol} {interval}: {exc}"
            ) from exc

        if not isinstance(payload, list):
            raise ProviderError(
                f"Expected a list of klines for {symbol} {interval}, got {type(payload).__name__}"
            )
        if not payload:
            raise ProviderError(f"Binance returned no klines for {symbol} {interval}")

        return [_parse_kline(kline) for kline in payload]


def _parse_kline(kline: object) -> VolumeBar:
    if not isinstance(kline, list) or len(kline) < _KLINE_FIELD_COUNT:
        raise ProviderError(f"Expected a {_KLINE_FIELD_COUNT}-field kline, got {kline!r}")
    try:
        total_volume = float(kline[_VOLUME])
        buy_volume = float(kline[_TAKER_BUY_VOLUME])
        return VolumeBar(
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
        raise ProviderError(f"Malformed kline {kline!r}: {exc}") from exc
