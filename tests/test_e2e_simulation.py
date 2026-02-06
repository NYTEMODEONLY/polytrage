"""End-to-end simulation tests — prove the full pipeline works with realistic scenarios.

These tests use simulated market data to verify:
  - Scanner correctly identifies arbitrage in binary and NegRisk markets
  - Scanner correctly rejects non-arbitrage markets (no false positives)
  - Profit guarantee calculations produce valid results
  - Paper trading portfolio tracks P&L accurately
  - The full pipeline (fetch → filter → detect → evaluate → trade) is sound
"""

import pytest
from unittest.mock import AsyncMock

from polytrage.api import PolymarketClient
from polytrage.arbitrage import detect_arbitrage_from_prices
from polytrage.bot import PaperPortfolio
from polytrage.models import Market, OrderBook, OrderBookLevel
from polytrage.profit import evaluate_opportunity
from polytrage.scanner import ScanConfig, Scanner


def _make_market(mid: str, q: str, prices: list[float], *, neg_risk: bool = False) -> Market:
    n = len(prices)
    return Market(
        id=mid, question=q, slug=f"sim-{mid}",
        outcomes=[f"Outcome-{i}" for i in range(n)],
        clob_token_ids=[f"tok-{mid}-{i}" for i in range(n)],
        outcome_prices=prices, neg_risk=neg_risk,
        volume=50000, liquidity=10000, active=True,
    )


def _make_books(asks: list[float]) -> list[OrderBook]:
    return [
        OrderBook(
            bids=[OrderBookLevel(price=a - 0.02, size=500)],
            asks=[OrderBookLevel(price=a, size=500)],
        )
        for a in asks
    ]


SIMULATED_MARKETS = [
    _make_market("A", "BTC > $200k by Dec?", [0.42, 0.42]),                    # arb: asks $0.86
    _make_market("B", "ETH flips BTC?", [0.55, 0.50]),                         # no arb: asks $1.07
    _make_market("C", "Who wins F1 2026?", [0.25, 0.20, 0.20], neg_risk=True), # arb: asks $0.69
    _make_market("D", "SOL > $500?", [0.48, 0.52]),                            # no arb: asks $1.02
    _make_market("E", "Rate cut in March?", [0.60, 0.55]),                     # no arb: asks $1.17
]

SIMULATED_BOOKS = {
    "A": _make_books([0.43, 0.43]),       # ask sum = 0.86 → arb
    "B": _make_books([0.56, 0.51]),       # ask sum = 1.07 → no arb
    "C": _make_books([0.26, 0.21, 0.22]), # ask sum = 0.69 → arb
    "D": _make_books([0.49, 0.53]),       # ask sum = 1.02 → no arb
    "E": _make_books([0.61, 0.56]),       # ask sum = 1.17 → no arb
}


@pytest.fixture
async def mock_scanner():
    """Scanner with mocked API returning simulated markets."""
    client = PolymarketClient()
    client.fetch_all_active_markets = AsyncMock(return_value=SIMULATED_MARKETS)

    async def mock_fetch_orderbooks(market):
        return SIMULATED_BOOKS[market.id]

    client.fetch_orderbooks_for_market = mock_fetch_orderbooks
    scanner = Scanner(client=client, config=ScanConfig(max_markets=10, min_profit=0.001))
    yield scanner
    await client.close()


