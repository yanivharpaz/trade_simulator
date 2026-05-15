from __future__ import annotations

import csv
import io
import json
import math
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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


class ExternalDataError(RuntimeError):
    pass


class CompositeMarketDataProvider(MarketDataProvider):
    """Try external data first, then fall back to synthetic demo data."""

    def __init__(self, providers: list[MarketDataProvider]) -> None:
        if not providers:
            raise ValueError("At least one provider is required.")
        self.providers = providers
        self.last_error: str | None = None

    def quote(self, conid: int) -> Quote:
        for provider in self.providers:
            try:
                return provider.quote(conid)
            except Exception as exc:  # noqa: BLE001 - provider fallback boundary
                self.last_error = f"{provider.__class__.__name__}: {exc}"
        raise ExternalDataError(self.last_error or "No provider could return a quote.")

    def history(self, conid: int, *, period: str = "1d", bar: str = "1h", outside_rth: bool = False) -> list[Bar]:
        for provider in self.providers:
            try:
                return provider.history(conid, period=period, bar=bar, outside_rth=outside_rth)
            except Exception as exc:  # noqa: BLE001 - provider fallback boundary
                self.last_error = f"{provider.__class__.__name__}: {exc}"
        raise ExternalDataError(self.last_error or "No provider could return history.")

    def depth(self, conid: int, *, levels: int = 5) -> list[BookLevel]:
        for provider in self.providers:
            try:
                return provider.depth(conid, levels=levels)
            except Exception as exc:  # noqa: BLE001 - provider fallback boundary
                self.last_error = f"{provider.__class__.__name__}: {exc}"
        raise ExternalDataError(self.last_error or "No provider could return depth.")


