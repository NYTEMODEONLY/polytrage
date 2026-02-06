"""Data models for Polytrage."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class MarketType(str, Enum):
    BINARY = "binary"
    NEGRISK = "negrisk"


class OrderBookLevel(BaseModel):
    price: float
    size: float


class OrderBook(BaseModel):
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None


class Outcome(BaseModel):
    name: str
    token_id: str
    price: float  # Current midpoint/last price
    best_ask: float | None = None  # Best ask from order book
    best_bid: float | None = None  # Best bid from order book


class Market(BaseModel):
    id: str
    question: str
    slug: str
    outcomes: list[str]
    clob_token_ids: list[str]
    outcome_prices: list[float]
    neg_risk: bool = False
    volume: float = 0.0
    liquidity: float = 0.0
    active: bool = True

    @property
    def market_type(self) -> MarketType:
        return MarketType.NEGRISK if self.neg_risk else MarketType.BINARY

    @property
    def num_outcomes(self) -> int:
        return len(self.outcomes)


class ArbitrageOpportunity(BaseModel):
    market: Market
    market_type: MarketType
    outcomes: list[Outcome]
    total_cost: float  # Sum of best asks for all outcomes
    gross_profit: float  # 1.0 - total_cost (per dollar)
    net_profit: float  # After fees
    roi_pct: float  # net_profit / total_cost * 100
    capital_required: float  # total_cost for 1 share of each outcome


class ProfitGuarantee(BaseModel):
    kl_divergence: float
    fw_gap: float
    guaranteed_profit: float  # D(μ̂||θ) - g(μ̂)
    extraction_pct: float  # 1 - g/D
    should_trade: bool
