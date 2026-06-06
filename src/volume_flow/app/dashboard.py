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

import altair as alt
import pandas as pd
import streamlit as st

from volume_flow.live import LiveWindow, TradeAggregator, interval_to_timedelta
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
from volume_flow.models import Symbol, Trade, VolumeBar
from volume_flow.providers.binance import BinanceProvider
from volume_flow.providers.errors import ProviderError
from volume_flow.providers.streaming import (
    ConnectionStatus,
    StreamingBinanceProvider,
    parse_agg_trade,
)

_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "TRXUSDT",
    "DOTUSDT",
    "LTCUSDT",
)
_INTERVALS = ("1m", "5m", "1h", "1d")
_DEFAULT_INTERVAL = "5m"
# Candle count scaled per interval as (min, max, default, step): short timeframes show recent
# activity, long timeframes span more history. Keeps you from staring at 1000 one-minute bars.
_LOOKBACK: dict[str, tuple[int, int, int, int]] = {
    "1m": (30, 240, 60, 30),
    "5m": (24, 288, 72, 24),
    "1h": (24, 336, 72, 24),
    "1d": (30, 365, 90, 30),
}
_BUY_COLOR = "#26a69a"
_SELL_COLOR = "#ef5350"
_DELTA_COLOR = "#42a5f5"
_LIVE_KEY = "live_session"
# The numbers are cheap; the charts are heavier, so they refresh on a slower cadence in a
# separate fragment. The charts are Altair/Vega so they reconcile in place without flashing.
_NUMBERS_REFRESH = "0.5s"
_CHART_REFRESH = "1s"


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


def _time_labels(bars: Sequence[VolumeBar]) -> list[str]:
    """Bar open times as ordinal axis labels, so charts use a width-filling band scale.

    A band scale sizes each bar to a share of the chart width rather than a fixed pixel
    count, keeping candles and volume bars proportional across display sizes and bar counts.
    Daily bars drop the (always-midnight) time to keep the label clean.
    """
    intraday = any(
        bar.open_time.hour or bar.open_time.minute or bar.open_time.second for bar in bars
    )
    fmt = "%m-%d %H:%M" if intraday else "%Y-%m-%d"
    return [bar.open_time.strftime(fmt) for bar in bars]


def _time_x() -> alt.X:
    """Band-scale x encoding over ordinal time labels, shared by the bar charts."""
    return alt.X(
        "time:N",
        sort=None,
        axis=alt.Axis(title=None, labelOverlap=True, labelAngle=-45),
    )


def _price_chart(bars: Sequence[VolumeBar]) -> alt.LayerChart:
    """Candlestick chart of OHLC prices, in Altair so it updates without flicker."""
    frame = pd.DataFrame(
        {
            "time": _time_labels(bars),
            "open": [bar.open for bar in bars],
            "high": [bar.high for bar in bars],
            "low": [bar.low for bar in bars],
            "close": [bar.close for bar in bars],
            "direction": ["up" if bar.close >= bar.open else "down" for bar in bars],
        }
    )
    color = alt.Color(
        "direction:N",
        scale=alt.Scale(domain=["up", "down"], range=[_BUY_COLOR, _SELL_COLOR]),
        legend=None,
    )
    # zero=False so the y-axis frames the actual price range instead of squashing the candles
    # against the top of a 0-based axis.
    price_scale = alt.Scale(zero=False)
    base = alt.Chart(frame).encode(x=_time_x(), color=color)
    wick = base.mark_rule().encode(
        y=alt.Y("low:Q", title="Price", scale=price_scale), y2="high:Q"
    )
    body = base.mark_bar().encode(y=alt.Y("open:Q", scale=price_scale), y2="close:Q")
    chart: alt.LayerChart = (wick + body).properties(height=360, width="container")
    return chart


def _volume_chart(bars: Sequence[VolumeBar]) -> alt.Chart:
    """Buy and sell taker volume per bar, stacked by side."""
    frame = pd.DataFrame(
        {
            "time": _time_labels(bars),
            "Buy": [bar.buy_volume for bar in bars],
            "Sell": [bar.sell_volume for bar in bars],
        }
    )
    melted = frame.melt("time", var_name="Side", value_name="Volume")
    chart: alt.Chart = (
        alt.Chart(melted)
        .mark_bar()
        .encode(
            x=_time_x(),
            xOffset=alt.XOffset("Side:N"),
            y=alt.Y("Volume:Q"),
            color=alt.Color(
                "Side:N",
                scale=alt.Scale(domain=["Buy", "Sell"], range=[_BUY_COLOR, _SELL_COLOR]),
                legend=alt.Legend(title=None, orient="top"),
            ),
        )
        .properties(height=300, width="container")
    )
    return chart


