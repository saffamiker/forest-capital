"""tools/council_question_bundles.py — question-type context bundles.

Refinement WITHIN PR #229's page-scoped context. PR #229 (May 29 2026)
established the OUTER scope: when a question is asked from one of the
three council-facing landing pages, the endpoint resolves that page's
live cached data into a JSON block and the CIO grounds its synthesis
in it. The fixed page bundles included every potentially-relevant
field for the page — a deliberately wide net.

The token-audit workstream (May 2026) showed the council was paying
for ~10-12k input tokens per query, much of which the CIO never used:
a "what is the current regime?" question read the bootstrap CI table,
the crisis windows, the factor loadings, all unused by the synthesis
for that question. This module narrows the page bundle to a question-
type-specific subset.

CLASSIFIER ARCHITECTURE
  Keyword matching. Each bundle owns a small set of keywords; the
  classifier scores each bundle by the number of distinct keywords
  present in the lowercased query. A bundle is selected if:
    - it scored >= 2 distinct hits, AND
    - no other bundle scored >= 1 hit.
  Otherwise the classifier returns None → caller falls back to
  PR #229's page bundle (or no context if no page).

  This is the "simple is fine — a coarse five-way split captures
  most of the token savings" specification from the user's June 3
  2026 directive. A future PR can swap the keyword classifier for a
  small embedding model without touching the bundles themselves.

THE FIVE BUNDLES
  REGIME         HMM state + confidence + transition matrix +
                 regime-conditional performance + regime correlation
                 history. Excludes bootstrap CIs, factor loadings,
                 crisis windows.
  RECOMMENDATION Current blend weights + OOS Sharpe + transaction-
                 cost sensitivity + constraint table + strategy
                 characterisations. Excludes transition matrix,
                 bootstrap CIs, factor loadings.
  RISK           CVaR + max drawdown + crisis window performance +
                 factor exposures + tail risk (skew/kurtosis).
                 Excludes forward projection, cost sensitivity,
                 bootstrap CIs.
  STATISTICAL    Bootstrap CIs + p-values + DSR + FDR result +
                 sample size limitation. Excludes regime data,
                 crisis windows, forward projection.
  FORWARD        Forward projection + transition matrix + horizons +
                 regime confidence. Excludes bootstrap CIs, factor
                 loadings, crisis windows.

EVERY BUNDLE IS FAIL-OPEN
  Each resolver pulls from existing warm caches (no recompute, no
  base64). A cache miss, a DB error, or a cold deploy returns None
  for that field — never a synchronous fetch, never a 500. If the
  whole bundle ends up empty, the resolver returns None and the
  caller falls back through the same chain PR #229 uses.

NO RECOMPUTATION — STRICT
  This module NEVER calls get_full_history(), run_all_strategies(),
  or any other compute helper. The reads are all single-row JSONB
  SELECTs against analytics_metrics_cache + cio_recommendations +
  strategy_results_cache, plus the live regime read (15-min in-
  process cache from tools.regime_detector).
"""
from __future__ import annotations

import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── Question types — the five named bundles ───────────────────────────────

QUESTION_TYPE_REGIME = "regime"
QUESTION_TYPE_RECOMMENDATION = "recommendation"
QUESTION_TYPE_RISK = "risk"
QUESTION_TYPE_STATISTICAL = "statistical"
QUESTION_TYPE_FORWARD = "forward"
QUESTION_TYPE_FULL = "full"             # explicit fallback label
QUESTION_TYPE_BASELINE_FULL = "baseline_full"   # baseline capture marker

ALL_QUESTION_TYPES = (
    QUESTION_TYPE_REGIME,
    QUESTION_TYPE_RECOMMENDATION,
    QUESTION_TYPE_RISK,
    QUESTION_TYPE_STATISTICAL,
    QUESTION_TYPE_FORWARD,
    QUESTION_TYPE_FULL,
    QUESTION_TYPE_BASELINE_FULL,
)


# ── Classifier — keyword sets per bundle ──────────────────────────────────
# Each bundle's keyword set names words that strongly signal that bundle
# WITHOUT overlap. Words that COULD signal multiple bundles (e.g.
# "portfolio" — every question touches it) are deliberately omitted so
# the disjoint-hit rule below can fire confidently.

