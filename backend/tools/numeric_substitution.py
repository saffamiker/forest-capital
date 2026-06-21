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
    implied_allocation: dict | None = None,
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
        "{{DATA_HASH}}": (data_hash or "")[:8] or "—",
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

        # Turnover + net-of-cost Sharpe sensitivity. Pulled from the
        # regime row (annualized_turnover is the canonical key the
        # backtester emits) and from top-level cache fields populated
        # by the cost-sensitivity job.
        "{{REGIME_SWITCHING_TURNOVER}}": format_pct(
            regime.get("annualized_turnover")),
        "{{NET_SHARPE_10BP}}": format_sharpe(
            strategy_cache.get("net_sharpe_10bp")),
        "{{NET_SHARPE_15BP}}": format_sharpe(
            strategy_cache.get("net_sharpe_15bp")),
        "{{NET_SHARPE_20BP}}": format_sharpe(
            strategy_cache.get("net_sharpe_20bp")),

        # Tail risk (deck slide 5 references CVaR explicitly).
        "{{CVAR_99_BENCHMARK}}": format_pct(
            benchmark.get("cvar_99_annualized")),

        # Live watch points (Slide 7 macro context). VIX / OAS / yield
        # curve / equity trend are populated by the regime_signals_cache
        # warm path; ESS by the ESS computation in cio_recommendation.
        # When cold, the str() fallback prevents a KeyError but renders
        # an em dash for the operator to spot.
        "{{VIX_CURRENT}}": str(
            strategy_cache.get("vix_current") or "—"),
        "{{CREDIT_SPREAD_CURRENT}}": str(
            strategy_cache.get("hy_oas_current") or "—"),
        "{{YIELD_CURVE_CURRENT}}": str(
            strategy_cache.get("yield_curve_current") or "—"),
        "{{EQUITY_TREND_CURRENT}}": format_pct(
            strategy_cache.get("equity_trend_current")),
        "{{ESS_CURRENT}}": str(
            strategy_cache.get("kish_ess") or "—"),

        # Live blend composition (slide 7 + slide 11). The CIO row
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
    }