def _delta_frame(bars: Sequence[VolumeBar], series: Sequence[float]) -> pd.DataFrame:
    """Cumulative delta series, indexed by bar time, for a native area chart."""
    return pd.DataFrame(
        {"Cumulative delta": list(series)}, index=[bar.open_time for bar in bars]
    )


@dataclass(frozen=True)
class _Headline:
    """The four direction-focused figures shown in the top metric row."""

    price: float
    net_delta: float
    imbalance: float
    buy_share: float


def _signed(current: float, previous: float | None, ndigits: int) -> float | None:
    """Per-tick change for an st.metric delta arrow, or None when there is no prior tick."""
    if previous is None:
        return None
    return round(current - previous, ndigits)


def _format_ratio(ratio: float) -> str:
    return "all buys" if ratio == float("inf") else f"{ratio:.2f}"


def _render_direction(flags: SignalFlags, imbalance: float) -> None:
    """A single prominent, colored read on which way taker volume is leaning."""
    if flags.buy_imbalance:
        st.success(f"Buyers in control — taker flow is buy-heavy (imbalance {imbalance:+.2f}).")
    elif flags.sell_imbalance:
        st.error(f"Sellers in control — taker flow is sell-heavy (imbalance {imbalance:+.2f}).")
    else:
        st.info(f"Balanced flow — neither side dominates (imbalance {imbalance:+.2f}).")
    note = "Heuristic read, not financial advice."
    if flags.above_average_volume:
        note = "Above-average volume on the latest bar. " + note
    st.caption(note)


def _render_headline(current: _Headline, previous: _Headline | None) -> None:
    cols = st.columns(4)
    cols[0].metric(
        "Price",
        f"{current.price:,.2f}",
        delta=_signed(current.price, previous.price if previous else None, 2),
    )
    cols[1].metric(
        "Net delta",
        f"{current.net_delta:+,.0f}",
        delta=None if previous is None else int(round(current.net_delta - previous.net_delta)),
    )
    cols[2].metric(
        "Imbalance",
        f"{current.imbalance:+.2f}",
        delta=_signed(current.imbalance, previous.imbalance if previous else None, 3),
    )
    cols[3].metric(
        "Buy share",
        f"{current.buy_share:.1f}%",
        delta=_signed(current.buy_share, previous.buy_share if previous else None, 2),
    )


def _render_secondary_metrics(bars: Sequence[VolumeBar], buys: float, sells: float) -> None:
    cols = st.columns(4)
    cols[0].metric("Total volume", f"{total_volume(bars):,.0f}")
    cols[1].metric("Buy volume", f"{buys:,.0f}")
    cols[2].metric("Sell volume", f"{sells:,.0f}")
    cols[3].metric("Buy/sell ratio", _format_ratio(buy_sell_ratio(buys, sells)))


def _candle_stats(bar: VolumeBar) -> None:
    """Current candle's OHLC, beside the price chart."""
    st.metric("Open", f"{bar.open:,.2f}")
    st.metric("High", f"{bar.high:,.2f}")
    st.metric("Low", f"{bar.low:,.2f}")
    st.metric("Close", f"{bar.close:,.2f}", delta=round(bar.close - bar.open, 2))


def _volume_stats(bar: VolumeBar) -> None:
    """Current bar's buy/sell split, beside the volume chart."""
    st.metric("Buy", f"{bar.buy_volume:,.3f}")
    st.metric("Sell", f"{bar.sell_volume:,.3f}")
    st.metric("Bar delta", f"{bar.buy_volume - bar.sell_volume:+,.3f}")


def _delta_stats(series: Sequence[float]) -> None:
    """Current cumulative delta and its change this bar, beside the delta chart."""
    st.metric("Cumulative", f"{series[-1]:+,.2f}")
    if len(series) >= 2:
        st.metric("This bar", f"{series[-1] - series[-2]:+,.2f}")