_KEYWORDS: dict[str, frozenset[str]] = {
    QUESTION_TYPE_REGIME: frozenset({
        "regime", "regimes", "hmm", "bull", "bear", "transition",
        "regime-switching", "macro", "vix", "volatility regime",
        "yield curve", "credit spread",
    }),
    QUESTION_TYPE_RECOMMENDATION: frozenset({
        # NOTE: "blend" was tempting here but it's the project's
        # generic noun for the regime-conditional portfolio — it
        # appears in RISK / STATISTICAL questions too ("tail risk
        # for the blend"). Left out so disjoint-evidence isn't
        # contaminated. Recommendation questions still classify
        # cleanly on weights / allocation / recommend.
        "recommend", "recommendation", "allocate", "allocation",
        "weights", "weighting", "overweight", "underweight",
        "constraint", "constraints", "what should", "what would you",
        "advice", "implementation",
    }),
    QUESTION_TYPE_RISK: frozenset({
        "drawdown", "cvar", "var", "tail", "downside", "crisis",
        "stress", "loss", "worst", "skew", "kurtosis",
        "max dd", "crash", "fat tail",
    }),
    QUESTION_TYPE_STATISTICAL: frozenset({
        "significant", "significance", "p-value", "p value", "pvalue",
        "bootstrap", "confidence interval", "deflated sharpe", "dsr",
        "fdr", "false discovery", "sample size", "statistical",
        "statistically", "robust", "robustness",
    }),
    QUESTION_TYPE_FORWARD: frozenset({
        "forecast", "projection", "outlook", "going forward",
        "next month", "next quarter", "horizon", "forward",
        "monte carlo", "what next", "future", "predict",
        "expected", "12-month", "6-month",
    }),
}


# Word-boundary regex per keyword. Compiled once at import time. A
# multi-word keyword (e.g. "yield curve") matches anywhere the literal
# phrase appears with whitespace flexibility; a single word matches on
# word boundaries so "regime" doesn't fire on "regimen" or "stylized".
_KEYWORD_PATTERNS: dict[str, list[re.Pattern[str]]] = {}
for _bundle, _kws in _KEYWORDS.items():
    _patterns: list[re.Pattern[str]] = []
    for _kw in _kws:
        if " " in _kw or "-" in _kw:
            # Multi-word / hyphenated: literal phrase, flexible spacing.
            _phrase = re.escape(_kw).replace(r"\ ", r"\s+")
            _patterns.append(re.compile(_phrase, re.IGNORECASE))
        else:
            _patterns.append(
                re.compile(rf"\b{re.escape(_kw)}\b", re.IGNORECASE))
    _KEYWORD_PATTERNS[_bundle] = _patterns


# Minimum disjoint-hit count to fire a bundle. "≥ 2 hits in one
# bundle's set AND zero hits in any other" — the simplest possible
# disjoint-evidence rule. Q2 default from the June 3 2026 finding.
_MIN_HITS = 2


def classify_question(query: str) -> str | None:
    """Returns the question-type bundle name (one of ALL_QUESTION_TYPES
    minus FULL/BASELINE_FULL) or None when no bundle clears the
    confidence threshold. The classifier is deterministic, single-pass,
    and never raises — a malformed query returns None.

    A bundle wins when it scored >= _MIN_HITS distinct keyword hits and
    every other bundle scored zero. Ties or split signals return None,
    so the caller falls back to the wider PR #229 page bundle (or no
    context).
    """
    if not query or not isinstance(query, str):
        return None
    text = query.lower()
    scores: dict[str, int] = {}
    for bundle, patterns in _KEYWORD_PATTERNS.items():
        n_hits = 0
        for p in patterns:
            if p.search(text):
                n_hits += 1
        if n_hits > 0:
            scores[bundle] = n_hits
    if not scores:
        return None
    top_bundle, top_hits = max(scores.items(), key=lambda kv: kv[1])
    if top_hits < _MIN_HITS:
        return None
    # No other bundle may have scored — strict disjoint-evidence rule.
    other_hits = sum(v for k, v in scores.items() if k != top_bundle)
    if other_hits > 0:
        return None
    return top_bundle


# ── Bundle resolvers ──────────────────────────────────────────────────────
#
# One async resolver per bundle. Each returns dict | None. The dict
# carries a curated subset of cached analytics; None means "no useful
# data is in cache yet — fall back upstream." Every cache read is
# fail-open per the convention PR #229 established.


