from __future__ import annotations

from fastapi.testclient import TestClient

from ibsim.adapters.webapi.app import create_app
from ibsim.service import SimulatorService


def make_client() -> TestClient:
    return TestClient(create_app(SimulatorService()))


def test_session_accounts_snapshot_and_history() -> None:
    client = make_client()
    tickle = client.post("/v1/api/tickle", json={})
    assert tickle.status_code == 200
    accounts = client.get("/v1/api/iserver/accounts")
    assert accounts.json()["selectedAccount"] == "U1234567"
    snapshot = client.get("/v1/api/iserver/marketdata/snapshot", params={"conids": "265598", "fields": "31,84,86"})
    assert snapshot.status_code == 200
    assert snapshot.json()[0]["conid"] == 265598
    history = client.get("/v1/api/iserver/marketdata/history", params={"conid": 265598, "period": "1d", "bar": "1h"})
    assert history.status_code == 200
    assert history.json()["points"] == 24


def test_order_confirmation_flow() -> None:
    client = make_client()
    client.post("/v1/api/tickle", json={})
    response = client.post(
        "/v1/api/iserver/account/U1234567/orders",
        json={
            "orders": [
                {
                    "conid": 265598,
                    "side": "BUY",
                    "orderType": "STP",
                    "auxPrice": 999.0,
                    "quantity": 1,
                    "tif": "DAY",
                }
            ]
        },
    )
    assert response.status_code == 200
    reply_id = response.json()[0]["id"]
    confirmed = client.post(f"/v1/api/iserver/reply/{reply_id}", json={"confirmed": True})
    assert confirmed.status_code == 200
    assert confirmed.json()[0]["order_status"] == "Submitted"


def test_order_cancel_and_recent_orders() -> None:
    client = make_client()
    client.post("/v1/api/tickle", json={})
    response = client.post(
        "/v1/api/iserver/account/U1234567/orders",
        json=[
            {
                "conid": 265598,
                "side": "BUY",
                "orderType": "LMT",
                "price": 1.0,
                "quantity": 1,
                "tif": "DAY",
            }
        ],
    )
    order_id = response.json()[0]["order_id"]
    cancelled = client.delete(f"/v1/api/iserver/account/U1234567/order/{order_id}")
    assert cancelled.json()["order_status"] == "Cancelled"
    orders = client.get("/v1/api/iserver/account/orders")
    assert orders.json()["orders"][0]["orderId"] == int(order_id)


def test_websocket_market_order_and_pnl_topics() -> None:
    client = make_client()
    client.post("/v1/api/tickle", json={})
    with client.websocket_connect("/v1/api/ws") as ws:
        ws.send_text('smd+265598+{"fields":["31","84"]}')
        first = ws.receive_json()
        assert first["topic"] == "smd+265598"
        ws.send_text("sor+{}")
        ws.send_text("spl+{}")
        seen = {ws.receive_json()["topic"] for _ in range(2)}
        assert "sor" in seen or "spl" in seen


def test_risk_and_strategy_utility_routes() -> None:
    client = make_client()
    kelly = client.post("/v1/api/sim/risk/kelly", json={"winProbability": 0.55, "payoffOdds": 1, "fraction": 0.5})
    assert kelly.json()["fullKellyFraction"] == 0.1
    prices = [{"date": f"2026-01-{day:02d}", "close": 100 + day} for day in range(1, 31)]
    result = client.post("/v1/api/sim/strategy/dual-sma", json={"prices": prices, "shortWindow": 3, "longWindow": 5})
    assert result.status_code == 200
    assert result.json()["observations"] == 30
