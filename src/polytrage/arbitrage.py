"""Arbitrage detection engine for Polymarket markets."""

from __future__ import annotations

import logging

from polytrage.models import (
    ArbitrageOpportunity,
    Market,
    MarketType,
    OrderBook,
    Outcome,
)

logger = logging.getLogger(__name__)

DEFAULT_FEE_RATE = 0.02  # Polymarket's 2% fee on winnings
MIN_PROFIT_THRESHOLD = 0.005  # $0.005 minimum profit per dollar


def detect_arbitrage_from_orderbooks(
    market: Market,
    orderbooks: list[OrderBook],
    *,
    fee_rate: float = DEFAULT_FEE_RATE,
    min_profit: float = MIN_PROFIT_THRESHOLD,
) -> ArbitrageOpportunity | None:
    """Detect arbitrage using actual order book best-ask prices.

    The core idea: if buying one share of every outcome costs less than $1.00,
    you're guaranteed $1.00 at settlement (exactly one outcome wins), minus fees.
    """
    if len(orderbooks) != len(market.clob_token_ids):
        return None

    outcomes: list[Outcome] = []
    for i, ob in enumerate(orderbooks):
        if ob.best_ask is None:
            return None  # Can't buy this outcome — no asks
        outcomes.append(Outcome(
            name=market.outcomes[i],
            token_id=market.clob_token_ids[i],
            price=market.outcome_prices[i],
            best_ask=ob.best_ask,
            best_bid=ob.best_bid,
        ))

    return _evaluate_arbitrage(market, outcomes, fee_rate=fee_rate, min_profit=min_profit)


def detect_arbitrage_from_prices(
    market: Market,
    ask_prices: list[float],
    *,
    fee_rate: float = DEFAULT_FEE_RATE,
    min_profit: float = MIN_PROFIT_THRESHOLD,
) -> ArbitrageOpportunity | None:
    """Detect arbitrage using fetched best-ask prices."""
    if len(ask_prices) != len(market.clob_token_ids):
        return None

    outcomes: list[Outcome] = []
    for i, ask in enumerate(ask_prices):
        outcomes.append(Outcome(
            name=market.outcomes[i],
            token_id=market.clob_token_ids[i],
            price=market.outcome_prices[i],
            best_ask=ask,
        ))

    return _evaluate_arbitrage(market, outcomes, fee_rate=fee_rate, min_profit=min_profit)


def detect_arbitrage_from_midpoints(
    market: Market,
    *,
    fee_rate: float = DEFAULT_FEE_RATE,
    min_profit: float = MIN_PROFIT_THRESHOLD,
) -> ArbitrageOpportunity | None:
    """Quick pre-filter using the market's embedded midpoint prices.

    Less accurate than order book prices but doesn't require extra API calls.
    Use as a first pass before fetching actual order books.
    """
    if len(market.outcome_prices) < 2:
        return None

    outcomes = [
        Outcome(
            name=market.outcomes[i],
            token_id=market.clob_token_ids[i],
            price=p,
            best_ask=p,  # Approximate: midpoint ≈ ask for screening
        )
        for i, p in enumerate(market.outcome_prices)
    ]

    return _evaluate_arbitrage(market, outcomes, fee_rate=fee_rate, min_profit=min_profit)


def _evaluate_arbitrage(
    market: Market,
    outcomes: list[Outcome],
    *,
    fee_rate: float,
    min_profit: float,
) -> ArbitrageOpportunity | None:
    """Core arbitrage evaluation.

    For any prediction market with mutually exclusive, exhaustive outcomes:
      total_cost = sum(best_ask for each outcome)
      payout = $1.00 (exactly one outcome wins)
      gross_profit = 1.0 - total_cost
      fee = fee_rate * gross_profit  (fee only on profit, not capital)
      net_profit = gross_profit - fee = gross_profit * (1 - fee_rate)

    Arbitrage exists when net_profit > 0, i.e., total_cost < 1.0 (after fees).
    """
    total_cost = sum(o.best_ask for o in outcomes if o.best_ask is not None)

    if total_cost >= 1.0:
        return None  # No arbitrage — costs at least $1 for $1 payout

    gross_profit = 1.0 - total_cost

    # Fee is charged on profit (winnings minus cost), not on the full payout
    net_profit = gross_profit * (1.0 - fee_rate)

    if net_profit < min_profit:
        return None  # Below minimum threshold

    roi_pct = (net_profit / total_cost) * 100.0 if total_cost > 0 else 0.0

    return ArbitrageOpportunity(
        market=market,
        market_type=market.market_type,
        outcomes=outcomes,
        total_cost=round(total_cost, 6),
        gross_profit=round(gross_profit, 6),
        net_profit=round(net_profit, 6),
        roi_pct=round(roi_pct, 4),
        capital_required=round(total_cost, 6),
    )
