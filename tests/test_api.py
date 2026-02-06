"""Tests for Polymarket API client with mocked HTTP responses."""

import json

import httpx
import pytest
import respx

from polytrage.api import (
    CLOB_API,
    GAMMA_API,
    PolymarketAPIError,
    PolymarketClient,
    _parse_market,
)


SAMPLE_MARKET_RAW = {
    "id": "12345",
    "question": "Will BTC hit 100k?",
    "slug": "btc-100k",
    "outcomes": json.dumps(["Yes", "No"]),
    "clobTokenIds": json.dumps(["token-yes", "token-no"]),
    "outcomePrices": json.dumps(["0.65", "0.35"]),
    "negRisk": False,
    "volume": "1000000",
    "liquidity": "50000",
    "active": True,
}

SAMPLE_ORDERBOOK = {
    "bids": [
        {"price": "0.62", "size": "500"},
        {"price": "0.60", "size": "1000"},
    ],
    "asks": [
        {"price": "0.65", "size": "300"},
        {"price": "0.68", "size": "800"},
    ],
}


class TestParseMarket:
    """Test market parsing from raw API response."""

    def test_parse_valid_market(self):
        market = _parse_market(SAMPLE_MARKET_RAW)
        assert market is not None
        assert market.id == "12345"
        assert market.question == "Will BTC hit 100k?"
        assert market.outcomes == ["Yes", "No"]
        assert market.clob_token_ids == ["token-yes", "token-no"]
        assert market.outcome_prices == [0.65, 0.35]
        assert market.neg_risk is False

    def test_parse_missing_clob_ids(self):
        raw = {**SAMPLE_MARKET_RAW, "clobTokenIds": None}
        assert _parse_market(raw) is None

    def test_parse_missing_prices(self):
        raw = {**SAMPLE_MARKET_RAW, "outcomePrices": None}
        assert _parse_market(raw) is None

    def test_parse_single_outcome(self):
        raw = {
            **SAMPLE_MARKET_RAW,
            "outcomes": json.dumps(["Only"]),
            "clobTokenIds": json.dumps(["token-only"]),
            "outcomePrices": json.dumps(["1.0"]),
        }
        assert _parse_market(raw) is None

    def test_parse_list_format(self):
        """API sometimes returns lists instead of JSON strings."""
        raw = {
            **SAMPLE_MARKET_RAW,
            "outcomes": ["Yes", "No"],
            "clobTokenIds": ["token-yes", "token-no"],
            "outcomePrices": ["0.65", "0.35"],
        }
        market = _parse_market(raw)
        assert market is not None
        assert market.outcomes == ["Yes", "No"]


class TestPolymarketClient:
    """Test API client with mocked HTTP responses."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_active_markets(self):
        respx.get(f"{GAMMA_API}/markets").mock(
            return_value=httpx.Response(200, json=[SAMPLE_MARKET_RAW])
        )

        client = PolymarketClient()
        try:
            markets = await client.fetch_active_markets()
            assert len(markets) == 1
            assert markets[0].question == "Will BTC hit 100k?"
        finally:
            await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_orderbook(self):
        respx.get(f"{CLOB_API}/book").mock(
            return_value=httpx.Response(200, json=SAMPLE_ORDERBOOK)
        )

        client = PolymarketClient()
        try:
            ob = await client.fetch_orderbook("token-yes")
            assert len(ob.bids) == 2
            assert len(ob.asks) == 2
            assert ob.best_bid == 0.62
            assert ob.best_ask == 0.65
        finally:
            await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_price(self):
        respx.get(f"{CLOB_API}/price").mock(
            return_value=httpx.Response(200, json={"price": "0.65"})
        )

        client = PolymarketClient()
        try:
            price = await client.fetch_price("token-yes", "buy")
            assert price == 0.65
        finally:
            await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_midpoint(self):
        respx.get(f"{CLOB_API}/midpoint").mock(
            return_value=httpx.Response(200, json={"mid": "0.63"})
        )

        client = PolymarketClient()
        try:
            mid = await client.fetch_midpoint("token-yes")
            assert mid == 0.63
        finally:
            await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_client_error_raises(self):
        """4xx errors should raise immediately (no retry)."""
        respx.get(f"{CLOB_API}/price").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )

        client = PolymarketClient()
        try:
            with pytest.raises(PolymarketAPIError):
                await client.fetch_price("nonexistent", "buy")
        finally:
            await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_market_list(self):
        respx.get(f"{GAMMA_API}/markets").mock(
            return_value=httpx.Response(200, json=[])
        )

        client = PolymarketClient()
        try:
            markets = await client.fetch_active_markets()
            assert markets == []
        finally:
            await client.close()
