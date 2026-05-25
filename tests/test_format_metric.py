"""
tests/test_format_metric.py — Workstream-follow-up (May 28 2026).

Centralised metric formatter. The slide generator, midpoint paper
generator, executive brief generator, and every agent prompt that
injects a numeric metric into the LLM input route through
format_metric() so precision is a property of the metric TYPE rather
than of the call site that happens to print it.

Returns a STRING, never a float. None / non-numeric returns the
em-dash placeholder so every callsite renders well-formed even when
the upstream metric is missing.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")


class TestFormatMetricRatios:
    """Ratios — 4dp on the raw value."""

    def test_sharpe_ratio_at_4dp(self):
        from tools.academic_export import format_metric

        assert format_metric(0.5472, "sharpe_ratio") == "0.5472"
        # Trailing zeros preserved — the user wanted "0.5472, not 0.55".
        assert format_metric(0.55, "sharpe_ratio") == "0.5500"
        # Negative ratio.
        assert format_metric(-0.1234, "sharpe_ratio") == "-0.1234"

    def test_every_ratio_metric_type_uses_4dp(self):
        from tools.academic_export import format_metric

        for kind in ("sharpe_ratio", "sortino_ratio", "calmar_ratio",
                     "information_ratio", "p_value"):
            out = format_metric(0.1234, kind)
            assert out == "0.1234", f"{kind}: got {out!r}"

    def test_p_value_extremes(self):
        from tools.academic_export import format_metric

        assert format_metric(0.0, "p_value") == "0.0000"
        # 4dp preserves the precision the audit cares about.
        assert format_metric(0.00499, "p_value") == "0.0050"


class TestFormatMetricFourDpPercents:
    """CAGR / Volatility / Max drawdown — 4dp on the percent."""

    def test_cagr_at_4dp_percent(self):
        from tools.academic_export import format_metric

        # 0.0854 decimal → 8.5400% with 4dp on the percent.
        assert format_metric(0.0854, "cagr") == "8.5400%"
        # 0.0854729 → 8.5473%.
        assert format_metric(0.0854729, "cagr") == "8.5473%"

    def test_volatility_at_4dp_percent(self):
        from tools.academic_export import format_metric

        assert format_metric(0.15, "volatility") == "15.0000%"
        assert format_metric(0.1547, "volatility") == "15.4700%"

    def test_max_drawdown_at_4dp_percent(self):
        from tools.academic_export import format_metric

        assert format_metric(-0.254, "max_drawdown") == "-25.4000%"
        assert format_metric(-0.2547, "max_drawdown") == "-25.4700%"


class TestFormatMetricTwoDpPercents:
    """Weight / Turnover — 2dp on the percent. Distinct from CAGR/Vol
    because weight and turnover are presentation values where 4dp adds
    no information."""

    def test_weight_at_2dp_percent(self):
        from tools.academic_export import format_metric

        assert format_metric(0.30, "weight") == "30.00%"
        assert format_metric(0.3047, "weight") == "30.47%"

    def test_turnover_at_2dp_percent(self):
        from tools.academic_export import format_metric

        assert format_metric(0.047, "turnover") == "4.70%"


class TestFormatMetricCurrency:
    """Currency — 2dp + thousands grouping."""

    def test_currency_with_thousands_grouping(self):
        from tools.academic_export import format_metric

        assert format_metric(1234567.89, "currency") == "$1,234,567.89"
        assert format_metric(42.0, "currency") == "$42.00"

    def test_currency_zero(self):
        from tools.academic_export import format_metric

        assert format_metric(0.0, "currency") == "$0.00"


class TestFormatMetricFallback:
    """Unknown metric_type falls back to 4dp — a new metric never
    silently inherits 2dp formatting."""

    def test_unknown_kind_falls_back_to_4dp(self):
        from tools.academic_export import format_metric

        assert format_metric(0.5472, "metric_we_havent_named_yet") == "0.5472"


class TestFormatMetricMissing:
    """None / non-numeric / NaN returns the em-dash placeholder so a
    missing metric never crashes a generator and never prints
    'None' or 'nan' to a reader."""

    def test_none_returns_em_dash(self):
        from tools.academic_export import format_metric

        assert format_metric(None, "sharpe_ratio") == "—"

    def test_non_numeric_returns_em_dash(self):
        from tools.academic_export import format_metric

        assert format_metric("not a number", "sharpe_ratio") == "—"
        assert format_metric([1, 2, 3], "sharpe_ratio") == "—"

    def test_bool_treated_as_numeric(self):
        # Python's bool IS a subclass of int — explicit by design.
        # A True bool surviving to this layer is a bug at the call
        # site, not in the formatter. Documented so the behaviour is
        # explicit and a future refactor doesn't silently change it.
        from tools.academic_export import format_metric

        # True → 1 → "1.0000". The formatter does not special-case bools.
        assert format_metric(True, "sharpe_ratio") == "1.0000"
