"""tools/numeric_substitution.py -- deterministic numeric substitution
for the executive brief.

WHY THIS MODULE EXISTS

Before this module, the brief Sonnet section writer wrote raw numeric
figures (e.g. "OOS Sharpe 1.24 vs 0.73") directly into its prose.
When the writer was uncertain about a figure it would emit a
"[[VERIFY: confirm X]]" placeholder. Both modes were failure-prone:
the writer could hallucinate a figure (no audit until post-gen
numeric cross-reference), or leave a verify tag that reached the
operator if the strip step missed it.

This module's contract: the writer never emits raw performance
numbers. It uses placeholder tokens ("{{OOS_SHARPE_BLEND}}") and
the platform substitutes verified cache values after generation.

ARCHITECTURE

  build_substitution_table(strategy_cache, cio_recommendation,
                           data_hash)
      Pure function. Reads the cache + CIO overlay and produces a
      flat {token -> string-value} mapping. Every value is pre-
      formatted (Sharpe to 2dp, percentage with sign, correlation
      with sign).

  apply_substitutions(text, table)
      Pure function. Replaces every token-in-text that's in the
      table. Returns (substituted_text, list_of_tokens_replaced).
      Unknown tokens are left intact so the post-generation audit
      (check_unresolved_placeholders) can flag them.

  format_sharpe, format_pct, format_corr
      The three formatters. Centralised so a future precision
      change is one edit, not 20 across the brief specs.

WIRING

  harness_narrative (tools/academic_export) calls apply_substitutions
  on the raw Sonnet output before the evaluator scores it -- so the
  evaluator sees real numbers, not tokens, and judges the prose the
  human reader will read.

  document_audit.check_unresolved_placeholders runs as a final
  post-substitution audit. Any {{...}} left over means the writer
  invented a token the table didn't anticipate; the operator must
  add the token to the table or rewrite the section.

This module has NO LLM calls and NO database reads. Test it by
constructing a mock strategy_cache + cio_recommendation and asserting
the output dictionary.
"""
from __future__ import annotations

import logging
import re
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    log = logging.getLogger(__name__)  # type: ignore[assignment]


# Approximate trading days per month -- used to convert the
# backtester's drawdown_recovery_days into the brief-facing
# "recovery months" figure. The cache stores DAYS; the brief reads
# MONTHS. One number, one place. Same convention the analytics
# narrative uses.
_TRADING_DAYS_PER_MONTH = 21


# Bump this integer when build_substitution_table's kwargs signature
# changes in a way that affects WHICH tokens the table emits. The
# get_substitution_table cache key encodes this version, so a deploy
# that bumps it forces every entry from the prior version to miss --
# even if the data_hash is unchanged. Without this, a process whose
# in-memory _substitution_cache was populated BEFORE the new kwargs
# became available will serve a stale token-less table for the rest
# of its lifetime.
#
# History:
#   v1 -- original; cache key was just data_hash
#   v2 -- June 22 2026; PR #374 added regime_conditional /
#         factor_loadings / cost_sensitivity kwargs. Cache key
#         now also encodes which of those are populated, and a
#         version bump ensures pre-#374 entries miss on first
#         post-deploy request.
#   v3 -- June 22 2026; added crisis_performance kwarg + new
#         per-strategy GFC / Rate Shock 2022 drawdown + post-
#         2022 CAGR tokens. Cache key adds the bool fingerprint
#         for crisis_performance; the version bump invalidates
#         v2 entries on first post-deploy request.
#     v4: Adds the eight data-provenance tokens (EQUITY_SERIES,
#         IG_SERIES, HY_SERIES, RISK_FREE_SERIES, FACTOR_SERIES,
#         IG_SPLICE_DATE, HY_EXTENSION_DATE, HY_TRACKING_ERROR)
#         as static descriptive strings. June 25 2026; the
#         version bump invalidates v3 entries on first post-
#         deploy request so the new tokens land in the cached
#         tables that the editor + audit + receipt all read from.
# June 27 2026 -- bumped 4 -> 6. The Bug 2 root cause (slide 7
# regime confidence 62.7% instead of the freshly-computed 95.4%)
# was the substitution table being cache-keyed only by data_hash,
# so a LIVE CIO row update did not invalidate the cached table.
# Fix: _cache_key now includes the cio_recommendation row's
# stable identity (id / recommendation_id / computed_at) so a CIO
# update naturally invalidates the cache. Bumping the cache
# version invalidates every pre-fix cached entry on first post-
# deploy read so the new key-shape takes effect immediately.
#
# Note: live CIO + regime tokens are intentionally LIVE -- not
# frozen by submission_freeze. Only historical analytics tokens
# (regime_conditional / factor_loadings / cost_sensitivity /
# crisis_performance) are in the freeze scope; that path is
# handled by load_substitution_metric_sources(data_hash=...).
_CACHE_VERSION = 6


def format_sharpe(v: Any) -> str:
    """Sharpe-ratio formatter. 2dp. None / non-numeric returns an
    em dash so the substitution still happens (an em dash in the
    brief is a visible signal the value was missing; a placeholder
    left in is a verify-tag-style audit signal we'd rather avoid)."""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def format_pct(v: Any) -> str:
    """Percentage formatter. Input is a decimal fraction (0.526 ->
    "52.6%"). Sign is preserved -- a max drawdown of -0.526 renders
    as "-52.6%", a correlation shift of +0.62 renders as "62.0%".
    1dp precision."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f * 100:.1f}%"


def format_corr(v: Any) -> str:
    """Correlation formatter. 2dp. Explicit sign is preserved so a
    negative correlation reads as "-0.05" (not just "0.05") -- the
    sign IS the finding for the equity-bond correlation regime
    break section."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f >= 0:
        return f"+{f:.2f}"
    return f"{f:.2f}"


def format_months_from_days(v: Any) -> str:
    """The backtester stores recovery in DAYS but the brief reads
    MONTHS. Convert + suffix. None or non-numeric returns an em
    dash -- never a placeholder leak.

    Returns the "<n> months" form. Pair with format_months_only
    when the LLM writes "months" after the token in prose (the
    "37 months months" bug from June 21 2026)."""
    try:
        days = float(v)
    except (TypeError, ValueError):
        return "—"
    if days <= 0:
        return "—"
    months = round(days / _TRADING_DAYS_PER_MONTH)
    return f"{months} months"


def format_months_only(v: Any) -> str:
    """The number-only companion to format_months_from_days. Used
    by the *_RECOVERY_MONTHS tokens (June 21 2026). The split lets
    the LLM choose between writing the bare number (and adding
    "months" in prose) vs the pre-formatted "<n> months" string.
    Em dash on invalid input -- never a placeholder leak."""
    try:
        days = float(v)
    except (TypeError, ValueError):
        return "—"
    if days <= 0:
        return "—"
    months = round(days / _TRADING_DAYS_PER_MONTH)
    return str(months)


def _get_strategy(cache: dict, strategy_id: str) -> dict:
    """Fetch a strategy's metrics dict from the cache by ID. Returns
    an empty dict on cache miss so the formatters degrade to em
    dashes rather than the build_substitution_table raising."""
    if not isinstance(cache, dict):
        return {}
    entry = cache.get(strategy_id) or {}
    if not isinstance(entry, dict):
        return {}
    return entry


def _index_by_strategy(rows: list[dict] | None) -> dict[str, dict]:
    """Build a {strategy_name -> row} lookup from a list of per-
    strategy dicts. Used by the kwargs that ship payload subsets
    from analytics_metrics_cache as lists (regime_conditional,
    factor_loadings). Falls back to empty dict on bad input so
    the caller can still look up by .get(name, {})."""
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get("strategy") or r.get("strategy_name")
        if name:
            out[str(name)] = r
    return out


