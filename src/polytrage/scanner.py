"""Market scanner — orchestrates API fetching and arbitrage detection."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from polytrage.api import PolymarketClient
from polytrage.arbitrage import (
    detect_arbitrage_from_midpoints,
    detect_arbitrage_from_orderbooks,
)
from polytrage.models import ArbitrageOpportunity, Market
from polytrage.profit import evaluate_opportunity

logger = logging.getLogger(__name__)


@dataclass
class ScanConfig:
    max_markets: int = 100
    min_profit: float = 0.005  # $0.005 minimum net profit per dollar
    fee_rate: float = 0.02
    use_orderbooks: bool = True  # If False, use midpoint prices only (faster)
    min_liquidity: float = 0.0  # Minimum liquidity filter
    min_volume: float = 0.0  # Minimum volume filter


@dataclass
class ScanResult:
    markets_scanned: int = 0
    opportunities: list[ArbitrageOpportunity] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_profit(self) -> float:
        return sum(o.net_profit for o in self.opportunities)

    @property
    def best_opportunity(self) -> ArbitrageOpportunity | None:
        if not self.opportunities:
            return None
        return max(self.opportunities, key=lambda o: o.net_profit)


class Scanner:
    """Scans Polymarket for arbitrage opportunities."""

    def __init__(
        self,
        client: PolymarketClient | None = None,
        config: ScanConfig | None = None,
    ) -> None:
        self.client = client or PolymarketClient()
        self.config = config or ScanConfig()

    async def scan(self) -> ScanResult:
        """Run a full scan cycle.

        Strategy:
        1. Fetch all active markets from Gamma API
        2. Pre-filter using embedded midpoint prices (cheap — no extra API calls)
        3. For candidates, fetch actual order books from CLOB API (expensive but accurate)
        4. Evaluate final arbitrage opportunities
        """
        result = ScanResult()

        # Step 1: Fetch markets
        try:
            markets = await self.client.fetch_all_active_markets(
                max_markets=self.config.max_markets
            )
        except Exception as exc:
            result.errors.append(f"Failed to fetch markets: {exc}")
            return result

        # Step 2: Apply filters
        markets = self._filter_markets(markets)
        result.markets_scanned = len(markets)
        logger.info("Scanning %d markets", len(markets))

        # Step 3: Pre-filter with midpoint prices
        candidates = self._prefilter_with_midpoints(markets)
        logger.info("Found %d candidates from midpoint pre-filter", len(candidates))

        # Step 4: Deep scan candidates with order books
        if self.config.use_orderbooks and candidates:
            opportunities = await self._deep_scan(candidates, result)
        else:
            # Use midpoint-based detection as final result
            opportunities = []
            for market in candidates:
                opp = detect_arbitrage_from_midpoints(
                    market,
                    fee_rate=self.config.fee_rate,
                    min_profit=self.config.min_profit,
                )
                if opp:
                    opportunities.append(opp)

        # Step 5: Sort by ROI
        opportunities.sort(key=lambda o: o.roi_pct, reverse=True)
        result.opportunities = opportunities

        return result

    def _filter_markets(self, markets: list[Market]) -> list[Market]:
        """Apply liquidity/volume filters."""
        filtered = []
        for m in markets:
            if not m.active:
                continue
            if m.liquidity < self.config.min_liquidity:
                continue
            if m.volume < self.config.min_volume:
                continue
            if len(m.clob_token_ids) < 2:
                continue
            filtered.append(m)
        return filtered

    def _prefilter_with_midpoints(self, markets: list[Market]) -> list[Market]:
        """Quick pre-filter: check if midpoint prices suggest possible arbitrage.

        Use a relaxed threshold here since midpoints are approximate.
        We'll verify with actual order book data later.
        """
        candidates = []
        for market in markets:
            total = sum(market.outcome_prices)
            # Relaxed threshold for pre-filter: total < 1.02 (allows for spread)
            if total < 1.02:
                candidates.append(market)
        return candidates

    async def _deep_scan(
        self,
        candidates: list[Market],
        result: ScanResult,
    ) -> list[ArbitrageOpportunity]:
        """Fetch order books and run precise arbitrage detection."""
        opportunities: list[ArbitrageOpportunity] = []

        # Process candidates concurrently but with rate limiting (via API client semaphore)
        tasks = [self._check_market(m) for m in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for market, res in zip(candidates, results):
            if isinstance(res, Exception):
                result.errors.append(f"Error scanning {market.slug}: {res}")
                continue
            if res is not None:
                opportunities.append(res)

        return opportunities

    async def _check_market(self, market: Market) -> ArbitrageOpportunity | None:
        """Check a single market for arbitrage using its order books."""
        orderbooks = await self.client.fetch_orderbooks_for_market(market)
        opp = detect_arbitrage_from_orderbooks(
            market,
            orderbooks,
            fee_rate=self.config.fee_rate,
            min_profit=self.config.min_profit,
        )

        if opp:
            # Enrich with profit guarantee analysis
            ask_prices = [o.best_ask for o in opp.outcomes if o.best_ask is not None]
            guarantee = evaluate_opportunity(
                ask_prices,
                fee_rate=self.config.fee_rate,
            )
            logger.info(
                "Arbitrage found: %s | cost=%.4f | net_profit=%.4f | ROI=%.2f%% | KL=%.4f",
                market.slug, opp.total_cost, opp.net_profit, opp.roi_pct,
                guarantee.kl_divergence,
            )

        return opp
