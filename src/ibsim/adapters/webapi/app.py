from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ibsim.models import OrderTicket
from ibsim.orders import OrderRejected
from ibsim.risk import expected_value, fractional_kelly, risk_metrics
from ibsim.service import SimulatorService
from ibsim.strategy import dual_moving_average_backtest


def create_app(service: SimulatorService | None = None) -> FastAPI:
    app = FastAPI(title="IB-shaped Trading Simulator", version="0.1.0")
    app.state.service = service or SimulatorService()

    def svc() -> SimulatorService:
        return app.state.service

    def require_brokerage() -> None:
        try:
            svc().sessions.require_brokerage()
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/v1/api/iserver/auth/status")
    def auth_status() -> dict[str, object]:
        return svc().sessions.auth_status()

    @app.post("/v1/api/tickle")
    def tickle() -> dict[str, object]:
        return svc().sessions.tickle()

    @app.post("/v1/api/logout")
    def logout() -> dict[str, object]:
        return svc().sessions.logout()

    @app.get("/v1/api/iserver/accounts")
    def iserver_accounts() -> dict[str, object]:
        require_brokerage()
        accounts = svc().accounts.account_ids()
        return {
            "accounts": accounts,
            "selectedAccount": accounts[0] if accounts else None,
            "supportsCashQty": True,
            "supportsFractions": True,
        }

    @app.get("/v1/api/portfolio/accounts")
    def portfolio_accounts() -> list[dict[str, object]]:
        require_brokerage()
        return svc().accounts.portfolio_accounts()

    @app.get("/v1/api/portfolio/{account_id}/ledger")
    def portfolio_ledger(account_id: str) -> dict[str, object]:
        require_brokerage()
        return svc().accounts.ledger(account_id)

    @app.get("/v1/api/portfolio/{account_id}/summary")
    def portfolio_summary(account_id: str) -> dict[str, object]:
        require_brokerage()
        quotes = [svc().market_data.quote(position.conid) for position in svc().accounts.positions_for(account_id)]
        snapshot = svc().accounts.mark_to_market(account_id, quotes) if quotes else svc().accounts.get(account_id)
        return snapshot.model_dump(mode="json", by_alias=True)

    @app.get("/v1/api/iserver/secdef/search")
    def secdef_search(symbol: str) -> list[dict[str, object]]:
        require_brokerage()
        return svc().contract_search(symbol)

    @app.get("/v1/api/iserver/contract/{conid}/info")
    def contract_info(conid: int) -> dict[str, object]:
        require_brokerage()
        try:
            return svc().contract_info(conid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown conid {conid}") from exc

    @app.get("/v1/api/iserver/marketdata/snapshot")
    def market_snapshot(
        conids: str,
        fields: str = Query(default="31,84,85,86,88,7059"),
    ) -> list[dict[str, object]]:
        require_brokerage()
        conid_list = [int(item) for item in conids.split(",") if item.strip()]
        field_list = [item.strip() for item in fields.split(",") if item.strip()]
        if len(conid_list) > 100:
            raise HTTPException(status_code=429, detail="IB-style pacing: snapshot supports at most 100 conids.")
        if len(field_list) > 50:
            raise HTTPException(status_code=429, detail="IB-style pacing: snapshot supports at most 50 fields.")
        out: list[dict[str, object]] = []
        for conid in conid_list:
            try:
                quote = svc().market_data.quote(conid)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            payload = quote.to_web_fields(field_list)
            out.append(payload)
            svc().events.append("market_subscription_opened", {"conid": conid, "fields": field_list, "mode": "snapshot"}, aggregate_id=str(conid), ts=svc().clock.now())
        return out

    @app.get("/v1/api/iserver/marketdata/history")
    def market_history(
        conid: int,
        period: str = "1d",
        bar: str = "1h",
        outsideRth: bool = False,
        source: str = "Trades",
    ) -> dict[str, object]:
        require_brokerage()
        try:
            bars = svc().market_data.history(conid, period=period, bar=bar, outside_rth=outsideRth)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        svc().events.append("historical_data_requested", {"conid": conid, "period": period, "bar": bar, "points": len(bars)}, aggregate_id=str(conid), ts=svc().clock.now())
        return {
            "conid": conid,
            "source": source,
            "period": period,
            "bar": bar,
            "points": len(bars),
            "data": [item.to_web() for item in bars],
        }

    @app.post("/v1/api/iserver/account/{account_id}/orders")
    def place_orders(account_id: str, body: Any) -> list[dict[str, object]]:
        require_brokerage()
        tickets = _parse_tickets(body)
        try:
            return svc().orders.submit_orders(account_id, tickets)
        except OrderRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/api/iserver/account/{account_id}/orders/whatif")
    def what_if(account_id: str, body: Any) -> list[dict[str, object]]:
        require_brokerage()
        tickets = _parse_tickets(body)
        return [item.model_dump(mode="json", by_alias=True) for item in svc().orders.what_if(account_id, tickets)]

    @app.post("/v1/api/iserver/reply/{reply_id}")
    def confirm_reply(reply_id: str, body: dict[str, Any] | None = None) -> list[dict[str, object]]:
        require_brokerage()
        confirmed = True if body is None else bool(body.get("confirmed", True))
        try:
            return svc().orders.confirm_reply(reply_id, confirmed=confirmed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OrderRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/api/iserver/account/orders")
    def recent_orders(accountId: str | None = None) -> dict[str, object]:
        require_brokerage()
        return {"orders": svc().orders.recent_orders(accountId)}

    @app.delete("/v1/api/iserver/account/{account_id}/order/{order_id}")
    def cancel_order(account_id: str, order_id: int) -> dict[str, object]:
        require_brokerage()
        try:
            return svc().orders.cancel_order(account_id, order_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/v1/api/iserver/account/{account_id}/order/{order_id}")
    def replace_order(account_id: str, order_id: int, body: dict[str, Any]) -> dict[str, object]:
        require_brokerage()
        try:
            ticket = OrderTicket.model_validate(body)
            return svc().orders.replace_order(account_id, order_id, ticket)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/api/sim/risk/expected-return")
    def expected_return_route(body: dict[str, Any]) -> dict[str, object]:
        return {"expectedReturn": expected_value(body.get("outcomes", []))}

    @app.get("/v1/api/sim/marketdata/provider")
    def market_data_provider_route() -> dict[str, object]:
        provider = svc().market_data
        return {
            "configured": svc().market_data_provider_name,
            "activeClass": provider.__class__.__name__,
            "lastFallbackError": getattr(provider, "last_error", None),
            "note": "IB-shaped API responses are preserved; prices come from external providers when reachable.",
        }

    @app.post("/v1/api/sim/risk/metrics")
    def risk_metrics_route(body: dict[str, Any]) -> dict[str, object]:
        result = risk_metrics([float(item) for item in body.get("returns", [])])
        return result.model_dump(mode="json", by_alias=True)

    @app.post("/v1/api/sim/risk/kelly")
    def kelly_route(body: dict[str, Any]) -> dict[str, object]:
        result = fractional_kelly(
            float(body["winProbability"]),
            float(body["payoffOdds"]),
            float(body.get("fraction", 0.5)),
        )
        return result.model_dump(mode="json", by_alias=True)

    @app.post("/v1/api/sim/strategy/dual-sma")
    def dual_sma_route(body: dict[str, Any]) -> dict[str, object]:
        result = dual_moving_average_backtest(
            body.get("prices", []),
            short_window=int(body.get("shortWindow", 20)),
            long_window=int(body.get("longWindow", 50)),
            allow_short=bool(body.get("allowShort", False)),
            transaction_cost_bps=float(body.get("transactionCostBps", 0.0)),
        )
        return result.model_dump(mode="json", by_alias=True)

    @app.websocket("/v1/api/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        subscriptions: dict[int, list[str]] = {}
        order_stream = False
        pnl_stream = False
        ledger_stream = False
        last_publish = 0.0
        while True:
            try:
                try:
                    message = await asyncio.wait_for(ws.receive_text(), timeout=0.25)
                    if message == "tic":
                        await ws.send_json({"topic": "tic", "args": svc().sessions.tickle()})
                    elif message.startswith("smd+"):
                        conid, fields = _parse_smd(message)
                        subscriptions[conid] = fields
                        svc().events.append("market_subscription_opened", {"conid": conid, "fields": fields, "mode": "ws"}, aggregate_id=str(conid), ts=svc().clock.now())
                    elif message.startswith("umd+"):
                        conid = int(message.split("+", 2)[1])
                        subscriptions.pop(conid, None)
                    elif message.startswith("sor+"):
                        order_stream = True
                    elif message.startswith("uor+"):
                        order_stream = False
                    elif message.startswith("spl+"):
                        pnl_stream = True
                    elif message.startswith("sld+"):
                        ledger_stream = True
                    else:
                        await ws.send_json({"topic": "error", "message": f"Unsupported topic: {message}"})
                except asyncio.TimeoutError:
                    pass

                now = svc().clock.now().timestamp()
                if now - last_publish < 1.0:
                    continue
                last_publish = now
                for conid, fields in subscriptions.items():
                    quote = svc().market_data.quote(conid)
                    payload = quote.to_web_fields(fields)
                    payload["topic"] = f"smd+{conid}"
                    await ws.send_json(payload)
                if order_stream:
                    await ws.send_json({"topic": "sor", "args": svc().orders.recent_orders()})
                if pnl_stream:
                    await ws.send_json({"topic": "spl", "args": _pnl_payload(svc())})
                if ledger_stream:
                    await ws.send_json({"topic": "sld", "args": {account: svc().accounts.ledger(account) for account in svc().accounts.account_ids()}})
            except WebSocketDisconnect:
                return

    return app


def _parse_tickets(body: Any) -> list[OrderTicket]:
    raw_orders = body if isinstance(body, list) else body.get("orders") if isinstance(body, dict) else None
    if not isinstance(raw_orders, list):
        raise HTTPException(status_code=400, detail="Order body must be an array or an object with an orders array.")
    try:
        return [OrderTicket.model_validate(item) for item in raw_orders]
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_smd(message: str) -> tuple[int, list[str]]:
    parts = message.split("+", 2)
    conid = int(parts[1])
    fields = ["31", "84", "85", "86", "88", "7059"]
    if len(parts) == 3 and parts[2]:
        try:
            payload = json.loads(parts[2])
            if isinstance(payload.get("fields"), list):
                fields = [str(item) for item in payload["fields"]]
        except json.JSONDecodeError:
            pass
    return conid, fields


def _pnl_payload(service: SimulatorService) -> dict[str, dict[str, float]]:
    payload: dict[str, dict[str, float]] = {}
    for account in service.accounts.account_ids():
        positions = service.accounts.positions_for(account)
        quotes = [service.market_data.quote(position.conid) for position in positions]
        snapshot = service.accounts.mark_to_market(account, quotes) if quotes else service.accounts.get(account)
        market_value = sum(position.market_value for position in service.accounts.positions_for(account))
        payload[f"{account}.Core"] = {
            "rowType": 1,
            "dpl": round(snapshot.realized_pnl, 2),
            "nl": round(snapshot.net_liquidation, 2),
            "upl": round(snapshot.unrealized_pnl, 2),
            "uel": round(snapshot.excess_liquidity, 2),
            "mv": round(market_value, 2),
        }
    return payload