def _render_numbers(bars: Sequence[VolumeBar], previous: _Headline | None = None) -> _Headline:
    """Render the direction banner and metric rows; return the headline for delta tracking."""
    buys = total_buy_volume(bars)
    sells = total_sell_volume(bars)
    imbalance = volume_imbalance(buys, sells)
    prior = bars[:-1]
    relative = relative_volume(bars[-1], prior) if prior else 1.0
    flags = evaluate_signals(imbalance, relative)
    current = _Headline(
        price=bars[-1].close,
        net_delta=buys - sells,
        imbalance=imbalance,
        buy_share=buy_fraction(buys, sells) * 100,
    )
    _render_direction(flags, imbalance)
    _render_headline(current, previous)
    _render_secondary_metrics(bars, buys, sells)
    return current


def _render_chart_panels(bars: Sequence[VolumeBar], interval: str) -> None:
    """Render the three charts, each with its current-interval stats panel."""
    st.subheader("Price")
    chart_col, stats_col = st.columns([4, 1], vertical_alignment="center")
    chart_col.altair_chart(_price_chart(bars))
    with stats_col:
        st.markdown(f"**Current {interval} candle**")
        _candle_stats(bars[-1])

    st.subheader("Buy vs. sell volume")
    chart_col, stats_col = st.columns([4, 1], vertical_alignment="center")
    chart_col.altair_chart(_volume_chart(bars))
    with stats_col:
        st.markdown(f"**Current {interval} bar**")
        _volume_stats(bars[-1])

    st.subheader("Cumulative delta")
    st.caption("Running buy-minus-sell total. Rising = buyers in control, falling = sellers.")
    series = cumulative_delta(bars)
    chart_col, stats_col = st.columns([4, 1], vertical_alignment="center")
    chart_col.area_chart(_delta_frame(bars, series), color=_DELTA_COLOR, height=240)
    with stats_col:
        st.markdown(f"**Current {interval} bar**")
        _delta_stats(series)


@dataclass
class _LiveSession:
    """A running live feed bound to one symbol/interval/lookback, held across reruns."""

    key: str
    symbol: str
    interval: str
    lookback: int
    provider: StreamingBinanceProvider[Trade]
    window: LiveWindow
    aggregator: TradeAggregator
    previous: _Headline | None = None


def _ensure_live_session(symbol: str, interval: str, lookback: int) -> _LiveSession:
    """Return the live session for these inputs, (re)starting the stream if needed."""
    key = f"{symbol}|{interval}|{lookback}"
    existing: _LiveSession | None = st.session_state.get(_LIVE_KEY)
    if existing is not None and existing.key == key and existing.provider.is_running():
        return existing
    if existing is not None:
        existing.provider.stop()
    seed = _seed_bars(symbol, interval, lookback)
    span = interval_to_timedelta(interval)
    window = LiveWindow(seed, capacity=lookback, interval=span)
    aggregator = TradeAggregator(seed[-1], span)
    provider: StreamingBinanceProvider[Trade] = StreamingBinanceProvider(
        Symbol(symbol), "aggTrade", parse_agg_trade
    )
    provider.start()
    session = _LiveSession(key, symbol, interval, lookback, provider, window, aggregator)
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
    span = interval_to_timedelta(session.interval)
    session.window = LiveWindow(seed, capacity=session.lookback, interval=span)
    session.aggregator = TradeAggregator(seed[-1], span)


def _render_connection_status(status: ConnectionStatus, last_error: str | None) -> None:
    if status is ConnectionStatus.CONNECTED:
        st.success("Live feed — connected")
    elif status in (ConnectionStatus.CONNECTING, ConnectionStatus.RECONNECTING):
        st.warning(f"Live feed — {status.value}")
    else:
        detail = f" ({last_error})" if last_error else ""
        st.error(f"Live feed — {status.value}{detail}")


@st.fragment(run_every=_NUMBERS_REFRESH)
def _live_numbers() -> None:
    """Fast path: fold new trades into the forming bar and redraw the (flicker-free) numbers."""
    session: _LiveSession | None = st.session_state.get(_LIVE_KEY)
    if session is None:
        return
    for trade in session.provider.drain():
        event = session.aggregator.add(trade)
        if event is not None:
            session.window.apply(event)
    if session.window.needs_reseed:
        _reseed(session)
    _render_connection_status(session.provider.status, session.provider.last_error)
    bars = session.window.bars
    if not bars:
        st.info("Waiting for the first live update…")
        return
    session.previous = _render_numbers(bars, previous=session.previous)


