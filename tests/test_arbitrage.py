"""Tests for arbitrage detection engine."""

import pytest

from polytrage.arbitrage import (
    DEFAULT_FEE_RATE,
    detect_arbitrage_from_midpoints,
    detect_arbitrage_from_orderbooks,
    detect_arbitrage_from_prices,
)
from polytrage.models import Market, OrderBook, OrderBookLevel


def _make_market(
    prices: list[float],
    outcomes: list[str] | None = None,
    neg_risk: bool = False,
) -> Market:
    n = len(prices)
    if outcomes is None:
        outcomes = [f"Outcome {i}" for i in range(n)]
    return Market(
        id="test-market",
        question="Test market?",
        slug="test-market",
        outcomes=outcomes,
        clob_token_ids=[f"token-{i}" for i in range(n)],
        outcome_prices=prices,
        neg_risk=neg_risk,
    )


def _make_orderbooks(ask_prices: list[float]) -> list[OrderBook]:
    return [
        OrderBook(
            bids=[OrderBookLevel(price=ask - 0.01, size=100.0)],
            asks=[OrderBookLevel(price=ask, size=100.0)],
        )
        for ask in ask_prices
    ]


class TestBinaryArbitrage:
    """Test arbitrage detection for binary (YES/NO) markets."""

    def test_clear_arbitrage(self):
        """YES=0.40, NO=0.40 → total=0.80, gross=0.20, net=0.196"""
        market = _make_market([0.40, 0.40], ["Yes", "No"])
        opp = detect_arbitrage_from_prices(market, [0.40, 0.40])

        assert opp is not None
        assert opp.total_cost == pytest.approx(0.80, abs=1e-4)
        assert opp.gross_profit == pytest.approx(0.20, abs=1e-4)
        assert opp.net_profit == pytest.approx(0.20 * (1 - DEFAULT_FEE_RATE), abs=1e-4)
        assert opp.roi_pct > 0

    def test_no_arbitrage_exact_dollar(self):
        """YES=0.60, NO=0.40 → total=1.00, no arbitrage."""
        market = _make_market([0.60, 0.40], ["Yes", "No"])
        opp = detect_arbitrage_from_prices(market, [0.60, 0.40])
        assert opp is None

    def test_no_arbitrage_over_dollar(self):
        """YES=0.55, NO=0.50 → total=1.05, no arbitrage."""
        market = _make_market([0.55, 0.50], ["Yes", "No"])
        opp = detect_arbitrage_from_prices(market, [0.55, 0.50])
        assert opp is None

    def test_tiny_arbitrage_below_threshold(self):
        """Arbitrage exists but net profit below minimum threshold."""
        # total=0.999, gross=0.001, net=0.00098 — below default 0.005 threshold
        market = _make_market([0.50, 0.499], ["Yes", "No"])
        opp = detect_arbitrage_from_prices(market, [0.50, 0.499])
        assert opp is None

    def test_custom_fee_rate(self):
        """Test with custom fee rate."""
        market = _make_market([0.45, 0.45], ["Yes", "No"])
        opp = detect_arbitrage_from_prices(market, [0.45, 0.45], fee_rate=0.05)

        assert opp is not None
        assert opp.gross_profit == pytest.approx(0.10, abs=1e-4)
        assert opp.net_profit == pytest.approx(0.10 * 0.95, abs=1e-4)

    def test_custom_min_profit(self):
        """Lower min profit threshold catches small opportunities."""
        market = _make_market([0.498, 0.498], ["Yes", "No"])
        # gross = 0.004, net ≈ 0.00392
        opp = detect_arbitrage_from_prices(market, [0.498, 0.498], min_profit=0.003)
        assert opp is not None

    def test_roi_calculation(self):
        """Verify ROI percentage is correct."""
        market = _make_market([0.40, 0.40], ["Yes", "No"])
        opp = detect_arbitrage_from_prices(market, [0.40, 0.40])

        assert opp is not None
        expected_roi = (opp.net_profit / opp.total_cost) * 100
        assert opp.roi_pct == pytest.approx(expected_roi, abs=0.01)


class TestOrderBookArbitrage:
    """Test arbitrage detection from order books."""

    def test_with_orderbooks(self):
        """Detect arbitrage from order book data."""
        market = _make_market([0.45, 0.45], ["Yes", "No"])
        orderbooks = _make_orderbooks([0.42, 0.42])

        opp = detect_arbitrage_from_orderbooks(market, orderbooks)
        assert opp is not None
        assert opp.total_cost == pytest.approx(0.84, abs=1e-4)

    def test_missing_asks(self):
        """No arbitrage possible if an outcome has no asks."""
        market = _make_market([0.45, 0.45], ["Yes", "No"])
        orderbooks = [
            OrderBook(asks=[OrderBookLevel(price=0.42, size=100)]),
            OrderBook(),  # No bids or asks
        ]

        opp = detect_arbitrage_from_orderbooks(market, orderbooks)
        assert opp is None

    def test_mismatched_orderbooks(self):
        """Returns None if number of orderbooks doesn't match outcomes."""
        market = _make_market([0.45, 0.45], ["Yes", "No"])
        orderbooks = _make_orderbooks([0.42])  # Only 1 book for 2 outcomes

        opp = detect_arbitrage_from_orderbooks(market, orderbooks)
        assert opp is None


class TestNegRiskArbitrage:
    """Test arbitrage detection for NegRisk (multi-outcome) markets."""

    def test_three_outcome_arbitrage(self):
        """3 outcomes at 0.30 each → total=0.90, arbitrage exists."""
        market = _make_market([0.30, 0.30, 0.30], neg_risk=True)
        opp = detect_arbitrage_from_prices(market, [0.30, 0.30, 0.30])

        assert opp is not None
        assert opp.total_cost == pytest.approx(0.90, abs=1e-4)
        assert opp.market_type.value == "negrisk"

    def test_four_outcome_no_arbitrage(self):
        """4 outcomes summing to 1.05 → no arbitrage."""
        market = _make_market([0.30, 0.30, 0.25, 0.20], neg_risk=True)
        opp = detect_arbitrage_from_prices(market, [0.30, 0.30, 0.25, 0.20])
        assert opp is None

    def test_many_outcomes(self):
        """10 outcomes at 0.08 each → total=0.80, strong arbitrage."""
        prices = [0.08] * 10
        market = _make_market(prices, neg_risk=True)
        opp = detect_arbitrage_from_prices(market, prices)

        assert opp is not None
        assert opp.total_cost == pytest.approx(0.80, abs=1e-4)
        assert opp.gross_profit == pytest.approx(0.20, abs=1e-4)


class TestMidpointDetection:
    """Test the quick midpoint-based pre-filter."""

    def test_midpoint_detection(self):
        """Detect arbitrage using embedded market prices."""
        market = _make_market([0.40, 0.40], ["Yes", "No"])
        opp = detect_arbitrage_from_midpoints(market)

        assert opp is not None
        assert opp.total_cost == pytest.approx(0.80, abs=1e-4)

    def test_midpoint_no_arbitrage(self):
        """No arbitrage when midpoints sum to 1.0."""
        market = _make_market([0.60, 0.40], ["Yes", "No"])
        opp = detect_arbitrage_from_midpoints(market)
        assert opp is None

    def test_single_outcome_rejected(self):
        """Markets with fewer than 2 outcomes are skipped."""
        market = _make_market([0.50])
        market.outcomes = ["Only"]
        opp = detect_arbitrage_from_midpoints(market)
        assert opp is None
