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
