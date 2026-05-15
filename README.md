# trade_simulator

Private-use Interactive Brokers-shaped trading simulator backend.

This project implements a Python simulator with a canonical brokerage core and
IBKR Client Portal / Web API-shaped REST and WebSocket adapters. It is intended
for local development, demos, QA, and training. It is not affiliated with
Interactive Brokers, does not automate real IB authentication, does not require
an IB account, and does not ship IB-originating market data.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
pytest
ibsim --host 127.0.0.1 --port 5000
```

The Web API adapter listens under `http://127.0.0.1:5000/v1/api`.

```bash
curl -X POST http://127.0.0.1:5000/v1/api/tickle -H 'content-type: application/json' -d '{}'
curl http://127.0.0.1:5000/v1/api/iserver/accounts
curl 'http://127.0.0.1:5000/v1/api/iserver/marketdata/snapshot?conids=265598&fields=31,84,85,86,88,7059'
```

## Market Data Without IB

The public API signatures stay IB-shaped, but market prices can come from
external non-IB providers. By default the simulator uses `external`, which tries
Yahoo Finance first, then Stooq, then deterministic synthetic fallback if the
network or provider is unavailable.

```bash
IBSIM_MARKET_DATA_PROVIDER=external ibsim --host 127.0.0.1 --port 5000
IBSIM_MARKET_DATA_PROVIDER=yahoo ibsim --host 127.0.0.1 --port 5000
IBSIM_MARKET_DATA_PROVIDER=stooq ibsim --host 127.0.0.1 --port 5000
IBSIM_MARKET_DATA_PROVIDER=synthetic ibsim --host 127.0.0.1 --port 5000
```

No `yfinance` package is required; the providers use the Python standard library
and best-effort public endpoints. External providers do not supply IB conids,
depth, entitlements, or brokerage sessions, so the simulator maps local seeded
contracts to external symbols:

- AAPL `conid=265598` -> `AAPL`
- IBM `conid=8314` -> `IBM`
- EUR.USD cash -> `EURUSD=X`
- ES future -> `ES=F`
- SPY option example -> `SPY` underlying proxy

You can inspect the current provider and fallback state:

```bash
curl http://127.0.0.1:5000/v1/api/sim/marketdata/provider
```

## Implemented Surface

- Session status and keepalive: `/iserver/auth/status`, `/tickle`, `/logout`
- Accounts and portfolio: `/iserver/accounts`, `/portfolio/accounts`,
  `/portfolio/{accountId}/summary`, `/portfolio/{accountId}/ledger`
- Market data: `/iserver/marketdata/snapshot`, `/iserver/marketdata/history`,
  WebSocket `smd` and `umd`, backed by Yahoo/Stooq/synthetic providers
- Orders: submit, what-if, confirmation reply, recent orders, cancel
- WebSocket topics: `tic`, `smd`, `umd`, `sor`, `uor`, `spl`, `sld`
- Risk utilities: expected value, drawdown/loss distribution, fractional Kelly
- Strategy utility: dual moving average backtest with data-cleaning safeguards
- TWS semantic adapter: request/callback-level compatibility helpers, without
  byte-level socket protocol emulation

## Legal Boundary

Use synthetic or licensed non-IB market data for shared demos and tests. Do not
redistribute recorded IB market data unless your agreements explicitly permit it.
