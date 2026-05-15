from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable

from pydantic import BaseModel, Field

from ibsim.risk import max_drawdown


class StrategyBacktestResult(BaseModel):
    short_window: int = Field(alias="shortWindow")
    long_window: int = Field(alias="longWindow")
    observations: int
    trades: int
    strategy_return: float = Field(alias="strategyReturn")
    buy_and_hold_return: float = Field(alias="buyAndHoldReturn")
    excess_return: float = Field(alias="excessReturn")
    max_drawdown: float = Field(alias="maxDrawdown")
    warnings: list[str] = Field(default_factory=list)


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return datetime.fromisoformat(str(value))


def _clean_prices(rows: Iterable[dict[str, Any]]) -> list[tuple[datetime, float]]:
    clean: list[tuple[datetime, float]] = []
    for row in rows:
        raw_close = row.get("close", row.get("c"))
        raw_date = row.get("date", row.get("t", row.get("timestamp")))
        if raw_close in (None, "") or raw_date in (None, ""):
            continue
        try:
            close = float(raw_close)
            if math.isnan(close) or close <= 0:
                continue
            clean.append((_parse_date(raw_date), close))
        except (TypeError, ValueError):
            continue
    clean.sort(key=lambda item: item[0])
    deduped: dict[datetime, float] = {}
    for ts, close in clean:
        deduped[ts] = close
    return sorted(deduped.items(), key=lambda item: item[0])


def _sma(values: list[float], index: int, window: int) -> float | None:
    if index + 1 < window:
        return None
    return sum(values[index + 1 - window : index + 1]) / window


def dual_moving_average_backtest(
    rows: Iterable[dict[str, Any]],
    *,
    short_window: int = 20,
    long_window: int = 50,
    allow_short: bool = False,
    transaction_cost_bps: float = 0.0,
) -> StrategyBacktestResult:
    if short_window <= 0 or long_window <= 0:
        raise ValueError("windows must be positive")
    if short_window >= long_window:
        raise ValueError("shortWindow must be smaller than longWindow")

    warnings: list[str] = []
    clean = _clean_prices(rows)
    if len(clean) <= long_window + 1:
        warnings.append("Insufficient clean observations for a stable dual-SMA backtest.")
        return StrategyBacktestResult(
            shortWindow=short_window,
            longWindow=long_window,
            observations=len(clean),
            trades=0,
            strategyReturn=0.0,
            buyAndHoldReturn=0.0,
            excessReturn=0.0,
            maxDrawdown=0.0,
            warnings=warnings,
        )

    prices = [close for _, close in clean]
    raw_signals: list[int] = []
    for idx in range(len(prices)):
        short = _sma(prices, idx, short_window)
        long = _sma(prices, idx, long_window)
        if short is None or long is None:
            raw_signals.append(0)
        elif short > long:
            raw_signals.append(1)
        else:
            raw_signals.append(-1 if allow_short else 0)

    # Shift by one bar so today's signal never sees today's close for today's return.
    positions = [0] + raw_signals[:-1]
    cost = transaction_cost_bps / 10_000
    strategy_log_return = 0.0
    buy_hold_log_return = 0.0
    equity = [1.0]
    trades = 0
    previous_position = 0
    for idx in range(1, len(prices)):
        log_return = math.log(prices[idx] / prices[idx - 1])
        position = positions[idx]
        if position != previous_position:
            trades += 1
            strategy_log_return -= cost
        strategy_log_return += position * log_return
        buy_hold_log_return += log_return
        equity.append(equity[-1] * math.exp(position * log_return - (cost if position != previous_position else 0.0)))
        previous_position = position

    warnings.append("Backtest result is not proof of future edge; paper trading is required before live use.")
    if trades > len(clean) / 5:
        warnings.append("High turnover may make the result fragile after transaction costs and slippage.")

    strategy_return = math.exp(strategy_log_return) - 1
    buy_hold_return = math.exp(buy_hold_log_return) - 1
    return StrategyBacktestResult(
        shortWindow=short_window,
        longWindow=long_window,
        observations=len(clean),
        trades=trades,
        strategyReturn=strategy_return,
        buyAndHoldReturn=buy_hold_return,
        excessReturn=strategy_return - buy_hold_return,
        maxDrawdown=max_drawdown(equity),
        warnings=warnings,
    )