def _pre_2022_months(
    study_months: int | None,
    strategy_cache: dict | None,
) -> str:
    """June 28 2026 -- months in the study period BEFORE 2022.

    Computed as study_months - post_2022_window_length where
    post_2022_window_length is the number of months from Jan
    2022 through STUDY_END (May 2026 = 53). Falls back to a
    direct subtraction against the cached n_observations when
    study_months is None, then to em-dash when neither source
    resolves.

    Used by brief / appendix prose that contrasts the pre- and
    post-2022 correlation regimes."""
    POST_2022_MONTHS = 53  # Jan 2022 -> May 2026 inclusive
    n = study_months
    if n is None and isinstance(strategy_cache, dict):
        n = strategy_cache.get("n_observations")
    try:
        n_int = int(n) if n is not None else None
    except (TypeError, ValueError):
        return "—"
    if n_int is None or n_int <= POST_2022_MONTHS:
        return "—"
    return str(n_int - POST_2022_MONTHS)


def _cost_scenario(
    cost_sensitivity: dict | None, bps: int,
) -> dict:
    """Pull the scenario row matching `bps` from the
    oos_cost_sensitivity payload. The payload has a flat
    `scenarios: [{bps, net_sharpe, vs_benchmark_pct, ...}]`
    list, NOT a dict keyed by bps -- so we walk the list."""
    if not isinstance(cost_sensitivity, dict):
        return {}
    for s in cost_sensitivity.get("scenarios") or []:
        if isinstance(s, dict) and s.get("bps") == bps:
            return s
    return {}


