from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from volume_flow.models import KlineEvent, Symbol
from volume_flow.providers.errors import ProviderError
from volume_flow.providers.streaming import (
    ConnectionStatus,
    StreamingBinanceProvider,
    _parse_kline_event,
    parse_agg_trade,
)

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "btcusdt_kline_event.json"
_TRADE_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "btcusdt_agg_trade.json"


def _load_event_message() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text())


def _load_trade_message() -> dict[str, Any]:
    return json.loads(_TRADE_FIXTURE_PATH.read_text())


# --- Parser ---------------------------------------------------------------------------------


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


# --- aggTrade parser ------------------------------------------------------------------------


def test_parse_agg_trade_maps_fields() -> None:
    message = _load_trade_message()
    trade = parse_agg_trade(message)
    assert trade.price == float(message["p"])
    assert trade.quantity == float(message["q"])
    assert trade.timestamp == datetime.fromtimestamp(int(message["T"]) / 1000, tz=timezone.utc)


def test_parse_agg_trade_buyer_maker_false_is_taker_buy() -> None:
    message = _load_trade_message()
    message["m"] = False
    assert parse_agg_trade(message).is_taker_buy is True


def test_parse_agg_trade_buyer_maker_true_is_taker_sell() -> None:
    message = _load_trade_message()
    message["m"] = True
    assert parse_agg_trade(message).is_taker_buy is False


def test_parse_agg_trade_non_object_raises_provider_error() -> None:
    with pytest.raises(ProviderError):
        parse_agg_trade([1, 2, 3])


def test_parse_agg_trade_missing_field_raises_provider_error() -> None:
    message = _load_trade_message()
    del message["q"]
    with pytest.raises(ProviderError):
        parse_agg_trade(message)


def test_parse_agg_trade_non_numeric_field_raises_provider_error() -> None:
    message = _load_trade_message()
    message["p"] = "not-a-number"
    with pytest.raises(ProviderError):
        parse_agg_trade(message)


# --- Streaming provider ---------------------------------------------------------------------


def _raw_event(open_time_ms: int, *, buy: str = "6", total: str = "10", closed: bool = False) -> str:
    return json.dumps(
        {
            "k": {
                "t": open_time_ms,
                "o": "1",
                "h": "2",
                "l": "1",
                "c": "2",
                "v": total,
                "V": buy,
                "x": closed,
            }
        }
    )


