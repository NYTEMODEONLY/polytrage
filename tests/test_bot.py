"""Tests for the bot CLI and paper trading."""

import json

import httpx
import pytest
import respx

from polytrage.api import CLOB_API, GAMMA_API
from polytrage.bot import (
    PaperPortfolio,
    PaperTrade,
    build_opportunities_table,
    build_paper_portfolio_table,
    parse_args,
    run_scan_loop,
)
from polytrage.models import (
    ArbitrageOpportunity,
    Market,
    MarketType,
    Outcome,
)
from polytrage.scanner import ScanResult


def _make_opportunity(
    total_cost: float = 0.90,
    net_profit: float = 0.098,
    roi_pct: float = 10.89,
) -> ArbitrageOpportunity:
    market = Market(
        id="test",
        question="Will it happen?",
        slug="test-market",
        outcomes=["Yes", "No"],
        clob_token_ids=["t1", "t2"],
        outcome_prices=[0.45, 0.45],
    )
    return ArbitrageOpportunity(
        market=market,
        market_type=MarketType.BINARY,
        outcomes=[
            Outcome(name="Yes", token_id="t1", price=0.45, best_ask=total_cost / 2),
            Outcome(name="No", token_id="t2", price=0.45, best_ask=total_cost / 2),
        ],
        total_cost=total_cost,
        gross_profit=1.0 - total_cost,
        net_profit=net_profit,
        roi_pct=roi_pct,
        capital_required=total_cost,
    )


class TestPaperPortfolio:
    """Test paper trading simulation."""

    def test_record_trade(self):
        portfolio = PaperPortfolio()
        opp = _make_opportunity()
        trade = portfolio.record_trade(opp)

        assert trade.total_cost == 0.90
        assert trade.net_profit == 0.098
        assert len(portfolio.trades) == 1
        assert portfolio.total_invested == 0.90
        assert portfolio.total_profit == 0.098

    def test_multiple_trades(self):
        portfolio = PaperPortfolio()
        portfolio.record_trade(_make_opportunity(total_cost=0.90, net_profit=0.098))
        portfolio.record_trade(_make_opportunity(total_cost=0.80, net_profit=0.196))

        assert len(portfolio.trades) == 2
        assert portfolio.total_invested == pytest.approx(1.70)
        assert portfolio.total_profit == pytest.approx(0.294)

    def test_roi_calculation(self):
        portfolio = PaperPortfolio()
        portfolio.record_trade(_make_opportunity(total_cost=1.0, net_profit=0.10))

        assert portfolio.total_roi_pct == pytest.approx(10.0)

    def test_empty_portfolio_roi(self):
        portfolio = PaperPortfolio()
        assert portfolio.total_roi_pct == 0.0


class TestCLIArgs:
    """Test CLI argument parsing."""

    def test_defaults(self):
        args = parse_args([])
        assert args.interval == 60
        assert args.min_profit == 0.005
        assert args.max_markets == 100
        assert args.fee_rate == 0.02
        assert args.paper is False
        assert args.once is False

    def test_custom_args(self):
        args = parse_args([
            "--interval", "30",
            "--min-profit", "0.01",
            "--max-markets", "200",
            "--fee-rate", "0.03",
            "--paper",
            "--once",
        ])
        assert args.interval == 30
        assert args.min_profit == 0.01
        assert args.max_markets == 200
        assert args.fee_rate == 0.03
        assert args.paper is True
        assert args.once is True

    def test_verbose_flag(self):
        args = parse_args(["-v"])
        assert args.verbose is True

    def test_no_orderbooks_flag(self):
        args = parse_args(["--no-orderbooks"])
        assert args.no_orderbooks is True


class TestTableBuilders:
    """Test Rich table output generation."""

    def test_opportunities_table_with_data(self):
        opp = _make_opportunity()
        result = ScanResult(markets_scanned=10, opportunities=[opp])
        table = build_opportunities_table([opp], result)
        assert table.title is not None
        assert "10 markets scanned" in table.title

    def test_opportunities_table_empty(self):
        result = ScanResult(markets_scanned=5)
        table = build_opportunities_table([], result)
        assert table.title is not None

    def test_paper_portfolio_table(self):
        portfolio = PaperPortfolio()
        portfolio.record_trade(_make_opportunity())
        table = build_paper_portfolio_table(portfolio)
        assert table.title == "Paper Portfolio"


class TestBotLoop:
    """Test the main scan loop (single-pass mode)."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_single_scan(self):
        """Run once mode completes without error."""
        market = {
            "id": "1",
            "question": "Test?",
            "slug": "test",
            "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["t1", "t2"]),
            "outcomePrices": json.dumps(["0.40", "0.40"]),
            "negRisk": False,
            "volume": "1000",
            "liquidity": "500",
            "active": True,
        }

        respx.get(f"{GAMMA_API}/markets").mock(
            return_value=httpx.Response(200, json=[market])
        )
        respx.get(f"{CLOB_API}/book", params__contains={"token_id": "t1"}).mock(
            return_value=httpx.Response(200, json={
                "bids": [{"price": "0.39", "size": "100"}],
                "asks": [{"price": "0.42", "size": "100"}],
            })
        )
        respx.get(f"{CLOB_API}/book", params__contains={"token_id": "t2"}).mock(
            return_value=httpx.Response(200, json={
                "bids": [{"price": "0.39", "size": "100"}],
                "asks": [{"price": "0.42", "size": "100"}],
            })
        )

        # Should complete without error
        await run_scan_loop(once=True, max_markets=10)

    @respx.mock
    @pytest.mark.asyncio
    async def test_paper_trading_mode(self):
        """Paper trading mode records trades."""
        market = {
            "id": "2",
            "question": "Paper test?",
            "slug": "paper-test",
            "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["p1", "p2"]),
            "outcomePrices": json.dumps(["0.35", "0.35"]),
            "negRisk": False,
            "volume": "2000",
            "liquidity": "1000",
            "active": True,
        }

        respx.get(f"{GAMMA_API}/markets").mock(
            return_value=httpx.Response(200, json=[market])
        )
        respx.get(f"{CLOB_API}/book", params__contains={"token_id": "p1"}).mock(
            return_value=httpx.Response(200, json={
                "bids": [{"price": "0.34", "size": "100"}],
                "asks": [{"price": "0.37", "size": "100"}],
            })
        )
        respx.get(f"{CLOB_API}/book", params__contains={"token_id": "p2"}).mock(
            return_value=httpx.Response(200, json={
                "bids": [{"price": "0.34", "size": "100"}],
                "asks": [{"price": "0.37", "size": "100"}],
            })
        )

        await run_scan_loop(once=True, paper=True, max_markets=10)
