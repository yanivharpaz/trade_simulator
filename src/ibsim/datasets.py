from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ibsim.models import Bar
from ibsim.service import SimulatorService


@dataclass(frozen=True)
class DatasetFile:
    conid: int
    symbol: str
    sec_type: str
    path: Path
    bars: int
    first_ts: str | None
    last_ts: str | None
    sources: dict[str, int]


class DatasetCollector:
    def __init__(self, service: SimulatorService) -> None:
        self.service = service

    def gather(
        self,
        *,
        out_dir: str | Path,
        conids: list[int] | None = None,
        period: str = "3y",
        bar: str = "1d",
        outside_rth: bool = False,
    ) -> dict[str, Any]:
        destination = Path(out_dir)
        destination.mkdir(parents=True, exist_ok=True)
        selected = conids or sorted(self.service.contracts)
        files: list[DatasetFile] = []
        errors: dict[str, str] = {}
        for conid in selected:
            contract = self.service.contracts.get(conid)
            if contract is None:
                errors[str(conid)] = "Unknown conid"
                continue
            try:
                bars = self.service.market_data.history(conid, period=period, bar=bar, outside_rth=outside_rth)
            except Exception as exc:  # noqa: BLE001 - dataset collection should continue per symbol
                errors[str(conid)] = str(exc)
                continue
            filename = _dataset_filename(contract.symbol, conid, bar)
            path = destination / filename
            self.write_bars(path, bars)
            files.append(
                DatasetFile(
                    conid=conid,
                    symbol=contract.symbol,
                    sec_type=contract.sec_type,
                    path=path,
                    bars=len(bars),
                    first_ts=bars[0].start.isoformat() if bars else None,
                    last_ts=bars[-1].end.isoformat() if bars else None,
                    sources=dict(Counter(bar_.source for bar_ in bars)),
                )
            )

        manifest = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "provider": self.service.market_data_provider_name,
            "activeProviderClass": self.service.market_data.__class__.__name__,
            "period": period,
            "bar": bar,
            "outsideRth": outside_rth,
            "files": [
                {
                    "conid": item.conid,
                    "symbol": item.symbol,
                    "secType": item.sec_type,
                    "path": str(item.path),
                    "bars": item.bars,
                    "firstTs": item.first_ts,
                    "lastTs": item.last_ts,
                    "sources": item.sources,
                }
                for item in files
            ],
            "errors": errors,
            "lastFallbackError": getattr(self.service.market_data, "last_error", None),
            "note": "External datasets are mapped into local IB-shaped contracts; they are not IB market data.",
        }
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest

    @staticmethod
    def write_bars(path: str | Path, bars: list[Bar]) -> None:
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "conid",
                    "start",
                    "end",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "source",
                ],
            )
            writer.writeheader()
            for bar in bars:
                writer.writerow(
                    {
                        "conid": bar.conid,
                        "start": bar.start.isoformat(),
                        "end": bar.end.isoformat(),
                        "open": f"{bar.open:.10g}",
                        "high": f"{bar.high:.10g}",
                        "low": f"{bar.low:.10g}",
                        "close": f"{bar.close:.10g}",
                        "volume": f"{bar.volume:.10g}",
                        "source": bar.source,
                    }
                )


def _dataset_filename(symbol: str, conid: int, bar: str) -> str:
    safe_symbol = "".join(ch.lower() if ch.isalnum() else "_" for ch in symbol).strip("_")
    safe_bar = "".join(ch.lower() if ch.isalnum() else "_" for ch in bar).strip("_")
    return f"{safe_symbol}_{conid}_{safe_bar}.csv"
