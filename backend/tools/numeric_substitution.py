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

import re
from typing import Any


# Approximate trading days per month -- used to convert the
# backtester's drawdown_recovery_days into the brief-facing
# "recovery months" figure. The cache stores DAYS; the brief reads
# MONTHS. One number, one place. Same convention the analytics
# narrative uses.
_TRADING_DAYS_PER_MONTH = 21


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
    dash -- never a placeholder leak."""
    try:
        days = float(v)
    except (TypeError, ValueError):
        return "—"
    if days <= 0:
        return "—"
    months = round(days / _TRADING_DAYS_PER_MONTH)
    return f"{months} months"


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


def build_substitution_table(
    strategy_cache: dict,
    cio_recommendation: dict | None = None,
    data_hash: str = "",
    *,
    oos_sharpe_blend: float | None = None,
    oos_sharpe_benchmark: float | None = None,
    pre_2022_eq_ig_correlation: float | None = None,
    post_2022_eq_ig_correlation: float | None = None,
    oos_window_definition: str = "January 2022 through May 2026",
    oos_window_months: int = 53,
    study_months: int | None = None,
    study_start: str = "July 2002",
    study_end: str = "May 2026",
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
    """
    benchmark = _get_strategy(strategy_cache, "BENCHMARK")
    classic = _get_strategy(strategy_cache, "CLASSIC_60_40")
    regime = _get_strategy(strategy_cache, "REGIME_SWITCHING")
    cio = cio_recommendation or {}

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
        "{{REGIME_SWITCHING_RECOVERY}}":
            format_months_from_days(regime.get("drawdown_recovery_days")),
        "{{BENCHMARK_RECOVERY}}":
            format_months_from_days(
                benchmark.get("drawdown_recovery_days")),
        "{{CLASSIC_6040_RECOVERY}}":
            format_months_from_days(
                classic.get("drawdown_recovery_days")),

        # ── Pre/post-2022 sub-period metrics ───────────────────────
        # These come from analytics.regime_conditional_performance,
        # which the caller merges into the strategy dict before
        # passing in. Falls back to em dash when missing.
        "{{REGIME_SWITCHING_POST2022_SHARPE}}":
            format_sharpe(regime.get("post_2022_sharpe")),
        "{{BENCHMARK_POST2022_SHARPE}}":
            format_sharpe(benchmark.get("post_2022_sharpe")),
        "{{CLASSIC_6040_POST2022_SHARPE}}":
            format_sharpe(classic.get("post_2022_sharpe")),
        "{{REGIME_SWITCHING_PRE2022_SHARPE}}":
            format_sharpe(regime.get("pre_2022_sharpe")),
        "{{BENCHMARK_PRE2022_SHARPE}}":
            format_sharpe(benchmark.get("pre_2022_sharpe")),

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
        "{{CURRENT_EQUITY_PCT}}": format_pct(cio.get("implied_equity")),
        "{{CURRENT_IG_PCT}}": format_pct(cio.get("implied_ig")),
        "{{CURRENT_HY_PCT}}": format_pct(cio.get("implied_hy")),

        # ── Study period ───────────────────────────────────────────
        "{{STUDY_MONTHS}}": (
            str(study_months) if study_months is not None
            else str(strategy_cache.get("n_observations") or "—")),
        "{{STUDY_START}}": study_start,
        "{{STUDY_END}}": study_end,
        "{{DATA_HASH}}": (data_hash or "")[:8] or "—",
    }
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
    "SHARPE":     ("sharpe_ratio",            format_sharpe),
    "MAX_DD":     ("max_drawdown",            format_pct),
    "CAGR":       ("cagr",                    format_pct),
    "VOLATILITY": ("volatility",              format_pct),
    "RECOVERY":   ("drawdown_recovery_days",  format_months_from_days),
}


def _append_per_strategy_tokens(
    table: dict[str, str], strategy_cache: dict,
) -> None:
    """Generate {{STRATEGY_NAME_METRIC}} tokens for every strategy in
    the cache. Appendix-only -- the brief never uses these because
    the brief is locked to the three-strategy lens. Mutates table
    in place.

    Strategy IDs come straight from the cache keys (BENCHMARK,
    REGIME_SWITCHING, CLASSIC_60_40, MIN_VARIANCE, ...). The token
    uses the cache key verbatim -- a strategy rename in the cache
    surfaces as a missing token rather than silent re-mapping, which
    is the safer default."""
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
# Process-wide dict, NOT per-request -- the data_hash is the
# invalidation key. When data ticks over, the new hash builds a new
# table; the old entry sticks around until the process restarts
# (the strategy_results_cache itself is the source of truth and
# can grow without bound; we trim later if memory becomes an issue).

_substitution_cache: dict[str, dict[str, str]] = {}


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
    if not data_hash:
        # An empty hash means data_status was unavailable -- build
        # the table inline but DON'T cache it, so the next call
        # (presumably with a real hash) builds afresh.
        table = build_substitution_table(
            strategy_cache, cio_recommendation, "", **kwargs)
        if include_per_strategy:
            _append_per_strategy_tokens(table, strategy_cache)
        return table
    if rebuild or data_hash not in _substitution_cache:
        table = build_substitution_table(
            strategy_cache, cio_recommendation, data_hash, **kwargs)
        if include_per_strategy:
            _append_per_strategy_tokens(table, strategy_cache)
        _substitution_cache[data_hash] = table
    return _substitution_cache[data_hash]


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
