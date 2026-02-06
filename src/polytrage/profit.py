"""Profit calculator implementing formulas from RohOnChain's article.

Key formulas:
  - KL Divergence D(μ̂||θ): measures distance between target and current price distributions
  - Frank-Wolfe Gap g(μ̂): optimization gap bounding remaining convergence
  - Proposition 4.1: guaranteed_profit ≥ D(μ̂||θ) - g(μ̂)
  - α-Extraction: stop trading when g(μ̂) ≤ (1-α)·D(μ̂||θ), i.e., α fraction extracted
  - Minimum threshold εD = 0.05: skip if max arbitrage < 5 cents/dollar
"""

from __future__ import annotations

import math

from polytrage.models import ProfitGuarantee

# Minimum meaningful arbitrage: 5 cents per dollar wagered
MIN_DIVERGENCE_THRESHOLD = 0.05

# Default extraction target: capture 90% of available arbitrage
DEFAULT_ALPHA = 0.9


def kl_divergence(mu: list[float], theta: list[float]) -> float:
    """Compute KL divergence D(μ̂||θ) between target distribution μ̂ and current θ.

    D(μ̂||θ) = Σ μ̂_i · ln(μ̂_i / θ_i)

    In the arbitrage context:
      μ̂ = target (uniform) probability distribution we want to push prices toward
      θ = current market prices (normalized to sum to 1)

    Higher divergence = more mispricing = more arbitrage profit available.
    """
    if len(mu) != len(theta):
        raise ValueError("Distributions must have the same length")

    divergence = 0.0
    for m, t in zip(mu, theta):
        if m <= 0:
            continue  # 0 · ln(0/t) = 0 by convention
        if t <= 0:
            return float("inf")  # Undefined — current price is 0 but target > 0
        divergence += m * math.log(m / t)

    return divergence


def frank_wolfe_gap(
    mu: list[float],
    theta: list[float],
    vertices: list[list[float]] | None = None,
) -> float:
    """Approximate Frank-Wolfe gap g(μ̂).

    The FW gap bounds how much optimization progress remains. For prediction markets,
    the vertices of the feasible set are the unit vectors e_i (one outcome wins).

    g(μ̂) = max_v ⟨∇f(μ̂), μ̂ - v⟩

    where f is the negative entropy (since we minimize KL) and v ranges over vertices.

    For KL divergence: ∇f(μ̂)_i = ln(μ̂_i / θ_i) + 1

    If vertices not provided, uses the standard simplex vertices (unit vectors).
    """
    n = len(mu)
    if vertices is None:
        # Standard simplex vertices: e_1, e_2, ..., e_n
        vertices = []
        for i in range(n):
            v = [0.0] * n
            v[i] = 1.0
            vertices.append(v)

    # Gradient of KL divergence at μ̂
    grad = []
    for m, t in zip(mu, theta):
        if m <= 0 or t <= 0:
            grad.append(0.0)
        else:
            grad.append(math.log(m / t) + 1.0)

    # FW gap: max over vertices of ⟨grad, μ̂ - v⟩
    max_gap = float("-inf")
    for v in vertices:
        inner = sum(g * (m - vi) for g, m, vi in zip(grad, mu, v))
        max_gap = max(max_gap, inner)

    return max(0.0, max_gap)


def guaranteed_profit(divergence: float, gap: float) -> float:
    """Proposition 4.1: guaranteed_profit ≥ D(μ̂||θ) - g(μ̂).

    This is a lower bound on extractable profit. The actual profit
    could be higher, but we're guaranteed at least this much.
    """
    return max(0.0, divergence - gap)


def alpha_extraction_check(
    divergence: float,
    gap: float,
    alpha: float = DEFAULT_ALPHA,
) -> bool:
    """Check if we've extracted at least α fraction of available arbitrage.

    Stop trading when: g(μ̂) ≤ (1-α)·D(μ̂||θ)
    This means we've captured α% of the mispricing.

    Returns True if extraction target is met (i.e., we should STOP trading).
    """
    if divergence <= 0:
        return True  # Nothing to extract
    return gap <= (1.0 - alpha) * divergence


def extraction_percentage(divergence: float, gap: float) -> float:
    """What fraction of the arbitrage has been extracted so far?

    extraction = 1 - g/D
    """
    if divergence <= 0:
        return 1.0  # Fully extracted (nothing was there)
    return max(0.0, min(1.0, 1.0 - gap / divergence))


def should_trade(
    divergence: float,
    gap: float,
    *,
    alpha: float = DEFAULT_ALPHA,
    min_threshold: float = MIN_DIVERGENCE_THRESHOLD,
) -> bool:
    """Combined decision: should we trade this opportunity?

    Trade if:
    1. Divergence exceeds minimum threshold (≥ εD = 0.05)
    2. We haven't already extracted α of the profit (gap > (1-α)·D)
    """
    if divergence < min_threshold:
        return False  # Not worth it — less than 5 cents/dollar
    if alpha_extraction_check(divergence, gap, alpha):
        return False  # Already extracted enough
    return True


def calculate_net_profit(
    gross_profit: float,
    fee_rate: float = 0.02,
) -> float:
    """Calculate net profit after Polymarket fees.

    Fees are charged on winnings (profit portion), not on capital.
    """
    if gross_profit <= 0:
        return 0.0
    return gross_profit * (1.0 - fee_rate)


def evaluate_opportunity(
    current_prices: list[float],
    target_prices: list[float] | None = None,
    *,
    alpha: float = DEFAULT_ALPHA,
    min_threshold: float = MIN_DIVERGENCE_THRESHOLD,
    fee_rate: float = 0.02,
) -> ProfitGuarantee:
    """Full evaluation of an arbitrage opportunity.

    Args:
        current_prices: Current market prices for each outcome (θ).
        target_prices: Target distribution (μ̂). If None, uses uniform distribution.
        alpha: Extraction target (default 0.9 = 90%).
        min_threshold: Minimum divergence threshold.
        fee_rate: Fee rate on winnings.

    Returns:
        ProfitGuarantee with all calculated metrics.
    """
    n = len(current_prices)

    # Normalize current prices to a distribution
    total = sum(current_prices)
    if total <= 0:
        return ProfitGuarantee(
            kl_divergence=0.0,
            fw_gap=0.0,
            guaranteed_profit=0.0,
            extraction_pct=1.0,
            should_trade=False,
        )
    theta = [p / total for p in current_prices]

    # Default target: uniform distribution (all outcomes equally likely)
    if target_prices is None:
        mu = [1.0 / n] * n
    else:
        t_total = sum(target_prices)
        mu = [p / t_total for p in target_prices] if t_total > 0 else [1.0 / n] * n

    d = kl_divergence(mu, theta)
    g = frank_wolfe_gap(mu, theta)
    gp = guaranteed_profit(d, g)
    net = calculate_net_profit(gp, fee_rate)
    ext = extraction_percentage(d, g)
    trade = should_trade(d, g, alpha=alpha, min_threshold=min_threshold)

    return ProfitGuarantee(
        kl_divergence=round(d, 6),
        fw_gap=round(g, 6),
        guaranteed_profit=round(net, 6),
        extraction_pct=round(ext, 4),
        should_trade=trade,
    )
