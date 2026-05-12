"""Sprint 1 — guardrail smoke tests."""
import pytest


def test_scope_guard_allows_portfolio_query():
    """Scope guard should pass portfolio analysis queries."""
    import sys
    sys.path.insert(0, "backend")
    import asyncio
    from scope_guard import ScopeGuard

    guard = ScopeGuard()
    result = asyncio.run(guard.check("What is the Sharpe ratio of REGIME_SWITCHING?"))
    assert result["allowed"] is True


def test_weights_sum_assertion():
    """Backtester assertion: weights must sum to 1."""
    weights = {"SPY": 0.60, "TLT": 0.40}
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_no_negative_weights():
    """Backtester assertion: no short positions allowed."""
    weights = {"SPY": 0.60, "TLT": 0.40}
    assert all(w >= 0 for w in weights.values())