def build_substitution_table(
    strategy_cache: dict,
    cio_recommendation: dict | None = None,
    data_hash: str = "",
    *,
    oos_sharpe_blend: float | None = None,
    oos_sharpe_benchmark: float | None = None,
    oos_sharpe_classic_6040: float | None = None,
    pre_2022_eq_ig_correlation: float | None = None,
    post_2022_eq_ig_correlation: float | None = None,
    oos_window_definition: str = "January 2022 through May 2026",
    oos_window_months: int = 53,
    oos_window_pct_of_study: float | None = None,
    study_months: int | None = None,
    study_start: str = "July 2002",
    study_end: str = "May 2026",
    implied_allocation: dict | None = None,
    live_signals: dict | None = None,
    regime_conditional: list[dict] | None = None,
    factor_loadings: list[dict] | None = None,
    cost_sensitivity: dict | None = None,
    crisis_performance: dict | None = None,
    hash_verified: bool = False,
) -> dict[str, str]:
    """Build the deterministic {token -> value} substitution table
    from verified cache values.

    Inputs:
      strategy_cache -- the results_json dict from
        strategy_results_cache. Expected to contain BENCHMARK,
        CLASSIC_60_40, REGIME_SWITCHING entries with the standard
        backtester result keys (sharpe_ratio, max_drawdown,
        drawdown_recovery_days, plus the regime-conditional
        post_2022_sharpe / pre_2022_sharpe that
        analytics.regime_conditional_performance computes).

      cio_recommendation -- the latest CIO overlay dict from
        cio_recommendation.get_latest_recommendation. May be None
        when no recommendation exists yet (the live-signal tokens
        then degrade to em dashes; the brief still renders).

      data_hash -- the current_data_hash, truncated to 8 chars for
        the brief's footer / data-provenance line.

      Optional kwargs for figures that don't live in the cache
      directly:
        oos_sharpe_blend / oos_sharpe_benchmark -- the post-
          initialization-window Sharpe pair (the headline 1.24 vs
          0.73 figure). Pulled from validated_constants by the
          caller; if None, falls back to None (-> em dash).
        pre_2022_eq_ig_correlation / post_2022_eq_ig_correlation --
          the equity-IG correlation pair across the regime break.
        oos_window_definition -- the window the OOS figures cover
          ("January 2022 through May 2026" by default).
        oos_window_months -- the month count for the window.
        study_months -- the total study-period length (n_observations
          from the cache).

    The table is intentionally flat (token -> string) so
    apply_substitutions is a single replace pass. Adding a new
    token is a one-line change here + a one-line addition to the
    NUMERIC_PLACEHOLDER_GUIDE prompt block in main.py.

    hash_verified -- June 27 2026. Defensive flag the caller MUST
    set when supplying a data_hash alongside a cio_recommendation
    + live_signals that were loaded HASH-AWARELY (i.e. via
    cio_recommendation.get_cached_for_hash and
    tools.cache.get_regime_snapshot_for_hash, not via the live
    get_latest_recommendation / get_regime_cache). When data_hash
    is non-empty but hash_verified=False, this function logs
    'build_substitution_table_hash_unverified' so a regression that
    re-introduces the freeze-hash-vs-live-CIO mismatch (the
    'slide 7 shows 62.7% instead of 95.4%' bug) surfaces in the
    operator log instead of silently producing a corrupt table.
    Does not raise -- legacy callers that pass data_hash without
    the flag continue to work; the log is the audit signal.
    """
    if data_hash and not hash_verified:
        log.warning(
            "build_substitution_table_hash_unverified",
            data_hash=str(data_hash)[:8],
            note=(
                "data_hash supplied without hash_verified=True -- "
                "the cio_recommendation + live_signals arguments "
                "must be loaded via the hash-aware path "
                "(get_cached_for_hash + get_regime_snapshot_for_hash) "
                "for freeze-correct substitution. See PR fix(freeze)."))
    benchmark = _get_strategy(strategy_cache, "BENCHMARK")
    classic = _get_strategy(strategy_cache, "CLASSIC_60_40")
    regime = _get_strategy(strategy_cache, "REGIME_SWITCHING")
    cio = cio_recommendation or {}
    # June 22 2026 -- live_signals carries the regime_signals_cache
    # payload (vix_level / credit_spread / yield_curve_slope /
    # equity_trend / etc). Falls back to {} so the watchpoint tokens
    # render em-dash on a cold environment rather than raising.
    ls = live_signals or {}

    # June 22 2026 (substitution wiring fix) -- the three new kwargs
    # carry payload subsets sourced from analytics_metrics_cache:
    #
    #   regime_conditional -- the list at
    #     `academic_analytics.regime_conditional` -- per-strategy
    #     pre_2022_sharpe / post_2022_sharpe / annualized_turnover
    #     rows. The previous wiring read these from
    #     strategy_cache[name].post_2022_sharpe but the strategy
    #     cache row never carried those fields; the data lives on
    #     the regime_conditional rows the academic_analytics
    #     payload writes.
    #
    #   factor_loadings -- the list at
    #     `academic_analytics.factor_loadings` -- per-strategy
    #     alpha / beta / smb_beta / hml_beta / r_squared rows. Used
    #     by _append_per_strategy_tokens for the Carhart factor
    #     loading tokens that the appendix Section E table cites.
    #
    #   cost_sensitivity -- the entire payload at
    #     `oos_cost_sensitivity` metric_kind. The previous wiring
    #     read `strategy_cache.get("net_sharpe_10bp")` -- a top-
    #     level cache field that doesn't exist. The data lives
    #     here as scenarios=[{bps, net_sharpe, ...}] + top-level
    #     gross_sharpe / oos_vol / n_rebalances.
    rc_by_strategy = _index_by_strategy(regime_conditional)
    cs = cost_sensitivity or {}

    # OOS improvement percentage: (blend / benchmark - 1) * 100.
    # Only compute when both inputs are real numbers; otherwise the
    # token resolves to an em dash so the brief never carries a
    # bogus "infinite improvement" line.
    oos_improvement: str = "—"
    try:
        if oos_sharpe_blend and oos_sharpe_benchmark:
            improvement_pct = (
                float(oos_sharpe_blend) / float(oos_sharpe_benchmark)
                - 1.0) * 100.0
            sign = "+" if improvement_pct >= 0 else "-"
            oos_improvement = f"{sign}{abs(improvement_pct):.0f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    # Drawdown-reduction-vs-benchmark in percentage points.
    # max_drawdown is negative; abs() lets us subtract magnitudes.
    dd_reduction: str = "—"
    try:
        bench_dd = benchmark.get("max_drawdown")
        regime_dd = regime.get("max_drawdown")
        if isinstance(bench_dd, (int, float)) \
                and isinstance(regime_dd, (int, float)):
            reduction_pp = abs(float(bench_dd)) - abs(float(regime_dd))
            sign = "+" if reduction_pp >= 0 else "-"
            dd_reduction = f"{sign}{abs(reduction_pp) * 100:.1f}pp"
    except (TypeError, ValueError):
        pass

    table: dict[str, str] = {
        # ── OOS metrics (always include window definition) ─────────
        "{{OOS_SHARPE_BLEND}}": format_sharpe(oos_sharpe_blend),
        "{{OOS_SHARPE_BENCHMARK}}":
            format_sharpe(oos_sharpe_benchmark),
        # June 29 2026 (Issue 4) -- lowercase aliases the LLM
        # occasionally emits in the Limitations section ("the
        # static {{static_post_2022_sharpe}} vs the regime
        # {{regime_post_2022_sharpe}}"). Same source values as
        # {{OOS_SHARPE_CLASSIC_6040}} + {{OOS_SHARPE_BLEND}}.
        # Adding aliases instead of fixing prompts because the
        # lowercase form is more readable in dense narrative
        # passages and the substitution layer is the right
        # place to absorb both styles.
        "{{static_post_2022_sharpe}}": format_sharpe(
            oos_sharpe_classic_6040),
        "{{regime_post_2022_sharpe}}": format_sharpe(
            oos_sharpe_blend),
        # June 29 2026 (Issue 9) -- Classic 60/40 OOS Sharpe
        # joins the panel-defended set so the strategy_comparison
        # figure caption and any "three-strategy comparison"
        # narrative can name it via token.
        "{{OOS_SHARPE_CLASSIC_6040}}": format_sharpe(
            oos_sharpe_classic_6040),
        "{{OOS_WINDOW}}": oos_window_definition,
        "{{OOS_WINDOW_MONTHS}}": str(oos_window_months),
        "{{OOS_SHARPE_IMPROVEMENT_PCT}}": oos_improvement,

        # ── Full-period strategy metrics ───────────────────────────
        "{{REGIME_SWITCHING_SHARPE}}":
            format_sharpe(regime.get("sharpe_ratio")),
        "{{BENCHMARK_SHARPE}}":
            format_sharpe(benchmark.get("sharpe_ratio")),
        "{{CLASSIC_6040_SHARPE}}":
            format_sharpe(classic.get("sharpe_ratio")),

        # ── Drawdown metrics ───────────────────────────────────────
        "{{REGIME_SWITCHING_MAX_DD}}":
            format_pct(regime.get("max_drawdown")),
        "{{BENCHMARK_MAX_DD}}":
            format_pct(benchmark.get("max_drawdown")),
        "{{CLASSIC_6040_MAX_DD}}":
            format_pct(classic.get("max_drawdown")),
        "{{DD_REDUCTION_REGIME_SWITCHING}}": dd_reduction,

        # ── Recovery metrics ───────────────────────────────────────
        # Two variants per strategy (June 21 2026 split):
        #   {{*_RECOVERY}}        -> bare integer ("71"); the LLM
        #                            writes "months" after in prose
        #   {{*_RECOVERY_MONTHS}} -> pre-formatted ("71 months"); the
        #                            LLM uses this when it does NOT
        #                            want to add the unit itself
        # The split eliminates the "71 months months" bug from before
        # the architecture distinguished the two cases.
        "{{REGIME_SWITCHING_RECOVERY}}":
            format_months_only(regime.get("drawdown_recovery_days")),
        "{{REGIME_SWITCHING_RECOVERY_MONTHS}}":
            format_months_from_days(regime.get("drawdown_recovery_days")),
        "{{BENCHMARK_RECOVERY}}":
            format_months_only(
                benchmark.get("drawdown_recovery_days")),
        "{{BENCHMARK_RECOVERY_MONTHS}}":
            format_months_from_days(
                benchmark.get("drawdown_recovery_days")),
        "{{CLASSIC_6040_RECOVERY}}":
            format_months_only(
                classic.get("drawdown_recovery_days")),
        "{{CLASSIC_6040_RECOVERY_MONTHS}}":
            format_months_from_days(
                classic.get("drawdown_recovery_days")),

        # ── Pre/post-2022 sub-period metrics ───────────────────────
        # June 22 2026 (wiring fix) -- read from the
        # regime_conditional kwarg (the analytics_metrics_cache
        # `academic_analytics.regime_conditional` list). The
        # earlier `strategy_cache[name].post_2022_sharpe` reads
        # silently resolved to em-dash because the strategy cache
        # row never carried these fields. Each row is keyed by
        # `strategy` or `strategy_name`; _index_by_strategy
        # builds the lookup.
        "{{REGIME_SWITCHING_POST2022_SHARPE}}":
            format_sharpe(rc_by_strategy.get(
                "REGIME_SWITCHING", {}).get("post_2022_sharpe")),
        "{{BENCHMARK_POST2022_SHARPE}}":
            format_sharpe(rc_by_strategy.get(
                "BENCHMARK", {}).get("post_2022_sharpe")),
        "{{CLASSIC_6040_POST2022_SHARPE}}":
            format_sharpe(rc_by_strategy.get(
                "CLASSIC_60_40", {}).get("post_2022_sharpe")),
        # June 25 2026 -- MIN_VARIANCE pre/post 2022 sharpe tokens.
        # The appendix LLM emitted {{MIN_VARIANCE_POST2022_SHARPE}}
        # as a citation but the substitution table never had the
        # key, so the literal token survived to the exported DOCX.
        # Added here alongside the other three brief-side
        # strategies. Same regime_conditional source.
        "{{MIN_VARIANCE_POST2022_SHARPE}}":
            format_sharpe(rc_by_strategy.get(
                "MIN_VARIANCE", {}).get("post_2022_sharpe")),
        "{{MIN_VARIANCE_PRE2022_SHARPE}}":
            format_sharpe(rc_by_strategy.get(
                "MIN_VARIANCE", {}).get("pre_2022_sharpe")),
        "{{REGIME_SWITCHING_PRE2022_SHARPE}}":
            format_sharpe(rc_by_strategy.get(
                "REGIME_SWITCHING", {}).get("pre_2022_sharpe")),
        "{{BENCHMARK_PRE2022_SHARPE}}":
            format_sharpe(rc_by_strategy.get(
                "BENCHMARK", {}).get("pre_2022_sharpe")),

        # ── Post-2022 CAGR per brief-side strategy ─────────────────
        # June 22 2026 -- same source as the POST2022_SHARPE pair
        # (regime_conditional rows from
        # analytics.regime_conditional_performance). The fields
        # already exist on the row (post_2022_cagr); the tokens
        # were missing so slides 4 and 6 rendered [DATA PENDING]
        # for per-strategy post-2022 CAGR.
        "{{REGIME_SWITCHING_POST2022_CAGR}}":
            format_pct(rc_by_strategy.get(
                "REGIME_SWITCHING", {}).get("post_2022_cagr")),
        "{{BENCHMARK_POST2022_CAGR}}":
            format_pct(rc_by_strategy.get(
                "BENCHMARK", {}).get("post_2022_cagr")),
        "{{CLASSIC_6040_POST2022_CAGR}}":
            format_pct(rc_by_strategy.get(
                "CLASSIC_60_40", {}).get("post_2022_cagr")),

        # ── Crisis-window drawdowns (June 22 2026) ─────────────────
        # Sourced from analytics_metrics_cache[crisis_performance]
        # written by refresh_diversification_metrics (precomputed
        # _analytics.py:917). Payload shape:
        #   {"windows": {...},
        #    "rows": {strategy: {"GFC_2008-2009": {max_dd, ...},
        #                        "Rate_Shock_2022": {...}, ...}}}
        # Slides 4 and 6 cite the GFC + Rate Shock 2022 drawdowns
        # for the three brief-side strategies; previously
        # [DATA PENDING] because no token existed.
        "{{REGIME_SWITCHING_GFC_DRAWDOWN}}": format_pct(
            ((crisis_performance or {}).get("rows") or {})
                .get("REGIME_SWITCHING", {})
                .get("GFC_2008-2009", {}).get("max_dd")),
        "{{BENCHMARK_GFC_DRAWDOWN}}": format_pct(
            ((crisis_performance or {}).get("rows") or {})
                .get("BENCHMARK", {})
                .get("GFC_2008-2009", {}).get("max_dd")),
        "{{CLASSIC_6040_GFC_DRAWDOWN}}": format_pct(
            ((crisis_performance or {}).get("rows") or {})
                .get("CLASSIC_60_40", {})
                .get("GFC_2008-2009", {}).get("max_dd")),
        "{{REGIME_SWITCHING_RATE_SHOCK_2022_DRAWDOWN}}":
            format_pct(
                ((crisis_performance or {}).get("rows") or {})
                    .get("REGIME_SWITCHING", {})
                    .get("Rate_Shock_2022", {}).get("max_dd")),
        "{{BENCHMARK_RATE_SHOCK_2022_DRAWDOWN}}": format_pct(
            ((crisis_performance or {}).get("rows") or {})
                .get("BENCHMARK", {})
                .get("Rate_Shock_2022", {}).get("max_dd")),
        "{{CLASSIC_6040_RATE_SHOCK_2022_DRAWDOWN}}": format_pct(
            ((crisis_performance or {}).get("rows") or {})
                .get("CLASSIC_60_40", {})
                .get("Rate_Shock_2022", {}).get("max_dd")),

        # ── Correlation regime ─────────────────────────────────────
        "{{PRE_2022_EQ_IG_CORR}}":
            format_corr(pre_2022_eq_ig_correlation),
        "{{POST_2022_EQ_IG_CORR}}":
            format_corr(post_2022_eq_ig_correlation),

        # ── Live signal (from CIO recommendation) ──────────────────
        "{{CURRENT_REGIME}}": str(cio.get("regime") or "—"),
        "{{REGIME_CONFIDENCE}}": format_pct(
            (cio.get("confidence") or {}).get("probability")
            if isinstance(cio.get("confidence"), dict)
            else cio.get("confidence")),

        # ── OOS window share of full study ─────────────────────────
        # June 22 2026 (Path A) -- prompts that hardcoded "14%"
        # against a 40-month OOS window are now tokenized. The
        # default OOS_WINDOW_PCT_OF_STUDY = 18.5 (53/287) when the
        # kwarg isn't supplied so a cold cache still renders a
        # plausible value rather than em-dash.
        "{{OOS_WINDOW_PCT_OF_STUDY}}": (
            f"{oos_window_pct_of_study:.1f}"
            if isinstance(oos_window_pct_of_study, (int, float))
            else "18.5"),
        # Implied-allocation tokens. Earlier versions of this table
        # read `cio.implied_equity` / `cio.implied_ig` / `cio.implied_hy`
        # -- those fields don't exist on the CIO recommendation row
        # (the values are computed on demand via
        # tools.cio_recommendation.compute_implied_asset_allocation
        # against the cio_row's blend_weights). Callers now compute
        # the allocation once and pass it in via the
        # implied_allocation kwarg; the keys we read are the
        # canonical ones the compute helper emits
        # (equity_pct / ig_bond_pct / hy_bond_pct / bond_pct).
        # Falls back to em dash when implied_allocation is None or
        # the keys are missing -- the bug this fix replaces was that
        # CURRENT_*_PCT silently resolved to em dashes everywhere.
        "{{CURRENT_EQUITY_PCT}}": format_pct(
            (implied_allocation or {}).get("equity_pct")),
        "{{CURRENT_IG_PCT}}": format_pct(
            (implied_allocation or {}).get("ig_bond_pct")),
        "{{CURRENT_HY_PCT}}": format_pct(
            (implied_allocation or {}).get("hy_bond_pct")),

        # ── Study period ───────────────────────────────────────────
        "{{STUDY_MONTHS}}": (
            str(study_months) if study_months is not None
            else str(strategy_cache.get("n_observations") or "—")),
        "{{STUDY_START}}": study_start,
        "{{STUDY_END}}": study_end,
        # June 29 2026 (Issue 3) -- the full 16-character hash
        # is the canonical freeze identifier; truncating to 8
        # characters in figure captions obscured the provenance
        # claim. The strategy_hash column in strategy_results_
        # cache stores 16 characters (SHA256[:16] per
        # tools/cache._compute_data_hash) so the supplied
        # data_hash arg is already the full 16-char value.
        "{{DATA_HASH}}": (data_hash or "")[:16] or "—",

        # June 28 2026 -- definitional Classic 60/40 weights.
        # These are by-construction strategy constants (not
        # cache-derived) but get tokens so prose like
        # "{{CLASSIC_6040_WEIGHT_EQUITY}} equity and
        # {{CLASSIC_6040_WEIGHT_BOND}} bonds" stays
        # substitution-aware -- the LLM can use the tokens
        # rather than emitting raw "60% equity" which trips
        # the hard-lock loop. The structural exemption
        # balanced_allocation_weights covers the bare "60%/40%"
        # form when the LLM prefers it.
        "{{CLASSIC_6040_WEIGHT_EQUITY}}": "60%",
        "{{CLASSIC_6040_WEIGHT_BOND}}":   "40%",
        # June 29 2026 (Issue 3) -- operator-spec alias names.
        # The brief_methodology + key_findings prompts emit
        # both naming conventions ({{CLASSIC_6040_WEIGHT_BOND}}
        # the existing form, {{CLASSIC_6040_BOND_WEIGHT}} the
        # operator-spec preferred form for the residual-fix
        # PR). Aliasing both to the same value lets the LLM
        # use either without one resolving to a literal token
        # in the rendered output.
        "{{CLASSIC_6040_BOND_WEIGHT}}":   "40%",
        "{{CLASSIC_6040_EQUITY_WEIGHT}}": "60%",
        # Underscored-variant aliases for the recovery + MaxDD
        # tokens. The Academic Writer prompt has historically
        # been inconsistent (emits both "{{CLASSIC_6040_*}}" and
        # "{{CLASSIC_60_40_*}}"); rather than fight the prompt
        # we accept both names. Resolved value is identical to
        # the canonical un-underscored form.
        "{{CLASSIC_60_40_RECOVERY}}":
            format_months_only(
                classic.get("drawdown_recovery_days")),
        "{{CLASSIC_60_40_RECOVERY_MONTHS}}":
            format_months_from_days(
                classic.get("drawdown_recovery_days")),
        "{{CLASSIC_60_40_MAX_DD}}": format_pct(
            classic.get("max_drawdown")),
        "{{CLASSIC_60_40_SHARPE}}": format_sharpe(
            classic.get("sharpe")),
        # June 28 2026 -- months-before-2022 in the study
        # period. Derived from STUDY_MONTHS minus the post-2022
        # window length (when available); falls back to em-dash
        # when either is null.
        "{{PRE_2022_MONTHS}}": _pre_2022_months(
            study_months, strategy_cache),

        # June 28 2026 (Fix 4) -- transaction-cost sensitivity
        # tier constants. The Limitations section's cost
        # sensitivity prose previously hijacked
        # {{RISK_PARITY_RECOVERY}} / {{BLACK_LITTERMAN_RECOVERY}}
        # as stand-ins (semantically wrong; those are recovery-
        # month tokens). These three dedicated bps constants
        # let the prompt reference each cost tier by its true
        # name. By-construction constants -- never cache-
        # derived. Locked.
        "{{SENSITIVITY_COST_BPS_LOW}}":  "10",
        "{{SENSITIVITY_COST_BPS_MID}}":  "15",
        "{{SENSITIVITY_COST_BPS_HIGH}}": "20",

        # June 28 2026 (Fix 5a) -- Benjamini-Hochberg false-
        # discovery-rate significance threshold used by the
        # statistical Council. By-construction constant
        # (academic standard 0.005); having it as a token lets
        # the LLM reference "p < {{BH_SIGNIFICANCE_THRESHOLD}}"
        # explicitly without tripping the hard-lock as a raw
        # 0.005 numeric. The structural exemption
        # stat_threshold also exempts the bare p < 0.005 form
        # below; this token provides the swappable alternative.
        "{{BH_SIGNIFICANCE_THRESHOLD}}": "0.005",

        # June 28 2026 (Fix 4) -- rebalancing gate: the
        # platform rebalances when any single strategy's blend
        # weight crosses N percentage points. By-construction
        # methodology constant (currently 2 pp). brief
        # Methodology + analytical_appendix prose reference it;
        # token form keeps the value out of the hard-lock's
        # untoken-numeric flag list.
        "{{REBALANCE_THRESHOLD_PP}}": "2",

        # June 28 2026 -- bootstrap methodology constants for
        # the analytical_appendix Section D (Bootstrap CI on
        # Sharpe). Block-bootstrap length matches the monthly
        # study cadence x 1 year; random seed is fixed so the
        # CI is reproducible. By-construction methodology
        # constants -- not data-derived.
        "{{BOOTSTRAP_BLOCK_LENGTH}}": "12",
        "{{BOOTSTRAP_SEED}}":         "42",
    }

    # ── Deck-specific tokens (Layer 2, June 21 2026) ────────────────────
    #
    # The deck uses tokens beyond the brief's three-strategy lens:
    # the play-by-play scorecard, transaction-cost sensitivity, live
    # watch points (VIX / yield curve / credit spread / equity trend
    # / ESS), and live blend composition. Many of these fields don't
    # live on the strategy cache today (they're computed on demand by
    # the regime / FRED / ESS pipelines); when the cache key is
    # absent the format_* helpers return em dashes, so the deck
    # renders cleanly even on a cold environment that doesn't carry
    # the live signal yet. The tokens are added here even when the
    # cache fields don't yet exist so the placeholder guide stays
    # accurate -- the operator can backfill the cache fields later
    # without touching this table.
    table.update({
        # Play-by-play scorecard. Defaults match the academic_deck
        # constants (PLAY_BY_PLAY_ADD_VALUE = 2, PLAY_BY_PLAY_EVENTS
        # = 9) so a cold cache still renders the canonical numbers.
        "{{PLAY_BY_PLAY_VALUE_ADD}}": str(
            strategy_cache.get("play_by_play_value_add", 2)),
        "{{PLAY_BY_PLAY_TOTAL}}": str(
            strategy_cache.get("play_by_play_total", 9)),
        # June 25 2026 -- the brief's Executive Summary references
        # the named-event scorecard as PLAY_BY_PLAY_EVENTS. Same
        # locked constant as PLAY_BY_PLAY_TOTAL (9); kept as a
        # distinct token so the brief prompt can address it under
        # the name the prompt template actually uses without
        # silently leaving a {{TOKEN}} placeholder in the export.
        "{{PLAY_BY_PLAY_EVENTS}}": str(
            strategy_cache.get("play_by_play_events", 9)),

        # Turnover + net-of-cost Sharpe sensitivity. June 22 2026
        # (wiring fix) -- read from the cost_sensitivity kwarg
        # (the analytics_metrics_cache `oos_cost_sensitivity`
        # payload). The earlier strategy_cache reads were dead --
        # the cache row never carried net_sharpe_10bp / 15bp /
        # 20bp fields; those live in the oos_cost_sensitivity
        # payload's scenarios list, written by
        # refresh_oos_cost_sensitivity. Turnover lives on the
        # strategy_cache row's true_turnover field (the
        # backtester's _true_turnover -- "Genuine annualised
        # portfolio turnover"). The previous read of
        # regime_conditional.annualized_turnover pointed at a
        # field analytics.regime_conditional_performance never
        # writes, so the token rendered em-dash.
        "{{REGIME_SWITCHING_TURNOVER}}": format_pct(
            (strategy_cache.get("REGIME_SWITCHING") or {})
                .get("true_turnover")),
        "{{NET_SHARPE_10BP}}": format_sharpe(
            _cost_scenario(cs, 10).get("net_sharpe")),
        "{{NET_SHARPE_15BP}}": format_sharpe(
            _cost_scenario(cs, 15).get("net_sharpe")),
        "{{NET_SHARPE_20BP}}": format_sharpe(
            _cost_scenario(cs, 20).get("net_sharpe")),

        # June 22 2026 -- {{CVAR_99_BENCHMARK}} removed. The
        # previous resolver read benchmark.cvar_99_annualized,
        # which was both the wrong field name (actual is
        # cvar_99_annual) and the wrong cache layer (cvar lives
        # in analytics_metrics_cache[tail_risk], not the strategy
        # cache). The token was advertised in the deck placeholder
        # guide but cited by zero slide specs, so removing it
        # eliminates an unresolved-placeholder footgun. See
        # data_reference_catalog.py for the removed catalog entry.

        # Live watch points (Slide 7 macro context). June 22 2026 --
        # rewired to read from the live_signals kwarg (populated by
        # the caller from regime_signals_cache) instead of from
        # strategy_cache. The strategy cache never carried these
        # fields, so the previous lookups silently resolved to
        # em-dash for every deck generation.
        #
        # Field-name mapping from regime_signals_cache (cache.py:670):
        #   vix_level             -> {{VIX_CURRENT}}
        #   credit_spread         -> {{CREDIT_SPREAD_CURRENT}}
        #   yield_curve_slope     -> {{YIELD_CURVE_CURRENT}}
        #   equity_trend          -> {{EQUITY_TREND_CURRENT}}
        #
        # ESS is NOT in regime_signals_cache -- it lives on the CIO
        # recommendation (cio.confidence.ess). Sourced separately.
        "{{VIX_CURRENT}}": str(
            ls.get("vix_level") or "—"),
        "{{CREDIT_SPREAD_CURRENT}}": str(
            ls.get("credit_spread") or "—"),
        "{{YIELD_CURVE_CURRENT}}": str(
            ls.get("yield_curve_slope") or "—"),
        "{{EQUITY_TREND_CURRENT}}": format_pct(
            ls.get("equity_trend")),
        "{{ESS_CURRENT}}": str(
            (cio.get("confidence") or {}).get("ess")
            if isinstance(cio.get("confidence"), dict)
            else cio.get("ess")
            or "—"),

        # Live blend composition (slide 8 + slide 12). The CIO row
        # carries blend_weights as a dict {strategy -> weight}; we
        # surface the three brief-side strategies explicitly so the
        # deck slide can quote each weight by name.
        "{{BLEND_REGIME_SWITCHING_WT}}": format_pct(
            (cio.get("blend_weights") or {}).get("REGIME_SWITCHING")
            or cio.get("blend_regime_switching")),
        "{{BLEND_BENCHMARK_WT}}": format_pct(
            (cio.get("blend_weights") or {}).get("BENCHMARK")
            or cio.get("blend_benchmark")),
        "{{BLEND_CLASSIC_6040_WT}}": format_pct(
            (cio.get("blend_weights") or {}).get("CLASSIC_60_40")
            or cio.get("blend_classic_6040")),

        # Strategy count. Falls back to the visible cache size when
        # not explicitly persisted.
        "{{N_STRATEGIES}}": str(
            strategy_cache.get("n_strategies")
            or sum(1 for v in strategy_cache.values()
                   if isinstance(v, dict) and "sharpe_ratio" in v)
            or 10),

        # ── Data provenance (June 25 2026) ──────────────────────
        # Static descriptive strings naming the canonical data
        # series the project relies on. These are NOT data-hash
        # dependent -- the source identities don't change with
        # the analytics cache -- so they're hardcoded constants
        # rather than cache reads. Adding them as tokens lets the
        # Academic Writer reference them verbatim in the brief's
        # methodology section without paraphrasing, and the
        # substitution layer resolves them at export time. Every
        # review flagged the methodology's missing provenance as
        # MEDIUM severity; with the tokens in place the prompt
        # can carry the canonical phrasing and the export will
        # surface it identically across regenerations.
        "{{EQUITY_SERIES}}": (
            "S&P 500 total-return index"),
        "{{IG_SERIES}}": (
            "iShares iBoxx $ Investment Grade Corporate Bond ETF "
            "(LQD) prior to 2007, spliced to Vanguard Total Bond "
            "Market ETF (BND) from 2007 onward"),
        "{{HY_SERIES}}": (
            "ICE BofA High Yield Master II Total Return Index "
            "(FRED: BAMLHYH0A0HYM2TRIV) through December 2025, "
            "extended via iShares iBoxx $ High Yield Corporate "
            "Bond ETF (HYG) from January 2026"),
        "{{RISK_FREE_SERIES}}": (
            "FRED DTB3 three-month Treasury bill yield "
            "(time-varying)"),
        "{{FACTOR_SERIES}}": (
            "Carhart four-factor series (MKT, SMB, HML, MOM) "
            "from the Kenneth French data library"),
        "{{IG_SPLICE_DATE}}": "January 2007",
        "{{HY_EXTENSION_DATE}}": "January 2026",
        "{{HY_TRACKING_ERROR}}": (
            "approximately 0.04% per month"),
    })

    return table


