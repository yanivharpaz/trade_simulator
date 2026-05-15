from __future__ import annotations

from datetime import timedelta

import pytest

from ibsim.clock import SimClock
from ibsim.events import SQLiteEventStore
from ibsim.models import ClockMode, OrderTicket
from ibsim.orders import OrderRejected
from ibsim.service import SimulatorService


def test_clock_can_pause_and_step() -> None:
    clock = SimClock(mode=ClockMode.PAUSED)
    start = clock.now()
    advanced = clock.step(timedelta(minutes=5))
    assert advanced - start == timedelta(minutes=5)


def test_event_store_appends_and_replays() -> None:
    store = SQLiteEventStore()
    event = store.append("session_started", {"session": "abc"}, aggregate_id="abc")
    events = store.list_events()
    assert event.event_id == 1
    assert events[0].event_type == "session_started"
    assert events[0].payload["session"] == "abc"


def test_marketable_limit_order_fills_and_updates_account() -> None:
    service = SimulatorService()
    quote = service.market_data.quote(265598)
    ticket = OrderTicket.model_validate(
        {
            "conid": 265598,
            "side": "BUY",
            "orderType": "LMT",
            "price": quote.ask,
            "quantity": 10,
            "tif": "DAY",
        }
    )
    response = service.orders.submit_orders("U1234567", [ticket])
    order_id = int(response[0]["order_id"])
    order = service.orders.orders[order_id]
    assert order.status.value == "Filled"
    assert order.filled == 10
    assert service.accounts.positions_for("U1234567")[0].position == 10
    assert service.events.list_events(event_type="order_filled")


def test_all_in_guardrail_rejects_oversized_order() -> None:
    service = SimulatorService()
    ticket = OrderTicket.model_validate(
        {
            "conid": 265598,
            "side": "BUY",
            "orderType": "MKT",
            "quantity": 10_000,
            "tif": "DAY",
        }
    )
    with pytest.raises(OrderRejected):
        service.orders.submit_orders("U1234567", [ticket])
