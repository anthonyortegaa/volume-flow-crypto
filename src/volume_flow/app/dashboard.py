"""Streamlit dashboard for real buy/sell crypto volume.

Presentation only: it reads inputs, asks the provider for data, hands that data to the
metrics functions, and renders the results. It contains no volume math and no network code
of its own.

Run with::

    streamlit run src/volume_flow/app/dashboard.py
"""
from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go
import streamlit as st

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

_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
_DEFAULT_INTERVAL = "1h"
_BUY_COLOR = "#26a69a"
_SELL_COLOR = "#ef5350"
_DELTA_COLOR = "#42a5f5"


@st.cache_resource
def _provider() -> BinanceProvider:
    return BinanceProvider()


@st.cache_data(ttl=60)
def _load_bars(symbol: str, interval: str, limit: int) -> list[VolumeBar]:
    """Fetch bars for the given inputs, cached briefly so reruns don't refetch."""
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
    """Surface the Phase 3 metrics for the window in plain language."""
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


def _sidebar_inputs() -> tuple[str, str, int]:
    """Collect symbol, interval, and lookback from the sidebar."""
    st.sidebar.header("Market")
    symbol = st.sidebar.text_input("Symbol", value="BTCUSDT").strip().upper()
    interval = st.sidebar.selectbox(
        "Interval", _INTERVALS, index=_INTERVALS.index(_DEFAULT_INTERVAL)
    )
    lookback = st.sidebar.slider("Bars (lookback)", min_value=24, max_value=1000, value=168, step=24)
    return symbol, interval, lookback


def render() -> None:
    """Draw the full dashboard for one rerun."""
    st.set_page_config(page_title="Volume Flow", layout="wide")
    st.title("Volume Flow")
    st.caption("Real taker buy vs. sell crypto volume from Binance public market data.")

    symbol, interval, lookback = _sidebar_inputs()
    if not symbol:
        st.info("Enter a symbol to begin, e.g. BTCUSDT.")
        return

    try:
        bars = _load_bars(symbol, interval, lookback)
    except ProviderError as exc:
        st.error(f"Could not load market data: {exc}")
        return

    st.subheader(f"{symbol} · {interval} · {len(bars)} bars")
    _render_metrics(bars)
    st.plotly_chart(_price_chart(bars), use_container_width=True)
    st.plotly_chart(_volume_chart(bars), use_container_width=True)


if __name__ == "__main__":
    render()
