from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import NewType

Symbol = NewType("Symbol", str)


class TradeSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class VolumeBar:
    """
    Attributes:
        open_time: Bar open time, as a timezone-aware UTC datetime.
        open: Open price.
        high: High price.
        low: Low price.
        close: Close price.
        total_volume: Total traded base-asset volume in the bar.
        buy_volume: Taker buy base-asset volume.
        sell_volume: Taker sell base-asset volume (total minus taker buy).
    """

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    total_volume: float
    buy_volume: float
    sell_volume: float


@dataclass(frozen=True, slots=True)
class KlineEvent:
    """A single kline-stream update for one bar.

    Attributes:
        bar: The bar carried by the update, with the same taker buy/sell split as a REST
            VolumeBar.
        is_closed: True once the bar's interval has closed and its values are final; False
            while the bar is still forming and its volume will keep changing.
    """

    bar: VolumeBar
    is_closed: bool


@dataclass(frozen=True, slots=True)
class Trade:
    """A single aggregated trade from the live trade stream.

    Attributes:
        timestamp: Trade time, as a timezone-aware UTC datetime.
        price: Trade price.
        quantity: Base-asset quantity traded.
        is_taker_buy: True when the taker was buying (lifting the ask), False when the taker
            was selling (hitting the bid).
    """

    timestamp: datetime
    price: float
    quantity: float
    is_taker_buy: bool
