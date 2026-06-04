from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from volume_flow.providers.errors import ProviderError
from volume_flow.providers.streaming import _parse_kline_event

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "btcusdt_kline_event.json"


def _load_event_message() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text())


def test_parse_kline_event_maps_fields_from_k_object() -> None:
    message = _load_event_message()
    kline = message["k"]
    event = _parse_kline_event(message)
    assert event.bar.open == float(kline["o"])
    assert event.bar.high == float(kline["h"])
    assert event.bar.low == float(kline["l"])
    assert event.bar.close == float(kline["c"])
    assert event.bar.total_volume == float(kline["v"])
    assert event.bar.buy_volume == float(kline["V"])
    assert event.bar.sell_volume == float(kline["v"]) - float(kline["V"])


def test_parse_kline_event_open_time_is_utc_from_start_field() -> None:
    message = _load_event_message()
    event = _parse_kline_event(message)
    expected = datetime.fromtimestamp(int(message["k"]["t"]) / 1000, tz=timezone.utc)
    assert event.bar.open_time == expected
    assert event.bar.open_time.tzinfo == timezone.utc


def test_parse_kline_event_preserves_buy_sell_invariant() -> None:
    event = _parse_kline_event(_load_event_message())
    assert event.bar.buy_volume + event.bar.sell_volume == pytest.approx(event.bar.total_volume)
    assert 0.0 <= event.bar.buy_volume <= event.bar.total_volume


def test_parse_kline_event_forming_bar_reads_not_closed() -> None:
    event = _parse_kline_event(_load_event_message())
    assert event.is_closed is False


def test_parse_kline_event_closed_flag_true_when_x_true() -> None:
    message = _load_event_message()
    message["k"]["x"] = True
    event = _parse_kline_event(message)
    assert event.is_closed is True


def test_parse_kline_event_non_object_message_raises_provider_error() -> None:
    with pytest.raises(ProviderError):
        _parse_kline_event([1, 2, 3])


def test_parse_kline_event_missing_k_object_raises_provider_error() -> None:
    with pytest.raises(ProviderError):
        _parse_kline_event({"e": "kline"})


def test_parse_kline_event_missing_field_raises_provider_error() -> None:
    message = _load_event_message()
    del message["k"]["V"]
    with pytest.raises(ProviderError):
        _parse_kline_event(message)


def test_parse_kline_event_non_numeric_field_raises_provider_error() -> None:
    message = _load_event_message()
    message["k"]["v"] = "not-a-number"
    with pytest.raises(ProviderError):
        _parse_kline_event(message)
