from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ClockMode(str, Enum):
    WALL = "wall"
    PAUSED = "paused"
    COMPRESSED = "compressed"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP LMT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


class OrderStatus(str, Enum):
    API_PENDING = "ApiPending"
    PENDING_SUBMIT = "PendingSubmit"
    PRE_SUBMITTED = "PreSubmitted"
    SUBMITTED = "Submitted"
    PENDING_CANCEL = "PendingCancel"
    CANCELLED = "Cancelled"
    FILLED = "Filled"
    INACTIVE = "Inactive"


class Contract(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conid: int
    symbol: str
    sec_type: str = Field(alias="secType")
    exchange: str = "SMART"
    primary_exchange: str | None = Field(default=None, alias="primaryExchange")
    currency: str = "USD"
    trading_class: str | None = Field(default=None, alias="tradingClass")
    min_tick: float = Field(default=0.01, alias="minTick")
    market_rule_ids: list[int] = Field(default_factory=list, alias="marketRuleIds")
    time_zone_id: str = Field(default="US/Eastern", alias="timeZoneId")
    trading_hours: list[str] = Field(default_factory=list, alias="tradingHours")
    liquid_hours: list[str] = Field(default_factory=list, alias="liquidHours")
    last_trade_date_or_contract_month: str | None = Field(default=None, alias="lastTradeDateOrContractMonth")
    strike: float | None = None
    right: str | None = None
    multiplier: str | None = None
    data_symbol: str | None = Field(default=None, alias="dataSymbol")


class Quote(BaseModel):
    ts: datetime = Field(default_factory=utc_now)
    conid: int
    bid: float
    bid_size: float = Field(alias="bidSize")
    ask: float
    ask_size: float = Field(alias="askSize")
    last: float
    last_size: float = Field(alias="lastSize")
    source: Literal["synthetic", "replay", "hybrid", "yahoo", "stooq", "external"] = "synthetic"
    delayed: bool = False
    stale: bool = False
    server_id: str = "q1"

    model_config = ConfigDict(populate_by_name=True)

    def to_web_fields(self, fields: list[str] | None = None) -> dict[str, Any]:
        values: dict[str, Any] = {
            "31": f"{self.last:.4f}".rstrip("0").rstrip("."),
            "84": f"{self.bid:.4f}".rstrip("0").rstrip("."),
            "85": str(int(self.bid_size)),
            "86": f"{self.ask:.4f}".rstrip("0").rstrip("."),
            "88": str(int(self.ask_size)),
            "7059": str(int(self.last_size)),
            "_updated": int(self.ts.timestamp() * 1000),
            "conid": self.conid,
            "conidEx": str(self.conid),
            "server_id": self.server_id,
        }
        requested = fields or ["31", "84", "85", "86", "88", "7059"]
        filtered = {field: values[field] for field in requested if field in values}
        filtered.update({"_updated": values["_updated"], "conid": self.conid, "conidEx": str(self.conid), "server_id": self.server_id})
        return filtered


class Bar(BaseModel):
    conid: int
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: Literal["synthetic", "replay", "hybrid", "yahoo", "stooq", "external"] = "synthetic"

    def to_web(self) -> dict[str, Any]:
        return {
            "t": int(self.start.timestamp() * 1000),
            "o": round(self.open, 6),
            "h": round(self.high, 6),
            "l": round(self.low, 6),
            "c": round(self.close, 6),
            "v": int(self.volume),
        }


class BookLevel(BaseModel):
    conid: int
    row: int
    side: Literal["bid", "ask"]
    price: float
    size: float
    exchange: str = "SIM"
    ts: datetime = Field(default_factory=utc_now)


class OrderTicket(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conid: int
    side: OrderSide
    order_type: OrderType = Field(alias="orderType")
    tif: TimeInForce = TimeInForce.DAY
    quantity: float
    price: float | None = None
    aux_price: float | None = Field(default=None, alias="auxPrice")
    outside_rth: bool = Field(default=False, alias="outsideRTH")
    manual_indicator: bool = Field(default=True, alias="manualIndicator")
    c_oid: str | None = Field(default=None, alias="cOID")
    parent_id: int | None = Field(default=None, alias="parentId")
    transmit: bool = True
    what_if: bool = Field(default=False, alias="whatIf")

    @field_validator("side", mode="before")
    @classmethod
    def normalize_side(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("order_type", mode="before")
    @classmethod
    def normalize_order_type(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("tif", mode="before")
    @classmethod
    def normalize_tif(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_order_fields(self) -> "OrderTicket":
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and self.price is None:
            raise ValueError(f"{self.order_type.value} orders require price")
        if self.order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and self.aux_price is None:
            raise ValueError(f"{self.order_type.value} orders require auxPrice")
        return self


class Execution(BaseModel):
    exec_id: str = Field(default_factory=lambda: f"SIM.{uuid4().hex[:12]}", alias="execId")
    order_id: int = Field(alias="orderId")
    perm_id: int = Field(alias="permId")
    account: str
    conid: int
    side: OrderSide
    price: float
    qty: float
    commission: float
    liquidity: Literal["TAKER", "MAKER", "SIMULATED"] = "SIMULATED"
    ts: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(populate_by_name=True)


class OrderState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    order_id: int = Field(alias="orderId")
    perm_id: int = Field(alias="permId")
    client_id: int = Field(default=0, alias="clientId")
    account: str
    ticket: OrderTicket
    status: OrderStatus
    filled: float = 0.0
    remaining: float
    avg_fill_price: float = Field(default=0.0, alias="avgFillPrice")
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now, alias="createdAt")
    updated_at: datetime = Field(default_factory=utc_now, alias="updatedAt")

    @property
    def is_open(self) -> bool:
        return self.status not in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.INACTIVE}


class Position(BaseModel):
    account: str
    conid: int
    position: float
    avg_cost: float = Field(alias="avgCost")
    market_price: float = Field(default=0.0, alias="marketPrice")
    market_value: float = Field(default=0.0, alias="marketValue")
    realized_pnl: float = Field(default=0.0, alias="realizedPnl")
    unrealized_pnl: float = Field(default=0.0, alias="unrealizedPnl")

    model_config = ConfigDict(populate_by_name=True)


class AccountSnapshot(BaseModel):
    account: str
    currency: str = "USD"
    net_liquidation: float = Field(alias="netLiquidation")
    total_cash_value: float = Field(alias="totalCashValue")
    buying_power: float = Field(alias="buyingPower")
    init_margin_req: float = Field(alias="initMarginReq")
    maint_margin_req: float = Field(alias="maintMarginReq")
    available_funds: float = Field(alias="availableFunds")
    excess_liquidity: float = Field(alias="excessLiquidity")
    realized_pnl: float = Field(default=0.0, alias="realizedPnl")
    unrealized_pnl: float = Field(default=0.0, alias="unrealizedPnl")
    timestamp: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(populate_by_name=True)


class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid4().hex, alias="session")
    username: str = "demo-user"
    authenticated: bool = True
    connected: bool = True
    brokerage_authenticated: bool = Field(default=True, alias="brokerageAuthenticated")
    competing: bool = False
    created_at: datetime = Field(default_factory=utc_now, alias="createdAt")
    last_tickle_at: datetime = Field(default_factory=utc_now, alias="lastTickleAt")
    expires_at: datetime = Field(alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True)


class EventEnvelope(BaseModel):
    event_id: int | None = Field(default=None, alias="eventId")
    event_type: str = Field(alias="eventType")
    aggregate_id: str | None = Field(default=None, alias="aggregateId")
    ts: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class ScenarioStep(BaseModel):
    at: datetime | None = None
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RiskCheck(BaseModel):
    accepted: bool = True
    requires_confirmation: bool = Field(default=False, alias="requiresConfirmation")
    messages: list[str] = Field(default_factory=list)
    message_ids: list[str] = Field(default_factory=list, alias="messageIds")
    notional: float = 0.0
    notional_fraction: float = Field(default=0.0, alias="notionalFraction")

    model_config = ConfigDict(populate_by_name=True)


class WhatIfResult(BaseModel):
    account: str
    conid: int
    side: OrderSide
    quantity: float
    notional: float
    estimated_commission: float = Field(alias="estimatedCommission")
    equity_with_loan_after: float = Field(alias="equityWithLoanAfter")
    init_margin_after: float = Field(alias="initMarginAfter")
    maint_margin_after: float = Field(alias="maintMarginAfter")
    buying_power_after: float = Field(alias="buyingPowerAfter")
    accepted: bool
    messages: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class RiskMetricResult(BaseModel):
    expected_return: float = Field(alias="expectedReturn")
    loss_probability: float = Field(alias="lossProbability")
    expected_loss: float = Field(alias="expectedLoss")
    max_drawdown: float = Field(alias="maxDrawdown")
    loss_distribution: dict[str, float] = Field(alias="lossDistribution")

    model_config = ConfigDict(populate_by_name=True)


class KellyResult(BaseModel):
    win_probability: float = Field(alias="winProbability")
    payoff_odds: float = Field(alias="payoffOdds")
    full_kelly_fraction: float = Field(alias="fullKellyFraction")
    applied_fraction: float = Field(alias="appliedFraction")
    recommended_fraction: float = Field(alias="recommendedFraction")

    model_config = ConfigDict(populate_by_name=True)