class YahooFinanceMarketDataProvider(MarketDataProvider):
    """Best-effort Yahoo Finance chart provider using IB-shaped conids locally.

    Yahoo does not provide IB conids, order-book depth, or IB field tags. This
    provider only sources prices/bars and the simulator maps them back into the
    same IB-shaped API responses as the synthetic provider.
    """

    def __init__(
        self,
        contracts: dict[int, Contract],
        *,
        timeout: float = 2.0,
        ttl_seconds: float = 15.0,
        transport: Any | None = None,
    ) -> None:
        self.contracts = contracts
        self.timeout = timeout
        self.ttl_seconds = ttl_seconds
        self.transport = transport or self._default_transport
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

    def _contract(self, conid: int) -> Contract:
        try:
            return self.contracts[conid]
        except KeyError as exc:
            raise KeyError(f"Unknown conid {conid}") from exc

    @staticmethod
    def _default_transport(url: str, timeout: float) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "ibsim/0.1 local simulator"})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-selected market data URL
            return json.loads(response.read().decode("utf-8"))

    def _symbol(self, conid: int) -> str:
        contract = self._contract(conid)
        if contract.data_symbol:
            return contract.data_symbol
        if contract.sec_type == "CASH":
            return f"{contract.symbol}{contract.currency}=X"
        return contract.symbol

    def _fetch_chart(self, symbol: str, *, range_: str, interval: str) -> dict[str, Any]:
        key = (symbol, range_, interval)
        cached = self._cache.get(key)
        now = time.time()
        if cached and now - cached[0] <= self.ttl_seconds:
            return cached[1]
        params = urlencode({"range": range_, "interval": interval, "includePrePost": "true"})
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{params}"
        payload = self.transport(url, self.timeout)
        self._cache[key] = (now, payload)
        return payload

    @staticmethod
    def _chart_result(payload: dict[str, Any]) -> dict[str, Any]:
        error = payload.get("chart", {}).get("error")
        if error:
            raise ExternalDataError(str(error))
        results = payload.get("chart", {}).get("result") or []
        if not results:
            raise ExternalDataError("Yahoo returned no chart data.")
        return results[0]

    @staticmethod
    def _range_interval(period: str, bar: str) -> tuple[str, str]:
        period = period.lower()
        bar = bar.lower()
        if bar.endswith("s"):
            interval = "1m"
        elif bar in {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}:
            interval = "60m" if bar == "1h" else bar
        elif bar.endswith("h"):
            interval = "60m"
        elif bar.endswith("d"):
            interval = "1d"
        else:
            interval = "1d"

        if period.endswith("w"):
            range_ = f"{max(1, int(period[:-1] or 1) * 7)}d"
        elif period.endswith(("d", "mo", "y")):
            range_ = period
        elif period.endswith("h"):
            range_ = "5d"
        else:
            range_ = "1d"
        return range_, interval

    def quote(self, conid: int) -> Quote:
        contract = self._contract(conid)
        result = self._chart_result(self._fetch_chart(self._symbol(conid), range_="1d", interval="1m"))
        meta = result.get("meta", {})
        price = _first_number(meta.get("regularMarketPrice"), meta.get("previousClose"))
        timestamps = result.get("timestamp") or []
        indicators = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = indicators.get("close") or []
        volumes = indicators.get("volume") or []
        for close in reversed(closes):
            if close is not None:
                price = float(close)
                break
        if price is None:
            raise ExternalDataError("Yahoo returned no usable price.")
        ts = datetime.fromtimestamp(timestamps[-1], timezone.utc) if timestamps else datetime.now(timezone.utc)
        volume = _last_number(volumes, 100.0)
        spread = _spread_for(contract, price)
        return Quote(
            ts=ts,
            conid=conid,
            bid=round(price - spread / 2, 6),
            bidSize=max(100.0, volume / 100),
            ask=round(price + spread / 2, 6),
            askSize=max(100.0, volume / 100),
            last=round(price, 6),
            lastSize=max(1.0, volume / 1000),
            source="yahoo",
            delayed=True,
        )

    def history(self, conid: int, *, period: str = "1d", bar: str = "1h", outside_rth: bool = False) -> list[Bar]:
        symbol = self._symbol(conid)
        range_, interval = self._range_interval(period, bar)
        result = self._chart_result(self._fetch_chart(symbol, range_=range_, interval=interval))
        timestamps = result.get("timestamp") or []
        indicators = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens = indicators.get("open") or []
        highs = indicators.get("high") or []
        lows = indicators.get("low") or []
        closes = indicators.get("close") or []
        volumes = indicators.get("volume") or []
        bars: list[Bar] = []
        for idx, raw_ts in enumerate(timestamps[:1000]):
            values = [opens, highs, lows, closes]
            if any(idx >= len(items) or items[idx] is None for items in values):
                continue
            start = datetime.fromtimestamp(raw_ts, timezone.utc)
            end = timestamps[idx + 1] if idx + 1 < len(timestamps) else raw_ts
            bars.append(
                Bar(
                    conid=conid,
                    start=start,
                    end=datetime.fromtimestamp(end, timezone.utc),
                    open=float(opens[idx]),
                    high=float(highs[idx]),
                    low=float(lows[idx]),
                    close=float(closes[idx]),
                    volume=float(volumes[idx] or 0) if idx < len(volumes) else 0.0,
                    source="yahoo",
                )
            )
        if not bars:
            raise ExternalDataError("Yahoo returned no usable bars.")
        return bars[-1000:]

    def depth(self, conid: int, *, levels: int = 5) -> list[BookLevel]:
        quote = self.quote(conid)
        contract = self._contract(conid)
        return _synthetic_depth_from_quote(contract, quote, levels=levels)


