"""Polymarket API client — read-only access to Gamma and CLOB APIs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from polytrage.models import Market, OrderBook, OrderBookLevel

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Rate limit: max concurrent requests
DEFAULT_CONCURRENCY = 10
DEFAULT_TIMEOUT = 15.0
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0
DEFAULT_CLIENT_REFRESH = 3600  # Recreate HTTP client every hour


class PolymarketAPIError(Exception):
    """Raised when an API request fails after retries."""


class PolymarketClient:
    """Async client for Polymarket Gamma + CLOB APIs (read-only)."""

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: float = DEFAULT_TIMEOUT,
        client_refresh_interval: int = DEFAULT_CLIENT_REFRESH,
    ) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout = timeout
        self._client_refresh_interval = client_refresh_interval
        self._client: httpx.AsyncClient | None = None
        self._client_created_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        now = time.monotonic()
        needs_refresh = (
            self._client is not None
            and not self._client.is_closed
            and self._client_refresh_interval > 0
            and (now - self._client_created_at) >= self._client_refresh_interval
        )
        if needs_refresh:
            logger.info("Refreshing HTTP client (age: %.0fs)", now - self._client_created_at)
            await self._client.aclose()
            self._client = None

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"Accept": "application/json"},
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                ),
            )
            self._client_created_at = now
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request with rate limiting and retries."""
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            async with self._semaphore:
                try:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    return resp.json()
                except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                    last_error = exc
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                        raise PolymarketAPIError(
                            f"{url} returned {exc.response.status_code}"
                        ) from exc
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "Request to %s failed (attempt %d/%d), retrying in %.1fs: %s",
                        url, attempt + 1, MAX_RETRIES, wait, exc,
                    )
                    await asyncio.sleep(wait)

        raise PolymarketAPIError(
            f"Failed after {MAX_RETRIES} retries: {url}"
        ) from last_error

    # ── Gamma API ──────────────────────────────────────────────

    async def fetch_active_markets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Market]:
        """Fetch active (non-closed) markets from the Gamma API."""
        data = await self._request(
            f"{GAMMA_API}/markets",
            params={"closed": "false", "limit": limit, "offset": offset},
        )
        markets: list[Market] = []
        for item in data:
            try:
                market = _parse_market(item)
                if market is not None:
                    markets.append(market)
            except Exception:
                logger.debug("Skipping unparseable market: %s", item.get("id", "?"))
        return markets

    async def fetch_all_active_markets(self, *, max_markets: int = 500) -> list[Market]:
        """Page through all active markets up to max_markets."""
        all_markets: list[Market] = []
        offset = 0
        page_size = 100
        while offset < max_markets:
            batch = await self.fetch_active_markets(limit=page_size, offset=offset)
            if not batch:
                break
            all_markets.extend(batch)
            offset += len(batch)
        return all_markets[:max_markets]

    # ── CLOB API ───────────────────────────────────────────────

    async def fetch_orderbook(self, token_id: str) -> OrderBook:
        """Fetch full order book for a token."""
        data = await self._request(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
        )
        return OrderBook(
            bids=[OrderBookLevel(price=float(b["price"]), size=float(b["size"])) for b in data.get("bids", [])],
            asks=[OrderBookLevel(price=float(a["price"]), size=float(a["size"])) for a in data.get("asks", [])],
        )

    async def fetch_price(self, token_id: str, side: str = "buy") -> float:
        """Fetch best price for a token on a given side."""
        data = await self._request(
            f"{CLOB_API}/price",
            params={"token_id": token_id, "side": side},
        )
        return float(data["price"])

    async def fetch_midpoint(self, token_id: str) -> float:
        """Fetch midpoint price for a token."""
        data = await self._request(
            f"{CLOB_API}/midpoint",
            params={"token_id": token_id},
        )
        return float(data["mid"])

    async def fetch_prices_for_market(self, market: Market) -> list[float]:
        """Fetch best ask prices for all outcomes in a market concurrently."""
        tasks = [self.fetch_price(tid, "buy") for tid in market.clob_token_ids]
        return await asyncio.gather(*tasks)

    async def fetch_orderbooks_for_market(self, market: Market) -> list[OrderBook]:
        """Fetch order books for all outcomes in a market concurrently."""
        tasks = [self.fetch_orderbook(tid) for tid in market.clob_token_ids]
        return await asyncio.gather(*tasks)


def _parse_market(raw: dict[str, Any]) -> Market | None:
    """Parse a raw Gamma API market response into a Market model."""
    clob_ids_raw = raw.get("clobTokenIds")
    prices_raw = raw.get("outcomePrices")
    outcomes_raw = raw.get("outcomes")

    # Skip markets without CLOB data
    if not clob_ids_raw or not prices_raw or not outcomes_raw:
        return None

    # These come as JSON strings from the API
    if isinstance(clob_ids_raw, str):
        import json
        clob_ids_raw = json.loads(clob_ids_raw)
    if isinstance(prices_raw, str):
        import json
        prices_raw = json.loads(prices_raw)
    if isinstance(outcomes_raw, str):
        import json
        outcomes_raw = json.loads(outcomes_raw)

    # Need at least 2 outcomes
    if len(clob_ids_raw) < 2:
        return None

    return Market(
        id=str(raw.get("id", "")),
        question=raw.get("question", ""),
        slug=raw.get("slug", ""),
        outcomes=outcomes_raw,
        clob_token_ids=clob_ids_raw,
        outcome_prices=[float(p) for p in prices_raw],
        neg_risk=bool(raw.get("negRisk", False)),
        volume=float(raw.get("volume", 0) or 0),
        liquidity=float(raw.get("liquidity", 0) or 0),
        active=raw.get("active", True),
    )
