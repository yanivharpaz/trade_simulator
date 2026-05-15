from __future__ import annotations

import math
from statistics import mean

from ibsim.models import AccountSnapshot, KellyResult, OrderTicket, OrderType, Quote, RiskCheck, RiskMetricResult


class RiskEngine:
    """Explainable demo risk engine, not a reproduction of IB proprietary margin."""

    def __init__(self, *, max_notional_fraction: float = 0.95, confirmation_fraction: float = 0.25) -> None:
        self.max_notional_fraction = max_notional_fraction
        self.confirmation_fraction = confirmation_fraction

    @staticmethod
    def estimate_commission(quantity: float, price: float) -> float:
        return max(1.0, min(abs(quantity) * 0.005, abs(quantity * price) * 0.01))

    @staticmethod
    def mark_price(ticket: OrderTicket, quote: Quote) -> float:
        if ticket.price is not None:
            return ticket.price
        if ticket.side.value == "BUY":
            return quote.ask
        return quote.bid

    def check_order(self, ticket: OrderTicket, account: AccountSnapshot, quote: Quote) -> RiskCheck:
        price = self.mark_price(ticket, quote)
        notional = abs(price * ticket.quantity)
        fraction = notional / max(account.net_liquidation, 1.0)
        messages: list[str] = []
        message_ids: list[str] = []

        if fraction >= self.max_notional_fraction:
            messages.append(
                f"Order would commit {fraction:.1%} of net liquidation. All-in sizing is blocked by simulator risk guardrails."
            )
            return RiskCheck(
                accepted=False,
                requiresConfirmation=False,
                messages=messages,
                messageIds=["SIM-RISK-ALL-IN"],
                notional=notional,
                notionalFraction=fraction,
            )

        if fraction >= self.confirmation_fraction:
            messages.append(f"Order uses {fraction:.1%} of net liquidation. Confirm intentional position sizing.")
            message_ids.append("SIM-RISK-SIZE")

        if ticket.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}:
            messages.append("You are about to submit a stop order. Stop orders may execute at a materially different price.")
            message_ids.append("SIM-RISK-STOP")

        if ticket.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and ticket.price is not None:
            if ticket.side.value == "BUY" and ticket.price > quote.last * 1.10:
                messages.append("Buy limit price is more than 10% above the current synthetic last price.")
                message_ids.append("SIM-RISK-PRICE")
            if ticket.side.value == "SELL" and ticket.price < quote.last * 0.90:
                messages.append("Sell limit price is more than 10% below the current synthetic last price.")
                message_ids.append("SIM-RISK-PRICE")

        return RiskCheck(
            accepted=True,
            requiresConfirmation=bool(messages),
            messages=messages,
            messageIds=message_ids,
            notional=notional,
            notionalFraction=fraction,
        )


def expected_value(outcomes: list[dict[str, float]]) -> float:
    return sum(item["probability"] * item["return"] for item in outcomes)


def max_drawdown(equity_curve: list[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak)
    return abs(worst)


def risk_metrics(returns: list[float]) -> RiskMetricResult:
    if not returns:
        return RiskMetricResult(
            expectedReturn=0.0,
            lossProbability=0.0,
            expectedLoss=0.0,
            maxDrawdown=0.0,
            lossDistribution={},
        )
    losses = [value for value in returns if value < 0]
    equity = [1.0]
    for value in returns:
        equity.append(equity[-1] * (1.0 + value))
    sorted_losses = sorted(losses)
    distribution = {
        "min": sorted_losses[0] if sorted_losses else 0.0,
        "median": sorted_losses[len(sorted_losses) // 2] if sorted_losses else 0.0,
        "max": sorted_losses[-1] if sorted_losses else 0.0,
    }
    return RiskMetricResult(
        expectedReturn=mean(returns),
        lossProbability=len(losses) / len(returns),
        expectedLoss=mean(losses) if losses else 0.0,
        maxDrawdown=max_drawdown(equity),
        lossDistribution=distribution,
    )


def fractional_kelly(win_probability: float, payoff_odds: float, fraction: float = 0.5) -> KellyResult:
    if not 0 <= win_probability <= 1:
        raise ValueError("winProbability must be between 0 and 1")
    if payoff_odds <= 0:
        raise ValueError("payoffOdds must be positive")
    loss_probability = 1 - win_probability
    full = round(max(0.0, (payoff_odds * win_probability - loss_probability) / payoff_odds), 12)
    applied = max(0.0, min(fraction, 1.0))
    return KellyResult(
        winProbability=win_probability,
        payoffOdds=payoff_odds,
        fullKellyFraction=full,
        appliedFraction=applied,
        recommendedFraction=round(full * applied, 12),
    )