class StooqMarketDataProvider(MarketDataProvider):
    """Best-effort daily quote/history provider using Stooq CSV data."""

    def __init__(
        self,
        contracts: dict[int, Contract],
        *,
        timeout: float = 2.0,
        ttl_seconds: float = 60.0,
        transport: Any | None = None,
    ) -> None:
        self.contracts = contracts
        self.timeout = timeout
        self.ttl_seconds = ttl_seconds
        self.transport = transport or self._default_transport
        self._cache: dict[str, tuple[float, str]] = {}

    @staticmethod
    def _default_transport(url: str, timeout: float) -> str:
        request = Request(url, headers={"User-Agent": "ibsim/0.1 local simulator"})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-selected market data URL
            return response.read().decode("utf-8")

    def _contract(self, conid: int) -> Contract:
        try:
            return self.contracts[conid]
        except KeyError as exc:
            raise KeyError(f"Unknown conid {conid}") from exc

    def _symbol(self, conid: int) -> str:
        contract = self._contract(conid)
        if contract.sec_type == "STK":
            return f"{contract.symbol.lower()}.us"
        if contract.sec_type == "CASH":
            return f"{contract.symbol.lower()}{contract.currency.lower()}"
        raise ExternalDataError(f"Stooq provider does not support {contract.sec_type} for {contract.symbol}.")

    def _fetch_csv(self, symbol: str) -> str:
        cached = self._cache.get(symbol)
        now = time.time()
        if cached and now - cached[0] <= self.ttl_seconds:
            return cached[1]
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        text = self.transport(url, self.timeout)
        self._cache[symbol] = (now, text)
        return text

    def _bars(self, conid: int) -> list[Bar]:
        symbol = self._symbol(conid)
        rows = list(csv.DictReader(io.StringIO(self._fetch_csv(symbol))))
        bars: list[Bar] = []
        for row in rows:
            if row.get("Close") in {None, "", "N/D"}:
                continue
            start = datetime.fromisoformat(row["Date"]).replace(tzinfo=timezone.utc)
            bars.append(
                Bar(
                    conid=conid,
                    start=start,
                    end=start + timedelta(days=1),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume") or 0),
                    source="stooq",
                )
            )
        if not bars:
            raise ExternalDataError("Stooq returned no usable bars.")
        return bars

    def quote(self, conid: int) -> Quote:
        contract = self._contract(conid)
        bar = self._bars(conid)[-1]
        spread = _spread_for(contract, bar.close)
        return Quote(
            ts=bar.end,
            conid=conid,
            bid=round(bar.close - spread / 2, 6),
            bidSize=max(100.0, bar.volume / 100),
            ask=round(bar.close + spread / 2, 6),
            askSize=max(100.0, bar.volume / 100),
            last=round(bar.close, 6),
            lastSize=max(1.0, bar.volume / 1000),
            source="stooq",
            delayed=True,
        )

    def history(self, conid: int, *, period: str = "1d", bar: str = "1h", outside_rth: bool = False) -> list[Bar]:
        return self._bars(conid)[-1000:]

    def depth(self, conid: int, *, levels: int = 5) -> list[BookLevel]:
        quote = self.quote(conid)
        contract = self._contract(conid)
        return _synthetic_depth_from_quote(contract, quote, levels=levels)


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


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value is not None:
            return float(value)
    return None


def _last_number(values: list[Any], default: float) -> float:
    for value in reversed(values):
        if value is not None:
            return float(value)
    return default


def _spread_for(contract: Contract, price: float) -> float:
    if contract.sec_type == "CASH":
        return max(contract.min_tick, 0.0001)
    if contract.sec_type == "FUT":
        return max(contract.min_tick, 0.25)
    if contract.sec_type == "OPT":
        return max(contract.min_tick, price * 0.02)
    return max(contract.min_tick, price * 0.0005)


def _synthetic_depth_from_quote(contract: Contract, quote: Quote, *, levels: int = 5) -> list[BookLevel]:
    levels = max(1, min(levels, 20))
    rows: list[BookLevel] = []
    for row in range(levels):
        distance = contract.min_tick * (row + 1)
        size = 100 * (levels - row)
        rows.append(BookLevel(conid=quote.conid, row=row, side="bid", price=round(quote.bid - distance, 6), size=size))
        rows.append(BookLevel(conid=quote.conid, row=row, side="ask", price=round(quote.ask + distance, 6), size=size))
    return rows
