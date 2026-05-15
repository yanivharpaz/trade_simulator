from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from ibsim.accounts import AccountService
from ibsim.clock import SimClock
from ibsim.events import SQLiteEventStore
from ibsim.marketdata import MarketDataProvider
from ibsim.models import (
    Contract,
    Execution,
    OrderSide,
    OrderState,
    OrderStatus,
    OrderTicket,
    OrderType,
    TimeInForce,
    WhatIfResult,
)
from ibsim.risk import RiskEngine


class OrderRejected(ValueError):
    pass


@dataclass
class PendingConfirmation:
    reply_id: str
    account: str
    tickets: list[OrderTicket]
    client_id: int
    created_order_ref: str


class OrderService:
    def __init__(
        self,
        *,
        clock: SimClock,
        market_data: MarketDataProvider,
        accounts: AccountService,
        events: SQLiteEventStore,
        risk: RiskEngine,
        contracts: dict[int, Contract],
    ) -> None:
        self.clock = clock
        self.market_data = market_data
        self.accounts = accounts
        self.events = events
        self.risk = risk
        self.contracts = contracts
        self.next_order_id = 1_000_001
        self.next_perm_id = 9_000_001
        self.orders: dict[int, OrderState] = {}
        self.executions: list[Execution] = []
        self.pending_confirmations: dict[str, PendingConfirmation] = {}

    def submit_orders(
        self,
        account: str,
        tickets: list[OrderTicket],
        *,
        client_id: int = 0,
        bypass_confirmation: bool = False,
    ) -> list[dict[str, object]]:
        self.accounts.get(account)
        if not tickets:
            raise OrderRejected("At least one order is required.")

        responses: list[dict[str, object]] = []
        for ticket in tickets:
            quote = self.market_data.quote(ticket.conid)
            check = self.risk.check_order(ticket, self.accounts.get(account), quote)
            if not check.accepted:
                self.events.append(
                    "order_rejected",
                    {"account": account, "ticket": ticket, "risk": check},
                    aggregate_id=account,
                    ts=self.clock.now(),
                )
                raise OrderRejected("; ".join(check.messages))
            if check.requires_confirmation and not bypass_confirmation:
                reply_id = uuid4().hex
                self.pending_confirmations[reply_id] = PendingConfirmation(
                    reply_id=reply_id,
                    account=account,
                    tickets=[ticket],
                    client_id=client_id,
                    created_order_ref=ticket.c_oid or reply_id,
                )
                payload = {
                    "id": reply_id,
                    "message": check.messages,
                    "isSuppressed": False,
                    "messageIds": check.message_ids,
                }
                self.events.append("order_confirmation_required", payload, aggregate_id=account, ts=self.clock.now())
                responses.append(payload)
                continue
            responses.append(self._submit_one(account, ticket, client_id=client_id))
        return responses

    def what_if(self, account: str, tickets: list[OrderTicket]) -> list[WhatIfResult]:
        out: list[WhatIfResult] = []
        for ticket in tickets:
            quote = self.market_data.quote(ticket.conid)
            check = self.risk.check_order(ticket, self.accounts.get(account), quote)
            price = self.risk.mark_price(ticket, quote)
            result = self.accounts.what_if(account, ticket.conid, ticket.side, ticket.quantity, price, check.messages)
            out.append(WhatIfResult(**result))
            self.events.append("what_if_requested", {"account": account, "ticket": ticket, "result": result}, aggregate_id=account, ts=self.clock.now())
        return out

    def confirm_reply(self, reply_id: str, *, confirmed: bool = True) -> list[dict[str, object]]:
        pending = self.pending_confirmations.get(reply_id)
        if pending is None:
            raise KeyError(f"Unknown reply id {reply_id}")
        created_event = next(
            (event for event in reversed(self.events.list_events(event_type="order_confirmation_required")) if event.payload.get("id") == reply_id),
            None,
        )
        if created_event and self.clock.now() - created_event.ts > timedelta(minutes=2):
            del self.pending_confirmations[reply_id]
            raise TimeoutError("Order confirmation expired.")
        del self.pending_confirmations[reply_id]
        self.events.append(
            "order_confirmed" if confirmed else "order_confirmation_declined",
            {"replyId": reply_id, "confirmed": confirmed},
            aggregate_id=pending.account,
            ts=self.clock.now(),
        )
        if not confirmed:
            return [{"reply_id": reply_id, "confirmed": False, "order_status": "Cancelled"}]
        return self.submit_orders(pending.account, pending.tickets, client_id=pending.client_id, bypass_confirmation=True)

    def _submit_one(self, account: str, ticket: OrderTicket, *, client_id: int = 0) -> dict[str, object]:
        order_id = self.next_order_id
        self.next_order_id += 1
        perm_id = self.next_perm_id
        self.next_perm_id += 1

        state = OrderState(
            orderId=order_id,
            permId=perm_id,
            clientId=client_id,
            account=account,
            ticket=ticket,
            status=OrderStatus.SUBMITTED,
            remaining=ticket.quantity,
            createdAt=self.clock.now(),
            updatedAt=self.clock.now(),
        )
        self.orders[order_id] = state
        self.events.append("order_submitted", state, aggregate_id=str(order_id), ts=self.clock.now())
        self._try_fill(state)
        return {
            "order_id": str(order_id),
            "order_status": self.orders[order_id].status.value,
            "encrypt_message": "1",
        }

    def _try_fill(self, state: OrderState) -> None:
        ticket = state.ticket
        quote = self.market_data.quote(ticket.conid)
        fill_price: float | None = None
        visible_qty = quote.ask_size if ticket.side == OrderSide.BUY else quote.bid_size

        if ticket.order_type == OrderType.MARKET:
            fill_price = quote.ask if ticket.side == OrderSide.BUY else quote.bid
        elif ticket.order_type == OrderType.LIMIT and ticket.price is not None:
            if ticket.side == OrderSide.BUY and ticket.price >= quote.ask:
                fill_price = min(ticket.price, quote.ask)
            elif ticket.side == OrderSide.SELL and ticket.price <= quote.bid:
                fill_price = max(ticket.price, quote.bid)
        elif ticket.order_type == OrderType.STOP and ticket.aux_price is not None:
            if self._stop_triggered(ticket, quote.last):
                fill_price = quote.ask if ticket.side == OrderSide.BUY else quote.bid
        elif ticket.order_type == OrderType.STOP_LIMIT and ticket.aux_price is not None and ticket.price is not None:
            if self._stop_triggered(ticket, quote.last):
                if ticket.side == OrderSide.BUY and ticket.price >= quote.ask:
                    fill_price = min(ticket.price, quote.ask)
                elif ticket.side == OrderSide.SELL and ticket.price <= quote.bid:
                    fill_price = max(ticket.price, quote.bid)

        if fill_price is None:
            if ticket.tif == TimeInForce.IOC:
                state.status = OrderStatus.CANCELLED
                state.updated_at = self.clock.now()
                self.orders[state.order_id] = state
                self.events.append("order_cancelled", state, aggregate_id=str(state.order_id), ts=self.clock.now())
            return

        fill_qty = min(state.remaining, max(1.0, visible_qty))
        commission = self.risk.estimate_commission(fill_qty, fill_price)
        execution = Execution(
            orderId=state.order_id,
            permId=state.perm_id,
            account=state.account,
            conid=ticket.conid,
            side=ticket.side,
            price=fill_price,
            qty=fill_qty,
            commission=commission,
            ts=self.clock.now(),
        )
        self.executions.append(execution)
        new_filled = state.filled + fill_qty
        state.avg_fill_price = ((state.avg_fill_price * state.filled) + fill_price * fill_qty) / new_filled
        state.filled = new_filled
        state.remaining = max(0.0, ticket.quantity - state.filled)
        state.status = OrderStatus.FILLED if state.remaining == 0 else (OrderStatus.CANCELLED if ticket.tif == TimeInForce.IOC else OrderStatus.SUBMITTED)
        state.updated_at = self.clock.now()
        self.orders[state.order_id] = state
        position = self.accounts.apply_execution(execution, self.contracts[ticket.conid], quote)
        self.events.append("order_filled", execution, aggregate_id=str(state.order_id), ts=self.clock.now())
        self.events.append("commission_booked", {"execId": execution.exec_id, "commission": commission}, aggregate_id=str(state.order_id), ts=self.clock.now())
        self.events.append("position_updated", position, aggregate_id=state.account, ts=self.clock.now())
        self.events.append("account_snapshot_published", self.accounts.get(state.account), aggregate_id=state.account, ts=self.clock.now())

    @staticmethod
    def _stop_triggered(ticket: OrderTicket, last: float) -> bool:
        if ticket.aux_price is None:
            return False
        if ticket.side == OrderSide.BUY:
            return last >= ticket.aux_price
        return last <= ticket.aux_price

    def cancel_order(self, account: str, order_id: int) -> dict[str, object]:
        state = self.orders.get(order_id)
        if state is None or state.account != account:
            raise KeyError(f"Unknown order {order_id}")
        if state.status in {OrderStatus.FILLED, OrderStatus.CANCELLED}:
            return {"order_id": str(order_id), "order_status": state.status.value}
        state.status = OrderStatus.CANCELLED
        state.updated_at = self.clock.now()
        self.orders[order_id] = state
        self.events.append("order_cancelled", state, aggregate_id=str(order_id), ts=self.clock.now())
        return {"order_id": str(order_id), "order_status": state.status.value}

    def replace_order(self, account: str, order_id: int, ticket: OrderTicket) -> dict[str, object]:
        state = self.orders.get(order_id)
        if state is None or state.account != account:
            raise KeyError(f"Unknown order {order_id}")
        if not state.is_open:
            raise OrderRejected("Only open orders can be replaced.")
        state.ticket = ticket
        state.remaining = max(0.0, ticket.quantity - state.filled)
        state.updated_at = self.clock.now()
        self.orders[order_id] = state
        self.events.append("order_replaced", state, aggregate_id=str(order_id), ts=self.clock.now())
        self._try_fill(state)
        return {"order_id": str(order_id), "order_status": self.orders[order_id].status.value, "encrypt_message": "1"}

    def recent_orders(self, account: str | None = None, *, include_terminal: bool = True) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for order in sorted(self.orders.values(), key=lambda item: item.order_id):
            if account and order.account != account:
                continue
            if not include_terminal and not order.is_open:
                continue
            rows.append(
                {
                    "acct": order.account,
                    "orderId": order.order_id,
                    "permId": order.perm_id,
                    "conid": order.ticket.conid,
                    "status": order.status.value,
                    "side": order.ticket.side.value,
                    "orderType": order.ticket.order_type.value,
                    "timeInForce": order.ticket.tif.value,
                    "totalQuantity": order.ticket.quantity,
                    "filledQuantity": order.filled,
                    "remainingQuantity": order.remaining,
                    "avgPrice": order.avg_fill_price,
                    "order_ref": order.ticket.c_oid,
                    "clientId": order.client_id,
                }
            )
        return rows

    def executions_for(self, account: str | None = None) -> list[Execution]:
        if account is None:
            return list(self.executions)
        return [execution for execution in self.executions if execution.account == account]