_TOKEN_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


# ── Per-strategy dynamic token generation (appendix) ─────────────────────
#
# The analytical appendix shows ALL strategies in the cache (the
# 10-strategy override, _APPENDIX_FRAMING_PRELUDE). Each strategy
# needs its own SHARPE / MAX_DD / CAGR / VOLATILITY / RECOVERY
# tokens, which makes 10 strategies x 5 metrics = 50 tokens. We
# generate them at table-build time rather than enumerating each in
# the module, so a strategy renaming or addition doesn't require a
# code change here. The brief-only tokens above remain hardcoded
# because they're shaped to the rubric's three-strategy lens.

_APPENDIX_METRIC_FORMATTERS: dict[str, Any] = {
    "SHARPE":         ("sharpe_ratio",            format_sharpe),
    "MAX_DD":         ("max_drawdown",            format_pct),
    "CAGR":           ("cagr",                    format_pct),
    "VOLATILITY":     ("volatility",              format_pct),
    # June 21 2026 -- RECOVERY split into bare-number + with-units
    # variants. Mirrors the brief-side hardcoded tokens so an
    # appendix-loop overwrite of e.g. {{BENCHMARK_RECOVERY}} keeps
    # the bare-number contract (the prose writer adds "months"
    # itself). RECOVERY_MONTHS supplies the pre-formatted variant
    # for prose that does NOT want to add units.
    "RECOVERY":       ("drawdown_recovery_days",  format_months_only),
    "RECOVERY_MONTHS": ("drawdown_recovery_days", format_months_from_days),
}


