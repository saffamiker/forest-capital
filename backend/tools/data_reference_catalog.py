"""
tools/data_reference_catalog.py

Static catalog backing the Data Reference Sheet endpoint
(GET /api/v1/export/data-reference-sheet). Maps every
substitution-table token to its display label, source
provenance, and the documents that reference it.

The catalog is the SOURCE OF TRUTH for which tokens appear
in the reference sheet, in what order, and under which
category. The endpoint walks this catalog at request time,
calls build_substitution_table() to resolve current values,
and zips them together into the response shape the frontend
DataReferenceSheetPanel consumes.

Layout: CATALOG is a list of (category_key, [TokenEntry])
tuples in display order. Each TokenEntry carries enough
metadata for the panel to render the row plus the audit
provenance Bob needs to defend any figure in the
submission.

Per-strategy tokens (10 strategies x 5 metrics + factor
loadings) are EXPANDED at endpoint-call time -- this module
just declares the per-strategy stub once via
APPENDIX_PER_STRATEGY_TOKENS to keep the static catalog
short.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenEntry:
    """One row in the Data Reference Sheet.

    Attributes:
        token              the substitution token literal,
                           e.g. "{{OOS_SHARPE_BLEND}}"
        label              human display string for the panel,
                           e.g. "OOS Sharpe -- regime-switching blend"
        source             provenance string identifying WHERE
                           the value comes from. Examples:
                             - "academic_deck.OOS_SHARPE_REGIME_CONDITIONAL"
                             - "strategy_cache.BENCHMARK.sharpe_ratio"
                             - "regime_signals_cache.vix_level"
                             - "cio_recommendation.confidence.ess"
                             - "derived: blend/benchmark - 1"
        is_locked          True when the value is an academic_deck
                           constant (locked at submission lock,
                           cannot drift); False when sourced from
                           live cache (strategy_results_cache,
                           cio_recommendation, regime_signals_cache).
        document_locations list of human-readable strings naming
                           every document section that quotes this
                           value. Example:
                             ["Brief §1, §3, §5",
                              "Deck slide 3",
                              "Appendix Section B"]
    """
    token: str
    label: str
    source: str
    is_locked: bool
    document_locations: tuple[str, ...]


# ── Per-strategy token stubs ─────────────────────────────────────
#
# Expanded at endpoint-call time to (strategy_name, TokenEntry)
# pairs. Mirrors _APPENDIX_METRIC_FORMATTERS in
# numeric_substitution.py so the catalog automatically tracks any
# future addition to the per-strategy token set.
APPENDIX_PER_STRATEGY_METRICS: tuple[tuple[str, str], ...] = (
    ("SHARPE",       "Sharpe ratio"),
    ("MAX_DD",       "Maximum drawdown"),
    ("CAGR",         "CAGR"),
    ("VOLATILITY",   "Volatility (annualised)"),
    ("RECOVERY",     "Drawdown recovery (months)"),
)


# All 10 strategies the platform tracks. The order matches the
# project universe (see academic_deck._STATIC_STRATEGIES +
# _DYNAMIC_STRATEGIES + BENCHMARK).
ALL_STRATEGIES: tuple[str, ...] = (
    "BENCHMARK", "REGIME_SWITCHING", "CLASSIC_60_40",
    "EQUAL_WEIGHT", "RISK_PARITY", "MIN_VARIANCE",
    "VOL_TARGETING", "BLACK_LITTERMAN",
    "MOMENTUM_ROTATION", "MAX_SHARPE_ROLLING",
)


# Factor loading metric names. Per-strategy expansion populates
# each strategy with one row per metric. R-squared, alpha, beta
# columns are read from data["factor_loadings"][strategy_name].
FACTOR_LOADING_METRICS: tuple[tuple[str, str], ...] = (
    ("alpha",        "Carhart alpha (monthly)"),
    ("beta",         "Market beta (mkt_rf)"),
    ("smb_beta",     "Size beta (smb)"),
    ("hml_beta",     "Value beta (hml)"),
    ("r_squared",    "R-squared"),
)


# ── Categorised catalog ───────────────────────────────────────────


# Document location shorthand
_BRIEF_S1 = "Brief §1 Executive Summary"
_BRIEF_S2 = "Brief §2 Methodology"
_BRIEF_S3 = "Brief §3 Key Findings"
_BRIEF_S4 = "Brief §4 Limitations"
_BRIEF_S5 = "Brief §5 Final Recommendations"
_BRIEF_S6 = "Brief §6 Visuals"

_DECK_S1 = "Deck slide 1 (verdict)"
_DECK_S2 = "Deck slide 2 (strategy categories)"
_DECK_S3 = "Deck slide 3 (the numbers)"
_DECK_S4 = "Deck slide 4 (2022 break)"
_DECK_S5 = "Deck slide 5 (drawdown)"
_DECK_S6 = "Deck slide 6 (OOS)"
_DECK_S7 = "Deck slide 7 (macro watchpoints)"
_DECK_S11 = "Deck slide 11 (closing answer)"

_APP_A = "Appendix §A Data sources"
_APP_B = "Appendix §B Full strategy performance"
_APP_C = "Appendix §C Statistical significance"
_APP_D = "Appendix §D Bootstrap CI on Sharpe"
_APP_E = "Appendix §E Carhart factor loadings"
_APP_F = "Appendix §F Drawdown analysis"
_APP_G = "Appendix §G Cost sensitivity"


CATALOG: tuple[tuple[str, str, tuple[TokenEntry, ...]], ...] = (
    # ── Study period --------------------------------------------------
    ("study_period", "Study period", (
        TokenEntry(
            token="{{STUDY_MONTHS}}",
            label="Study period length (months)",
            source="data.study_period.n_months",
            is_locked=False,
            document_locations=(
                _BRIEF_S2, _BRIEF_S4, _APP_A,
            )),
        TokenEntry(
            token="{{STUDY_START}}",
            label="Study period start month",
            source="academic_deck (constant, July 2002)",
            is_locked=True,
            document_locations=(_BRIEF_S2, _APP_A)),
        TokenEntry(
            token="{{STUDY_END}}",
            label="Study period end month",
            source="academic_deck (constant, May 2026)",
            is_locked=True,
            document_locations=(_BRIEF_S2, _APP_A)),
        TokenEntry(
            token="{{DATA_HASH}}",
            label="Strategy cache hash (8-char prefix)",
            source="current_data_hash() (truncated)",
            is_locked=False,
            document_locations=(
                "Brief footer", "Appendix footer", "Deck footer")),
    )),

    # ── OOS window ---------------------------------------------------
    ("oos_window", "Out-of-sample window", (
        TokenEntry(
            token="{{OOS_WINDOW_MONTHS}}",
            label="OOS window length (months)",
            source="academic_deck.OOS_WINDOW_MONTHS",
            is_locked=True,
            document_locations=(
                _BRIEF_S3, _BRIEF_S4, _DECK_S6, _APP_C)),
        TokenEntry(
            token="{{OOS_WINDOW_PCT_OF_STUDY}}",
            label="OOS window as % of full study",
            source="academic_deck.OOS_WINDOW_PCT_OF_STUDY",
            is_locked=True,
            document_locations=(_BRIEF_S4,)),
        TokenEntry(
            token="{{OOS_WINDOW}}",
            label="OOS window definition (text)",
            source="academic_deck (constant)",
            is_locked=True,
            document_locations=(_BRIEF_S2, _DECK_S6)),
    )),

    # ── Full-period performance --------------------------------------
    ("full_period_performance", "Full-period performance", (
        TokenEntry(
            token="{{OOS_SHARPE_BLEND}}",
            label="Blend OOS Sharpe (locked academic value)",
            source="academic_deck.OOS_SHARPE_REGIME_CONDITIONAL",
            is_locked=True,
            document_locations=(
                _BRIEF_S1, _BRIEF_S3, _BRIEF_S5,
                _DECK_S1, _DECK_S3, _DECK_S6,
                _APP_B)),
        TokenEntry(
            token="{{OOS_SHARPE_BENCHMARK}}",
            label="Benchmark OOS Sharpe (locked academic value)",
            source="academic_deck.OOS_SHARPE_BENCHMARK",
            is_locked=True,
            document_locations=(
                _BRIEF_S1, _BRIEF_S3, _BRIEF_S5,
                _DECK_S1, _DECK_S3, _DECK_S6,
                _APP_B)),
        TokenEntry(
            token="{{OOS_SHARPE_IMPROVEMENT_PCT}}",
            label="Blend Sharpe vs benchmark (%)",
            source="derived: blend/benchmark - 1",
            is_locked=True,
            document_locations=(_BRIEF_S1, _DECK_S3)),
        TokenEntry(
            token="{{REGIME_SWITCHING_SHARPE}}",
            label="Blend full-period Sharpe (cache)",
            source="strategy_cache.REGIME_SWITCHING.sharpe_ratio",
            is_locked=False,
            document_locations=(_APP_B, _DECK_S3)),
        TokenEntry(
            token="{{BENCHMARK_SHARPE}}",
            label="Benchmark full-period Sharpe (cache)",
            source="strategy_cache.BENCHMARK.sharpe_ratio",
            is_locked=False,
            document_locations=(_APP_B, _DECK_S3)),
        TokenEntry(
            token="{{CLASSIC_6040_SHARPE}}",
            label="Classic 60/40 full-period Sharpe (cache)",
            source="strategy_cache.CLASSIC_60_40.sharpe_ratio",
            is_locked=False,
            document_locations=(_APP_B, _DECK_S2, _DECK_S3)),
    )),

    # ── Pre/post 2022 ------------------------------------------------
    ("pre_post_2022", "Pre / post-2022 sub-period", (
        TokenEntry(
            token="{{REGIME_SWITCHING_POST2022_SHARPE}}",
            label="Blend post-2022 Sharpe",
            source="regime_conditional.REGIME_SWITCHING.post_2022_sharpe",
            is_locked=False,
            document_locations=(_APP_B, _APP_G, _DECK_S6)),
        TokenEntry(
            token="{{BENCHMARK_POST2022_SHARPE}}",
            label="Benchmark post-2022 Sharpe",
            source="regime_conditional.BENCHMARK.post_2022_sharpe",
            is_locked=False,
            document_locations=(_APP_B, _APP_G, _DECK_S6)),
        TokenEntry(
            token="{{CLASSIC_6040_POST2022_SHARPE}}",
            label="Classic 60/40 post-2022 Sharpe",
            source="regime_conditional.CLASSIC_60_40.post_2022_sharpe",
            is_locked=False,
            document_locations=(_APP_B, _DECK_S6)),
        TokenEntry(
            token="{{REGIME_SWITCHING_PRE2022_SHARPE}}",
            label="Blend pre-2022 Sharpe (in-sample)",
            source="regime_conditional.REGIME_SWITCHING.pre_2022_sharpe",
            is_locked=False,
            document_locations=(_DECK_S6,)),
        TokenEntry(
            token="{{BENCHMARK_PRE2022_SHARPE}}",
            label="Benchmark pre-2022 Sharpe (in-sample)",
            source="regime_conditional.BENCHMARK.pre_2022_sharpe",
            is_locked=False,
            document_locations=(_DECK_S6,)),
    )),

    # ── Drawdown / recovery ------------------------------------------
    ("drawdown_recovery", "Drawdown and recovery", (
        TokenEntry(
            token="{{REGIME_SWITCHING_MAX_DD}}",
            label="Blend maximum drawdown",
            source="academic_deck.MAX_DRAWDOWN_REGIME_CONDITIONAL",
            is_locked=True,
            document_locations=(
                _BRIEF_S1, _BRIEF_S3, _BRIEF_S5,
                _DECK_S1, _DECK_S5, _APP_F)),
        TokenEntry(
            token="{{BENCHMARK_MAX_DD}}",
            label="Benchmark maximum drawdown",
            source="academic_deck.MAX_DRAWDOWN_BENCHMARK",
            is_locked=True,
            document_locations=(
                _BRIEF_S1, _BRIEF_S3, _BRIEF_S5,
                _DECK_S1, _DECK_S5, _APP_F)),
        TokenEntry(
            token="{{CLASSIC_6040_MAX_DD}}",
            label="Classic 60/40 maximum drawdown",
            source="strategy_cache.CLASSIC_60_40.max_drawdown",
            is_locked=False,
            document_locations=(_DECK_S5, _APP_F)),
        TokenEntry(
            token="{{DD_REDUCTION_REGIME_SWITCHING}}",
            label="Blend DD reduction vs benchmark (pp)",
            source="derived: |bench_dd| - |blend_dd|",
            is_locked=True,
            document_locations=(_DECK_S5,)),
        TokenEntry(
            token="{{REGIME_SWITCHING_RECOVERY}}",
            label="Blend recovery (trading-day months)",
            source="strategy_cache.REGIME_SWITCHING.drawdown_recovery_days",
            is_locked=False,
            document_locations=(_BRIEF_S3, _APP_F)),
        TokenEntry(
            token="{{REGIME_SWITCHING_RECOVERY_MONTHS}}",
            label="Blend recovery (with 'months' unit)",
            source="strategy_cache.REGIME_SWITCHING.drawdown_recovery_days",
            is_locked=False,
            document_locations=(_BRIEF_S3, _APP_F)),
        TokenEntry(
            token="{{BENCHMARK_RECOVERY}}",
            label="Benchmark recovery (trading-day months)",
            source="strategy_cache.BENCHMARK.drawdown_recovery_days",
            is_locked=False,
            document_locations=(_BRIEF_S3, _APP_F)),
        TokenEntry(
            token="{{BENCHMARK_RECOVERY_MONTHS}}",
            label="Benchmark recovery (with 'months' unit)",
            source="strategy_cache.BENCHMARK.drawdown_recovery_days",
            is_locked=False,
            document_locations=(_BRIEF_S3, _APP_F)),
        TokenEntry(
            token="{{CLASSIC_6040_RECOVERY}}",
            label="Classic 60/40 recovery (trading-day months)",
            source="strategy_cache.CLASSIC_60_40.drawdown_recovery_days",
            is_locked=False,
            document_locations=(_BRIEF_S3, _APP_F)),
        TokenEntry(
            token="{{CLASSIC_6040_RECOVERY_MONTHS}}",
            label="Classic 60/40 recovery (with 'months' unit)",
            source="strategy_cache.CLASSIC_60_40.drawdown_recovery_days",
            is_locked=False,
            document_locations=(_BRIEF_S3, _APP_F)),
    )),

    # ── Correlation regime -------------------------------------------
    ("correlation", "Equity-IG correlation regime", (
        TokenEntry(
            token="{{PRE_2022_EQ_IG_CORR}}",
            label="Equity-IG correlation, pre-2022 (rolling avg)",
            source="academic_deck.CORRELATION_PRE_2022",
            is_locked=True,
            document_locations=(_BRIEF_S1, _BRIEF_S6, _DECK_S4)),
        TokenEntry(
            token="{{POST_2022_EQ_IG_CORR}}",
            label="Equity-IG correlation, post-2022 (rolling avg)",
            source="academic_deck.CORRELATION_POST_2022",
            is_locked=True,
            document_locations=(_BRIEF_S1, _BRIEF_S6, _DECK_S4)),
    )),

    # ── Live regime / allocation -------------------------------------
    ("live_regime", "Live regime + allocation", (
        TokenEntry(
            token="{{CURRENT_REGIME}}",
            label="Current regime classification",
            source="cio_recommendation.regime",
            is_locked=False,
            document_locations=(_BRIEF_S5, _DECK_S7, _DECK_S11)),
        TokenEntry(
            token="{{REGIME_CONFIDENCE}}",
            label="Current regime confidence",
            source="cio_recommendation.confidence.probability",
            is_locked=False,
            document_locations=(_DECK_S7,)),
        TokenEntry(
            token="{{CURRENT_EQUITY_PCT}}",
            label="Implied equity allocation (live blend)",
            source="implied_allocation.equity_pct",
            is_locked=False,
            document_locations=(_BRIEF_S5, _DECK_S11)),
        TokenEntry(
            token="{{CURRENT_IG_PCT}}",
            label="Implied IG bond allocation",
            source="implied_allocation.ig_bond_pct",
            is_locked=False,
            document_locations=(_DECK_S11,)),
        TokenEntry(
            token="{{CURRENT_HY_PCT}}",
            label="Implied HY bond allocation",
            source="implied_allocation.hy_bond_pct",
            is_locked=False,
            document_locations=(_DECK_S11,)),
        TokenEntry(
            token="{{BLEND_REGIME_SWITCHING_WT}}",
            label="Live blend weight: REGIME_SWITCHING",
            source="cio_recommendation.blend_weights.REGIME_SWITCHING",
            is_locked=False,
            document_locations=(_DECK_S11,)),
        TokenEntry(
            token="{{BLEND_BENCHMARK_WT}}",
            label="Live blend weight: BENCHMARK",
            source="cio_recommendation.blend_weights.BENCHMARK",
            is_locked=False,
            document_locations=(_DECK_S11,)),
        TokenEntry(
            token="{{BLEND_CLASSIC_6040_WT}}",
            label="Live blend weight: CLASSIC_60_40",
            source="cio_recommendation.blend_weights.CLASSIC_60_40",
            is_locked=False,
            document_locations=(_DECK_S11,)),
        TokenEntry(
            token="{{VIX_CURRENT}}",
            label="VIX level (live macro signal)",
            source="regime_signals_cache.vix_level",
            is_locked=False,
            document_locations=(_DECK_S7,)),
        TokenEntry(
            token="{{CREDIT_SPREAD_CURRENT}}",
            label="HY credit spread (live)",
            source="regime_signals_cache.credit_spread",
            is_locked=False,
            document_locations=(_DECK_S7,)),
        TokenEntry(
            token="{{YIELD_CURVE_CURRENT}}",
            label="Yield curve slope (live)",
            source="regime_signals_cache.yield_curve_slope",
            is_locked=False,
            document_locations=(_DECK_S7,)),
        TokenEntry(
            token="{{EQUITY_TREND_CURRENT}}",
            label="Equity trend (live)",
            source="regime_signals_cache.equity_trend",
            is_locked=False,
            document_locations=(_DECK_S7,)),
        TokenEntry(
            token="{{ESS_CURRENT}}",
            label="Effective sample size (Kish, live)",
            source="cio_recommendation.confidence.ess",
            is_locked=False,
            document_locations=(_DECK_S7,)),
    )),

    # ── Cost sensitivity ---------------------------------------------
    ("cost_sensitivity", "Transaction cost sensitivity", (
        TokenEntry(
            token="{{REGIME_SWITCHING_TURNOVER}}",
            label="Blend annualized turnover",
            source="regime_conditional.REGIME_SWITCHING.annualized_turnover",
            is_locked=False,
            document_locations=(_APP_G,)),
        TokenEntry(
            token="{{NET_SHARPE_10BP}}",
            label="Net Sharpe @ 10 bps cost",
            source="strategy_cache.net_sharpe_10bp",
            is_locked=False,
            document_locations=(_BRIEF_S4, _APP_G)),
        TokenEntry(
            token="{{NET_SHARPE_15BP}}",
            label="Net Sharpe @ 15 bps cost",
            source="strategy_cache.net_sharpe_15bp",
            is_locked=False,
            document_locations=(_BRIEF_S4, _APP_G)),
        TokenEntry(
            token="{{NET_SHARPE_20BP}}",
            label="Net Sharpe @ 20 bps cost",
            source="strategy_cache.net_sharpe_20bp",
            is_locked=False,
            document_locations=(_BRIEF_S4, _APP_G)),
    )),

    # ── Play-by-play scorecard ---------------------------------------
    ("play_by_play", "Play-by-play scorecard", (
        TokenEntry(
            token="{{PLAY_BY_PLAY_VALUE_ADD}}",
            label="Value-add events (count)",
            source="academic_deck.PLAY_BY_PLAY_ADD_VALUE",
            is_locked=True,
            document_locations=(
                _BRIEF_S3, _BRIEF_S5, _DECK_S4)),
        TokenEntry(
            token="{{PLAY_BY_PLAY_TOTAL}}",
            label="Total rebalance events tested",
            source="academic_deck.PLAY_BY_PLAY_EVENTS",
            is_locked=True,
            document_locations=(
                _BRIEF_S3, _BRIEF_S5, _DECK_S4)),
    )),

    # ── Tail risk -----------------------------------------------------
    ("tail_risk", "Tail risk", (
        TokenEntry(
            token="{{CVAR_99_BENCHMARK}}",
            label="Benchmark CVaR @ 99% (annualized)",
            source="strategy_cache.BENCHMARK.cvar_99_annualized",
            is_locked=False,
            document_locations=(_DECK_S5,)),
    )),
)


# ── Per-strategy expansion (factor loadings + per-strategy
#    SHARPE / MAX_DD / CAGR / VOLATILITY / RECOVERY tokens) ─────────


def expand_per_strategy_appendix_metrics() -> tuple[TokenEntry, ...]:
    """The 10-strategy x 5-metric token grid the appendix
    Sections B-F tables surface. Mirrors the auto-generation
    inside numeric_substitution._append_per_strategy_tokens so
    the catalog stays in lockstep with the actual table builder.

    Returns a flat tuple of TokenEntry rows, one per
    (strategy, metric) pair = 50 entries.
    """
    entries: list[TokenEntry] = []
    for strategy in ALL_STRATEGIES:
        for metric_suffix, metric_label in (
            APPENDIX_PER_STRATEGY_METRICS
        ):
            cache_field = {
                "SHARPE":     "sharpe_ratio",
                "MAX_DD":     "max_drawdown",
                "CAGR":       "cagr",
                "VOLATILITY": "volatility",
                "RECOVERY":   "drawdown_recovery_days",
            }[metric_suffix]
            doc_section = {
                "SHARPE":     _APP_B,
                "MAX_DD":     _APP_F,
                "CAGR":       _APP_B,
                "VOLATILITY": _APP_B,
                "RECOVERY":   _APP_F,
            }[metric_suffix]
            entries.append(TokenEntry(
                token=f"{{{{{strategy}_{metric_suffix}}}}}",
                label=f"{strategy} {metric_label}",
                source=f"strategy_cache.{strategy}.{cache_field}",
                is_locked=False,
                document_locations=(doc_section,)))
    return tuple(entries)


def expand_per_strategy_factor_loadings() -> tuple[TokenEntry, ...]:
    """The per-strategy factor regression grid for Appendix
    Section E. Five metrics x 10 strategies = 50 entries.

    Factor-loading tokens don't pass through the substitution
    table -- the Section E table renderer reads them directly
    from data['factor_loadings']. They appear in the reference
    sheet so Bob can cross-check the table against the
    underlying regression output."""
    entries: list[TokenEntry] = []
    for strategy in ALL_STRATEGIES:
        for metric, label in FACTOR_LOADING_METRICS:
            entries.append(TokenEntry(
                token=f"factor_loadings.{strategy}.{metric}",
                label=f"{strategy} -- {label}",
                source=f"data.factor_loadings.{strategy}.{metric}",
                is_locked=False,
                document_locations=(_APP_E,)))
    return tuple(entries)


# ── Category labels for the panel display ─────────────────────────


CATEGORY_LABELS: dict[str, str] = {
    "study_period":              "Study period",
    "oos_window":                "Out-of-sample window",
    "full_period_performance":   "Full-period performance",
    "pre_post_2022":             "Pre / post-2022 sub-period",
    "drawdown_recovery":         "Drawdown and recovery",
    "correlation":               "Equity-IG correlation regime",
    "live_regime":               "Live regime + allocation",
    "cost_sensitivity":          "Transaction cost sensitivity",
    "play_by_play":              "Play-by-play scorecard",
    "tail_risk":                 "Tail risk",
    "per_strategy_appendix":     "Per-strategy metrics (all 10 strategies)",
    "factor_loadings":           "Carhart factor loadings (per strategy)",
}
