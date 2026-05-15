# trade_simulator

Private-use Interactive Brokers-shaped trading simulator backend.

This project implements a Python simulator with a canonical brokerage core and
IBKR Client Portal / Web API-shaped REST and WebSocket adapters. It is intended
for local development, demos, QA, and training. It is not affiliated with
Interactive Brokers, does not automate real IB authentication, and does not ship
IB-originating market data.

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

## Implemented Surface

- Session status and keepalive: `/iserver/auth/status`, `/tickle`, `/logout`
- Accounts and portfolio: `/iserver/accounts`, `/portfolio/accounts`,
  `/portfolio/{accountId}/summary`, `/portfolio/{accountId}/ledger`
- Market data: `/iserver/marketdata/snapshot`, `/iserver/marketdata/history`,
  WebSocket `smd` and `umd`
- Orders: submit, what-if, confirmation reply, recent orders, cancel
- WebSocket topics: `tic`, `smd`, `umd`, `sor`, `uor`, `spl`, `sld`
- Risk utilities: expected value, drawdown/loss distribution, fractional Kelly
- Strategy utility: dual moving average backtest with data-cleaning safeguards
- TWS semantic adapter: request/callback-level compatibility helpers, without
  byte-level socket protocol emulation

## Legal Boundary

Use synthetic or licensed non-IB market data for shared demos and tests. Do not
redistribute recorded IB market data unless your agreements explicitly permit it.
