from __future__ import annotations

from pathlib import Path

from ibsim.accounts import AccountService
from ibsim.clock import SimClock
from ibsim.events import SQLiteEventStore
from ibsim.marketdata import SyntheticMarketDataProvider
from ibsim.orders import OrderService
from ibsim.risk import RiskEngine
from ibsim.seed import DEFAULT_ACCOUNT, seeded_account, seeded_contracts
from ibsim.sessions import SessionManager


class SimulatorService:
    def __init__(self, *, event_store_path: str | Path = ":memory:") -> None:
        self.clock = SimClock()
        self.events = SQLiteEventStore(event_store_path)
        self.contracts = seeded_contracts()
        self.accounts = AccountService({DEFAULT_ACCOUNT: seeded_account(DEFAULT_ACCOUNT)})
        self.risk = RiskEngine()
        self.market_data = SyntheticMarketDataProvider(self.contracts, self.clock)
        self.sessions = SessionManager(clock=self.clock, events=self.events)
        self.orders = OrderService(
            clock=self.clock,
            market_data=self.market_data,
            accounts=self.accounts,
            events=self.events,
            risk=self.risk,
            contracts=self.contracts,
        )

    def contract_search(self, symbol: str) -> list[dict[str, object]]:
        symbol = symbol.upper()
        matches = [contract for contract in self.contracts.values() if contract.symbol.upper() == symbol]
        return [
            {
                "conid": contract.conid,
                "symbol": contract.symbol,
                "description": f"{contract.symbol} {contract.sec_type}",
                "sections": [{"secType": contract.sec_type}],
            }
            for contract in matches
        ]

    def contract_info(self, conid: int) -> dict[str, object]:
        contract = self.contracts[conid]
        return contract.model_dump(mode="json", by_alias=True)
