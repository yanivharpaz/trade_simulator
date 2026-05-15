from __future__ import annotations

from ibsim.adapters.tws import TwsSemanticAdapter
from ibsim.service import SimulatorService


def make_adapter() -> TwsSemanticAdapter:
    return TwsSemanticAdapter(SimulatorService(market_data_provider="synthetic"))


def test_tws_connect_emits_readiness_callbacks() -> None:
    adapter = make_adapter()
    callbacks = adapter.connect(client_id=7)
    assert [item["callback"] for item in callbacks] == ["managedAccounts", "nextValidId"]


def test_tws_place_order_emits_open_order_status_and_execution_callbacks() -> None:
    adapter = make_adapter()
    adapter.connect(client_id=7)
    quote = adapter.service.market_data.quote(265598)
    callbacks = adapter.place_order(
        client_id=7,
        account="U1234567",
        order={"conid": 265598, "side": "BUY", "orderType": "LMT", "price": quote.ask, "quantity": 1, "tif": "DAY"},
    )
    names = [item["callback"] for item in callbacks]
    assert "openOrder" in names
    assert names.count("orderStatus") >= 2
    assert "execDetails" in names
    assert "commissionReport" in names


def test_tws_open_orders_are_scoped_by_client_id() -> None:
    adapter = make_adapter()
    adapter.connect(client_id=7)
    adapter.place_order(
        client_id=7,
        account="U1234567",
        order={"conid": 265598, "side": "BUY", "orderType": "LMT", "price": 1.0, "quantity": 1, "tif": "DAY"},
    )
    own = adapter.req_open_orders(client_id=7)
    other = adapter.req_open_orders(client_id=8)
    assert any(item["callback"] == "openOrder" for item in own)
    assert not any(item["callback"] == "openOrder" for item in other)