def _append_per_strategy_tokens(
    table: dict[str, str], strategy_cache: dict,
    factor_loadings: list[dict] | None = None,
) -> None:
    """Generate {{STRATEGY_NAME_METRIC}} tokens for every strategy in
    the cache. Appendix-only -- the brief never uses these because
    the brief is locked to the three-strategy lens. Mutates table
    in place.

    Strategy IDs come straight from the cache keys (BENCHMARK,
    REGIME_SWITCHING, CLASSIC_60_40, MIN_VARIANCE, ...). The token
    uses the cache key verbatim -- a strategy rename in the cache
    surfaces as a missing token rather than silent re-mapping, which
    is the safer default.

    June 22 2026 (wiring fix) -- when factor_loadings is supplied
    (the `academic_analytics.factor_loadings` list), this function
    also emits per-strategy Carhart factor-loading tokens
    ({{<STRATEGY>_ALPHA}}, {{<STRATEGY>_BETA}}, {{<STRATEGY>_SMB_BETA}},
    {{<STRATEGY>_HML_BETA}}, {{<STRATEGY>_R_SQUARED}}). The appendix
    Section E table cites these tokens; the previous wiring left
    them undefined and the audit flagged them as unresolved
    placeholders."""
    if not isinstance(strategy_cache, dict):
        return
    for raw_name, entry in strategy_cache.items():
        if not isinstance(entry, dict):
            continue
        # Some cache wrappers carry non-strategy metadata at the same
        # level (n_observations, etc). Skip rows without a real
        # sharpe_ratio key as a cheap sentinel.
        if "sharpe_ratio" not in entry:
            continue
        upper = str(raw_name).upper().replace(" ", "_")
        for metric_suffix, (cache_key, fmt) in (
                _APPENDIX_METRIC_FORMATTERS.items()):
            token = f"{{{{{upper}_{metric_suffix}}}}}"
            # Don't overwrite an explicit brief-side token if the
            # appendix loop collides with it (e.g. BENCHMARK_SHARPE
            # is set both ways). Last-write-wins is fine because
            # both reads come from the same cache entry.
            table[token] = fmt(entry.get(cache_key))

    # Carhart factor-loading tokens, one per (strategy, metric).
    # Each metric has its own formatter -- alpha + factor betas
    # render as 4dp decimals, r_squared as 4dp 0-1 fraction.
    if isinstance(factor_loadings, list):
        for row in factor_loadings:
            if not isinstance(row, dict):
                continue
            name = row.get("strategy") or row.get("strategy_name")
            if not name:
                continue
            upper = str(name).upper().replace(" ", "_")
            # Field names MUST match what tools.analytics.factor_loadings
            # actually writes -- it emits `alpha_annualized`, `mkt_rf`,
            # `smb`, `hml`, `r_squared` (the raw statsmodels OLS
            # parameter names, with alpha multiplied by _ANN for the
            # annualized rate). The previous list used the conceptual
            # names (`alpha`, `beta`, `smb_beta`, `hml_beta`) which the
            # analytics never writes, so .get() returned None for four
            # of five fields and the tokens rendered em-dash; only
            # r_squared matched and resolved correctly.
            for metric_key, suffix in (
                ("alpha_annualized", "ALPHA"),
                ("mkt_rf",           "BETA"),
                ("smb",              "SMB_BETA"),
                ("hml",              "HML_BETA"),
                ("r_squared",        "R_SQUARED"),
            ):
                token = f"{{{{{upper}_{suffix}}}}}"
                v = row.get(metric_key)
                try:
                    table[token] = (
                        f"{float(v):.4f}" if v is not None else "—")
                except (TypeError, ValueError):
                    table[token] = "—"


