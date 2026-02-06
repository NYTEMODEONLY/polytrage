"""Discord webhook notifications."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from polytrage.config import NotifySettings

logger = logging.getLogger(__name__)

# Embed colors
COLOR_GREEN = 0x2ECC71   # arbitrage found
COLOR_RED = 0xE74C3C     # error / circuit breaker
COLOR_BLUE = 0x3498DB    # startup / info


class Notifier:
    """Sends Discord webhook notifications with rate limiting."""

    def __init__(self, settings: NotifySettings) -> None:
        self.settings = settings
        self._cooldowns: dict[str, float] = {}  # market_id -> last_notified timestamp
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.discord_webhook)

    def _is_cooled_down(self, market_id: str) -> bool:
        """Check if a market notification is past its cooldown period."""
        last = self._cooldowns.get(market_id, 0.0)
        return (time.time() - last) >= self.settings.cooldown

    def _mark_sent(self, market_id: str) -> None:
        self._cooldowns[market_id] = time.time()

    async def _send_embed(
        self,
        title: str,
        description: str,
        color: int,
        fields: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Send a Discord embed. Returns True on success."""
        if not self.enabled:
            return False

        payload: dict[str, Any] = {
            "embeds": [{
                "title": title,
                "description": description,
                "color": color,
            }],
        }
        if fields:
            payload["embeds"][0]["fields"] = fields

        try:
            client = await self._get_client()
            resp = await client.post(self.settings.discord_webhook, json=payload)
            resp.raise_for_status()
            return True
        except Exception:
            logger.warning("Discord webhook failed", exc_info=True)
            return False

    async def notify_startup(self, config_summary: str) -> None:
        """Send startup notification."""
        if not self.settings.on_startup:
            return
        await self._send_embed(
            title="Polytrage Started",
            description=config_summary,
            color=COLOR_BLUE,
        )

    async def notify_arb(
        self,
        market_id: str,
        market_question: str,
        net_profit: float,
        roi_pct: float,
        total_cost: float,
    ) -> None:
        """Send arbitrage opportunity notification (with per-market cooldown)."""
        if not self.settings.on_arb:
            return
        if not self._is_cooled_down(market_id):
            return

        await self._send_embed(
            title="Arbitrage Found",
            description=market_question[:200],
            color=COLOR_GREEN,
            fields=[
                {"name": "Net Profit", "value": f"${net_profit:.4f}", "inline": True},
                {"name": "ROI", "value": f"{roi_pct:.2f}%", "inline": True},
                {"name": "Total Cost", "value": f"${total_cost:.4f}", "inline": True},
            ],
        )
        self._mark_sent(market_id)

    async def notify_error(self, error_msg: str) -> None:
        """Send error / circuit breaker notification."""
        if not self.settings.on_error:
            return
        await self._send_embed(
            title="Polytrage Error",
            description=error_msg[:2000],
            color=COLOR_RED,
        )

    async def notify_shutdown(self, reason: str) -> None:
        """Send shutdown notification."""
        await self._send_embed(
            title="Polytrage Shutdown",
            description=reason[:2000],
            color=COLOR_RED,
        )
