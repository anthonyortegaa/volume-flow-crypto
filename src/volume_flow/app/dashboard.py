"""Streamlit dashboard for real buy/sell crypto volume.

Presentation only: it reads inputs, asks the provider for data, hands that data to the
metrics functions, and renders the results. It holds no volume math and no socket code of its
own. An optional live mode streams updates through a background provider and a pure merge
window — the wiring lives here, the I/O and the math do not.

Run with::

    streamlit run src/volume_flow/app/dashboard.py
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import plotly.graph_objects as go
import streamlit as st

from volume_flow.live import LiveWindow, interval_to_timedelta
from volume_flow.metrics.signals import SignalFlags, evaluate_signals
from volume_flow.metrics.volume import (
    buy_fraction,
    buy_sell_ratio,
    cumulative_delta,
    relative_volume,
    total_buy_volume,
    total_sell_volume,
    total_volume,
    volume_imbalance,
)
from volume_flow.models import Symbol, VolumeBar
from volume_flow.providers.binance import BinanceProvider
from volume_flow.providers.errors import ProviderError
from volume_flow.providers.streaming import ConnectionStatus, StreamingBinanceProvider

_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
_DEFAULT_INTERVAL = "1h"
_BUY_COLOR = "#26a69a"
_SELL_COLOR = "#ef5350"
_DELTA_COLOR = "#42a5f5"
_LIVE_KEY = "live_session"


@st.cache_resource
def _provider() -> BinanceProvider:
    return BinanceProvider()


@st.cache_data(ttl=60)
def _load_bars(symbol: str, interval: str, limit: int) -> list[VolumeBar]:
    """Fetch bars for the given inputs, cached briefly so reruns don't refetch."""
    return _provider().get_volume_bars(Symbol(symbol), interval, limit=limit)


def _seed_bars(symbol: str, interval: str, limit: int) -> list[VolumeBar]:
    """Fetch a fresh history to seed or reseed the live window, bypassing the poll cache."""
    return _provider().get_volume_bars(Symbol(symbol), interval, limit=limit)


def _price_chart(bars: Sequence[VolumeBar]) -> go.Figure:
    """Candlestick chart of OHLC prices over the window."""
    figure = go.Figure(
        go.Candlestick(
            x=[bar.open_time for bar in bars],
            open=[bar.open for bar in bars],
            high=[bar.high for bar in bars],
            low=[bar.low for bar in bars],
            close=[bar.close for bar in bars],
            increasing_line_color=_BUY_COLOR,
            decreasing_line_color=_SELL_COLOR,
            name="Price",
        )
    )
    figure.update_layout(
        height=420,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_rangeslider_visible=False,
        yaxis_title="Price",
    )
    return figure


def _volume_chart(bars: Sequence[VolumeBar]) -> go.Figure:
    """Grouped buy/sell volume bars with cumulative delta on a secondary axis."""
    times = [bar.open_time for bar in bars]
    figure = go.Figure()
    figure.add_bar(
        x=times, y=[bar.buy_volume for bar in bars], name="Buy", marker_color=_BUY_COLOR
    )
    figure.add_bar(
        x=times, y=[bar.sell_volume for bar in bars], name="Sell", marker_color=_SELL_COLOR
    )
    figure.add_scatter(
        x=times,
        y=cumulative_delta(bars),
        name="Cumulative delta",
        yaxis="y2",
        line=dict(color=_DELTA_COLOR, width=2),
    )
    figure.update_layout(
        height=360,
        margin=dict(l=0, r=0, t=10, b=0),
        barmode="group",
        yaxis_title="Volume",
        yaxis2=dict(title="Cumulative delta", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0.0),
    )
    return figure


def _render_metrics(bars: Sequence[VolumeBar]) -> None:
    """Surface the volume metrics for the window in plain language."""
    buys = total_buy_volume(bars)
    sells = total_sell_volume(bars)
    overall = total_volume(bars)
    imbalance = volume_imbalance(buys, sells)

    top = st.columns(4)
    top[0].metric("Total volume", f"{overall:,.0f}")
    top[1].metric("Buy volume", f"{buys:,.0f}")
    top[2].metric("Sell volume", f"{sells:,.0f}")
    top[3].metric("Buy share", f"{buy_fraction(buys, sells) * 100:.1f}%")

    bottom = st.columns(4)
    bottom[0].metric("Imbalance", f"{imbalance:+.2f}")
    bottom[1].metric("Buy/sell ratio", _format_ratio(buy_sell_ratio(buys, sells)))
    bottom[2].metric("Net delta", f"{buys - sells:+,.0f}")

    latest = bars[-1]
    prior = bars[:-1]
    if prior:
        latest_relative_volume = relative_volume(latest, prior)
        bottom[3].metric("Latest rel. volume", f"{latest_relative_volume:.2f}x")
        _render_signals(evaluate_signals(imbalance, latest_relative_volume))
    else:
        bottom[3].metric("Latest rel. volume", "n/a")


