from __future__ import annotations

import csv
import json

from ibsim.datasets import DatasetCollector
from ibsim.marketdata import CSVReplayMarketDataProvider
from ibsim.seed import seeded_contracts
from ibsim.service import SimulatorService


def test_dataset_collector_writes_csv_and_manifest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service = SimulatorService(market_data_provider="synthetic")
    manifest = DatasetCollector(service).gather(out_dir=tmp_path, conids=[265598], period="10d", bar="1d")

    manifest_path = tmp_path / "manifest.json"
    assert manifest_path.exists()
    assert manifest["files"][0]["conid"] == 265598
    assert manifest["files"][0]["bars"] == 10

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    csv_path = tmp_path / "aapl_265598_1d.csv"
    assert payload["files"][0]["path"] == str(csv_path)
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 10
    assert rows[0]["source"] == "synthetic"

    replay = CSVReplayMarketDataProvider(seeded_contracts(), str(tmp_path))
    quote = replay.quote(265598)
    assert quote.conid == 265598
    assert quote.source == "synthetic"
    assert len(replay.history(265598)) == 10
