# volume-flow-crypto

A local tool for viewing real buy-side vs. sell-side trading volume for crypto pairs,
built on Binance public market data.

Binance klines report taker buy base volume directly, so the buy/sell split shown here is
real order-flow data — `sell = total volume - taker buy volume` — not an approximation.

> Status: under active development.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
```

## Development

```bash
uv run pytest
uv run mypy --strict src
```

The Streamlit app is added in a later phase; run instructions will land here when it does.

## Disclaimer

Signal flags surfaced by this tool are heuristics, not financial advice.
