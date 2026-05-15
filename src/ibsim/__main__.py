from __future__ import annotations

import argparse
import json
import sys

from ibsim.datasets import DatasetCollector
from ibsim.service import SimulatorService


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "gather-dataset":
        gather_dataset(argv[1:])
        return
    if argv and argv[0] == "serve":
        argv = argv[1:]
    parser = argparse.ArgumentParser(description="Run the IB-shaped simulator API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5000, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run("ibsim.adapters.webapi.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)


def gather_dataset(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Gather an initial OHLCV dataset for simulator backtests.")
    parser.add_argument("--out", default="data/initial", help="Output directory for CSV files and manifest.json.")
    parser.add_argument("--provider", default=None, help="synthetic, yahoo, stooq, or external. Defaults to IBSIM_MARKET_DATA_PROVIDER/external.")
    parser.add_argument("--period", default="3y", help="History range such as 1y, 3y, 5y, 730d.")
    parser.add_argument("--bar", default="1d", help="Bar size such as 1d, 1h, 30m.")
    parser.add_argument("--conids", default=None, help="Comma-separated local conids. Defaults to all seeded contracts.")
    parser.add_argument("--outside-rth", action="store_true")
    args = parser.parse_args(argv)
    conids = [int(item) for item in args.conids.split(",") if item.strip()] if args.conids else None
    service = SimulatorService(market_data_provider=args.provider)
    manifest = DatasetCollector(service).gather(
        out_dir=args.out,
        conids=conids,
        period=args.period,
        bar=args.bar,
        outside_rth=args.outside_rth,
    )
    print(json.dumps({"manifest": f"{args.out.rstrip('/')}/manifest.json", "files": len(manifest["files"]), "errors": manifest["errors"]}, indent=2))


if __name__ == "__main__":
    main()
