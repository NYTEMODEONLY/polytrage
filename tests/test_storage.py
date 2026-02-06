"""Tests for trade persistence."""

import json

import pytest

from polytrage.config import StorageSettings
from polytrage.storage import TradeRecord, TradeStore


def _settings(tmp_path, **kwargs) -> StorageSettings:
    defaults = {
        "enabled": True,
        "trades_file": str(tmp_path / "trades.jsonl"),
        "max_memory": 1000,
    }
    defaults.update(kwargs)
    return StorageSettings(**defaults)


class TestTradeRecord:
    def test_roundtrip(self):
        record = TradeRecord(
            timestamp=1000.0,
            market_id="m1",
            market_question="Will it happen?",
            total_cost=0.95,
            net_profit=0.049,
            roi_pct=5.16,
        )
        d = record.to_dict()
        restored = TradeRecord.from_dict(d)
        assert restored.market_id == "m1"
        assert restored.total_cost == 0.95
        assert restored.net_profit == 0.049


class TestTradeStore:
    def test_record_trade(self, tmp_path):
        store = TradeStore(_settings(tmp_path))
        record = store.record(
            market_id="m1",
            market_question="Test?",
            total_cost=0.90,
            net_profit=0.098,
            roi_pct=10.89,
        )

        assert record.market_id == "m1"
        assert store.trade_count == 1
        assert store.total_invested == pytest.approx(0.90)
        assert store.total_profit == pytest.approx(0.098)

    def test_multiple_trades(self, tmp_path):
        store = TradeStore(_settings(tmp_path))
        store.record(market_id="m1", market_question="Q1",
                     total_cost=0.90, net_profit=0.098, roi_pct=10.89)
        store.record(market_id="m2", market_question="Q2",
                     total_cost=0.80, net_profit=0.196, roi_pct=24.50)

        assert store.trade_count == 2
        assert store.total_invested == pytest.approx(1.70)
        assert store.total_profit == pytest.approx(0.294)

    def test_roi_calculation(self, tmp_path):
        store = TradeStore(_settings(tmp_path))
        store.record(market_id="m1", market_question="Q",
                     total_cost=1.0, net_profit=0.10, roi_pct=10.0)
        assert store.total_roi_pct == pytest.approx(10.0)

    def test_empty_roi(self, tmp_path):
        store = TradeStore(_settings(tmp_path))
        assert store.total_roi_pct == 0.0

    def test_persistence_to_disk(self, tmp_path):
        settings = _settings(tmp_path)
        store = TradeStore(settings)
        store.record(market_id="m1", market_question="Q",
                     total_cost=0.95, net_profit=0.049, roi_pct=5.16)

        # Read the file directly
        lines = open(settings.trades_file).readlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["market_id"] == "m1"

    def test_load_from_disk(self, tmp_path):
        settings = _settings(tmp_path)

        # Write trades manually
        trades_file = tmp_path / "trades.jsonl"
        records = [
            {"timestamp": 1000.0, "market_id": "m1", "market_question": "Q1",
             "total_cost": 0.90, "net_profit": 0.098, "roi_pct": 10.89},
            {"timestamp": 1001.0, "market_id": "m2", "market_question": "Q2",
             "total_cost": 0.80, "net_profit": 0.196, "roi_pct": 24.50},
        ]
        with open(trades_file, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        store = TradeStore(settings)
        store.load()

        assert store.trade_count == 2
        assert store.total_invested == pytest.approx(1.70)
        assert store.total_profit == pytest.approx(0.294)
        assert len(store.trades) == 2

    def test_memory_cap(self, tmp_path):
        settings = _settings(tmp_path, max_memory=3)
        store = TradeStore(settings)

        for i in range(5):
            store.record(market_id=f"m{i}", market_question=f"Q{i}",
                         total_cost=1.0, net_profit=0.01, roi_pct=1.0)

        # Only 3 in memory, but all 5 counted
        assert len(store.trades) == 3
        assert store.trade_count == 5
        # Most recent trades kept
        assert store.trades[0].market_id == "m2"
        assert store.trades[-1].market_id == "m4"

    def test_load_skips_malformed_lines(self, tmp_path):
        settings = _settings(tmp_path)
        trades_file = tmp_path / "trades.jsonl"
        trades_file.write_text(
            '{"timestamp":1000,"market_id":"m1","market_question":"Q",'
            '"total_cost":0.9,"net_profit":0.1,"roi_pct":11.1}\n'
            'not json\n'
            '{"missing": "fields"}\n'
        )

        store = TradeStore(settings)
        store.load()
        assert store.trade_count == 1  # Only the valid line

    def test_disabled_storage(self, tmp_path):
        settings = _settings(tmp_path, enabled=False)
        store = TradeStore(settings)
        store.record(market_id="m1", market_question="Q",
                     total_cost=0.9, net_profit=0.1, roi_pct=11.0)

        # Trade recorded in memory but not written to disk
        assert store.trade_count == 1
        assert not (tmp_path / "trades.jsonl").exists()

    def test_load_disabled(self, tmp_path):
        settings = _settings(tmp_path, enabled=False)
        store = TradeStore(settings)
        store.load()  # Should not raise
        assert store.trade_count == 0