class TestE2EScanner:
    """End-to-end scanner simulation."""

    @pytest.mark.asyncio
    async def test_finds_correct_number_of_opportunities(self, mock_scanner):
        result = await mock_scanner.scan()
        assert result.markets_scanned == 5
        assert len(result.opportunities) == 2
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_binary_arb_detected(self, mock_scanner):
        """BTC > $200k: asks $0.43+$0.43=$0.86 → $0.14 gross, ~$0.137 net."""
        result = await mock_scanner.scan()
        btc_opp = next(o for o in result.opportunities if "BTC" in o.market.question)

        assert btc_opp.total_cost == pytest.approx(0.86, abs=0.01)
        assert btc_opp.gross_profit == pytest.approx(0.14, abs=0.01)
        assert btc_opp.net_profit == pytest.approx(0.1372, abs=0.01)
        assert btc_opp.roi_pct > 15.0
        assert btc_opp.market_type.value == "binary"

    @pytest.mark.asyncio
    async def test_negrisk_arb_detected(self, mock_scanner):
        """F1 2026: asks $0.26+$0.21+$0.22=$0.69 → $0.31 gross, ~$0.304 net."""
        result = await mock_scanner.scan()
        f1_opp = next(o for o in result.opportunities if "F1" in o.market.question)

        assert f1_opp.total_cost == pytest.approx(0.69, abs=0.01)
        assert f1_opp.gross_profit == pytest.approx(0.31, abs=0.01)
        assert f1_opp.net_profit == pytest.approx(0.3038, abs=0.01)
        assert f1_opp.roi_pct > 40.0
        assert f1_opp.market_type.value == "negrisk"

    @pytest.mark.asyncio
    async def test_sorted_by_roi_descending(self, mock_scanner):
        """Opportunities should be sorted by ROI, highest first."""
        result = await mock_scanner.scan()
        rois = [o.roi_pct for o in result.opportunities]
        assert rois == sorted(rois, reverse=True)
        # F1 (44% ROI) should come before BTC (16% ROI)
        assert "F1" in result.opportunities[0].market.question

    @pytest.mark.asyncio
    async def test_no_false_positives(self, mock_scanner):
        """Non-arb markets (ETH, SOL, Rate cut) must not appear as opportunities."""
        result = await mock_scanner.scan()
        found_questions = {o.market.question for o in result.opportunities}
        assert "ETH flips BTC?" not in found_questions
        assert "SOL > $500?" not in found_questions
        assert "Rate cut in March?" not in found_questions


class TestE2ENoFalsePositives:
    """Verify non-arb markets are rejected at the detection level."""

    def test_eth_rejected(self):
        """ETH: ask sum $1.07 → no arbitrage."""
        m = SIMULATED_MARKETS[1]
        asks = [ob.best_ask for ob in SIMULATED_BOOKS["B"]]
        assert detect_arbitrage_from_prices(m, asks) is None

    def test_sol_rejected(self):
        """SOL: ask sum $1.02 → no arbitrage."""
        m = SIMULATED_MARKETS[3]
        asks = [ob.best_ask for ob in SIMULATED_BOOKS["D"]]
        assert detect_arbitrage_from_prices(m, asks) is None

    def test_rate_cut_rejected(self):
        """Rate cut: ask sum $1.17 → no arbitrage."""
        m = SIMULATED_MARKETS[4]
        asks = [ob.best_ask for ob in SIMULATED_BOOKS["E"]]
        assert detect_arbitrage_from_prices(m, asks) is None


class TestE2EProfitGuarantees:
    """Verify profit guarantee analysis on detected opportunities."""

    @pytest.mark.asyncio
    async def test_profit_guarantees_valid(self, mock_scanner):
        result = await mock_scanner.scan()
        for opp in result.opportunities:
            asks = [o.best_ask for o in opp.outcomes if o.best_ask is not None]
            guarantee = evaluate_opportunity(asks)
            assert guarantee.kl_divergence >= 0
            assert guarantee.fw_gap >= 0
            assert guarantee.guaranteed_profit >= 0
            assert 0.0 <= guarantee.extraction_pct <= 1.0


class TestE2EPaperTrading:
    """Verify paper trading portfolio tracking."""

    @pytest.mark.asyncio
    async def test_paper_portfolio_from_scan(self, mock_scanner):
        result = await mock_scanner.scan()
        portfolio = PaperPortfolio()

        for opp in result.opportunities:
            portfolio.record_trade(opp)

        assert len(portfolio.trades) == 2
        assert portfolio.total_invested == pytest.approx(0.69 + 0.86, abs=0.01)
        assert portfolio.total_profit > 0
        assert portfolio.total_roi_pct > 20.0  # Blended ROI of ~28%

    @pytest.mark.asyncio
    async def test_individual_trade_amounts(self, mock_scanner):
        result = await mock_scanner.scan()
        portfolio = PaperPortfolio()

        for opp in result.opportunities:
            portfolio.record_trade(opp)

        # F1 trade (first by ROI sort)
        f1_trade = portfolio.trades[0]
        assert f1_trade.total_cost == pytest.approx(0.69, abs=0.01)
        assert f1_trade.net_profit == pytest.approx(0.3038, abs=0.01)

        # BTC trade (second by ROI sort)
        btc_trade = portfolio.trades[1]
        assert btc_trade.total_cost == pytest.approx(0.86, abs=0.01)
        assert btc_trade.net_profit == pytest.approx(0.1372, abs=0.01)