class _ScriptedConnection:
    """A fake open websocket: yields the given frames, then drops, hangs, or closes."""

    def __init__(self, messages: list[str], *, then: str) -> None:
        self._messages = list(messages)
        self._then = then

    async def __aenter__(self) -> _ScriptedConnection:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self) -> _ScriptedConnection:
        return self

    async def __anext__(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        if self._then == "drop":
            raise OSError("simulated drop")
        if self._then == "hang":
            await asyncio.Event().wait()
        raise StopAsyncIteration


class _Transport:
    """Hands out scripted connections in order; counts how often it is asked to connect."""

    def __init__(self, connections: list[_ScriptedConnection]) -> None:
        self._connections = list(connections)
        self.connect_calls = 0

    def __call__(self, url: str) -> _ScriptedConnection:
        self.connect_calls += 1
        if self._connections:
            return self._connections.pop(0)
        return _ScriptedConnection([], then="hang")


def _wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _provider(transport: Callable[[str], Any]) -> StreamingBinanceProvider[KlineEvent]:
    return StreamingBinanceProvider(
        Symbol("BTCUSDT"),
        "kline_1m",
        _parse_kline_event,
        connect=transport,
        backoff_initial=0.0,
        backoff_max=0.0,
    )


def test_streaming_provider_emits_events_in_order() -> None:
    transport = _Transport([_ScriptedConnection([_raw_event(1000), _raw_event(61000)], then="hang")])
    provider = _provider(transport)
    collected: list[KlineEvent] = []

    def have_two() -> bool:
        collected.extend(provider.drain())
        return len(collected) >= 2

    with provider:
        assert _wait_until(have_two)

    open_times = [event.bar.open_time for event in collected]
    assert open_times == [
        datetime.fromtimestamp(1.0, tz=timezone.utc),
        datetime.fromtimestamp(61.0, tz=timezone.utc),
    ]


def test_streaming_provider_reconnects_after_drop() -> None:
    transport = _Transport(
        [
            _ScriptedConnection([_raw_event(1000)], then="drop"),
            _ScriptedConnection([_raw_event(61000)], then="hang"),
        ]
    )
    provider = _provider(transport)
    collected: list[KlineEvent] = []

    def have_two() -> bool:
        collected.extend(provider.drain())
        return len(collected) >= 2

    with provider:
        assert _wait_until(have_two)

    assert transport.connect_calls >= 2
    assert len(collected) == 2


def test_streaming_provider_skips_malformed_frames() -> None:
    transport = _Transport(
        [_ScriptedConnection(["not json", json.dumps({"e": "kline"}), _raw_event(1000)], then="hang")]
    )
    provider = _provider(transport)
    collected: list[KlineEvent] = []

    def have_one() -> bool:
        collected.extend(provider.drain())
        return len(collected) >= 1

    with provider:
        assert _wait_until(have_one)

    assert len(collected) == 1
    assert collected[0].bar.open_time == datetime.fromtimestamp(1.0, tz=timezone.utc)


def test_streaming_provider_stop_marks_stopped_and_joins_thread() -> None:
    provider = _provider(_Transport([_ScriptedConnection([], then="hang")]))
    provider.start()
    assert _wait_until(lambda: provider.status == ConnectionStatus.CONNECTED)
    provider.stop()
    assert not provider.is_running()
    assert provider.status == ConnectionStatus.STOPPED


def test_streaming_provider_works_as_context_manager() -> None:
    provider = _provider(_Transport([_ScriptedConnection([], then="hang")]))
    with provider as entered:
        assert _wait_until(entered.is_running)
    assert not provider.is_running()
    assert provider.status == ConnectionStatus.STOPPED


def test_streaming_provider_unexpected_error_sets_error_status() -> None:
    def boom(url: str) -> Any:
        raise ValueError("boom")

    provider = _provider(boom)
    provider.start()
    assert _wait_until(lambda: provider.status == ConnectionStatus.ERROR)
    assert provider.last_error == "boom"
    provider.stop()


class _ChattyConnection:
    """Yields the same frame forever, pausing briefly so the loop stays cancellable."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def __aenter__(self) -> _ChattyConnection:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self) -> _ChattyConnection:
        return self

    async def __anext__(self) -> str:
        await asyncio.sleep(0.01)
        return self._message


def test_streaming_provider_bounded_queue_drops_oldest_events() -> None:
    messages = [_raw_event(1000 + index * 60000) for index in range(4)]
    provider = StreamingBinanceProvider(
        Symbol("BTCUSDT"),
        "kline_1m",
        _parse_kline_event,
        connect=_Transport([_ScriptedConnection(messages, then="hang")]),
        backoff_initial=0.0,
        backoff_max=0.0,
        idle_timeout=None,
        queue_maxsize=2,
    )
    provider.start()
    assert _wait_until(lambda: provider.status == ConnectionStatus.CONNECTED)
    time.sleep(0.3)
    events = provider.drain()
    provider.stop()

    assert [event.bar.open_time for event in events] == [
        datetime.fromtimestamp(121.0, tz=timezone.utc),
        datetime.fromtimestamp(181.0, tz=timezone.utc),
    ]


def test_streaming_provider_stops_itself_when_consumer_goes_idle() -> None:
    provider = StreamingBinanceProvider(
        Symbol("BTCUSDT"),
        "kline_1m",
        _parse_kline_event,
        connect=lambda url: _ChattyConnection(_raw_event(1000)),
        backoff_initial=0.0,
        backoff_max=0.0,
        idle_timeout=0.2,
    )
    provider.start()
    assert _wait_until(lambda: not provider.is_running())
    assert provider.status == ConnectionStatus.STOPPED
