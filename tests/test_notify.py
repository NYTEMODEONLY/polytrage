"""Tests for Discord notification system."""

import time

import httpx
import pytest
import respx

from polytrage.config import NotifySettings
from polytrage.notify import Notifier


def _settings(webhook: str = "https://discord.com/api/webhooks/test/token") -> NotifySettings:
    return NotifySettings(
        discord_webhook=webhook,
        cooldown=300,
        on_startup=True,
        on_error=True,
        on_arb=True,
    )


class TestNotifierEnabled:
    def test_enabled_with_webhook(self):
        n = Notifier(_settings())
        assert n.enabled is True

    def test_disabled_without_webhook(self):
        n = Notifier(_settings(webhook=""))
        assert n.enabled is False


class TestCooldown:
    def test_first_message_not_cooled(self):
        n = Notifier(_settings())
        assert n._is_cooled_down("market-1") is True

    def test_cooled_down_after_send(self):
        n = Notifier(_settings())
        n._mark_sent("market-1")
        assert n._is_cooled_down("market-1") is False

    def test_different_markets_independent(self):
        n = Notifier(_settings())
        n._mark_sent("market-1")
        assert n._is_cooled_down("market-2") is True

    def test_cooldown_expires(self):
        settings = _settings()
        settings.cooldown = 0  # Immediate expiry
        n = Notifier(settings)
        n._cooldowns["market-1"] = time.time() - 1
        assert n._is_cooled_down("market-1") is True


class TestSendEmbed:
    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_send(self):
        webhook = "https://discord.com/api/webhooks/test/token"
        respx.post(webhook).mock(return_value=httpx.Response(204))

        n = Notifier(_settings(webhook))
        try:
            result = await n._send_embed("Test", "Hello", 0x00FF00)
            assert result is True
        finally:
            await n.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_failed_send_returns_false(self):
        webhook = "https://discord.com/api/webhooks/test/token"
        respx.post(webhook).mock(return_value=httpx.Response(500))

        n = Notifier(_settings(webhook))
        try:
            result = await n._send_embed("Test", "Hello", 0xFF0000)
            assert result is False
        finally:
            await n.close()

    @pytest.mark.asyncio
    async def test_disabled_returns_false(self):
        n = Notifier(_settings(webhook=""))
        result = await n._send_embed("Test", "Hello", 0x00FF00)
        assert result is False


class TestNotifyArb:
    @respx.mock
    @pytest.mark.asyncio
    async def test_arb_notification(self):
        webhook = "https://discord.com/api/webhooks/test/token"
        respx.post(webhook).mock(return_value=httpx.Response(204))

        n = Notifier(_settings(webhook))
        try:
            await n.notify_arb(
                market_id="m1",
                market_question="Will it happen?",
                net_profit=0.05,
                roi_pct=5.2,
                total_cost=0.95,
            )
            assert len(respx.calls) == 1
        finally:
            await n.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_arb_cooldown_prevents_duplicate(self):
        webhook = "https://discord.com/api/webhooks/test/token"
        respx.post(webhook).mock(return_value=httpx.Response(204))

        n = Notifier(_settings(webhook))
        try:
            await n.notify_arb(
                market_id="m1",
                market_question="Q",
                net_profit=0.05, roi_pct=5.0, total_cost=0.95,
            )
            await n.notify_arb(
                market_id="m1",
                market_question="Q",
                net_profit=0.05, roi_pct=5.0, total_cost=0.95,
            )
            # Second call should be suppressed by cooldown
            assert len(respx.calls) == 1
        finally:
            await n.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_arb_disabled_flag(self):
        webhook = "https://discord.com/api/webhooks/test/token"
        respx.post(webhook).mock(return_value=httpx.Response(204))

        settings = _settings(webhook)
        settings.on_arb = False
        n = Notifier(settings)
        try:
            await n.notify_arb(
                market_id="m1",
                market_question="Q",
                net_profit=0.05, roi_pct=5.0, total_cost=0.95,
            )
            assert len(respx.calls) == 0
        finally:
            await n.close()


class TestNotifyStartup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_startup_notification(self):
        webhook = "https://discord.com/api/webhooks/test/token"
        respx.post(webhook).mock(return_value=httpx.Response(204))

        n = Notifier(_settings(webhook))
        try:
            await n.notify_startup("interval=60s, markets=100")
            assert len(respx.calls) == 1
        finally:
            await n.close()


class TestNotifyError:
    @respx.mock
    @pytest.mark.asyncio
    async def test_error_notification(self):
        webhook = "https://discord.com/api/webhooks/test/token"
        respx.post(webhook).mock(return_value=httpx.Response(204))

        n = Notifier(_settings(webhook))
        try:
            await n.notify_error("Circuit breaker tripped")
            assert len(respx.calls) == 1
        finally:
            await n.close()
