from __future__ import annotations

from ibsim.models import AccountSnapshot, Contract, Execution, OrderSide, Position, Quote
from ibsim.risk import RiskEngine


class AccountService:
    def __init__(self, snapshots: dict[str, AccountSnapshot]) -> None:
        self.snapshots = snapshots
        self.positions: dict[tuple[str, int], Position] = {}

    def account_ids(self) -> list[str]:
        return list(self.snapshots.keys())

    def get(self, account: str) -> AccountSnapshot:
        try:
            return self.snapshots[account]
        except KeyError as exc:
            raise KeyError(f"Unknown account {account}") from exc

    def portfolio_accounts(self) -> list[dict[str, object]]:
        return [
            {
                "accountId": snapshot.account,
                "currency": snapshot.currency,
                "type": "DEMO",
                "tradingType": "STKCASH",
                "businessType": "IBSIM",
            }
            for snapshot in self.snapshots.values()
        ]

    def ledger(self, account: str) -> dict[str, object]:
        snapshot = self.get(account)
        return {
            "BASE": {
                "currency": snapshot.currency,
                "cashbalance": round(snapshot.total_cash_value, 2),
                "stockmarketvalue": round(sum(p.market_value for p in self.positions_for(account)), 2),
                "netliquidationvalue": round(snapshot.net_liquidation, 2),
                "realizedpnl": round(snapshot.realized_pnl, 2),
                "unrealizedpnl": round(snapshot.unrealized_pnl, 2),
            }
        }

    def positions_for(self, account: str) -> list[Position]:
        return [position for (acct, _), position in self.positions.items() if acct == account and position.position != 0]

    def apply_execution(self, execution: Execution, contract: Contract, quote: Quote) -> Position:
        snapshot = self.get(execution.account)
        key = (execution.account, execution.conid)
        current = self.positions.get(key, Position(account=execution.account, conid=execution.conid, position=0, avgCost=0))
        signed_qty = execution.qty if execution.side == OrderSide.BUY else -execution.qty
        old_qty = current.position
        new_qty = old_qty + signed_qty
        realized = current.realized_pnl

        if old_qty == 0 or (old_qty > 0) == (signed_qty > 0):
            total_cost = current.avg_cost * abs(old_qty) + execution.price * abs(signed_qty)
            avg_cost = total_cost / abs(new_qty) if new_qty else 0.0
        else:
            closed_qty = min(abs(old_qty), abs(signed_qty))
            pnl_per_unit = execution.price - current.avg_cost if old_qty > 0 else current.avg_cost - execution.price
            realized += closed_qty * pnl_per_unit - execution.commission
            avg_cost = current.avg_cost if new_qty else 0.0
            if old_qty + signed_qty and (old_qty > 0) != (new_qty > 0):
                avg_cost = execution.price

        cash_delta = execution.price * execution.qty
        if execution.side == OrderSide.BUY:
            snapshot.total_cash_value -= cash_delta + execution.commission
        else:
            snapshot.total_cash_value += cash_delta - execution.commission

        market_price = quote.last
        market_value = new_qty * market_price * (float(contract.multiplier or 1) if contract.sec_type in {"FUT", "OPT"} else 1.0)
        unrealized = (market_price - avg_cost) * new_qty if new_qty else 0.0
        position = Position(
            account=execution.account,
            conid=execution.conid,
            position=new_qty,
            avgCost=avg_cost,
            marketPrice=market_price,
            marketValue=market_value,
            realizedPnl=realized,
            unrealizedPnl=unrealized,
        )
        self.positions[key] = position
        self._revalue_snapshot(snapshot.account)
        return position

    def mark_to_market(self, account: str, quotes: list[Quote]) -> AccountSnapshot:
        by_conid = {quote.conid: quote for quote in quotes}
        for key, position in list(self.positions.items()):
            acct, conid = key
            if acct != account or conid not in by_conid:
                continue
            quote = by_conid[conid]
            position.market_price = quote.last
            position.market_value = position.position * quote.last
            position.unrealized_pnl = (quote.last - position.avg_cost) * position.position
            self.positions[key] = position
        return self._revalue_snapshot(account)

    def _revalue_snapshot(self, account: str) -> AccountSnapshot:
        snapshot = self.get(account)
        positions = self.positions_for(account)
        market_value = sum(position.market_value for position in positions)
        realized = sum(position.realized_pnl for position in positions)
        unrealized = sum(position.unrealized_pnl for position in positions)
        gross_exposure = sum(abs(position.market_value) for position in positions)
        init_margin = gross_exposure * 0.25
        maint_margin = gross_exposure * 0.20
        snapshot.realized_pnl = realized
        snapshot.unrealized_pnl = unrealized
        snapshot.init_margin_req = init_margin
        snapshot.maint_margin_req = maint_margin
        snapshot.net_liquidation = snapshot.total_cash_value + market_value
        snapshot.available_funds = snapshot.net_liquidation - init_margin
        snapshot.excess_liquidity = snapshot.net_liquidation - maint_margin
        snapshot.buying_power = max(0.0, snapshot.available_funds * 2)
        self.snapshots[account] = snapshot
        return snapshot

    def what_if(self, account: str, conid: int, side: OrderSide, quantity: float, price: float, messages: list[str]) -> dict[str, object]:
        snapshot = self.get(account)
        notional = abs(quantity * price)
        commission = RiskEngine.estimate_commission(quantity, price)
        init_after = snapshot.init_margin_req + notional * 0.25
        maint_after = snapshot.maint_margin_req + notional * 0.20
        equity_after = snapshot.net_liquidation - (commission if side == OrderSide.BUY else 0.0)
        return {
            "account": account,
            "conid": conid,
            "side": side,
            "quantity": quantity,
            "notional": notional,
            "estimatedCommission": commission,
            "equityWithLoanAfter": equity_after,
            "initMarginAfter": init_after,
            "maintMarginAfter": maint_after,
            "buyingPowerAfter": max(0.0, (equity_after - init_after) * 2),
            "accepted": not any("blocked" in message.lower() for message in messages),
            "messages": messages,
        }