async def regime_bundle() -> dict[str, Any] | None:
    """REGIME bundle — HMM state, confidence, transition matrix,
    regime-conditional perf, regime correlation history."""
    out: dict[str, Any] = {}
    try:
        from tools.regime_detector import detect_current_regime
        r = detect_current_regime() or {}
        if r:
            probs = r.get("hmm_probabilities") or {}
            hmm_state = r.get("hmm_regime")
            confidence = (float(probs.get(hmm_state, 0.0))
                          if hmm_state and isinstance(probs, dict)
                          else None)
            out["regime"] = {
                "hmm_state": hmm_state,
                "hmm_confidence": confidence,
                "hmm_probabilities": probs,
                "threshold_regime": r.get("threshold_regime"),
                "regimes_agree": r.get("regimes_agree"),
                "vix_level": r.get("vix_level"),
                "yield_curve_slope": r.get("yield_curve_slope"),
                "pre_2022_avg_correlation": r.get(
                    "pre_2022_avg_correlation"),
                "post_2022_avg_correlation": r.get(
                    "post_2022_avg_correlation"),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_regime_read_failed", error=str(exc))

    try:
        from tools.precomputed_analytics import get_latest_metric
        tm = await get_latest_metric("transition_matrix")
        if tm:
            out["transition_matrix"] = tm
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_regime_tm_read_failed", error=str(exc))

    try:
        from tools.precomputed_analytics import get_latest_metric
        aa = await get_latest_metric("academic_analytics") or {}
        if aa.get("regime_conditional"):
            out["regime_conditional"] = aa["regime_conditional"]
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_regime_rc_read_failed",
                    error=str(exc))

    return out or None


async def recommendation_bundle() -> dict[str, Any] | None:
    """RECOMMENDATION bundle — blend weights, OOS Sharpe, cost
    sensitivity, constraint table, strategy characterisations."""
    out: dict[str, Any] = {}

    try:
        from tools.precomputed_analytics import get_latest_metric
        fp = await get_latest_metric("forward_projection") or {}
        if fp.get("blend_weights"):
            out["blend_weights"] = fp["blend_weights"]
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_rec_blend_failed", error=str(exc))

    try:
        from tools.precomputed_analytics import get_latest_metric
        oos = await get_latest_metric("oos_summary")
        if oos:
            out["oos_summary"] = oos
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_rec_oos_failed", error=str(exc))

    try:
        from tools.regime_meta_validation import get_cached_cost_sensitivity
        cs = await get_cached_cost_sensitivity()
        if cs:
            # Keep the scenarios + headline counts; drop the per-event
            # rebalance log (too verbose for a single agent prompt).
            out["cost_sensitivity"] = {
                "n_rebalances": cs.get("n_rebalances"),
                "scenarios": cs.get("scenarios"),
                "gross_sharpe": cs.get("gross_sharpe"),
                "benchmark_sharpe": cs.get("benchmark_sharpe"),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_rec_cost_failed", error=str(exc))

    # Constraint table — assembled from config.py at injection time.
    # No cache, no recompute, no new state to manage.
    try:
        import config as _cfg
        out["constraints"] = {
            "min_weight":           getattr(_cfg, "MIN_WEIGHT", None),
            "max_weight":           getattr(_cfg, "MAX_WEIGHT", None),
            "rebalance_frequency":  getattr(_cfg, "REBALANCE_FREQ_DYNAMIC", None),
            "transaction_cost_bps": getattr(_cfg, "TRANSACTION_COST_BPS", None),
            "fully_invested":       getattr(_cfg, "FULLY_INVESTED", None),
            "target_volatility":    getattr(_cfg, "TARGET_VOLATILITY", None),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_rec_constraints_failed",
                    error=str(exc))

    try:
        from tools.precomputed_analytics import get_latest_metric
        chars = await get_latest_metric("strategy_characterisations")
        if chars:
            out["strategy_characterisations"] = chars
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_rec_chars_failed", error=str(exc))

    return out or None


async def risk_bundle() -> dict[str, Any] | None:
    """RISK bundle — CVaR, max DD, crisis windows, factor exposures,
    tail risk (skew/kurtosis)."""
    out: dict[str, Any] = {}

    try:
        from tools.cache import get_latest_strategy_cache
        strategies = await get_latest_strategy_cache() or {}
        # Compact slice — every strategy's risk-relevant fields only.
        risk_slice = {}
        for name, r in strategies.items():
            risk_slice[name] = {
                "max_drawdown":           r.get("max_drawdown"),
                "drawdown_duration_days": r.get("drawdown_duration_days"),
                "drawdown_recovery_days": r.get("drawdown_recovery_days"),
                "var_95":                 r.get("var_95"),
                "cvar_95":                r.get("cvar_95"),
                "skewness":               r.get("skewness"),
                "kurtosis":               r.get("kurtosis"),
                "volatility":             r.get("volatility"),
            }
        if risk_slice:
            out["risk_metrics"] = risk_slice
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_risk_metrics_failed", error=str(exc))

    try:
        from tools.precomputed_analytics import get_latest_metric
        crisis = await get_latest_metric("crisis_performance")
        if crisis:
            out["crisis_performance"] = crisis
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_risk_crisis_failed", error=str(exc))

    try:
        from tools.precomputed_analytics import get_latest_metric
        aa = await get_latest_metric("academic_analytics") or {}
        if aa.get("factor_loadings"):
            out["factor_loadings"] = aa["factor_loadings"]
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_risk_factor_failed", error=str(exc))

    return out or None


async def statistical_bundle() -> dict[str, Any] | None:
    """STATISTICAL bundle — bootstrap CIs, p-values, DSR, FDR result,
    sample size limitation."""
    out: dict[str, Any] = {}

    try:
        from tools.precomputed_analytics import get_latest_metric
        aa = await get_latest_metric("academic_analytics") or {}
        if aa.get("bootstrap_ci_sharpe"):
            out["bootstrap_ci_sharpe"] = aa["bootstrap_ci_sharpe"]
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_stat_bootstrap_failed",
                    error=str(exc))

    try:
        from tools.cache import get_latest_strategy_cache
        strategies = await get_latest_strategy_cache() or {}
        sig_slice = {}
        for name, r in strategies.items():
            if name == "BENCHMARK":
                continue
            sig_slice[name] = {
                "sharpe_ratio":               r.get("sharpe_ratio"),
                "p_value_ttest":              r.get("p_value_ttest"),
                "p_value_corrected":          r.get("p_value_corrected"),
                "dsr_p_value":                r.get("dsr_p_value"),
                "probabilistic_sharpe_ratio": r.get(
                    "probabilistic_sharpe_ratio"),
                "passes_spa":                 r.get("passes_spa"),
                "tier1_gates_passed":         r.get("tier1_gates_passed"),
                "is_significant":             r.get("is_significant"),
            }
        if sig_slice:
            out["statistical_tests"] = sig_slice
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_stat_sig_failed", error=str(exc))

    # The fixed-copy FDR result + sample-size limitation statement.
    # Documented in CLAUDE.md (Significance Strategies Tile framing).
    # Embedded verbatim so the agent answers "is X significant?" with
    # the project's standard answer (not a confabulation).
    out["fdr_and_sample_size_note"] = (
        "Under the Benjamin et al. (2018) p < 0.005 threshold with "
        "Benjamini-Hochberg FDR correction across all 10 strategies, "
        "NO strategy clears formal statistical significance. This is "
        "the strict academic standard. Three strategies show "
        "economically meaningful outperformance vs the benchmark "
        "(Sharpe 0.52): Regime Switching (+11 bps), Momentum Rotation "
        "(+6 bps), Equal Weight (+5 bps). The 0/10 result is "
        "methodological honesty — a single-strategy test of Regime "
        "Switching would pass p < 0.05 uncorrected. With 282 monthly "
        "observations (2002-07 to 2025-12) and 10 strategies tested "
        "simultaneously, the sample is at the lower bound of adequate "
        "power for the strict threshold."
    )

    return out or None


