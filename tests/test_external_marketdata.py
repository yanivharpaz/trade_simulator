from __future__ import annotations

from ibsim.clock import SimClock
from ibsim.marketdata import CompositeMarketDataProvider, ExternalDataError, SyntheticMarketDataProvider, YahooFinanceMarketDataProvider
from ibsim.seed import seeded_contracts


def test_yahoo_provider_maps_external_chart_to_ib_quote_fields() -> None:
    contracts = seeded_contracts()

    def fake_transport(_url: str, _timeout: float) -> dict[str, object]:
        return {
            "chart": {
                "result": [
                    {
                        "meta": {"regularMarketPrice": 190.0, "previousClose": 189.0},
                        "timestamp": [1_800_000_000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [189.0],
                                    "high": [191.0],
                                    "low": [188.0],
                                    "close": [190.5],
                                    "volume": [123_000],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }

    provider = YahooFinanceMarketDataProvider(contracts, transport=fake_transport)
    quote = provider.quote(265598)
    fields = quote.to_web_fields(["31", "84", "86"])
    assert quote.source == "yahoo"
    assert fields["31"] == "190.5"
    assert fields["conid"] == 265598


def test_composite_provider_falls_back_to_synthetic() -> None:
    contracts = seeded_contracts()
    synthetic = SyntheticMarketDataProvider(contracts, SimClock())

    class BrokenProvider(YahooFinanceMarketDataProvider):
        def quote(self, conid: int):  # type: ignore[no-untyped-def]
            raise ExternalDataError("offline")

    provider = CompositeMarketDataProvider([BrokenProvider(contracts), synthetic])
    quote = provider.quote(265598)
    assert quote.source == "synthetic"
    assert provider.last_error is not None
