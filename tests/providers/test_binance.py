from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

from volume_flow.models import Symbol, VolumeBar
from volume_flow.providers.binance import BinanceProvider, _parse_kline
from volume_flow.providers.errors import ProviderError

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "btcusdt_1h.json"


def _load_raw_klines() -> list[list[Any]]:
    return json.loads(_FIXTURE_PATH.read_text())


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error

    def get(self, url: str, params: Any = None, timeout: float | None = None) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _provider_returning(payload: Any) -> BinanceProvider:
    return BinanceProvider(session=_FakeSession(response=_FakeResponse(payload)))


def test_parse_kline_maps_each_field_from_correct_index() -> None:
    for raw in _load_raw_klines():
        bar = _parse_kline(raw)
        assert bar.open == float(raw[1])
        assert bar.high == float(raw[2])
        assert bar.low == float(raw[3])
        assert bar.close == float(raw[4])
        assert bar.total_volume == float(raw[5])
        assert bar.buy_volume == float(raw[9])
        assert bar.sell_volume == float(raw[5]) - float(raw[9])


def test_parse_kline_first_bar_matches_hand_checked_values() -> None:
    bar = _parse_kline(_load_raw_klines()[0])
    assert bar.open_time == datetime(2026, 6, 3, 23, 0, tzinfo=timezone.utc)
    assert bar.open == 64912.97
    assert bar.high == 64979.99
    assert bar.low == 64092.49
    assert bar.close == 64142.75
    assert bar.total_volume == 1820.23083
    assert bar.buy_volume == 746.2465
    assert bar.sell_volume == 1073.98433


def test_parse_kline_preserves_buy_sell_invariant() -> None:
    for raw in _load_raw_klines():
        bar = _parse_kline(raw)
        assert bar.buy_volume + bar.sell_volume == pytest.approx(bar.total_volume)
        assert 0.0 <= bar.buy_volume <= bar.total_volume
        assert 0.0 <= bar.sell_volume <= bar.total_volume


def test_parse_kline_short_row_raises_provider_error() -> None:
    with pytest.raises(ProviderError):
        _parse_kline([1780527600000, "1", "2", "3"])


def test_parse_kline_non_numeric_field_raises_provider_error() -> None:
    raw = list(_load_raw_klines()[0])
    raw[5] = "not-a-number"
    with pytest.raises(ProviderError):
        _parse_kline(raw)


def test_get_volume_bars_valid_payload_returns_all_bars_in_open_time_order() -> None:
    provider = _provider_returning(_load_raw_klines())
    bars = provider.get_volume_bars(Symbol("BTCUSDT"), "1h", limit=5)
    assert len(bars) == 5
    assert all(isinstance(bar, VolumeBar) for bar in bars)
    open_times = [bar.open_time for bar in bars]
    assert open_times == sorted(open_times)


def test_get_volume_bars_empty_response_raises_provider_error() -> None:
    provider = _provider_returning([])
    with pytest.raises(ProviderError):
        provider.get_volume_bars(Symbol("BTCUSDT"), "1h")


def test_get_volume_bars_error_object_response_raises_provider_error() -> None:
    provider = _provider_returning({"code": -1121, "msg": "Invalid symbol."})
    with pytest.raises(ProviderError):
        provider.get_volume_bars(Symbol("NOTAREALPAIR"), "1h")


def test_get_volume_bars_http_error_is_wrapped_in_provider_error() -> None:
    provider = BinanceProvider(session=_FakeSession(response=_FakeResponse([], status_code=451)))
    with pytest.raises(ProviderError):
        provider.get_volume_bars(Symbol("BTCUSDT"), "1h")


def test_get_volume_bars_network_error_is_wrapped_and_chained() -> None:
    provider = BinanceProvider(session=_FakeSession(error=requests.ConnectionError("refused")))
    with pytest.raises(ProviderError) as exc_info:
        provider.get_volume_bars(Symbol("BTCUSDT"), "1h")
    assert isinstance(exc_info.value.__cause__, requests.ConnectionError)
