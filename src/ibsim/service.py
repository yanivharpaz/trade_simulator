from __future__ import annotations

import os
from pathlib import Path

from ibsim.accounts import AccountService
from ibsim.clock import SimClock
from ibsim.events import SQLiteEventStore
from ibsim.marketdata import (
    CSVReplayMarketDataProvider,
    CompositeMarketDataProvider,
    MarketDataProvider,
    StooqMarketDataProvider,
    SyntheticMarketDataProvider,
    YahooFinanceMarketDataProvider,
)
from ibsim.orders import OrderService
from ibsim.risk import RiskEngine
from ibsim.seed import DEFAULT_ACCOUNT, seeded_account, seeded_contracts
from ibsim.sessions import SessionManager


class SimulatorService:
    def __init__(
        self,
        *,
        event_store_path: str | Path = ":memory:",
        market_data_provider: str | MarketDataProvider | None = None,
    ) -> None:
        self.clock = SimClock()
        self.events = SQLiteEventStore(event_store_path)
        self.contracts = seeded_contracts()
        self.accounts = AccountService({DEFAULT_ACCOUNT: seeded_account(DEFAULT_ACCOUNT)})
        self.risk = RiskEngine()
        self.market_data_provider_name = market_data_provider if isinstance(market_data_provider, str) else None
        self.market_data = self._build_market_data_provider(market_data_provider)
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

    def _build_market_data_provider(self, provider: str | MarketDataProvider | None) -> MarketDataProvider:
        if isinstance(provider, MarketDataProvider):
            return provider
        raw_provider = provider or os.getenv("IBSIM_MARKET_DATA_PROVIDER") or "external"
        provider_name = raw_provider.lower()
        synthetic = SyntheticMarketDataProvider(self.contracts, self.clock)
        if provider_name == "synthetic":
            self.market_data_provider_name = "synthetic"
            return synthetic
        if provider_name == "replay":
            dataset_dir = os.getenv("IBSIM_REPLAY_DIR", "data/initial")
            self.market_data_provider_name = f"replay:{dataset_dir}"
            return CompositeMarketDataProvider([CSVReplayMarketDataProvider(self.contracts, dataset_dir), synthetic])
        if provider_name.startswith(("replay:", "csv:")):
            dataset_dir = raw_provider.split(":", 1)[1]
            self.market_data_provider_name = f"replay:{dataset_dir}"
            return CompositeMarketDataProvider([CSVReplayMarketDataProvider(self.contracts, dataset_dir), synthetic])
        if provider_name == "yahoo":
            self.market_data_provider_name = "yahoo"
            return CompositeMarketDataProvider([YahooFinanceMarketDataProvider(self.contracts), synthetic])
        if provider_name == "stooq":
            self.market_data_provider_name = "stooq"
            return CompositeMarketDataProvider([StooqMarketDataProvider(self.contracts), synthetic])
        if provider_name == "external":
            self.market_data_provider_name = "external"
            return CompositeMarketDataProvider(
                [
                    YahooFinanceMarketDataProvider(self.contracts),
                    StooqMarketDataProvider(self.contracts),
                    synthetic,
                ]
            )
        raise ValueError("market_data_provider must be one of synthetic, yahoo, stooq, external, replay, replay:/path, or a MarketDataProvider")
