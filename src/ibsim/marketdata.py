from __future__ import annotations

import math
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from ibsim.clock import SimClock
from ibsim.models import Bar, BookLevel, Contract, Quote


class MarketDataProvider(ABC):
    @abstractmethod
    def quote(self, conid: int) -> Quote:
        raise NotImplementedError

    @abstractmethod
    def history(self, conid: int, *, period: str = "1d", bar: str = "1h", outside_rth: bool = False) -> list[Bar]:
        raise NotImplementedError

    @abstractmethod
    def depth(self, conid: int, *, levels: int = 5) -> list[BookLevel]:
        raise NotImplementedError


class ReplayMarketDataProvider(MarketDataProvider):
    """Placeholder interface for lawful replay tapes; v1 ships synthetic data only."""

    def quote(self, conid: int) -> Quote:
        raise NotImplementedError("Replay tapes are not loaded in this simulator profile.")

    def history(self, conid: int, *, period: str = "1d", bar: str = "1h", outside_rth: bool = False) -> list[Bar]:
        raise NotImplementedError("Replay tapes are not loaded in this simulator profile.")

    def depth(self, conid: int, *, levels: int = 5) -> list[BookLevel]:
        raise NotImplementedError("Replay tapes are not loaded in this simulator profile.")


class SyntheticMarketDataProvider(MarketDataProvider):
    def __init__(self, contracts: dict[int, Contract], clock: SimClock) -> None:
        self.contracts = contracts
        self.clock = clock
        self.base_prices = {
            265598: 189.10,
            8314: 171.35,
            12087792: 1.0850,
            495512552: 5365.25,
            900000001: 7.25,
        }

    def _contract(self, conid: int) -> Contract:
        try:
            return self.contracts[conid]
        except KeyError as exc:
            raise KeyError(f"Unknown conid {conid}") from exc

    @staticmethod
    def _parse_duration(value: str) -> timedelta:
        value = value.strip().lower()
        number = int(value[:-1]) if value[:-1].isdigit() else 1
        unit = value[-1]
        if unit == "s":
            return timedelta(seconds=number)
        if unit == "m":
            return timedelta(minutes=number)
        if unit == "h":
            return timedelta(hours=number)
        if unit == "d":
            return timedelta(days=number)
        if unit == "w":
            return timedelta(weeks=number)
        raise ValueError(f"Unsupported duration {value!r}")

    def _spread(self, contract: Contract, mid: float) -> float:
        if contract.sec_type == "CASH":
            return 0.0001
        if contract.sec_type == "FUT":
            return max(contract.min_tick, 0.25)
        if contract.sec_type == "OPT":
            return max(contract.min_tick, mid * 0.015)
        return max(contract.min_tick, mid * 0.0002)

    def _mid(self, conid: int, ts: datetime | None = None) -> float:
        contract = self._contract(conid)
        now = ts or self.clock.now()
        seconds = now.timestamp()
        base = self.base_prices.get(conid, 100.0)
        intraday = math.sin(seconds / 900.0 + (conid % 31)) * base * 0.0018
        slow = math.sin(seconds / 12_000.0 + (conid % 17)) * base * 0.006
        if contract.sec_type == "CASH":
            intraday *= 0.15
            slow *= 0.20
        return max(contract.min_tick, base + intraday + slow)

    def quote(self, conid: int) -> Quote:
        contract = self._contract(conid)
        now = self.clock.now()
        mid = self._mid(conid, now)
        spread = self._spread(contract, mid)
        bid = round(mid - spread / 2, 6)
        ask = round(mid + spread / 2, 6)
        size_base = 100 + ((int(now.timestamp()) + conid) % 9) * 50
        return Quote(
            ts=now,
            conid=conid,
            bid=bid,
            bidSize=size_base,
            ask=ask,
            askSize=size_base + 100,
            last=round(mid, 6),
            lastSize=100 + ((int(now.timestamp()) + conid) % 5) * 25,
            source="synthetic",
        )

    def history(self, conid: int, *, period: str = "1d", bar: str = "1h", outside_rth: bool = False) -> list[Bar]:
        self._contract(conid)
        total = self._parse_duration(period)
        step = self._parse_duration(bar)
        if step.total_seconds() <= 0:
            raise ValueError("bar duration must be positive")
        points = min(1000, max(1, int(total.total_seconds() // step.total_seconds())))
        end = self.clock.now()
        start = end - step * points
        bars: list[Bar] = []
        for idx in range(points):
            bar_start = start + step * idx
            bar_end = bar_start + step
            open_ = self._mid(conid, bar_start)
            close = self._mid(conid, bar_end)
            wave = abs(math.sin((bar_start.timestamp() + conid) / 333.0))
            high = max(open_, close) * (1 + wave * 0.001)
            low = min(open_, close) * (1 - wave * 0.001)
            bars.append(
                Bar(
                    conid=conid,
                    start=bar_start,
                    end=bar_end,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=1_000 + idx * 7 + (conid % 100),
                    source="synthetic",
                )
            )
        return bars

    def depth(self, conid: int, *, levels: int = 5) -> list[BookLevel]:
        contract = self._contract(conid)
        quote = self.quote(conid)
        levels = max(1, min(levels, 20))
        rows: list[BookLevel] = []
        for row in range(levels):
            distance = contract.min_tick * (row + 1)
            size = 100 * (levels - row)
            rows.append(BookLevel(conid=conid, row=row, side="bid", price=round(quote.bid - distance, 6), size=size))
            rows.append(BookLevel(conid=conid, row=row, side="ask", price=round(quote.ask + distance, 6), size=size))
        return rows