# ── Per-data_hash cache (one table per generation job) ──────────────────
#
# build_substitution_table is cheap (no I/O, just dict lookups +
# formatters) but the brief + deck + appendix all need the same
# table within the same generation job. Caching by data_hash means
# (a) determinism: the three deliverables see the same numbers by
# construction, and (b) cheap operation: rebuilding the table on
# every section call would still work, but caching makes the
# substitution-audit-log token counts comparable across deliverables.
#
# Process-wide dict, NOT per-request. The cache key is a tuple:
#   (_CACHE_VERSION, data_hash,
#    bool(regime_conditional), bool(factor_loadings),
#    bool(cost_sensitivity))
#
# WHY THE TUPLE KEY (June 22 2026 -- PR addressing the stale-cache
# wiring bug on PR #374's new kwargs):
#
#   When data ticks over, the new hash builds a new table; the old
#   entry sticks around until the process restarts.
#
#   The bool-of-kwargs fingerprint exists because PR #374 added the
#   regime_conditional / factor_loadings / cost_sensitivity kwargs
#   that emit additional tokens. If a pre-PR-#374 caller hit
#   get_substitution_table first (warmup, an early verify check,
#   etc.), the table cached under bare data_hash would have lacked
#   those tokens. Subsequent callers passing the new kwargs would
#   hit that stale entry and get the OLD token-less table -- the
#   new kwargs were silently ignored.
#
#   With the bool fingerprint in the key, a call with the new
#   kwargs MISSES any entry built without them, forcing a fresh
#   build that emits the new tokens.
#
#   _CACHE_VERSION layers on top: a deploy that bumps the version
#   number invalidates every entry from the prior version, even if
#   the bool fingerprint matches -- belt-and-suspenders for the
#   moment a new kwarg slot lands.
#
# (the strategy_results_cache itself is the source of truth and
# can grow without bound; we trim later if memory becomes an issue).

_substitution_cache: dict[tuple, dict[str, str]] = {}