@st.fragment(run_every=_CHART_REFRESH)
def _live_charts() -> None:
    """Slow path: redraw the heavy Plotly charts less often so they don't flash."""
    session: _LiveSession | None = st.session_state.get(_LIVE_KEY)
    if session is None:
        return
    bars = session.window.bars
    if not bars:
        return
    _render_chart_panels(bars, session.interval)


def _format_span(lookback: int, interval: str) -> str:
    """Human-readable time span covered by `lookback` bars of `interval`."""
    total = interval_to_timedelta(interval) * lookback
    hours, remainder = divmod(total.seconds, 3600)
    minutes = remainder // 60
    parts = []
    if total.days:
        parts.append(f"{total.days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "0m"


def _sidebar_inputs() -> tuple[str, str, int, bool]:
    """Collect symbol, interval, lookback, and the live toggle from the sidebar."""
    st.sidebar.header("Market")
    symbol = st.sidebar.selectbox("Symbol", _SYMBOLS)
    interval = st.sidebar.selectbox(
        "Interval", _INTERVALS, index=_INTERVALS.index(_DEFAULT_INTERVAL)
    )
    low, high, default, step = _LOOKBACK[interval]
    # A per-interval key so each timeframe keeps its own scaled lookback across reruns.
    lookback = st.sidebar.slider(
        "Bars (lookback)",
        min_value=low,
        max_value=high,
        value=default,
        step=step,
        key=f"lookback_{interval}",
    )
    st.sidebar.caption(f"Window spans ~{_format_span(lookback, interval)}")
    live = st.sidebar.toggle(
        "Live feed", value=False, help="Stream every trade live for fluid, real-time updates."
    )
    _render_glossary()
    return symbol, interval, lookback, live


def _render_glossary() -> None:
    """A plain-language cheat sheet for the less-obvious metrics, in the sidebar."""
    with st.sidebar.expander("What the metrics mean"):
        st.markdown(
            "**Buy vs. sell volume** — how much was *taker* buying (lifting the ask) vs. "
            "*taker* selling (hitting the bid). This is real order flow.\n\n"
            "**Net delta** — buy volume minus sell volume. Positive means more buying pressure.\n\n"
            "**Imbalance** — net delta scaled to a −1…+1 score. **+1** = all buys, **−1** = all "
            "sells, **0** = even.\n\n"
            "**Buy share** — the percent of volume that was buying. Above **50%** means buyers "
            "were the more aggressive side.\n\n"
            "**Buy/sell ratio** — buy volume ÷ sell volume. **2.0** means twice as much buying "
            "as selling.\n\n"
            "**Cumulative delta** — a running total of net delta across the bars. A **rising** "
            "line means buyers are in control over time; **falling** means sellers are.\n\n"
            "**Relative volume** — the latest bar's volume vs. the recent average. **2.0×** means "
            "it's twice as busy as usual."
        )


def _render_live(symbol: str, interval: str, lookback: int) -> None:
    try:
        _ensure_live_session(symbol, interval, lookback)
    except ProviderError as exc:
        st.error(f"Could not start the live feed: {exc}")
        return
    st.subheader(f"{symbol} · {interval} · live")
    _live_numbers()
    st.divider()
    _live_charts()


def _render_static(symbol: str, interval: str, lookback: int) -> None:
    try:
        bars = _load_bars(symbol, interval, lookback)
    except ProviderError as exc:
        st.error(f"Could not load market data: {exc}")
        return
    st.subheader(f"{symbol} · {interval} · {len(bars)} bars")
    _render_numbers(bars)
    st.divider()
    _render_chart_panels(bars, interval)


def render() -> None:
    """Draw the full dashboard for one rerun."""
    st.set_page_config(page_title="Volume Flow", layout="wide")
    st.title("Volume Flow")
    st.caption("Real taker buy vs. sell crypto volume from Binance public market data.")

    symbol, interval, lookback, live = _sidebar_inputs()
    if live:
        _render_live(symbol, interval, lookback)
    else:
        _teardown_live_session()
        _render_static(symbol, interval, lookback)


if __name__ == "__main__":
    render()
