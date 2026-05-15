from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ibsim.models import OrderTicket
from ibsim.orders import OrderRejected
from ibsim.service import SimulatorService


Callback = dict[str, Any]


def cb(name: str, *args: Any, **kwargs: Any) -> Callback:
    return {"callback": name, "args": list(args), "kwargs": kwargs}


class TwsSemanticAdapter:
    """Semantic TWS compatibility layer.

    This is intentionally not a byte-level TWS socket implementation. It gives
    tests and client shims the IB-like request/callback contract while the
    canonical simulator core remains the source of truth.
    """

    def __init__(self, service: SimulatorService | None = None) -> None:
        self.service = service or SimulatorService()
        self.connected_clients: set[int] = set()

    def connect(self, *, client_id: int = 0) -> list[Callback]:
        self.connected_clients.add(client_id)
        accounts = ",".join(self.service.accounts.account_ids())
        return [
            cb("managedAccounts", accounts),
            cb("nextValidId", self.service.orders.next_order_id),
        ]

    def disconnect(self, *, client_id: int = 0) -> list[Callback]:
        self.connected_clients.discard(client_id)
        return [cb("connectionClosed")]

    def req_contract_details(self, req_id: int, *, conid: int | None = None, symbol: str | None = None) -> list[Callback]:
        contracts = []
        if conid is not None and conid in self.service.contracts:
            contracts = [self.service.contracts[conid]]
        elif symbol:
            contracts = [contract for contract in self.service.contracts.values() if contract.symbol.upper() == symbol.upper()]
        out = [
            cb("contractDetails", req_id, contract.model_dump(mode="json", by_alias=True))
            for contract in contracts
        ]
        out.append(cb("contractDetailsEnd", req_id))
        return out

    def req_mkt_data(self, ticker_id: int, conid: int) -> list[Callback]:
        quote = self.service.market_data.quote(conid)
        return [
            cb("marketDataType", ticker_id, 1),
            cb("tickPrice", ticker_id, "BID", quote.bid, {}),
            cb("tickSize", ticker_id, "BID_SIZE", quote.bid_size),
            cb("tickPrice", ticker_id, "ASK", quote.ask, {}),
            cb("tickSize", ticker_id, "ASK_SIZE", quote.ask_size),
            cb("tickPrice", ticker_id, "LAST", quote.last, {}),
            cb("tickSize", ticker_id, "LAST_SIZE", quote.last_size),
        ]

    def req_historical_data(self, req_id: int, conid: int, *, period: str = "1d", bar: str = "1h") -> list[Callback]:
        bars = self.service.market_data.history(conid, period=period, bar=bar)
        out = [cb("historicalData", req_id, item.to_web()) for item in bars]
        out.append(cb("historicalDataEnd", req_id, bars[0].start.isoformat() if bars else "", bars[-1].end.isoformat() if bars else ""))
        return out

    def place_order(self, *, client_id: int, account: str, order: dict[str, Any]) -> list[Callback]:
        try:
            ticket = OrderTicket.model_validate(order)
            response = self.service.orders.submit_orders(account, [ticket], client_id=client_id)
        except (ValidationError, OrderRejected) as exc:
            return [cb("error", -1, 201, str(exc), "")]
        if response and "id" in response[0]:
            return [cb("error", -1, 399, "; ".join(response[0].get("message", [])), "")]

        order_id = int(response[0]["order_id"])
        state = self.service.orders.orders[order_id]
        callbacks = [
            cb("openOrder", order_id, self.service.contract_info(state.ticket.conid), state.ticket.model_dump(mode="json", by_alias=True), {"status": state.status.value}),
            cb("orderStatus", order_id, state.status.value, state.filled, state.remaining, state.avg_fill_price, state.perm_id, state.ticket.parent_id or 0, state.avg_fill_price, state.client_id, "", 0.0),
        ]
        # IB documents duplicate orderStatus messages as normal, so emit one duplicate for compatibility tests.
        callbacks.append(callbacks[-1].copy())
        for execution in self.service.orders.executions_for(account):
            if execution.order_id != order_id:
                continue
            callbacks.append(cb("execDetails", -1, self.service.contract_info(execution.conid), execution.model_dump(mode="json", by_alias=True)))
            callbacks.append(cb("commissionReport", {"execId": execution.exec_id, "commission": execution.commission, "currency": "USD"}))
        return callbacks

    def cancel_order(self, *, client_id: int, account: str, order_id: int) -> list[Callback]:
        try:
            result = self.service.orders.cancel_order(account, order_id)
            return [cb("orderStatus", order_id, result["order_status"], 0.0, 0.0, 0.0, 0, 0, 0.0, client_id, "", 0.0)]
        except KeyError as exc:
            return [cb("error", order_id, 202, str(exc), "")]

    def req_open_orders(self, *, client_id: int) -> list[Callback]:
        return [
            cb("openOrder", item["orderId"], self.service.contract_info(int(item["conid"])), item, {"status": item["status"]})
            for item in self.service.orders.recent_orders(include_terminal=False)
            if item["clientId"] == client_id
        ] + [cb("openOrderEnd")]

    def req_all_open_orders(self) -> list[Callback]:
        return [
            cb("openOrder", item["orderId"], self.service.contract_info(int(item["conid"])), item, {"status": item["status"]})
            for item in self.service.orders.recent_orders(include_terminal=False)
        ] + [cb("openOrderEnd")]

    def req_executions(self, *, account: str | None = None) -> list[Callback]:
        executions = self.service.orders.executions_for(account)
        out = [
            cb("execDetails", -1, self.service.contract_info(execution.conid), execution.model_dump(mode="json", by_alias=True))
            for execution in executions
        ]
        out.append(cb("execDetailsEnd", -1))
        return out

    def req_positions(self) -> list[Callback]:
        out: list[Callback] = []
        for account in self.service.accounts.account_ids():
            for position in self.service.accounts.positions_for(account):
                out.append(cb("position", account, self.service.contract_info(position.conid), position.position, position.avg_cost))
        out.append(cb("positionEnd"))
        return out

    def req_account_summary(self, req_id: int, *, account: str) -> list[Callback]:
        snapshot = self.service.accounts.get(account)
        values = snapshot.model_dump(mode="json", by_alias=True)
        callbacks = [cb("accountSummary", req_id, account, key, value, snapshot.currency) for key, value in values.items() if isinstance(value, (int, float))]
        callbacks.append(cb("accountSummaryEnd", req_id))
        return callbacks

    def req_pnl_single(self, req_id: int, *, account: str, conid: int) -> list[Callback]:
        positions = [position for position in self.service.accounts.positions_for(account) if position.conid == conid]
        position = positions[0] if positions else None
        if position is None:
            return [cb("pnlSingle", req_id, 0, 0.0, 0.0, 0.0, 0.0)]
        return [cb("pnlSingle", req_id, position.position, position.unrealized_pnl, position.realized_pnl, position.unrealized_pnl + position.realized_pnl, position.market_value)]
