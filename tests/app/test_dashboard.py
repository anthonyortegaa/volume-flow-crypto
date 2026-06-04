from __future__ import annotations

import math
from datetime import datetime, timezone

import plotly.graph_objects as go

from volume_flow.app import dashboard
from volume_flow.models import VolumeBar


def _bar(buy: float, sell: float) -> VolumeBar:
    return VolumeBar(
        open_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        total_volume=buy + sell,
        buy_volume=buy,
        sell_volume=sell,
    )


def test_format_ratio_finite_value_is_two_decimals() -> None:
    assert dashboard._format_ratio(3.0) == "3.00"


def test_format_ratio_infinite_value_reads_all_buys() -> None:
    assert dashboard._format_ratio(math.inf) == "all buys"


def test_price_chart_returns_figure() -> None:
    figure = dashboard._price_chart([_bar(10.0, 5.0), _bar(20.0, 1.0)])
    assert isinstance(figure, go.Figure)


def test_volume_chart_has_buy_sell_and_delta_traces() -> None:
    figure = dashboard._volume_chart([_bar(10.0, 5.0), _bar(20.0, 1.0)])
    trace_names = {trace.name for trace in figure.data}
    assert trace_names == {"Buy", "Sell", "Cumulative delta"}