async def forward_bundle() -> dict[str, Any] | None:
    """FORWARD bundle — forward projection, transition matrix,
    horizons, regime confidence."""
    out: dict[str, Any] = {}

    try:
        from tools.precomputed_analytics import get_latest_metric
        fp = await get_latest_metric("forward_projection") or {}
        if fp:
            # Strip the (sizeable) percentile band arrays; keep the
            # blend_weights + horizons + transition_matrix the agent
            # needs to reason directionally.
            out["forward_projection"] = {
                "blend_weights":     fp.get("blend_weights"),
                "horizons":          fp.get("horizons"),
                "transition_matrix": fp.get("transition_matrix"),
                "regime_history":    fp.get("regime_history"),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_forward_fp_failed", error=str(exc))

    try:
        from tools.regime_detector import detect_current_regime
        r = detect_current_regime() or {}
        if r:
            probs = r.get("hmm_probabilities") or {}
            hmm_state = r.get("hmm_regime")
            out["regime"] = {
                "hmm_state": hmm_state,
                "hmm_confidence": (float(probs.get(hmm_state, 0.0))
                                   if hmm_state and isinstance(probs, dict)
                                   else None),
                "hmm_probabilities": probs,
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_forward_regime_failed",
                    error=str(exc))

    return out or None


# ── Dispatcher ────────────────────────────────────────────────────────────

_BUNDLE_RESOLVERS = {
    QUESTION_TYPE_REGIME:         regime_bundle,
    QUESTION_TYPE_RECOMMENDATION: recommendation_bundle,
    QUESTION_TYPE_RISK:           risk_bundle,
    QUESTION_TYPE_STATISTICAL:    statistical_bundle,
    QUESTION_TYPE_FORWARD:        forward_bundle,
}


async def resolve_bundle(question_type: str) -> dict[str, Any] | None:
    """Look up and run the resolver for the given question_type.
    Returns None for unknown / fallback types or when the resolver
    found nothing to return."""
    resolver = _BUNDLE_RESOLVERS.get(question_type)
    if resolver is None:
        return None
    try:
        return await resolver()
    except Exception as exc:  # noqa: BLE001
        log.warning("question_bundle_resolve_failed",
                    question_type=question_type, error=str(exc))
        return None
