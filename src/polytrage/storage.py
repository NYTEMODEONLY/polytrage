"""Trade persistence — JSON-lines append-only storage."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from polytrage.config import StorageSettings

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    timestamp: float
    market_id: str
    market_question: str
    total_cost: float
    net_profit: float
    roi_pct: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "market_id": self.market_id,
            "market_question": self.market_question,
            "total_cost": self.total_cost,
            "net_profit": self.net_profit,
            "roi_pct": self.roi_pct,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TradeRecord:
        return cls(
            timestamp=d["timestamp"],
            market_id=d["market_id"],
            market_question=d["market_question"],
            total_cost=d["total_cost"],
            net_profit=d["net_profit"],
            roi_pct=d["roi_pct"],
        )


class TradeStore:
    """Append-only JSON-lines trade storage with capped in-memory buffer."""

    def __init__(self, settings: StorageSettings) -> None:
        self.settings = settings
        self._trades: list[TradeRecord] = []
        self._total_invested: float = 0.0
        self._total_profit: float = 0.0
        self._trade_count: int = 0

    @property
    def trades(self) -> list[TradeRecord]:
        return self._trades

    @property
    def total_invested(self) -> float:
        return self._total_invested

    @property
    def total_profit(self) -> float:
        return self._total_profit

    @property
    def trade_count(self) -> int:
        return self._trade_count

    @property
    def total_roi_pct(self) -> float:
        if self._total_invested <= 0:
            return 0.0
        return (self._total_profit / self._total_invested) * 100.0

    def load(self) -> None:
        """Load existing trades from JSONL file on startup."""
        if not self.settings.enabled:
            return

        path = Path(self.settings.trades_file)
        if not path.exists():
            return

        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        record = TradeRecord.from_dict(d)
                        self._trades.append(record)
                        self._total_invested += record.total_cost
                        self._total_profit += record.net_profit
                        self._trade_count += 1
                    except (json.JSONDecodeError, KeyError):
                        logger.warning("Skipping malformed trade record")
        except OSError:
            logger.warning("Could not read trades file: %s", path)

        # Trim to max_memory, keeping newest
        if len(self._trades) > self.settings.max_memory:
            self._trades = self._trades[-self.settings.max_memory:]

        logger.info(
            "Loaded %d trades (%.4f invested, %.4f profit)",
            self._trade_count, self._total_invested, self._total_profit,
        )

    def record(
        self,
        *,
        market_id: str,
        market_question: str,
        total_cost: float,
        net_profit: float,
        roi_pct: float,
    ) -> TradeRecord:
        """Append a trade to the store and write to disk."""
        record = TradeRecord(
            timestamp=time.time(),
            market_id=market_id,
            market_question=market_question[:200],
            total_cost=total_cost,
            net_profit=net_profit,
            roi_pct=roi_pct,
        )

        self._trades.append(record)
        self._total_invested += total_cost
        self._total_profit += net_profit
        self._trade_count += 1

        # Trim oldest from memory
        if len(self._trades) > self.settings.max_memory:
            self._trades = self._trades[-self.settings.max_memory:]

        # Append to disk
        if self.settings.enabled:
            self._append_to_file(record)

        return record

    def _append_to_file(self, record: TradeRecord) -> None:
        try:
            path = Path(self.settings.trades_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
        except OSError:
            logger.warning("Failed to write trade to disk", exc_info=True)
