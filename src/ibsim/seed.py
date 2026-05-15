from __future__ import annotations

from ibsim.models import AccountSnapshot, Contract


DEFAULT_ACCOUNT = "U1234567"


def seeded_contracts() -> dict[int, Contract]:
    return {
        265598: Contract(
            conid=265598,
            symbol="AAPL",
            secType="STK",
            exchange="SMART",
            primaryExchange="NASDAQ",
            currency="USD",
            tradingClass="NMS",
            minTick=0.01,
            marketRuleIds=[26],
            tradingHours=["20260515:0930-1600"],
            liquidHours=["20260515:0930-1600"],
        ),
        8314: Contract(
            conid=8314,
            symbol="IBM",
            secType="STK",
            exchange="SMART",
            primaryExchange="NYSE",
            currency="USD",
            tradingClass="IBM",
            minTick=0.01,
            marketRuleIds=[26],
            tradingHours=["20260515:0930-1600"],
            liquidHours=["20260515:0930-1600"],
        ),
        12087792: Contract(
            conid=12087792,
            symbol="EUR",
            secType="CASH",
            exchange="IDEALPRO",
            currency="USD",
            tradingClass="EUR.USD",
            minTick=0.00005,
            marketRuleIds=[120],
            tradingHours=["20260515:0000-2359"],
            liquidHours=["20260515:0000-2359"],
        ),
        495512552: Contract(
            conid=495512552,
            symbol="ES",
            secType="FUT",
            exchange="CME",
            currency="USD",
            tradingClass="ES",
            minTick=0.25,
            marketRuleIds=[32],
            lastTradeDateOrContractMonth="202606",
            multiplier="50",
            tradingHours=["20260515:1700-1600"],
            liquidHours=["20260515:0830-1515"],
        ),
        900000001: Contract(
            conid=900000001,
            symbol="SPY",
            secType="OPT",
            exchange="SMART",
            currency="USD",
            tradingClass="SPY",
            minTick=0.01,
            marketRuleIds=[26],
            lastTradeDateOrContractMonth="20260619",
            strike=500.0,
            right="C",
            multiplier="100",
            tradingHours=["20260515:0930-1600"],
            liquidHours=["20260515:0930-1600"],
        ),
    }


def seeded_account(account: str = DEFAULT_ACCOUNT) -> AccountSnapshot:
    return AccountSnapshot(
        account=account,
        currency="USD",
        netLiquidation=100_000.0,
        totalCashValue=100_000.0,
        buyingPower=200_000.0,
        initMarginReq=0.0,
        maintMarginReq=0.0,
        availableFunds=100_000.0,
        excessLiquidity=100_000.0,
    )
