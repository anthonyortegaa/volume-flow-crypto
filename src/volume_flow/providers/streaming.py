from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from enum import Enum
from types import TracebackType
from typing import Protocol

import websockets
from websockets.exceptions import WebSocketException

from volume_flow.models import KlineEvent, Symbol, VolumeBar
from volume_flow.providers.errors import ProviderError

# data-stream.binance.vision is the public market-data websocket host, the streaming counterpart
# of data-api.binance.vision. Like the REST host it is not geo-restricted and needs no API key.
_STREAM_BASE_URL = "wss://data-stream.binance.vision"

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


class ConnectionStatus(Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class _Connection(Protocol):
    """The async-iterable view of an open websocket, yielding raw frames."""

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


class _Connector(Protocol):
    """An async context manager that opens a `_Connection`, e.g. `websockets.connect(url)`."""

    async def __aenter__(self) -> _Connection: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


Connect = Callable[[str], _Connector]


def _default_connect(url: str) -> _Connector:
    return websockets.connect(url)


class _Backoff:
    """Bounded exponential backoff. `next()` returns the current delay, then grows it."""

    def __init__(self, initial: float, maximum: float, factor: float = 2.0) -> None:
        self._initial = initial
        self._maximum = maximum
        self._factor = factor
        self._current = initial

    def reset(self) -> None:
        self._current = self._initial

    def next(self) -> float:
        delay = self._current
        self._current = min(self._current * self._factor, self._maximum)
        return delay


class StreamingBinanceProvider:
    """Live taker buy/sell volume over the Binance kline websocket stream.

    Runs an asyncio websocket client on a background thread, parses each kline update into a
    `KlineEvent`, and pushes it onto a thread-safe queue that the caller drains with `drain()`.
    The connection reconnects on failure with bounded backoff; failures are reflected in
    `status` and `last_error` rather than raised, so they never leak into the caller's thread.

    The queue is bounded and drops the oldest event when full, and the stream stops itself if
    the caller goes `idle_timeout` seconds without draining — so an abandoned consumer (e.g. a
    closed browser tab, which Streamlit cannot signal) cannot leak an unbounded queue or a
    forever-running connection.
    """

    def __init__(
        self,
        symbol: Symbol,
        interval: str,
        *,
        base_url: str = _STREAM_BASE_URL,
        connect: Connect | None = None,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
        idle_timeout: float | None = 30.0,
        queue_maxsize: int = 1000,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/ws/{symbol.lower()}@kline_{interval}"
        self._connect: Connect = connect if connect is not None else _default_connect
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._idle_timeout = idle_timeout
        self._queue: queue.Queue[KlineEvent] = queue.Queue(maxsize=queue_maxsize)
        self._status = ConnectionStatus.STOPPED
        self._last_error: str | None = None
        self._last_active = time.monotonic()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = False

    @property
    def status(self) -> ConnectionStatus:
        with self._lock:
            return self._status

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        self._stop = False
        self._last_active = time.monotonic()
        thread = threading.Thread(target=self._run_loop, name="binance-stream", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop = True
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                # The loop already finished (e.g. the stream errored out) — nothing to cancel.
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        self._set_status(ConnectionStatus.STOPPED)

    def drain(self) -> list[KlineEvent]:
        """Remove and return every event queued since the last call."""
        self._last_active = time.monotonic()
        events: list[KlineEvent] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    def __enter__(self) -> StreamingBinanceProvider:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        task = loop.create_task(self._stream())
        self._task = task
        # Top-of-thread boundary: record why the stream ended instead of dying silently.
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            self._set_status(ConnectionStatus.STOPPED)
        except Exception as exc:
            self._set_status(ConnectionStatus.ERROR, error=str(exc))
        else:
            self._set_status(ConnectionStatus.STOPPED)
        finally:
            loop.close()

    async def _stream(self) -> None:
        backoff = _Backoff(self._backoff_initial, self._backoff_max)
        while not self._stop:
            self._set_status(ConnectionStatus.CONNECTING)
            try:
                async with self._connect(self._url) as connection:
                    self._set_status(ConnectionStatus.CONNECTED)
                    backoff.reset()
                    async for raw in connection:
                        if self._stop or self._is_idle():
                            return
                        self._handle_message(raw)
            except (OSError, WebSocketException) as exc:
                self._record_error(str(exc))
            if self._stop:
                return
            self._set_status(ConnectionStatus.RECONNECTING)
            await asyncio.sleep(backoff.next())

    def _handle_message(self, raw: str | bytes) -> None:
        try:
            event = _parse_kline_event(json.loads(raw))
        except (json.JSONDecodeError, ProviderError):
            # A single malformed frame is skipped, not fatal — keep the stream alive.
            return
        # Single producer: if the queue is full, drop the oldest event and keep the newest.
        while True:
            try:
                self._queue.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return

    def _is_idle(self) -> bool:
        if self._idle_timeout is None:
            return False
        return time.monotonic() - self._last_active > self._idle_timeout

    def _set_status(self, status: ConnectionStatus, *, error: str | None = None) -> None:
        with self._lock:
            self._status = status
            if error is not None:
                self._last_error = error

    def _record_error(self, error: str) -> None:
        with self._lock:
            self._last_error = error