def _cache_key(
    data_hash: str,
    kwargs: dict,
    cio_recommendation: dict | None = None,
) -> tuple[int, str, bool, bool, bool, bool, str]:
    """Compose the composite cache key for get_substitution_table.

    Returns (version, data_hash, has_regime_conditional,
    has_factor_loadings, has_cost_sensitivity,
    has_crisis_performance, cio_identity). bool() is used instead
    of length so callers passing an empty list still get distinct
    treatment from callers passing None -- but two callers passing
    non-empty lists land on the same key.

    June 22 2026 -- 6th element added for crisis_performance kwarg.
    _CACHE_VERSION bumped to 3 in parallel.

    June 27 2026 -- 7th element added for cio_recommendation
    identity. Bug 2 root cause was: the live CIO row updates
    (regime confidence 95.4% -> 62.7%) but the substitution table
    was cache-keyed only by data_hash, so the stale cached table
    kept serving the OLD confidence even after the CIO updated.
    The cio_identity element is the recommendation row's stable id
    (or computed_at ISO string as fallback) so a CIO update
    naturally invalidates the cache. Empty string when no CIO row
    -- legacy callers without a CIO are unaffected. _CACHE_VERSION
    bumped to 6 in parallel so every pre-fix cached entry misses
    on first post-deploy read.
    """
    cio_identity = ""
    if isinstance(cio_recommendation, dict):
        # Prefer id (stable PK); fall back to computed_at (monotonic
        # per recompute); fall back to empty string (treated as
        # 'no CIO' -- identical to None case so legacy behaviour
        # holds).
        raw = (
            cio_recommendation.get("id")
            or cio_recommendation.get("recommendation_id")
            or cio_recommendation.get("computed_at"))
        if raw is not None:
            cio_identity = str(raw)
    return (
        _CACHE_VERSION,
        data_hash,
        bool(kwargs.get("regime_conditional")),
        bool(kwargs.get("factor_loadings")),
        bool(kwargs.get("cost_sensitivity")),
        bool(kwargs.get("crisis_performance")),
        cio_identity,
    )


def get_substitution_table(
    data_hash: str,
    strategy_cache: dict,
    cio_recommendation: dict | None = None,
    *,
    include_per_strategy: bool = True,
    rebuild: bool = False,
    **kwargs: Any,
) -> dict[str, str]:
    """Get-or-build the substitution table for a data_hash.

    On cache hit returns the SAME dict instance the first builder
    populated -- callers reading from it across the three
    deliverables therefore see byte-identical values for every
    substituted token. That's the structural determinism guarantee
    cross_deliverable_consistency_check relies on.

    rebuild=True forces a re-build (used after a strategy cache
    invalidation when the same data_hash should NOT serve the old
    table). Tests use it to reset between runs.

    Extra kwargs (oos_sharpe_blend, pre_2022_eq_ig_correlation, etc)
    flow through to build_substitution_table.

    include_per_strategy=True (the default) appends the dynamic
    per-strategy SHARPE/MAX_DD/CAGR/VOLATILITY/RECOVERY tokens; the
    appendix needs them, brief + deck don't but they're harmless
    extras."""
    # The factor_loadings kwarg flows TWICE: once into
    # build_substitution_table (no-op there today, but reserved
    # so a future per-strategy token relies on it inside the
    # function), and once into _append_per_strategy_tokens to
    # emit the Carhart factor-loading tokens. Pull it out of
    # kwargs without consuming it so build_substitution_table
    # also gets to see it via **kwargs.
    factor_loadings_for_append = kwargs.get("factor_loadings")

    if not data_hash:
        # An empty hash means data_status was unavailable -- build
        # the table inline but DON'T cache it, so the next call
        # (presumably with a real hash) builds afresh.
        table = build_substitution_table(
            strategy_cache, cio_recommendation, "", **kwargs)
        if include_per_strategy:
            _append_per_strategy_tokens(
                table, strategy_cache,
                factor_loadings=factor_loadings_for_append)
        return table
    key = _cache_key(data_hash, kwargs, cio_recommendation)
    if rebuild or key not in _substitution_cache:
        table = build_substitution_table(
            strategy_cache, cio_recommendation, data_hash, **kwargs)
        if include_per_strategy:
            _append_per_strategy_tokens(
                table, strategy_cache,
                factor_loadings=factor_loadings_for_append)
        _substitution_cache[key] = table
    return _substitution_cache[key]


def clear_substitution_cache() -> None:
    """Test helper -- empties the cache so a fresh table is built on
    the next get_substitution_table call. Production callers don't
    need this; the data_hash + rebuild=True pair handles
    invalidation."""
    _substitution_cache.clear()


def apply_substitutions(
    text: str | None, table: dict[str, str],
) -> tuple[str, list[str]]:
    """Replace every token from the table that appears in the text.

    Returns (substituted_text, sorted_unique_tokens_replaced). Unknown
    tokens (those in the text but not in the table) are LEFT INTACT --
    they're the audit signal check_unresolved_placeholders fires on.
    Silently dropping them would hide a writer that invented its own
    token name.

    Multiple occurrences of the same token are all replaced; the
    return list only lists each token once.
    """
    if not text:
        return ("", [])
    out = text
    replaced: set[str] = set()
    # Iterate in sorted order for deterministic test output.
    for token in sorted(table.keys()):
        if token in out:
            out = out.replace(token, table[token])
            replaced.add(token)
    return (out, sorted(replaced))


def unresolved_placeholders(text: str | None) -> list[str]:
    """Find any {{TOKEN}} remaining after substitution. Returns the
    sorted unique set so the audit flag carries each distinct token
    once. Used by document_audit.check_unresolved_placeholders."""
    if not text:
        return []
    found = _TOKEN_RE.findall(text)
    return sorted(set(found))


# ── Layer 3 (June 21 2026) -- export-time verification ─────────────────
#
# The substitution architecture eliminates drift at GENERATION time.
# Layer 3 closes the loop at EXPORT time: a manual edit in the
# editor (Bob accidentally typing "1.23" while editing prose around
# "1.24") would silently slip into the downloaded DOCX without this
# layer. The export path now:
#
#   1. Reads the value_manifest snapshot persisted on editor_drafts
#      at generation time (every numeric value the substitution
#      table produced, with provenance).
#   2. Scans the document text being exported for every manifest
#      value. A missing value is "edited away" -- flagged. A
#      variant ("1.2" where "1.24" expected) is "rounded away" --
#      flagged.
#   3. Compares the current data_hash against the generation
#      data_hash -- stale data is a warning (not an error: the
#      document is still internally consistent with the cache
#      it was generated against; just the cache may have moved on).
#
# verify_export_against_cache returns a structured dict the
# export handler persists on editor_drafts.export_verification
# and surfaces in response headers (X-Verification-Status).
# Fail-open: errors NEVER block the download. The user gets the
# file plus a clear warning banner.


# Numeric values worth verifying: Sharpe-shaped decimals,
# percentages, month counts. String values (BULL/BEAR, July 2002,
# correlation labels) are not in the manifest because they don't
# round-corrupt -- the only drift mode for "July 2002" is a wholesale
# rewrite, which the substitution audit catches separately.
_NUMERIC_VALUE_RE = re.compile(
    r"^[+-]?\d+(?:\.\d+)?(?:%|pp|\s*months?)?$"
)


def _is_numeric_value(value: str | None) -> bool:
    """True if value looks like a numeric figure worth verifying.
    Used to filter the substitution table down to the keys that
    matter for export-time verification."""
    if not value:
        return False
    return bool(_NUMERIC_VALUE_RE.match(value.strip()))


def build_value_manifest(
    substitution_table: dict[str, str],
    data_hash: str,
    generated_at: str,
) -> dict[str, dict[str, str]]:
    """Snapshot of every numeric value the substitution table
    produced, keyed by the value string. Records each value's
    provenance: source token + generation data_hash + timestamp.

    Only numeric values land in the manifest -- string values
    (BULL/BEAR, July 2002, etc) and em-dashes don't round-corrupt
    and don't benefit from export-time presence checking.

    Stored on editor_drafts.value_manifest (migration 057) at
    generation time. Read at export time by
    verify_export_against_cache as the authoritative reference for
    what every number in the document should be.

    Returns {value -> {token, data_hash, generated_at}}.
    Multiple tokens resolving to the same value (e.g. two strategies
    that happen to share a Sharpe of "0.86") collapse to one entry
    -- the manifest just needs to know the value should appear; the
    source token is recorded for the operator's first-encountered
    token (last write wins in the dict comprehension)."""
    return {
        value: {
            "token": token,
            "data_hash": data_hash,
            "generated_at": generated_at,
        }
        for token, value in substitution_table.items()
        if _is_numeric_value(value)
    }


