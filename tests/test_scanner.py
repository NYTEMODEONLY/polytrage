"""Integration tests for the market scanner with mocked API."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from polytrage.api import CLOB_API, GAMMA_API, PolymarketClient
from polytrage.models import Market
from polytrage.scanner import ScanConfig, Scanner


def _make_market(
    market_id: str,
    question: str,
    yes_price: float,
    no_price: float,
    *,
    neg_risk: bool = False,
    volume: float = 10000,
    liquidity: float = 5000,
) -> Market:
    return Market(
        id=market_id,
        question=question,
        slug=f"market-{market_id}",
        outcomes=["Yes", "No"],
        clob_token_ids=[f"token-{market_id}-yes", f"token-{market_id}-no"],
        outcome_prices=[yes_price, no_price],
        neg_risk=neg_risk,
        volume=volume,
        liquidity=liquidity,
        active=True,
    )


def _orderbook_json(ask_price: float, bid_price: float | None = None) -> dict:
    bid = bid_price if bid_price is not None else ask_price - 0.02
    return {
        "bids": [{"price": str(bid), "size": "500"}],
        "asks": [{"price": str(ask_price), "size": "500"}],
    }


class TestScanner:
    """Test full scan pipeline with mocked API responses."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_finds_arbitrage(self):
        """Scanner should find arbitrage when total ask < 1.0."""
        market = _make_market("1", "Arb market?", 0.40, 0.40)

        client = PolymarketClient()
        # Bypass paging: mock fetch_all_active_markets directly
        client.fetch_all_active_markets = AsyncMock(return_value=[market])

        respx.get(f"{CLOB_API}/book").mock(
            side_effect=[
                httpx.Response(200, json=_orderbook_json(0.42)),
                httpx.Response(200, json=_orderbook_json(0.42)),
            ]
        )

        scanner = Scanner(client=client, config=ScanConfig(max_markets=10))
        try:
            result = await scanner.scan()
            assert result.markets_scanned == 1
            assert len(result.opportunities) == 1
            assert result.opportunities[0].total_cost == pytest.approx(0.84, abs=1e-3)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_arbitrage(self):
        """Scanner correctly identifies no arbitrage."""
        market = _make_market("2", "No arb?", 0.55, 0.50)

        client = PolymarketClient()
        client.fetch_all_active_markets = AsyncMock(return_value=[market])

        scanner = Scanner(client=client, config=ScanConfig(max_markets=10))
        try:
            result = await scanner.scan()
            # total midpoint = 1.05, no candidates pass pre-filter
            assert len(result.opportunities) == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_midpoint_only_mode(self):
        """Scanner works without order book fetching."""
        market = _make_market("3", "Midpoint arb?", 0.40, 0.40)

        client = PolymarketClient()
        client.fetch_all_active_markets = AsyncMock(return_value=[market])

        config = ScanConfig(max_markets=10, use_orderbooks=False)
        scanner = Scanner(client=client, config=config)
        try:
            result = await scanner.scan()
            assert len(result.opportunities) == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_liquidity_filter(self):
        """Markets below minimum liquidity are filtered out."""
        market = _make_market("4", "Low liq?", 0.40, 0.40, liquidity=10)

        client = PolymarketClient()
        client.fetch_all_active_markets = AsyncMock(return_value=[market])

        config = ScanConfig(max_markets=10, min_liquidity=1000)
        scanner = Scanner(client=client, config=config)
        try:
            result = await scanner.scan()
            assert result.markets_scanned == 0
            assert len(result.opportunities) == 0
        finally:
            await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_multiple_markets(self):
        """Scanner handles multiple markets, some with arbitrage, some without."""
        markets = [
            _make_market("10", "Has arb?", 0.40, 0.40),
            _make_market("11", "No arb?", 0.55, 0.50),
            _make_market("12", "Also arb?", 0.35, 0.35),
        ]

        client = PolymarketClient()
        client.fetch_all_active_markets = AsyncMock(return_value=markets)

        # respx will match requests in order — 4 book requests for 2 arb candidates
        # Market 10: yes, no orderbooks
        # Market 12: yes, no orderbooks
        # The scanner processes candidates (10 and 12), each needing 2 book fetches
        respx.get(f"{CLOB_API}/book").mock(
            side_effect=[
                httpx.Response(200, json=_orderbook_json(0.42)),  # token-10-yes
                httpx.Response(200, json=_orderbook_json(0.42)),  # token-10-no
                httpx.Response(200, json=_orderbook_json(0.37)),  # token-12-yes
                httpx.Response(200, json=_orderbook_json(0.37)),  # token-12-no
            ]
        )

        scanner = Scanner(client=client, config=ScanConfig(max_markets=50))
        try:
            result = await scanner.scan()
            assert result.markets_scanned == 3
            assert len(result.opportunities) == 2
            # Should be sorted by ROI (descending) — market 12 (lower cost) has higher ROI
            assert result.opportunities[0].roi_pct >= result.opportunities[1].roi_pct
        finally:
            await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_error_handled(self):
        """Scanner handles API errors gracefully."""
        market = _make_market("20", "Error market?", 0.40, 0.40)

        client = PolymarketClient(timeout=2.0)
        client.fetch_all_active_markets = AsyncMock(return_value=[market])

        # Simulate server error on order book fetch (retries will all fail)
        respx.get(f"{CLOB_API}/book").mock(
            return_value=httpx.Response(500, json={"error": "internal"})
        )

        scanner = Scanner(client=client, config=ScanConfig(max_markets=10))
        try:
            result = await scanner.scan()
            assert len(result.errors) > 0
        finally:
            await client.close()
