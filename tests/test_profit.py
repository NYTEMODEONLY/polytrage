"""Tests for profit calculator (KL divergence, FW gap, profit guarantees)."""

import math

import pytest

from polytrage.profit import (
    alpha_extraction_check,
    calculate_net_profit,
    evaluate_opportunity,
    extraction_percentage,
    frank_wolfe_gap,
    guaranteed_profit,
    kl_divergence,
    should_trade,
)


class TestKLDivergence:
    """Test KL divergence D(μ̂||θ) computation."""

    def test_identical_distributions(self):
        """D(p||p) = 0 for any distribution."""
        mu = [0.5, 0.5]
        theta = [0.5, 0.5]
        assert kl_divergence(mu, theta) == pytest.approx(0.0, abs=1e-10)

    def test_uniform_vs_skewed(self):
        """D(uniform || skewed) > 0."""
        mu = [0.5, 0.5]
        theta = [0.8, 0.2]
        d = kl_divergence(mu, theta)
        assert d > 0
        # Manual: 0.5*ln(0.5/0.8) + 0.5*ln(0.5/0.2) = 0.5*(-0.47) + 0.5*(0.916) ≈ 0.223
        expected = 0.5 * math.log(0.5 / 0.8) + 0.5 * math.log(0.5 / 0.2)
        assert d == pytest.approx(expected, abs=1e-6)

    def test_three_outcomes(self):
        """KL divergence with 3 outcomes."""
        mu = [1 / 3, 1 / 3, 1 / 3]
        theta = [0.5, 0.3, 0.2]
        d = kl_divergence(mu, theta)
        assert d > 0

    def test_zero_in_mu(self):
        """0 * ln(0/t) = 0 by convention."""
        mu = [1.0, 0.0]
        theta = [0.5, 0.5]
        d = kl_divergence(mu, theta)
        # Only first term: 1.0 * ln(1.0/0.5) = ln(2) ≈ 0.693
        assert d == pytest.approx(math.log(2), abs=1e-6)

    def test_zero_in_theta(self):
        """D is infinite when θ_i = 0 but μ_i > 0."""
        mu = [0.5, 0.5]
        theta = [1.0, 0.0]
        d = kl_divergence(mu, theta)
        assert d == float("inf")

    def test_mismatched_lengths(self):
        """Raises ValueError for different-length distributions."""
        with pytest.raises(ValueError):
            kl_divergence([0.5, 0.5], [0.5, 0.3, 0.2])

    def test_symmetry_broken(self):
        """KL divergence is not symmetric: D(p||q) ≠ D(q||p) in general."""
        p = [0.3, 0.7]
        q = [0.6, 0.4]
        assert kl_divergence(p, q) != pytest.approx(kl_divergence(q, p), abs=1e-4)


class TestFrankWolfeGap:
    """Test Frank-Wolfe gap computation."""

    def test_at_vertex(self):
        """When μ̂ is already a vertex, gap should be ≥ 0."""
        mu = [1.0, 0.0]
        theta = [0.5, 0.5]
        g = frank_wolfe_gap(mu, theta)
        assert g >= 0

    def test_uniform_distribution(self):
        """Gap for uniform distribution."""
        mu = [0.5, 0.5]
        theta = [0.5, 0.5]
        g = frank_wolfe_gap(mu, theta)
        # When mu == theta, gradient = [1, 1], inner products = 0.5 for each vertex
        # Both vertices give same value since grad is constant
        assert g >= 0

    def test_gap_decreases_with_convergence(self):
        """As θ approaches μ̂, the FW gap should decrease."""
        mu = [0.5, 0.5]
        # θ far from μ̂
        g_far = frank_wolfe_gap(mu, [0.9, 0.1])
        # θ closer to μ̂
        g_close = frank_wolfe_gap(mu, [0.6, 0.4])
        assert g_far >= g_close

    def test_custom_vertices(self):
        """Test with custom vertex set."""
        mu = [0.5, 0.5]
        theta = [0.7, 0.3]
        vertices = [[1.0, 0.0], [0.0, 1.0]]
        g = frank_wolfe_gap(mu, theta, vertices=vertices)
        assert g >= 0

    def test_non_negative(self):
        """Gap is always non-negative."""
        for mu, theta in [
            ([0.5, 0.5], [0.3, 0.7]),
            ([0.25, 0.25, 0.25, 0.25], [0.1, 0.2, 0.3, 0.4]),
            ([0.9, 0.1], [0.5, 0.5]),
        ]:
            assert frank_wolfe_gap(mu, theta) >= 0