def _render_signals(flags: SignalFlags) -> None:
    """Show the heuristic flags as plain-language callouts."""
    st.caption("Heuristic flags (not financial advice)")
    if flags.buy_imbalance:
        st.success("Buy-side imbalance: takers are lifting the ask more than hitting the bid.")
    if flags.sell_imbalance:
        st.error("Sell-side imbalance: takers are hitting the bid more than lifting the ask.")
    if flags.above_average_volume:
        st.warning("Above-average volume: the latest bar is unusually active.")
    if not (flags.buy_imbalance or flags.sell_imbalance or flags.above_average_volume):
        st.info("Quiet, balanced flow: no heuristic flags on the latest bar.")


def _format_ratio(ratio: float) -> str:
    return "all buys" if ratio == float("inf") else f"{ratio:.2f}"


def _render_charts(bars: Sequence[VolumeBar]) -> None:
    _render_metrics(bars)
    st.plotly_chart(_price_chart(bars), width="stretch")
    st.plotly_chart(_volume_chart(bars), width="stretch")


@dataclass
class _LiveSession:
    """A running live feed bound to one symbol/interval/lookback, held across reruns."""

    key: str
    symbol: str
    interval: str
    lookback: int
    provider: StreamingBinanceProvider
    window: LiveWindow


def _ensure_live_session(symbol: str, interval: str, lookback: int) -> _LiveSession:
    """Return the live session for these inputs, (re)starting the stream if needed."""
    key = f"{symbol}|{interval}|{lookback}"
    existing: _LiveSession | None = st.session_state.get(_LIVE_KEY)
    if existing is not None and existing.key == key and existing.provider.is_running():
        return existing
    if existing is not None:
        existing.provider.stop()
    seed = _seed_bars(symbol, interval, lookback)
    window = LiveWindow(seed, capacity=lookback, interval=interval_to_timedelta(interval))
    provider = StreamingBinanceProvider(Symbol(symbol), interval)
    provider.start()
    session = _LiveSession(key, symbol, interval, lookback, provider, window)
    st.session_state[_LIVE_KEY] = session
    return session


def _teardown_live_session() -> None:
    """Stop and forget any running live session."""
    existing: _LiveSession | None = st.session_state.get(_LIVE_KEY)
    if existing is not None:
        existing.provider.stop()
        del st.session_state[_LIVE_KEY]


def _reseed(session: _LiveSession) -> None:
    try:
        seed = _seed_bars(session.symbol, session.interval, session.lookback)
    except ProviderError:
        return
    session.window = LiveWindow(
        seed, capacity=session.lookback, interval=interval_to_timedelta(session.interval)
    )


def _render_connection_status(status: ConnectionStatus, last_error: str | None) -> None:
    if status is ConnectionStatus.CONNECTED:
        st.success("Live feed — connected")
    elif status in (ConnectionStatus.CONNECTING, ConnectionStatus.RECONNECTING):
        st.warning(f"Live feed — {status.value}")
    else:
        detail = f" ({last_error})" if last_error else ""
        st.error(f"Live feed — {status.value}{detail}")


@st.fragment(run_every="1s")
def _live_view() -> None:
    """Auto-refreshing view: drain new events into the window and redraw once a second."""
    session: _LiveSession | None = st.session_state.get(_LIVE_KEY)
    if session is None:
        return
    for event in session.provider.drain():
        session.window.apply(event)
    if session.window.needs_reseed:
        _reseed(session)
    _render_connection_status(session.provider.status, session.provider.last_error)
    bars = session.window.bars
    if not bars:
        st.info("Waiting for the first live update…")
        return
    _render_charts(bars)


def _sidebar_inputs() -> tuple[str, str, int, bool]:
    """Collect symbol, interval, lookback, and the live toggle from the sidebar."""
    st.sidebar.header("Market")
    symbol = st.sidebar.text_input("Symbol", value="BTCUSDT").strip().upper()
    interval = st.sidebar.selectbox(
        "Interval", _INTERVALS, index=_INTERVALS.index(_DEFAULT_INTERVAL)
    )
    lookback = st.sidebar.slider("Bars (lookback)", min_value=24, max_value=1000, value=168, step=24)
    live = st.sidebar.toggle(
        "Live feed", value=False, help="Stream updates over the Binance kline websocket."
    )
    return symbol, interval, lookback, live


def _render_live(symbol: str, interval: str, lookback: int) -> None:
    try:
        _ensure_live_session(symbol, interval, lookback)
    except ProviderError as exc:
        st.error(f"Could not start the live feed: {exc}")
        return
    st.subheader(f"{symbol} · {interval} · live")
    _live_view()


def _render_static(symbol: str, interval: str, lookback: int) -> None:
    try:
        bars = _load_bars(symbol, interval, lookback)
    except ProviderError as exc:
        st.error(f"Could not load market data: {exc}")
        return
    st.subheader(f"{symbol} · {interval} · {len(bars)} bars")
    _render_charts(bars)


def render() -> None:
    """Draw the full dashboard for one rerun."""
    st.set_page_config(page_title="Volume Flow", layout="wide")
    st.title("Volume Flow")
    st.caption("Real taker buy vs. sell crypto volume from Binance public market data.")

    symbol, interval, lookback, live = _sidebar_inputs()
    if not symbol:
        _teardown_live_session()
        st.info("Enter a symbol to begin, e.g. BTCUSDT.")
        return

    if live:
        _render_live(symbol, interval, lookback)
    else:
        _teardown_live_session()
        _render_static(symbol, interval, lookback)


if __name__ == "__main__":
    render()