def _find_corrupted_variants(
    value: str, text: str,
) -> list[str]:
    """Find variants of `value` in `text` that look like rounding
    or truncation corruptions. Conservative scan:

      For "1.24":
        Plausible corruptions: "1.2" (1dp truncation), "1.240"
          (trailing zero), "1.25" (rounded up), "1.23" (rounded
          down)
        Implausible: "0.24", "2.24" (different integer part --
          unambiguously distinct figures)

    Strategy: for each variant in a small bounded set, check if it
    appears in the text. The canonical value's own presence is
    irrelevant -- a document containing BOTH "1.24" AND "1.23"
    (e.g. the headline + a Section 2 mis-quote) is a corruption
    case the check catches by reporting the "1.23" variant.

    Returns sorted list of distinct variants found."""
    if not value or not text:
        return []
    # Strip sign + percent + pp + months suffix for the comparison.
    stripped = value.lstrip("+-").rstrip()
    suffix = ""
    for suf in (" months", " month", "%", "pp"):
        if stripped.endswith(suf):
            suffix = suf
            stripped = stripped[: -len(suf)].rstrip()
            break
    try:
        canonical = float(stripped)
    except (TypeError, ValueError):
        return []
    if canonical == 0:
        return []

    # Build the candidate variant set. Conservative: only round-style
    # corruptions of the same integer part. "1.24" -> ["1.2", "1.3",
    # "1.25", "1.23", "1.240"]. "52.6" -> ["52.5", "52.7", "53",
    # "52.60"].
    candidates: set[str] = set()
    # 1-decimal truncation
    candidates.add(f"{canonical:.1f}")
    # +/- 1 in last decimal place (rounded variant)
    decimals = stripped.split(".")[-1] if "." in stripped else ""
    if decimals:
        digits = len(decimals)
        step = 10 ** -digits
        candidates.add(f"{canonical + step:.{digits}f}")
        candidates.add(f"{canonical - step:.{digits}f}")
        # Trailing-zero variant
        candidates.add(f"{canonical:.{digits + 1}f}")
    # Integer-only truncation (for values like 52.6 -> 53)
    candidates.add(str(round(canonical)))
    # Remove the canonical value itself from the candidate set --
    # only proper variants matter.
    canonical_str_no_suffix = stripped
    candidates.discard(canonical_str_no_suffix)
    candidates.discard(f"{canonical:g}")

    # Restore the sign + suffix on each candidate for the text scan.
    sign = "-" if value.startswith("-") else ("+" if value.startswith("+") else "")
    found: set[str] = set()
    for cand in candidates:
        # Strip a possible leading "-" the formatting may have
        # introduced (e.g. when canonical is small negative).
        cand_clean = cand.lstrip("-+")
        variant = f"{sign}{cand_clean}{suffix}"
        # Word-boundary-ish check: the variant should appear as a
        # standalone number, not as a substring of a larger number.
        # Use a regex with non-digit-non-dot lookarounds.
        pattern = re.compile(
            rf"(?<![\d.]){re.escape(variant)}(?![\d.])")
        if pattern.search(text):
            found.add(variant)
    return sorted(found)


def verify_export_against_cache(
    content_text: str | None,
    value_manifest: dict[str, dict[str, str]] | None,
    current_data_hash: str,
    generation_data_hash: str,
    document_type: str,
) -> dict[str, Any]:
    """Layer 3 export-time check. Confirms the exported document
    matches the cache the manifest was built against.

    Three checks:
      1. data_hash staleness -- WARNING (not error). The document
         is still internally consistent with the cache it was
         generated against; the operator just needs to know fresher
         data may have arrived.
      2. value presence -- ERROR. A manifest value missing from the
         document means a manual edit (or a render-time corruption)
         removed it. Submission-blocking.
      3. corrupted variants -- ERROR. A "1.2" where "1.24" was
         expected is a rounding-edit corruption.

    Fail-open across the board. None / empty inputs return a
    passed=True dict with a 'skipped' field so the export handler
    can decide whether to surface the skip reason or treat it as a
    pre-Layer-3 draft (no manifest, nothing to verify).

    Returns:
      {passed, warnings, errors, data_hash_match, verified_at,
       document_type, n_values_verified, n_values_missing}
    """
    from datetime import datetime, timezone

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # No manifest = pre-Layer-3 draft. The export still ships; the
    # verification result records the skip reason so the frontend
    # can show a neutral "Not yet verified" state instead of a
    # spurious "Failed" badge.
    if not value_manifest:
        return {
            "passed": True,
            "warnings": [],
            "errors": [],
            "data_hash_match": (
                current_data_hash == generation_data_hash),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "document_type": document_type,
            "n_values_verified": 0,
            "n_values_missing": 0,
            "skipped": "no_value_manifest",
        }

    # Check 1: data hash staleness (WARNING, not error).
    if current_data_hash and generation_data_hash \
            and current_data_hash != generation_data_hash:
        warnings.append({
            "type": "stale_data_hash",
            "severity": "medium",
            "message": (
                f"Document generated against hash "
                f"{generation_data_hash[:8]} but current cache "
                f"is {current_data_hash[:8]}. New data may have "
                "arrived. Consider regenerating before "
                "submitting."),
        })

    content = content_text or ""
    n_verified = 0
    n_missing = 0

    # Check 2: value presence. A manifest value not in the document
    # is "edited away" -- ERROR.
    for value, meta in value_manifest.items():
        if value in content:
            n_verified += 1
        else:
            n_missing += 1
            errors.append({
                "type": "value_missing_from_export",
                "severity": "high",
                "token": meta.get("token"),
                "expected_value": value,
                "message": (
                    f"Expected '{value}' from {meta.get('token')} "
                    "not found in export. The value may have been "
                    "edited or removed."),
            })

    # Check 3: corrupted variants. A "1.2" where "1.24" was expected
    # signals a rounding-edit corruption -- ERROR.
    for value, meta in value_manifest.items():
        variants = _find_corrupted_variants(value, content)
        for variant in variants:
            errors.append({
                "type": "value_corrupted",
                "severity": "high",
                "token": meta.get("token"),
                "expected": value,
                "found": variant,
                "message": (
                    f"'{value}' appears as '{variant}' in the "
                    "export. This may be a rounding or "
                    "formatting error introduced by a manual edit."),
            })

    # Check 4: unfilled tokens (June 25 2026). Any literal
    # {{TOKEN}} remaining in the exported content means the
    # substitution layer didn't have a key for that name --
    # either the writer prompt cited a non-existent token, or
    # the table builder doesn't yet emit it. Document_audit's
    # check_unresolved_placeholders surfaces the same condition
    # as a warning post-generation, but it doesn't block the
    # download. Elevating to an error here closes the loop:
    # the user-visible symptom (a literal '{{MIN_VARIANCE_
    # POST2022_SHARPE}}' string in the exported DOCX) is
    # exactly what this check catches at export time.
    leftover_tokens = unresolved_placeholders(content)
    for token in leftover_tokens:
        errors.append({
            "type": "unfilled_token",
            "severity": "high",
            "token": token,
            "message": (
                f"Token {token} was not substituted before export. "
                "Either the substitution table is missing this "
                "key, or the writer cited a token that does not "
                "exist. Re-generate the document; if the symptom "
                "persists, the token name must be added to "
                "tools/numeric_substitution.build_substitution_table."),
        })

    return {
        "passed": len(errors) == 0,
        "warnings": warnings,
        "errors": errors,
        "data_hash_match": (
            current_data_hash == generation_data_hash),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "document_type": document_type,
        "n_values_verified": n_verified,
        "n_values_missing": n_missing,
        "n_unfilled_tokens": len(leftover_tokens),
    }