class TestGuaranteedProfit:
    """Test Proposition 4.1: guaranteed_profit ≥ D - g."""

    def test_positive_guarantee(self):
        """When D > g, guaranteed profit is positive."""
        assert guaranteed_profit(0.10, 0.02) == pytest.approx(0.08, abs=1e-10)

    def test_zero_guarantee(self):
        """When D = g, guaranteed profit is zero."""
        assert guaranteed_profit(0.10, 0.10) == pytest.approx(0.0, abs=1e-10)

    def test_negative_clamped_to_zero(self):
        """When g > D, clamped to 0 (can't have negative guarantee)."""
        assert guaranteed_profit(0.05, 0.10) == 0.0


class TestAlphaExtraction:
    """Test α-extraction stopping criterion."""

    def test_fully_extracted(self):
        """g = 0 means 100% extracted → should stop."""
        assert alpha_extraction_check(0.10, 0.0, alpha=0.9) is True

    def test_mostly_extracted(self):
        """g ≤ (1-0.9)·D = 0.1·D → 90%+ extracted → should stop."""
        # g=0.009 < (1-0.9)*0.10=0.01 → True (more than 90% extracted)
        assert alpha_extraction_check(0.10, 0.009, alpha=0.9) is True

    def test_exactly_at_boundary(self):
        """g just below (1-0.9)·D → 90%+ extracted → should stop."""
        # Use 0.009 to avoid floating point boundary issues with 0.1*0.1
        assert alpha_extraction_check(0.10, 0.009, alpha=0.9) is True
        # And just above: g=0.02 > 0.01 → not extracted enough
        assert alpha_extraction_check(0.10, 0.02, alpha=0.9) is False

    def test_not_enough_extracted(self):
        """g > (1-0.9)·D → less than 90% extracted → keep trading."""
        assert alpha_extraction_check(0.10, 0.05, alpha=0.9) is False

    def test_zero_divergence(self):
        """No mispricing → trivially extracted."""
        assert alpha_extraction_check(0.0, 0.0, alpha=0.9) is True

    def test_extraction_percentage(self):
        """Test extraction percentage calculation."""
        assert extraction_percentage(0.10, 0.01) == pytest.approx(0.90, abs=1e-6)
        assert extraction_percentage(0.10, 0.00) == pytest.approx(1.00, abs=1e-6)
        assert extraction_percentage(0.10, 0.10) == pytest.approx(0.00, abs=1e-6)
        assert extraction_percentage(0.0, 0.0) == pytest.approx(1.0, abs=1e-6)


class TestShouldTrade:
    """Test combined trading decision."""

    def test_good_opportunity(self):
        """High divergence, gap still above (1-α)·D → should trade."""
        # D=0.10, g=0.05 → extraction=50%, well below α=90% → trade
        assert should_trade(0.10, 0.05) is True

    def test_below_threshold(self):
        """Divergence below εD = 0.05 → skip."""
        assert should_trade(0.03, 0.02) is False

    def test_already_extracted(self):
        """Already extracted 90%+ → don't trade more."""
        # D=0.10, g=0.005 → g ≤ (1-0.9)*0.10=0.01 → 95% extracted → stop
        assert should_trade(0.10, 0.005, alpha=0.9) is False

    def test_custom_threshold(self):
        """Custom minimum threshold."""
        # D=0.03 ≥ 0.02, g=0.02 > (1-0.9)*0.03=0.003 → not extracted → trade
        assert should_trade(0.03, 0.02, min_threshold=0.02) is True
        assert should_trade(0.01, 0.005, min_threshold=0.02) is False


class TestNetProfit:
    """Test net profit calculation after fees."""

    def test_standard_fee(self):
        """2% fee on $0.10 gross profit → $0.098 net."""
        assert calculate_net_profit(0.10, 0.02) == pytest.approx(0.098, abs=1e-6)

    def test_zero_profit(self):
        """No gross profit → no net profit."""
        assert calculate_net_profit(0.0) == 0.0

    def test_negative_profit(self):
        """Negative gross → clamped to 0."""
        assert calculate_net_profit(-0.05) == 0.0


class TestEvaluateOpportunity:
    """Test full opportunity evaluation pipeline."""

    def test_uniform_prices(self):
        """Equal prices → no divergence from uniform target."""
        result = evaluate_opportunity([0.50, 0.50])
        assert result.kl_divergence == pytest.approx(0.0, abs=1e-4)
        assert result.should_trade is False

    def test_skewed_prices(self):
        """Skewed prices → positive divergence."""
        result = evaluate_opportunity([0.80, 0.20])
        assert result.kl_divergence > 0
        assert result.extraction_pct >= 0

    def test_three_outcomes(self):
        """Multi-outcome evaluation."""
        result = evaluate_opportunity([0.50, 0.30, 0.20])
        assert result.kl_divergence > 0

    def test_custom_target(self):
        """Evaluate against custom target distribution."""
        result = evaluate_opportunity(
            [0.60, 0.40],
            target_prices=[0.50, 0.50],
        )
        assert result.kl_divergence > 0
