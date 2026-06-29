"""
tools/academic_export.py

Shared data-gathering and narrative layer behind the three generated
academic deliverables — the midpoint paper, the executive brief, and the
final presentation deck (the .docx/.pptx builders live in
tools/academic_docx.py and tools/academic_deck.py).

Two responsibilities:

  gather_document_data()  — pulls every figure the documents cite from
    data already in PostgreSQL (market_data_monthly,
    strategy_results_cache, ff_factors_monthly), the Team Activity tables,
    and the academic_documents table. Light reads only — never
    get_full_history() or run_all_strategies(). On a cold cache or in the
    test environment it returns available=False and the builders fall
    back to [DATA PENDING] markers rather than failing the document.

  harness_narrative()  — runs one Academic Writer generation through the
    generator-evaluator harness with the academic_review peer-evaluator
    criteria (the spec mandates the harness for every academic_writer
    call). Fail-open: any error — including the test environment, where
    no API key is configured — returns a [DATA PENDING] marker, so one
    failed section never sinks the whole document.

Every generated document is a FIRST DRAFT for Bob to refine. The
[DATA PENDING] marker and the AI DRAFT banner make that explicit.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import structlog

from config import ENVIRONMENT

log = structlog.get_logger(__name__)

# Inserted wherever a section's source data could not be loaded. A grep
# for this string across a generated document tells Bob exactly what he
# still has to supply by hand.
DATA_PENDING = "[DATA PENDING]"


async def load_substitution_metric_sources(
    data_hash: str | None = None,
) -> tuple[
    list[dict], list[dict], dict | None, dict | None,
]:
    """Read the analytics_metrics_cache metric_kinds that feed
    the substitution table's pre/post 2022 Sharpes, Carhart
    factor loadings, net-of-cost Sharpe, and crisis-window
    drawdown tokens.

    data_hash -- June 27 2026. When supplied, routes through
    get_metric(data_hash, metric_kind) so the deck / brief /
    appendix under submission freeze read the metrics row that
    matches the freeze hash, NOT the latest row. Without this,
    a freeze-keyed substitution table was being populated with
    LIVE metric values (same architectural bug class as
    get_latest_recommendation vs get_cached_for_hash for the
    CIO row). When None (legacy callers, live platform reads),
    falls through to get_latest_metric and returns the most
    recent row per metric_kind regardless of hash.

    Returns (regime_conditional, factor_loadings,
    cost_sensitivity, crisis_performance).

    Single read for both academic_analytics fields -- the payload
    bundles regime_conditional + factor_loadings together at
    metric_kind='academic_analytics' so one fetch covers both
    Gap 1 (pre/post 2022 Sharpes) and Gap 2 (factor loadings).
    Cost sensitivity is a separate metric_kind. Crisis
    performance is its own metric_kind written by
    refresh_diversification_metrics (precomputed_analytics.py:917)
    and feeds the per-strategy GFC / Rate Shock 2022 drawdown
    tokens.

    Fail-open: missing fields return empty lists / None so the
    substitution table degrades to em-dashes rather than raising.
    """
    regime_conditional: list[dict] = []
    factor_loadings: list[dict] = []
    cost_sensitivity: dict | None = None
    crisis_performance: dict | None = None
    try:
        from tools.precomputed_analytics import (
            get_latest_metric, get_metric,
        )

        async def _read(kind: str) -> Any:
            if data_hash:
                return await get_metric(data_hash, kind)
            return await get_latest_metric(kind)

        aa = await _read("academic_analytics")
        if isinstance(aa, dict):
            rc = aa.get("regime_conditional")
            if isinstance(rc, list):
                regime_conditional = rc
            fl = aa.get("factor_loadings")
            if isinstance(fl, list):
                factor_loadings = fl
        cs = await _read("oos_cost_sensitivity")
        if isinstance(cs, dict):
            cost_sensitivity = cs
        cp = await _read("crisis_performance")
        if isinstance(cp, dict):
            crisis_performance = cp
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "substitution_metric_sources_load_failed",
            error=str(exc))
    return (
        regime_conditional, factor_loadings,
        cost_sensitivity, crisis_performance)


class StrategyCacheMissingForHashError(RuntimeError):
    """June 27 2026 -- raised by gather_document_data when called
    with an explicit data_hash that has no strategy_results_cache
    row. Translated by the 3 document generators in main.py into
    HTTPException 500 with the spec error:
        'Export failed: strategy cache unavailable for hash <prefix>.
         Run light refresh and try again.'

    This was the dominant freeze leak before PR 1 v3: the strategy
    headlines (Sharpe / max_drawdown / recovery / blend weights)
    were sourced from get_latest_strategy_cache() which returns the
    live latest row regardless of hash. Under freeze, the deck /
    brief / appendix substitution table would carry post-freeze
    strategy values for ~20 distinct tokens. With this exception
    the freeze deck fails loudly when the freeze hash has no
    cached strategy results, instead of silently leaking live."""

    def __init__(self, data_hash: str):
        super().__init__(
            f"Export failed: strategy_results_cache has no row "
            f"for hash {data_hash[:8]}. Run light refresh and "
            "try again.")
        self.data_hash = data_hash


# June 29 2026 -- SUBMISSION SCOPE: the brief, appendix, deck,
# and script expose ONLY these three strategies. The full
# 10-strategy analytical engine still runs in the platform's
# live caches (Performance Record, CIO Recommendation Card,
# Implied Asset Allocation chart, all admin / dashboard
# surfaces); document generation deliberately narrows to the
# panel-defense set so every table / narrative / token
# substitution operates on the same scope.
#
# This is the SINGLE FILTER POINT. gather_document_data and
# gather_analytical_appendix_data apply it to the data bundle
# they return; every downstream consumer (table builders,
# Academic Writer prompts, story plan, substitution table)
# operates on the filtered set without per-site changes.
SUBMISSION_STRATEGIES: frozenset[str] = frozenset({
    "BENCHMARK",
    "CLASSIC_60_40",
    "REGIME_SWITCHING",
})


def _filter_to_submission_scope(
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Mutate `bundle` in place so every strategy-bearing
    surface only carries entries for SUBMISSION_STRATEGIES.

    Filters:
      strategy_results (dict)       -> keys in SUBMISSION_STRATEGIES
      strategy_metadata (dict)      -> keys in SUBMISSION_STRATEGIES
      summary_statistics (list)     -> rows whose 'strategy' is in scope
      regime_conditional (list)     -> rows whose 'strategy' / 'strategy_name'
                                       is in scope
      factor_loadings (list)        -> rows whose 'strategy' is in scope
      drawdown_comparison (list)    -> rows whose 'strategy' is in scope
      cost_sensitivity (list)       -> NOT filtered (one row per bps
                                       tier, not per strategy)
      crisis_performance (dict)     -> 'rows' inner dict filtered to
                                       SUBMISSION_STRATEGIES keys

    Lists with no recognised strategy field pass through
    unchanged. Fail-open on unexpected shapes -- the document
    generator handles missing rows via [DATA PENDING].
    """
    def _is_in(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        s = row.get("strategy") or row.get("strategy_name")
        return isinstance(s, str) and s in SUBMISSION_STRATEGIES

    # Top-level strategy_results dict.
    sr = bundle.get("strategy_results")
    if isinstance(sr, dict):
        bundle["strategy_results"] = {
            k: v for k, v in sr.items()
            if k in SUBMISSION_STRATEGIES}

    sm = bundle.get("strategy_metadata")
    if isinstance(sm, dict):
        bundle["strategy_metadata"] = {
            k: v for k, v in sm.items()
            if k in SUBMISSION_STRATEGIES}

    for list_key in (
            "summary_statistics", "regime_conditional",
            "factor_loadings", "drawdown_comparison",
            # June 29 2026 -- bootstrap_ci_sharpe filter
            # (audit-finding 1, appendix-export PR). The Table
            # D1 builder iterates this list unfiltered; without
            # the scope filter the table renders all 10
            # strategies. Per operator: keep BENCHMARK +
            # CLASSIC_60_40 + REGIME_SWITCHING (the latter is
            # the closest proxy in the bootstrap data for the
            # regime-conditional blend's statistical
            # properties; the table footnote spells out that
            # the blend's OOS Sharpe is derived from the HMM-
            # posterior-weighted combination and is not
            # directly represented).
            "bootstrap_ci_sharpe"):
        rows = bundle.get(list_key)
        if isinstance(rows, list):
            bundle[list_key] = [r for r in rows if _is_in(r)]

    # crisis_performance has nested {windows, rows: {strategy: {...}}}.
    cp = bundle.get("crisis_performance")
    if isinstance(cp, dict) and isinstance(cp.get("rows"), dict):
        cp["rows"] = {
            k: v for k, v in cp["rows"].items()
            if k in SUBMISSION_STRATEGIES}
        bundle["crisis_performance"] = cp

    return bundle


async def gather_document_data(
    data_hash: str | None = None,
) -> dict[str, Any]:
    """
    Assembles the full data bundle the document builders consume.

    data_hash -- June 27 2026 (PR 1 v3, LEAK 1 closer). When
    supplied, the strategy_results_cache read routes through the
    hash-aware get_strategy_cache(data_hash) instead of
    get_latest_strategy_cache(). On hash miss raises
    StrategyCacheMissingForHashError -- the 3 document generators
    catch + translate to HTTPException 500 with the spec
    'Run light refresh and try again' message. Without this, the
    freeze deck silently leaked LIVE strategy values into ~20
    headline tokens (Sharpe / max_drawdown / recovery / blend
    weights). When None (legacy / live-platform callers),
    falls through to get_latest_strategy_cache (legacy behaviour).

    SOFT LEAK 2 reminder: get_monthly_returns() reads the
    market_data_monthly table with NO hash filter. Under the
    current freeze (Dec 2025) the table has no rows past freeze
    date so rolling correlation tokens are de-facto frozen, but
    this is NOT structurally enforced. A future backfill of
    post-freeze market data would leak the rolling correlation
    tokens. Acceptable for now per operator spec; flagged here
    so a future audit catches the soft leak if it surfaces.

    Never silently degrades on hash miss (would mask freeze
    integrity violation). Other failure modes (cold caches, API
    timeouts) still degrade to available=False with empty
    collections.
    """
    bundle: dict[str, Any] = {
        "available": False,
        "study_period": {"start": "—", "end": "—", "n_months": 0,
                         "ff_factors_end": None},
        "summary_statistics": [],
        "regime_conditional": [],
        "drawdown_comparison": [],
        "factor_loadings": [],
        "cumulative_returns": {"strategies": [], "points": []},
        "rolling_correlation": {},
        "strategy_results": {},
        "strategy_metadata": {},
        "risk_free_rate": None,
        "team_summary": {},
        "last_review_text": None,
        "academic_docs": [],
        # Workstream D — audit-disclosures bundle the report builders
        # consume. Empty here; populated below for non-test environments.
        # The builders fall through to a "no audit on record" disclosure
        # paragraph if this stays empty.
        "audit_disclosures": None,
    }

    # The test environment has no warmed caches and no API key — return
    # the empty bundle so the builders exercise their [DATA PENDING] path.
    if ENVIRONMENT == "test":
        return bundle

    # ── Analytics bundle — the same light reads /api/v1/analytics/academic
    #    uses; no get_full_history(), no run_all_strategies(). ──────────────
    try:
        import pandas as pd

        from tools.cache import (
            get_ff_factors, get_latest_strategy_cache,
            get_monthly_returns, get_strategy_cache,
        )
        from tools import analytics as an

        monthly = await get_monthly_returns()
        # June 27 2026 (PR 1 v3 LEAK 1 closer) -- hash-aware
        # strategy load. When data_hash is supplied (the 3 doc
        # generators always supply it now), pull the exact
        # strategy_results_cache row keyed to that hash. On miss
        # under freeze, raise loudly -- DO NOT fall back to
        # get_latest_strategy_cache (would silently leak LIVE
        # strategy headlines into the freeze-locked deliverable).
        if data_hash:
            strategies = await get_strategy_cache(data_hash)
            if strategies is None:
                log.warning(
                    "gather_document_data_strategy_cache_miss",
                    data_hash=data_hash[:8],
                    hint=(
                        "Run /api/v1/light-refresh to recompute "
                        "strategy_results_cache for the active hash, "
                        "then retry document export."))
                raise StrategyCacheMissingForHashError(data_hash)
        else:
            # Legacy / live-platform path -- the latest cached row
            # regardless of hash. Acceptable when no hash discipline
            # is required (e.g. interactive dashboard reads).
            strategies = await get_latest_strategy_cache()
        ff = await get_ff_factors()

        if monthly and strategies:
            idx = pd.to_datetime(monthly["dates"])
            equity = pd.Series(monthly["equity"], index=idx)
            ig = pd.Series(monthly["ig"], index=idx)
            hy = pd.Series(monthly["hy"], index=idx)
            rf = pd.Series(monthly["rf"], index=idx)

            benchmark = strategies.get("BENCHMARK", {})
            bench_series = an._pairs_to_series(benchmark.get("monthly_returns") or [])
            asset_series: dict[str, Any] = {"EQUITY": equity, "IG": ig, "HY": hy}
            if not bench_series.empty:
                asset_series["BENCHMARK"] = bench_series

            try:
                from strategy_metadata import STRATEGY_METADATA
            except Exception:  # noqa: BLE001
                STRATEGY_METADATA = {}

            rf_list = monthly.get("rf") or []
            # ff_factors_end — the last Carhart-factor month on record, so
            # Section 1's study-period description reflects the actual
            # database state rather than a hardcoded value.
            ff_end = None
            if ff:
                raw = str(ff[-1].get("yyyymm", "")).strip()
                ff_end = (f"{raw[:4]}-{raw[4:6]}" if len(raw) == 6 else raw)

            regime_conditional_rows = an.regime_conditional_performance(
                strategies, rf)
            # June 22 2026 (PR A scope) -- merge per-strategy
            # pre_2022 / post_2022 Sharpe figures back into the
            # strategy dict so the substitution table reads find
            # them. The {{REGIME_SWITCHING_POST2022_SHARPE}} and
            # {{BENCHMARK_POST2022_SHARPE}} tokens previously
            # resolved to em-dash because these fields live on
            # regime_conditional rows, not on the strategy
            # entries themselves.
            for row in regime_conditional_rows:
                strategy_name = row.get("strategy")
                if (not strategy_name
                        or strategy_name not in strategies):
                    continue
                if isinstance(strategies[strategy_name], dict):
                    if "post_2022_sharpe" in row:
                        strategies[strategy_name][
                            "post_2022_sharpe"] = (
                            row["post_2022_sharpe"])
                    if "pre_2022_sharpe" in row:
                        strategies[strategy_name][
                            "pre_2022_sharpe"] = (
                            row["pre_2022_sharpe"])

            # June 22 2026 (PR A scope) -- validated_constants block.
            # Threaded through every document generator so the story
            # plan resolver and the substitution table all see the
            # same locked figures. Reads from academic_deck.py so
            # Path A constant updates propagate to every consumer
            # without a parallel edit. Before this block existed,
            # the brief story plan saw an empty constants dict and
            # the per-section Sonnet writer emitted "--" placeholders
            # where the locked numbers should have appeared.
            from tools.academic_deck import (
                CORRELATION_POST_2022, CORRELATION_PRE_2022,
                CURRENT_EQUITY_WEIGHT, CURRENT_REGIME,
                MAX_DRAWDOWN_BENCHMARK,
                MAX_DRAWDOWN_REGIME_CONDITIONAL,
                OOS_SHARPE_EQUAL_WEIGHT,
                OOS_WINDOW_MONTHS, OOS_WINDOW_PCT_OF_STUDY,
                PLAY_BY_PLAY_ADD_VALUE, PLAY_BY_PLAY_EVENTS,
            )
            # Fix A (June 29 2026, rounding-consistency PR) --
            # OOS Sharpe pair + classic_60_40 sourced from the
            # frozen academic_lock cache row. Cold-cache
            # fallback inside get_academic_lock() returns the
            # academic_deck.py constants so document generation
            # never breaks before the first refresh fires.
            from tools.play_by_play import get_academic_lock
            _lock = await get_academic_lock()
            validated_constants = {
                "oos_sharpe_regime_conditional":
                    _lock["oos_sharpe_blend"],
                "oos_sharpe_benchmark":
                    _lock["oos_sharpe_benchmark"],
                "oos_sharpe_classic_6040":
                    _lock["oos_sharpe_classic_6040"],
                "oos_sharpe_equal_weight":   OOS_SHARPE_EQUAL_WEIGHT,
                "correlation_pre_2022":      CORRELATION_PRE_2022,
                "correlation_post_2022":     CORRELATION_POST_2022,
                "max_drawdown_benchmark":    MAX_DRAWDOWN_BENCHMARK,
                "max_drawdown_regime_conditional":
                    MAX_DRAWDOWN_REGIME_CONDITIONAL,
                "play_by_play_events":       PLAY_BY_PLAY_EVENTS,
                "play_by_play_add_value":    PLAY_BY_PLAY_ADD_VALUE,
                "oos_window_months":         OOS_WINDOW_MONTHS,
                "oos_window_pct_of_study":   OOS_WINDOW_PCT_OF_STUDY,
                "current_regime":            CURRENT_REGIME,
                "current_equity_weight":     CURRENT_EQUITY_WEIGHT,
            }

            bundle.update({
                "available": True,
                "study_period": {
                    "start": str(idx[0].date()),
                    "end": str(idx[-1].date()),
                    "n_months": len(idx),
                    "ff_factors_end": ff_end,
                },
                "summary_statistics": an.summary_statistics(asset_series, rf),
                "regime_conditional": regime_conditional_rows,
                "drawdown_comparison": an.drawdown_comparison(strategies),
                "factor_loadings": an.factor_loadings(strategies, ff or []),
                "cumulative_returns": an.cumulative_returns(strategies),
                "rolling_correlation": an.rolling_correlation(equity, ig, hy, window=12),
                "strategy_results": strategies,
                "strategy_metadata": STRATEGY_METADATA,
                "risk_free_rate": (
                    round(sum(rf_list) / len(rf_list) * 12, 4) if rf_list else None
                ),
                "validated_constants": validated_constants,
            })
    except StrategyCacheMissingForHashError:
        # June 27 2026 (PR 1 v3, LEAK 1 closer) -- DO NOT swallow.
        # The hash-aware strategy load raised because the freeze
        # hash has no cached strategy_results_cache row. Re-raise
        # so the 3 document generators see the spec error + the
        # job worker writes 'Run light refresh and try again' to
        # the user-facing job error field.
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_analytics_failed", error=str(exc))

    # ── Team Activity — per-member counts behind the Roles section ─────────
    try:
        from tools.activity_log import get_activity_summary
        bundle["team_summary"] = await get_activity_summary(analytical_only=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_team_summary_failed", error=str(exc))

    # ── Last Academic Review verdict — seeds the Next Steps section ────────
    try:
        bundle["last_review_text"] = await _last_academic_review_verdict()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_review_read_failed", error=str(exc))

    # ── Audit disclosures — populates the Workstream D appendix and the
    #    executive brief's audit summary sentence + body paragraph. Reads
    #    the latest statistical audit + methodology QA + intentional
    #    overrides; fail-open inside the helper so a bad read leaves
    #    audit_disclosures=None and the builders surface a "no audit on
    #    record" disclosure block. ─────────────────────────────────────────
    try:
        from tools.audit_summary import gather_audit_disclosures
        bundle["audit_disclosures"] = await gather_audit_disclosures()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_audit_disclosures_failed",
                    error=str(exc))

    # ── oos_summary cache (June 29 2026, Issue 9 chart fix) ──
    # The Figure 4 / Slide 4 OOS Sharpe bar chart sources the
    # regime-conditional BLEND value (and the benchmark reference
    # line) from this cache, NOT from data["regime_conditional"]
    # (which only has per-strategy Sharpes -- the BLEND is the OOS
    # validation output written by tools/play_by_play.refresh_
    # performance_chart). Shape:
    #   {"blend": float, "benchmark": float,
    #    "equal_weight": float | None,
    #    "value_add_events": int | None,
    #    "total_events": int | None}
    # Fail-open: a cold cache leaves bundle["oos_summary"] = None
    # and the chart renderer falls back to the per-strategy table
    # for the benchmark line + omits the blend bar.
    try:
        from tools.play_by_play import get_cached_oos_summary
        bundle["oos_summary"] = await get_cached_oos_summary()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "academic_export_oos_summary_read_failed",
            error=str(exc))
        bundle["oos_summary"] = None

    # ── Uploaded requirements / rubric documents ───────────────────────────
    try:
        from tools.academic_context import _read_all_with_content
        bundle["academic_docs"] = await _read_all_with_content()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_docs_read_failed", error=str(exc))

    # ── Live recommendation block (June 6 2026 — brief rewrite) ────────────
    # Section 5 of the executive brief states the current regime + the
    # blend expressed as asset-class allocations (equity vs bonds). The
    # source is the same compute_context() the CIO recommendation panel
    # uses on the dashboard, so the brief and the dashboard always agree
    # on the live state. Aggregation to asset-class shares happens here
    # so the section prompt receives ready-to-quote {equity_pct,
    # bond_pct} numbers and doesn't try to compute them from per-strategy
    # weights itself. Fail-open per the rest of the bundle pattern.
    bundle["live_recommendation"] = await _gather_live_recommendation(
        bundle.get("strategy_results") or {})

    # June 29 2026 -- SUBMISSION SCOPE filter. Applied LAST so
    # the live_recommendation aggregate (which crosses the full
    # blend weights to portfolio-level shares) computes against
    # the unfiltered set first. The filtered bundle then flows
    # to every document generator + table builder + narrative
    # prompt.
    bundle = _filter_to_submission_scope(bundle)

    return bundle


async def _gather_live_recommendation(
    strategy_results: dict[str, Any],
) -> dict[str, Any]:
    """Fetches the current regime + live blend weights and aggregates
    the blend to portfolio-level asset-class shares (equity vs bonds).

    The aggregator uses each strategy's `avg_equity_weight` and
    `avg_bond_weight` from strategy_results_cache; the brief section
    states the result as "Equity X% / Bonds Y%" rather than splitting
    bonds further (the IG/HY split isn't persisted to the cache today,
    and is downstream of strategy choice rather than a separate
    allocation axis — Forest Capital fills the bond envelope with its
    own security selection).

    Returns:
      {
        regime:         "BULL" | "BEAR" | "TRANSITION" | None,
        confidence:     float 0..1 or None,
        blend_weights:  {strategy: float} or {},
        equity_pct:     float 0..1 or None,
        bond_pct:       float 0..1 or None,
        ess:            float or None,
      }

    On any failure (cold cache, no monthly data, HMM fit error) the
    helper returns the dict with every value set to None / empty so
    the brief Section 5 renders a [DATA PENDING] block rather than
    crashing the generation."""
    empty = {
        "regime":        None,
        "confidence":    None,
        "blend_weights": {},
        "equity_pct":    None,
        "bond_pct":      None,
        "ess":           None,
        # June 18 2026 -- staleness flag + as-of timestamp. The brief
        # Final Recommendations section reads these so the prose can
        # disclose when the recommendation is built from a cached
        # regime read rather than the live HMM fit.
        "is_stale":      False,
        "stale_as_of":   None,
    }
    try:
        # Reuse the platform's canonical live-context builder so the
        # brief and the dashboard agree on the live state. Returns
        # {"context": {...}, "macro": ...} on success.
        from tools.cio_recommendation import _build_live_context
        live = await _build_live_context()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_live_recommendation_failed",
                    error=str(exc))
        live = None

    ctx = (live or {}).get("context") if isinstance(live, dict) else None
    if ctx is None or (live and live.get("error")):
        ctx = {}
    regime = ctx.get("regime")
    confidence = ctx.get("probability")
    blend_weights = ctx.get("blend_weights") or {}
    ess = ctx.get("ess")

    equity_pct, bond_pct = aggregate_blend_to_asset_classes(
        blend_weights, strategy_results)

    # June 18 2026 -- cached-regime fallback. The brief's Final
    # Recommendations section previously rendered "[DATA PENDING]" when
    # the live build was degraded (cold cache, transient HMM fit error,
    # CIO call that fell to deterministic_fallback). The fallback below
    # reads the most recent NON-FALLBACK CIO recommendation from the
    # persistence layer and lifts its regime + confidence + blend so
    # the section can ALWAYS state a recommendation; the prose
    # discloses the staleness explicitly via is_stale + stale_as_of.
    if not regime or equity_pct is None:
        try:
            from tools.cio_recommendation import (
                get_latest_non_fallback_recommendation,
            )
            cached = await get_latest_non_fallback_recommendation()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "academic_export_cached_regime_lookup_failed",
                error=str(exc))
            cached = None
        if cached and cached.get("regime"):
            log.info("academic_export_cached_regime_fallback_used",
                     stale_as_of=cached.get("computed_at"),
                     model=cached.get("model"))
            # Lift the regime + a reasonable confidence proxy from the
            # cached row's stored confidence dict. The blend weights
            # live in raw_json["confidence"] only loosely (the raw_json
            # is the four-component object, not a strategies->weight
            # map), so we use any blend_weights surfaced in the row's
            # raw_json directly if present.
            cached_regime = cached.get("regime")
            cached_conf_block = cached.get("confidence") or {}
            cached_confidence = cached_conf_block.get("probability")
            cached_ess = cached_conf_block.get("ess")
            cached_blend = cached.get("blend_weights") or {}
            cached_equity, cached_bond = (
                aggregate_blend_to_asset_classes(
                    cached_blend, strategy_results))
            return {
                "regime":        cached_regime,
                "confidence":    cached_confidence,
                "blend_weights": cached_blend,
                "equity_pct":    cached_equity,
                "bond_pct":      cached_bond,
                "ess":           cached_ess,
                "is_stale":      True,
                "stale_as_of":   cached.get("computed_at"),
            }
        # No cached non-fallback row either -- return the empty
        # contract so the section still renders [DATA PENDING].
        return empty

    return {
        "regime":        regime,
        "confidence":    confidence,
        "blend_weights": blend_weights,
        "equity_pct":    equity_pct,
        "bond_pct":      bond_pct,
        "ess":           ess,
        "is_stale":      False,
        "stale_as_of":   None,
    }


def aggregate_blend_to_asset_classes(
    blend_weights: dict[str, Any],
    strategy_results: dict[str, Any],
) -> tuple[float | None, float | None]:
    """Aggregate the per-strategy blend weights into portfolio-level
    asset-class shares (equity vs bonds).

    The math:
      equity_pct = sum_s ( blend_w_s * avg_equity_weight_s )
      bond_pct   = sum_s ( blend_w_s * avg_bond_weight_s )

    With a fully-invested blend (sum of blend weights ≈ 1) and fully-
    invested strategies (eq_s + bond_s ≈ 1 per strategy), the two
    aggregates sum to ~1 (the small residual is rounding noise across
    avg_equity_weight + avg_bond_weight not strictly summing to 1 in
    every strategy result row).

    Returns (None, None) when blend_weights is empty or no strategy
    contributes a positive (eq + bond) share — the caller renders the
    [DATA PENDING] section.

    Shared with the daily digest's _section_implied_asset_allocation
    so the brief and the digest always agree on the per-strategy →
    portfolio aggregation. June 6 2026."""
    if not blend_weights or not strategy_results:
        return None, None
    equity_acc = 0.0
    bond_acc = 0.0
    saw_any = False
    for strategy, weight in blend_weights.items():
        try:
            w = float(weight or 0)
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        s = strategy_results.get(strategy) or {}
        try:
            eq = float(s.get("avg_equity_weight") or 0)
            bd = float(s.get("avg_bond_weight") or 0)
        except (TypeError, ValueError):
            continue
        if eq + bd <= 0:
            continue
        equity_acc += w * eq
        bond_acc += w * bd
        saw_any = True
    if not saw_any:
        return None, None
    return equity_acc, bond_acc


_ROLES_BY_EMAIL = {
    "ruurdsm@queens.edu": ("michael_ruurds",
                           "Platform Engineer and System Administrator"),
    "thaob@queens.edu":   ("bob_thao",
                           "Written Deliverables and Analysis"),
    "murdockm@queens.edu": ("molly_murdock",
                            "Presentation and User Acceptance Testing"),
}


async def gather_roles_activity(team_summary: dict[str, Any]) -> dict[str, Any]:
    """
    Builds the per-member team_activity_summary that pre-seeds the
    midpoint paper's Roles and Division of Labor section.

    team_summary is the get_activity_summary() bundle already gathered by
    gather_document_data — its per_member counts and commits.by_author are
    reused here, with two extra light reads (UAT sections attested, the
    completed-audit count). The result is keyed by a stable member slug so
    the Academic Writer can attribute documented activity to each person.

    Fail-open: a missing table or query error simply drops that count to 0
    — the section still pre-seeds from whatever activity is on record.
    """
    per_member = {m.get("user"): m for m in
                  (team_summary or {}).get("per_member", [])}
    by_author = (team_summary or {}).get("commits", {}).get("by_author", {})

    # UAT sections attested — distinct script_id per tester.
    uat: dict[str, int] = {}
    audit_runs = 0
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is not None:
            async with AsyncSessionLocal() as session:
                rows = await session.execute(text(
                    "SELECT user_email, COUNT(DISTINCT script_id) "
                    "FROM test_results GROUP BY user_email"))
                uat = {e: int(n) for e, n in rows.fetchall()}
                arow = await session.execute(text(
                    "SELECT COUNT(*) FROM audit_runs "
                    "WHERE status = 'complete'"))
                found = arow.fetchone()
                audit_runs = int(found[0]) if found else 0
    except Exception as exc:  # noqa: BLE001 — fail-open, counts drop to 0
        log.warning("roles_activity_extra_reads_failed", error=str(exc))

    summary: dict[str, Any] = {}
    for email, (slug, role) in _ROLES_BY_EMAIL.items():
        m = per_member.get(email, {})
        entry: dict[str, Any] = {
            "role": role,
            "commits": int(by_author.get(email, 0)),
            "council_sessions_run": int(m.get("council_interactions", 0)),
            "academic_review_sessions": int(
                m.get("academic_review_sessions", 0)),
            "documents_uploaded": int(m.get("document_uploads", 0)),
            "qa_audits": int(m.get("qa_audits", 0)),
            "page_views": int(m.get("page_views", 0)),
            "uat_sections_attested": int(uat.get(email, 0)),
        }
        # The completed-audit count is attributed to Michael — only the
        # sysadmin runs the statistical audit; audit_runs carries no
        # per-user attribution of its own.
        if slug == "michael_ruurds":
            entry["audit_runs"] = audit_runs
            entry["platform_built"] = True
        summary[slug] = entry
    return summary


async def _last_academic_review_verdict() -> str | None:
    """
    The full text of the most recent Academic Review arbiter verdict, or
    None when no review has been run. Stored in agent_interactions by the
    /api/council/academic-review endpoint as response_summary.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT response_summary FROM agent_interactions "
                "WHERE interaction_type = 'academic_review' "
                "ORDER BY timestamp DESC LIMIT 1"
            ))
            found = row.fetchone()
            return found[0] if found and found[0] else None
    except Exception as exc:  # noqa: BLE001
        log.warning("last_academic_review_query_failed", error=str(exc))
        return None


def academic_doc_present(academic_docs: list[dict], document_type: str) -> bool:
    """True when a document of the given type has been uploaded in Settings."""
    return any(d.get("document_type") == document_type for d in academic_docs)


# Issue 2 (June 21 2026, Option 2) -- post-pass story-plan violation
# retry threshold. When a brief section's prose emits more than this
# many numbers outside its locked anchors set (and not in the
# strategy cache and not year-like in citation parens), the
# harness_narrative path re-calls the generator ONCE with explicit
# feedback. Set generously: a section with 1-2 stray numbers may
# still be fine (the post-generation audit flags individually). A
# section with 3+ stray numbers is a writer-drift signal.
_STORY_PLAN_VIOLATION_RETRY_THRESHOLD = 3

# June 28 2026 -- hard-lock numeric guardrail. After the initial
# generation pass + the story-plan-violation retry, the brief /
# appendix paths loop the generator up to this many times to
# eliminate untoken-backed numerics from the prose. Each pass
# uses build_correction_prompt to feed the LLM the offending
# sentences + suggested {{TOKEN}} swaps (when the value matches
# a substitution-table output). On the Nth pass with violations
# still present, raises UntokenNumericLockError so the operator
# sees the offending list rather than getting a silent
# hallucination through to the editor.
_UNTOKEN_LOCK_MAX_PASSES = 3


# Matches the same numeric pattern the document_audit's story-plan
# check uses. Decimal with optional %, sign, comma thousands. Kept
# local so this module doesn't take a runtime dependency on the
# audit module's regex.
_STORY_PLAN_NUMBER_RE = re.compile(
    r"(?<![A-Za-z_])([-+]?\d{1,3}(?:,\d{3})*"
    r"(?:\.\d+)?%?|\d+\.\d+%?)")
_STORY_PLAN_TOLERANCE = 0.01


def _count_unauthorized_numbers(
    prose: str,
    anchor_values: list[float],
    substitution_table: dict[str, str] | None = None,
) -> list[str]:
    """Returns the list of token strings in prose that are NOT in
    the anchor_values set (within _STORY_PLAN_TOLERANCE) and NOT
    year-like in citation parens. Used by harness_narrative's
    post-pass check; mirrors the logic in
    document_audit.check_brief_story_plan_violations but on a
    per-section scope.

    Empty list = no unauthorized numbers -> no retry needed.

    June 28 2026 (Fix 9) -- the structural-exemption registry
    from tools.untoken_numeric_check is consulted before
    flagging a numeric as a violation. Without this, the story-
    plan check produces noise-violation counts of 20-30+ per
    section because the LLM correctly emits ordinals (Section
    2 / Figure 3), citation years ((Smith, 2020)), document-
    format references (5 pages / 16 slides), methodology
    constants (10,000 resamples / 12-month block), index names
    (S&P 500), and definitional weights (60% equity / 40%
    bonds) -- ALL of which are structurally exempt from the
    hard-lock scanner but the story-plan check was treating
    as bare numbers. The result: failing retries that produce
    MORE violations than the original, scores tanking below
    6.0 on substantively-correct content.

    Substitution-table-priority constraint preserved: a value
    that IS in the substitution table flags as a violation
    (no exemption) so the writer gets feedback to swap the
    raw value for the matching {{TOKEN}}."""
    if not prose or not anchor_values:
        return []
    from tools.untoken_numeric_check import (
        _matches_structural_pattern, _build_token_index,
    )
    value_to_token = _build_token_index(substitution_table or {})
    found: list[str] = []
    seen: set[float] = set()
    for m in _STORY_PLAN_NUMBER_RE.finditer(prose):
        tok = m.group(1)
        try:
            val = float(tok.replace(",", "").replace("%", ""))
        except ValueError:
            continue
        # Skip citation-year numbers in parens ("(1989)").
        prev_char = (prose[m.start() - 1] if m.start() > 0 else "")
        is_year_like = (
            "." not in tok and "%" not in tok
            and 1900 <= val <= 2100)
        if prev_char == "(" and is_year_like:
            continue
        if val in seen:
            continue
        if any(abs(val - a) <= _STORY_PLAN_TOLERANCE
               for a in anchor_values):
            continue
        # June 28 2026 (Fix 9) -- structural-exemption check.
        # Substitution-table values are NEVER exempted (the LLM
        # gets the swap suggestion in feedback); other
        # structural-prose numerics (ordinals, citation years,
        # S&P index name, definitional weights, methodology
        # counts, document-format references, etc.) are exempt.
        in_table = tok in value_to_token or (
            tok.rstrip("%") in value_to_token) or (
            (tok + "%") in value_to_token)
        if not in_table:
            structural_name = _matches_structural_pattern(
                tok, prose, m.span(1))
            if structural_name is not None:
                continue
        seen.add(val)
        found.append(tok)
    return found


def harness_narrative(
    agent_id: str,
    task: str,
    context: Any,
    *,
    # May 26 2026 — bumped from 900 to 1500. User reported Section 3
    # of the midpoint paper terminating mid-sentence: the 110-135 word
    # target is well under 900 tokens for the prose alone, but each
    # section also emits [[VERIFY]] markers, inline citations and the
    # Academic Writer's hedging language — which together pushed the
    # output past the 900-token cap mid-sentence. 1500 gives ~2.5x
    # headroom for a typical 300-word section + its markers and
    # citations, with negligible cost overhead (Sonnet is per-token).
    max_tokens: int = 1500,
    n_strategies: int | None = None,
    # June 21 2026 -- numeric substitution architecture. When the
    # caller passes a {token -> value} table, every call_claude
    # response is run through apply_substitutions BEFORE the harness
    # evaluator scores it. The evaluator sees real numbers (the
    # values the human reader will read), not raw tokens. None
    # preserves the pre-substitution behaviour for the midpoint /
    # appendix / deck callers that haven't been wired through yet
    # (those wire in the Layer-2 PR).
    substitution_table: dict[str, str] | None = None,
    # June 21 2026 -- Issue 2 (Option 2): post-pass story-plan
    # violation check. When the caller supplies the section's
    # locked numeric_anchors, after harness.run() returns the
    # function scans the final prose for unauthorized numbers
    # (numbers not in anchors, not in cache, not year-like in
    # citation parens). If the count exceeds
    # _STORY_PLAN_VIOLATION_RETRY_THRESHOLD, the function does
    # ONE additional generator call with explicit "unauthorized
    # numbers: X, Y, Z" feedback. Accepts the second-call
    # output if cleaner. Fail-open: any error in the check
    # leaves the original prose unchanged.
    numeric_anchors: dict[str, Any] | None = None,
    # June 28 2026 -- hard-lock numeric guardrail. When the
    # caller supplies the document_type AND it is one of the
    # protected types (executive_brief / analytical_appendix),
    # the harness loop post-scans the final approved prose for
    # numerics not backed by a {{TOKEN}} from the substitution
    # table AND not in the numeric_anchors AND not allowlisted
    # (year, citation, etc). When violations are found, the
    # function loops the generator with explicit correction
    # feedback up to _UNTOKEN_LOCK_MAX_PASSES times before
    # raising UntokenNumericLockError. None / non-protected
    # types preserve the legacy single-pass behaviour.
    document_type: str | None = None,
    # June 28 2026 -- async-resolved deferral flag passed in
    # from the async caller (e.g. _generate_narratives awaits
    # is_defer_substitution_enabled() ONCE before launching
    # asyncio.to_thread jobs and threads the bool through here).
    # Eliminates the failed asyncio.run-from-worker-thread path
    # which raised "Future attached to a different loop" because
    # SQLAlchemy's async session is bound to the main event
    # loop; opening a fresh loop in a worker thread cannot reuse
    # connections from the main loop's pool. With the bool
    # pre-resolved, the worker thread never touches the loop
    # boundary. Default False = legacy behaviour (no deferral).
    defer_substitution: bool = False,
) -> str:
    """
    Generates one section of academic prose through the Academic Writer
    agent, wrapped in the generator-evaluator harness.

    The harness scores the draft against the academic_review peer-evaluator
    criteria and retries below threshold — the spec requires every
    academic_writer call to run through it. Synchronous (the harness and
    call_claude are both synchronous); callers run it in asyncio.to_thread.

    n_strategies — the count of strategies in the cache. Threaded into the
    chart-vision scope sentences so the all-strategy chart captions render
    "Showing all N strategies" rather than the count-omitting fallback.
    Caller is _generate_narratives in main.py, which has the count from
    gather_document_data()["strategy_results"].

    Fail-open: in the test environment, or on any generation error, a
    [DATA PENDING] marker is returned so the surrounding document still
    assembles.
    """
    if ENVIRONMENT == "test":
        return (
            f"{DATA_PENDING} — section narrative is generated at runtime "
            "and is skipped in the test environment."
        )

    ctx_str = context if isinstance(context, str) else json.dumps(
        context, indent=2, default=str)
    user_message = f"{task}\n\nDATA (cite only these figures):\n{ctx_str}"

    # Item 9 commit 5 — strategy context. The midpoint paper / brief /
    # deck sections reference specific strategies (Section 2 leads with
    # ranked_findings[0], every paragraph carries a verified number).
    # Detect every strategy id named in the task + context dump and
    # set the per-request ContextVar so call_claude inside the harness
    # picks up each strategy's characterisation block. No-op when no
    # strategy is named — the harness retry path reuses the same
    # ContextVar value the first attempt set.
    try:
        from tools.strategy_context import (
            detect_strategies_in_query, set_active_strategies,
        )
        named = detect_strategies_in_query(f"{task} {ctx_str}")
        if named:
            set_active_strategies(named)
    except Exception:  # noqa: BLE001
        pass

    try:
        from agents.academic_writer import _SYSTEM_PROMPT
        from agents.base import SONNET_MODEL, call_claude
        from agents.evaluator_prompts import (
            academic_export_evaluator_pm_prompt,
            academic_review_peer_evaluator_prompt,
            brief_executive_summary_evaluator_prompt,
            brief_section_evaluator_prompt,
        )
        from agents.harness import GeneratorEvaluatorHarness
        from tools.chart_vision import (
            DOCUMENT_GENERATION_CHARTS, get_charts_for_context,
            snapshots_dir_exists,
        )

        # The brief sections each have a dedicated evaluator that
        # scores against the criteria THAT section was written to
        # satisfy. Earlier (pre-PR-347) every brief section used
        # academic_review_peer_evaluator_prompt('academic writer'),
        # whose criteria (rubric_mapped, data_specific,
        # requirements_aligned, role_authentic,
        # actionable_next_steps) score a PEER REVIEW VERDICT --
        # responses about whether someone else's academic work has
        # gaps -- not a brief section.
        #
        # PR #347 fixed executive_summary. June 21 2026 finishes
        # the follow-up: methodology, key_findings, limitations,
        # final_recommendations, visuals each get their own
        # section-specific evaluator via
        # brief_section_evaluator_prompt(section_key). The
        # agent_id -> section_key mapping below is the dispatch
        # table; an agent_id without a brief_ prefix (the
        # midpoint / deck / appendix paths) falls through to the
        # peer-review evaluator as before.
        _BRIEF_AGENT_TO_SECTION_KEY = {
            "brief_executive_summary":    "executive_summary",
            "brief_methodology":          "methodology",
            "brief_key_findings":         "key_findings",
            "brief_limitations":          "limitations",
            "brief_final_recommendations": "final_recommendations",
            "brief_visuals":              "visuals",
        }
        section_key = _BRIEF_AGENT_TO_SECTION_KEY.get(agent_id)
        if section_key == "executive_summary":
            primary_evaluator = brief_executive_summary_evaluator_prompt()
        elif section_key is not None:
            primary_evaluator = brief_section_evaluator_prompt(section_key)
        else:
            primary_evaluator = academic_review_peer_evaluator_prompt(
                "academic writer")

        # DOCUMENT_GENERATION_CHARTS snapshots — the academic writer
        # reasons about regime + factor + drawdown visuals when drafting
        # the analytical section. Built once and captured in the
        # generator-fn closure so a harness retry reuses them. Evaluators
        # MUST NOT see this — harness._evaluate omits the kwarg.
        visual_context: list[dict] | None = None
        if snapshots_dir_exists():
            blocks = get_charts_for_context(
                DOCUMENT_GENERATION_CHARTS, n_strategies=n_strategies)
            visual_context = blocks if blocks else None
            if not blocks:
                log.info("academic_writer_no_snapshots_available",
                         agent_id=agent_id,
                         note="proceeding without visual context")
        else:
            log.info("academic_writer_no_snapshots_dir",
                     agent_id=agent_id,
                     note="proceeding without visual context")

        harness = GeneratorEvaluatorHarness()

        # Substitution wrapper around the generator. When a
        # substitution_table is supplied, every Sonnet response is
        # post-processed through apply_substitutions before being
        # returned to the harness. That means the evaluator (and the
        # downstream caller / .docx assembler) only ever sees
        # substituted text -- structurally impossible to evaluate or
        # render the raw {{TOKEN}} placeholders.
        #
        # June 21 2026 -- self-healing truncation retry. After each
        # Sonnet call, the response is checked with
        # tools.document_audit.is_content_truncated. If truncated
        # (open {{TOKEN, mid-URL, mid-word, no terminator in last
        # 200 chars), re-call with max_tokens + 1000. Repeat once
        # more at max_tokens + 2000. Fail-open after two retries --
        # a truncated section is better than a blocked generation;
        # the downstream check_section_truncation audit surfaces
        # the residual flag to Bob in the editor banner.
        from tools.document_audit import is_content_truncated

        # June 21 2026 -- WEB_SEARCH_TOOL removed from the section
        # writer. The writer used to call out to Anthropic's
        # server-side web_search (max_uses=3) per section, which:
        #   1. Bloated the model's input context with the scraped
        #      page bodies (server-side, but the model still saw
        #      them and reasoned over them), eating into the output
        #      budget before prose even started.
        #   2. Drove the writer to spend output tokens formatting
        #      URLs and DOIs inline, which then pushed the
        #      References block past the per-section ceiling --
        #      the production symptom that fired
        #      section_content_truncated_unrecoverable on Section
        #      3 (key_findings) + Section 6 (visuals).
        # The registry at data/references.json already carries
        # every citation the writer's system prompt historically
        # web-searched for. With web search gone, the writer cites
        # from the registry only -- the system prompt's CITATIONS
        # block was updated in parallel so this is a coordinated
        # change, not a contradiction with the prompt's
        # instructions.
        #
        # If a future section legitimately needs to cite something
        # not in the registry, the right answer is to add it to
        # data/references.json -- not to re-enable web search.
        def _call_sonnet(prompt: str, tok_budget: int) -> str:
            return call_claude(
                SONNET_MODEL, _SYSTEM_PROMPT, prompt,
                max_tokens=tok_budget,
                visual_context=visual_context,
                trigger="document_export_narrative")

        # June 28 2026 -- DEFER_SUBSTITUTION_TO_EXPORT support.
        # Closure-captured dict mapping substituted -> raw for
        # every Sonnet call this section makes. When the flag is
        # ON, the persistence path (post-harness, post-untoken-
        # lock) looks up final_text here to recover the raw
        # form with {{TOKEN}} placeholders intact. When OFF, the
        # stash is populated but unused -- the legacy behaviour
        # (substituted text persisted) is preserved.
        _raw_per_substituted: dict[str, str] = {}

        def _substituting_generator(prompt: str) -> str:
            raw = _call_sonnet(prompt, max_tokens)
            # Self-healing retry loop. Two attempts at +1000 / +2000
            # tokens before giving up.
            if is_content_truncated(raw):
                retry_budget = max_tokens + 1000
                log.warning(
                    "section_content_truncated",
                    agent_id=agent_id,
                    current_max_tokens=max_tokens,
                    retry_max_tokens=retry_budget,
                    last_chars=raw[-100:])
                raw = _call_sonnet(prompt, retry_budget)
                if is_content_truncated(raw):
                    retry_budget_2 = max_tokens + 2000
                    log.warning(
                        "section_content_truncated_retry2",
                        agent_id=agent_id,
                        retry_max_tokens=retry_budget_2,
                        last_chars=raw[-100:])
                    raw = _call_sonnet(prompt, retry_budget_2)
                    if is_content_truncated(raw):
                        log.error(
                            "section_content_truncated_unrecoverable",
                            agent_id=agent_id,
                            last_chars=raw[-100:],
                            message=(
                                "Section still truncated after two "
                                "retries. Accepting truncated output "
                                "rather than blocking generation."))
            if substitution_table is None:
                return raw
            from tools.numeric_substitution import apply_substitutions
            substituted, replaced = apply_substitutions(
                raw, substitution_table)
            log.info("numeric_substitution_applied",
                     agent_id=agent_id,
                     tokens_replaced=replaced,
                     count=len(replaced))
            # June 28 2026 -- stash raw -> substituted so the
            # post-harness layer can recover raw text when the
            # DEFER_SUBSTITUTION_TO_EXPORT flag is on. The
            # harness's evaluator + scoring all see substituted
            # text (current behaviour); only the persistence
            # path swaps back to raw + token-bearing form.
            #
            # CRITICAL: key the stash by the BANNER-STRIPPED
            # form of the substituted text. final_text (the
            # lookup key at the deferral-swap site) is the
            # result of _strip_banner(result.response) which
            # drops leading "AI DRAFT" lines + trims
            # whitespace. Storing by the raw substituted form
            # (un-stripped) means the lookup misses + the
            # swap silently falls through to substituted -- the
            # exact failure mode operator reported on draft 73.
            # Store under BOTH the un-stripped AND the stripped
            # form so existing code paths that look up by the
            # un-stripped substituted (the harness evaluator
            # cycle) also work.
            stripped_sub = _strip_banner(substituted) or substituted
            stripped_raw = _strip_banner(raw) or raw
            _raw_per_substituted[substituted] = raw
            _raw_per_substituted[stripped_sub] = stripped_raw
            return substituted

        # _substituting_generator handles BOTH the no-substitution
        # (returns raw) and the with-substitution path -- always
        # passing it keeps the harness.run call shape stable
        # regardless of caller.
        result = harness.run(
            # Web search is enabled so the section can cite verified
            # external literature for its key findings (see EXTERNAL
            # CITATIONS in the academic writer's system prompt).
            generator_fn=_substituting_generator,
            evaluator_prompt=primary_evaluator,
            # Audience-aware second pass — every document section
            # (midpoint paper, executive brief, deck narrative) is also
            # scored against the PM rubric. The harness retries when
            # EITHER rubric returns NEEDS WORK. The presentation script
            # generator does NOT pass a secondary evaluator (spoken
            # delivery is a different audience); the council and triage
            # generators also do not.
            secondary_evaluator_prompt=academic_export_evaluator_pm_prompt(),
            generator_prompt=user_message,
            context=ctx_str,
            agent_id=agent_id,
        )
        final_text = _strip_banner(result.response) or ""

        # Issue 2 (June 21 2026, Option 2) -- post-pass story-plan
        # violation check. When numeric_anchors are supplied, scan
        # the final prose for unauthorized numbers. If count
        # exceeds the threshold, re-call the generator ONCE with
        # explicit feedback listing the offending tokens. Use
        # whichever output has fewer violations. Fail-open: any
        # error in the check leaves the original prose unchanged.
        try:
            if numeric_anchors and final_text:
                anchor_values: list[float] = []
                for v in numeric_anchors.values():
                    try:
                        anchor_values.append(float(v))
                    except (TypeError, ValueError):
                        continue
                if anchor_values:
                    bad = _count_unauthorized_numbers(
                        final_text, anchor_values,
                        substitution_table=substitution_table)
                    if len(bad) >= _STORY_PLAN_VIOLATION_RETRY_THRESHOLD:
                        log.info(
                            "harness_story_plan_violation_retry",
                            agent_id=agent_id,
                            violation_count=len(bad),
                            offending_tokens=bad[:10])
                        feedback_prompt = (
                            user_message
                            + "\n\nREGENERATION FEEDBACK -- "
                            "STORY PLAN VIOLATIONS:\n"
                            "Your previous draft emitted the "
                            "following numbers that are NOT in "
                            "this section's locked numeric_anchors "
                            "and are NOT in the strategy cache:\n  "
                            + ", ".join(bad[:10])
                            + "\n\nRegenerate the section. Every "
                            "number you emit must EITHER match "
                            "one of the locked anchors above OR "
                            "be a {{TOKEN}} placeholder from the "
                            "substitution table. Remove any "
                            "unauthorized number entirely; do "
                            "not paraphrase it into prose. Years "
                            "in citation parens "
                            "((Hamilton, 1989)) are permitted "
                            "and do not count as violations.")
                        retry_text = _substituting_generator(
                            feedback_prompt)
                        retry_clean = _strip_banner(retry_text) or ""
                        retry_bad = _count_unauthorized_numbers(
                            retry_clean, anchor_values,
                            substitution_table=substitution_table)
                        if (retry_clean
                                and len(retry_bad) < len(bad)):
                            log.info(
                                "harness_story_plan_retry_accepted",
                                agent_id=agent_id,
                                original_violations=len(bad),
                                retry_violations=len(retry_bad))
                            final_text = retry_clean
                        else:
                            log.info(
                                "harness_story_plan_retry_rejected",
                                agent_id=agent_id,
                                original_violations=len(bad),
                                retry_violations=len(retry_bad))
        except Exception as _exc:  # noqa: BLE001
            log.warning(
                "harness_story_plan_check_failed",
                agent_id=agent_id, error=str(_exc))

        # ── June 28 2026 -- hard-lock numeric guardrail ─────────
        # For executive_brief + analytical_appendix only, scan
        # the approved prose for numerics not backed by a token.
        # Loop the generator with explicit correction feedback
        # up to _UNTOKEN_LOCK_MAX_PASSES times. Persist on a
        # clean pass; raise UntokenNumericLockError otherwise.
        #
        # CRITICAL: scan the RAW pre-substitution text, NOT the
        # substituted final_text. Otherwise every legitimate
        # substituted value (e.g. "+0.57" from
        # {{POST_2022_EQ_IG_CORR}}) looks like an untoken-backed
        # numeric and the lock recommends swapping it for the
        # very token that already produced it -- an infinite
        # loop until the 3-pass cap raises. The
        # _raw_per_substituted stash captures (substituted ->
        # raw) for every Sonnet response in this section; look
        # up the raw form to scan against.
        _PROTECTED = {"executive_brief", "analytical_appendix"}
        if (document_type in _PROTECTED
                and substitution_table is not None
                and final_text):
            try:
                from tools.untoken_numeric_check import (
                    UntokenNumericLockError,
                    build_correction_prompt,
                    find_untoken_backed_numerics,
                )
                for _pass in range(1, _UNTOKEN_LOCK_MAX_PASSES + 1):
                    # Resolve the raw (token-bearing) form of
                    # final_text via the stash. Fallback to
                    # final_text when the stash misses (defensive
                    # -- shouldn't happen since every accepted
                    # response was generated via
                    # _substituting_generator which populates
                    # the stash).
                    raw_for_scan = _raw_per_substituted.get(
                        final_text, final_text)
                    viols = find_untoken_backed_numerics(
                        raw_for_scan, substitution_table,
                        numeric_anchors)
                    if not viols:
                        if _pass > 1:
                            log.info(
                                "untoken_lock_cleared",
                                agent_id=agent_id,
                                document_type=document_type,
                                passes_used=_pass - 1)
                        break
                    log.warning(
                        "untoken_lock_correction_pass",
                        agent_id=agent_id,
                        document_type=document_type,
                        pass_n=_pass,
                        violation_count=len(viols),
                        sample_offenders=[
                            v.raw_value for v in viols[:5]])
                    if _pass == _UNTOKEN_LOCK_MAX_PASSES:
                        # June 28 2026 -- SOFT-FAIL + TAG on
                        # hard-lock cap. Operator-directed
                        # change for the June 30 deadline:
                        # [DATA PENDING] is a hard submission
                        # blocker; a flagged numeric is
                        # recoverable via human review.
                        #
                        # The cap-branch wraps each surviving
                        # violation in <unverified>...</unverified>
                        # tags inline in the best-attempt
                        # narrative, then breaks (no raise).
                        # The tagged form persists into
                        # content_json so Bob + Molly see the
                        # exact offenders highlighted during
                        # in-editor review. The downstream
                        # document_audit also flags them in
                        # the AuditWarningsBanner.
                        #
                        # Span-based wrap on the raw text (the
                        # form the deferral swap will persist),
                        # value-based wrap on the substituted
                        # final_text (the form the legacy path
                        # persists). Both share the same set
                        # of raw values; spans align with the
                        # raw text only.
                        from tools.untoken_numeric_check import (
                            wrap_unverified,
                            wrap_unverified_by_value,
                        )
                        wrapped_raw = wrap_unverified(
                            raw_for_scan, viols)
                        if (raw_for_scan in
                                _raw_per_substituted.values()
                                or final_text in
                                _raw_per_substituted):
                            _raw_per_substituted[final_text] = (
                                wrapped_raw)
                        final_text = wrap_unverified_by_value(
                            final_text,
                            {v.raw_value for v in viols})
                        log.warning(
                            "untoken_lock_soft_fail",
                            agent_id=agent_id,
                            document_type=document_type,
                            remaining_violations=len(viols),
                            sample_offenders=[
                                v.raw_value for v in viols[:10]],
                            note=(
                                "hard-lock cap reached; "
                                "persisting best-attempt "
                                "narrative with each surviving "
                                "raw numeric wrapped in "
                                "<unverified> tags for "
                                "in-editor human review. "
                                "audit_warnings will also "
                                "flag for the banner."))
                        break
                    # Re-call the generator with explicit
                    # correction feedback. _substituting_generator
                    # already handles substitution + truncation
                    # retry per call.
                    correction_prompt = build_correction_prompt(
                        user_message, viols, _pass)
                    try:
                        retry_text = _substituting_generator(
                            correction_prompt)
                        retry_text = (
                            _strip_banner(retry_text) or "")
                        if retry_text:
                            final_text = retry_text
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "untoken_lock_retry_call_failed",
                            agent_id=agent_id, error=str(exc))
                        break
            except UntokenNumericLockError:
                # Bubble up so the brief / appendix generator
                # endpoint can surface a structured 500 with
                # the list of remaining offenders.
                raise
            except Exception as _lock_exc:  # noqa: BLE001
                log.warning(
                    "untoken_lock_check_failed",
                    agent_id=agent_id, error=str(_lock_exc))

        # June 28 2026 -- DEFER_SUBSTITUTION_TO_EXPORT swap.
        # When the flag is ON for protected document types, swap
        # final_text (substituted form) for the raw form with
        # {{TOKEN}} placeholders intact. The stash was populated
        # by every _substituting_generator call; look up the raw
        # form of whichever attempt the harness ultimately
        # picked. Falls through to substituted text when no match
        # is found in the stash (defensive -- shouldn't happen
        # but the legacy text is correct either way).
        # June 28 2026 (diagnostic) -- emit a structured log
        # entry at every swap-eligible attempt so the operator
        # can pin which gate fails when content_json
        # unexpectedly carries resolved values instead of
        # {{TOKEN}} placeholders. Audit-only; no behavior
        # change. Remove once draft-74-style deferral failures
        # are pinned + the root cause is fixed.
        try:
            from tools.platform_flags import (
                _SYNC_CACHE as _DEFERRAL_SYNC_CACHE,
                DEFER_SUBSTITUTION_TO_EXPORT_KEY as _DEF_KEY,
            )
            _cache_value = _DEFERRAL_SYNC_CACHE.get(
                _DEF_KEY, "<unset>")
        except Exception:  # noqa: BLE001
            _cache_value = "<import_failed>"
        log.info(
            "deferred_substitution_gate_check",
            agent_id=agent_id,
            document_type=document_type,
            document_type_in_protected=(
                document_type in _PROTECTED),
            substitution_table_present=(
                substitution_table is not None),
            final_text_truthy=bool(final_text),
            stash_size=len(_raw_per_substituted),
            stash_has_final_text_key=(
                final_text in _raw_per_substituted
                if final_text else False),
            sync_cache_value=str(_cache_value),
        )

        if (document_type in _PROTECTED
                and substitution_table is not None
                and final_text):
            try:
                # June 28 2026 -- the deferral flag is now
                # pre-resolved by the async caller and threaded
                # in as `defer_substitution`. Reads from inside
                # this worker thread raised "Future attached to
                # a different loop" because SQLAlchemy's async
                # session is bound to the main loop and a fresh
                # asyncio.run inside asyncio.to_thread cannot
                # reuse those connections. No DB query happens
                # here -- the bool was settled at dispatch time.
                _flag_state = defer_substitution
                log.info(
                    "deferred_substitution_flag_resolved",
                    agent_id=agent_id,
                    document_type=document_type,
                    flag_enabled=_flag_state,
                    source="async_caller_threaded")
                if _flag_state:
                    raw_form = _raw_per_substituted.get(
                        final_text)
                    if raw_form is not None:
                        log.info(
                            "deferred_substitution_persisting_raw",
                            agent_id=agent_id,
                            document_type=document_type,
                            raw_len=len(raw_form),
                            substituted_len=len(final_text))
                        final_text = raw_form
                    else:
                        log.warning(
                            "deferred_substitution_stash_miss",
                            agent_id=agent_id,
                            document_type=document_type,
                            note=(
                                "final_text not in raw stash; "
                                "persisting substituted form"))
            except Exception as _flag_exc:  # noqa: BLE001
                log.warning(
                    "deferred_substitution_check_failed",
                    agent_id=agent_id, error=str(_flag_exc))

        return final_text or (
            f"{DATA_PENDING} — narrative generation returned no content."
        )
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.warning("academic_narrative_failed",
                    agent_id=agent_id, ref=ref, error=str(exc))
        return f"{DATA_PENDING} — narrative generation unavailable (ref: {ref})."


def _strip_banner(text: str) -> str:
    """
    Drops any 'AI DRAFT — REQUIRES HUMAN REVIEW' line wherever it
    appears (leading, trailing, or mid-body). The .docx / .pptx
    builders emit the banner themselves on every page / slide; an
    inline copy is a visible duplicate.

    June 29 2026 (Issue 4) -- previously only the LEADING contiguous
    AI DRAFT line was stripped. The LLM occasionally emitted the
    banner as a paragraph-footer after specific sections (operator
    reported Sections 1 + 5 in latest brief, absent from 3 + 4),
    which then survived into the rendered DOCX. The full-line filter
    below drops every AI DRAFT-bearing line regardless of position
    + collapses runs of blank lines that result so paragraph breaks
    stay clean.
    """
    out = (text or "").strip()
    if not out:
        return ""
    lines = out.split("\n")
    kept: list[str] = []
    for ln in lines:
        if "AI DRAFT" in ln.upper():
            continue
        kept.append(ln)
    # Collapse runs of >= 2 consecutive blank lines that the strip
    # may have created (e.g., banner sandwiched between two
    # blank-line separators).
    collapsed: list[str] = []
    prev_blank = False
    for ln in kept:
        is_blank = not ln.strip()
        if is_blank and prev_blank:
            continue
        collapsed.append(ln)
        prev_blank = is_blank
    return "\n".join(collapsed).strip()


# ── Table adapters ────────────────────────────────────────────────────────────
#
# Convert the analytics-layer dicts into a (headers, rows-of-strings) pair.
# Both the .docx builders and the .pptx deck embed the same four tables, so
# the formatting lives here once. Every cell is a display string — the
# builders only lay them out.


def _pct(v: Any) -> str:
    """Decimal fraction → percentage string, or an em dash when absent.
    Kept as a thin wrapper to format_metric so existing callsites do
    not have to migrate in the same commit."""
    return f"{v * 100:.2f}%" if isinstance(v, (int, float)) else "—"


def _num(v: Any, places: int = 3) -> str:
    """Number → fixed-decimal string, or an em dash when absent.
    Kept as a thin wrapper for callsites that have not yet migrated
    to format_metric. New code should use format_metric(value, kind)
    so precision is governed by the metric's semantics rather than
    a per-callsite literal."""
    return f"{v:.{places}f}" if isinstance(v, (int, float)) else "—"


# May 28 2026 — centralised metric formatter. The slide generator,
# midpoint generator, executive brief generator, and every agent
# prompt that injects a numeric metric into the LLM input ALL route
# through this function so a metric's precision is a property of its
# TYPE, not of the call site that happens to print it. The user's
# directive: an agent never receives a raw float for a metric that
# will appear in a report — it receives a pre-formatted string from
# format_metric, so the model cannot accidentally round differently.
#
# Precision rules:
#   sharpe_ratio / sortino_ratio / calmar_ratio       4dp on the ratio
#   information_ratio / p_value                       4dp on the ratio
#   cagr / volatility / max_drawdown                  4dp on the percent
#   weight / turnover                                 2dp on the percent
#   currency                                          2dp + thousands grouping
#   (fallback)                                        4dp
#
# Returns a STRING, never a float. None / non-numeric returns "—" so
# every callsite renders well-formed even when the upstream metric is
# missing.
_FOUR_DP_RATIOS: frozenset[str] = frozenset({
    "sharpe_ratio", "sortino_ratio", "calmar_ratio",
    "information_ratio", "p_value",
})
_FOUR_DP_PERCENTS: frozenset[str] = frozenset({
    "cagr", "volatility", "max_drawdown",
})
_TWO_DP_PERCENTS: frozenset[str] = frozenset({
    "weight", "turnover",
    # June 28 2026 -- Excess return vs benchmark (annualised CAGR
    # alpha relative to BENCHMARK, stored as fraction in
    # strategy_results_cache.results_json[STRATEGY].excess_return
    # from tools/backtester.py:537). Rendered at 2dp + '%' suffix
    # so Table B.1 reads e.g. '-1.10%' / '0.00%'. Negative sign
    # appears naturally; positives have no '+' prefix (matches
    # the operator spec values).
    "excess_return",
})


def format_metric(value: Any, metric_type: str) -> str:
    """Centralised metric formatter. See _FOUR_DP_RATIOS /
    _FOUR_DP_PERCENTS / _TWO_DP_PERCENTS / 'currency' for the
    precision per metric type. Unknown metric_type falls back to 4dp
    so a new metric never silently inherits 2dp formatting."""
    if value is None or not isinstance(value, (int, float)):
        return "—"
    if metric_type in _FOUR_DP_RATIOS:
        return f"{value:.4f}"
    if metric_type in _FOUR_DP_PERCENTS:
        return f"{value * 100:.4f}%"
    if metric_type in _TWO_DP_PERCENTS:
        return f"{value * 100:.2f}%"
    if metric_type == "currency":
        return f"${value:,.2f}"
    # Default — 4dp on the raw value. A new metric falls here until
    # someone registers it explicitly above.
    return f"{value:.4f}"


def table_summary_statistics(stats: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Asset-level summary statistics — the headline figures table.
    Every numeric column routes through format_metric so precision
    is governed by the metric type, not the call site."""
    headers = ["Asset", "CAGR", "Volatility", "Sharpe", "Max DD", "Skew"]
    rows = [
        [
            str(r.get("asset", "—")),
            format_metric(r.get("cagr"), "cagr"),
            format_metric(r.get("ann_volatility"), "volatility"),
            format_metric(r.get("sharpe_ratio"), "sharpe_ratio"),
            format_metric(r.get("max_drawdown"), "max_drawdown"),
            # Skew has no canonical type in format_metric — it is a
            # raw moment, not a metric the user listed for the 4dp
            # standard. Kept on _num at 2dp to preserve legacy
            # display ("0.12" stays "0.12", not "0.1234").
            _num(r.get("skewness"), 2),
        ]
        for r in stats
    ]
    return headers, rows


def table_regime_conditional(rows_in: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Per-strategy Sharpe and CAGR split at the 2022 regime break.
    Every numeric column routes through format_metric. The Sharpe
    discrepancy that motivated the centralisation (deck showed 0.55,
    midpoint showed 0.5472) is closed here — both surfaces now read
    "0.5472" identically from this builder."""
    headers = ["Strategy", "Pre-2022 Sharpe", "Post-2022 Sharpe",
               "Pre-2022 CAGR", "Post-2022 CAGR"]
    rows = [
        [
            str(r.get("strategy", "—")),
            format_metric(r.get("pre_2022_sharpe"), "sharpe_ratio"),
            format_metric(r.get("post_2022_sharpe"), "sharpe_ratio"),
            format_metric(r.get("pre_2022_cagr"), "cagr"),
            format_metric(r.get("post_2022_cagr"), "cagr"),
        ]
        for r in rows_in
    ]
    return headers, rows


def table_factor_loadings(rows_in: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Carhart four-factor betas, annualised alpha and R² per strategy.
    Every numeric column routes through format_metric — coefficients
    fall through to the 4dp fallback path (no canonical metric_type
    for a factor beta yet, and 4dp is the right precision for them)."""
    headers = ["Strategy", "Alpha (ann.)", "MKT-RF", "SMB", "HML", "MOM", "R²"]
    rows = []
    for r in rows_in:
        # A trailing '*' marks a coefficient significant at p < 0.05.
        # `factor_coefficient` is not a registered metric_type — the
        # formatter falls through to the 4dp default, which is the
        # right precision for these.
        def _star(value: Any, sig_key: str) -> str:
            s = format_metric(value, "factor_coefficient")
            return s + ("*" if r.get(sig_key) else "") if s != "—" else "—"
        rows.append([
            str(r.get("strategy", "—")),
            _star(r.get("alpha_annualized"), "alpha_significant"),
            _star(r.get("mkt_rf"), "mkt_rf_significant"),
            _star(r.get("smb"), "smb_significant"),
            _star(r.get("hml"), "hml_significant"),
            _star(r.get("mom"), "mom_significant"),
            format_metric(r.get("r_squared"), "r_squared"),
        ])
    return headers, rows


def table_drawdown(rows_in: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Max drawdown and recovery period per strategy, deepest loss first.
    Drawdown column routes through format_metric so the precision
    matches every other max_drawdown display across the platform."""
    headers = ["Strategy", "Max Drawdown", "Recovery (months)"]
    rows = [
        [
            str(r.get("strategy", "—")),
            format_metric(r.get("max_drawdown"), "max_drawdown"),
            (str(r["recovery_months"]) if r.get("recovery_months") is not None
             else "not recovered"),
        ]
        for r in rows_in
    ]
    return headers, rows


# ── Analytical Appendix tables (June 2 2026) ──────────────────────────────────
# The Appendix is a different document type from the brief and the midpoint
# paper: dense, table-heavy, no rhetorical framing. Each helper below maps a
# cached payload to (headers, rows) the DOCX assembler renders identically
# to every other table on the platform.


def table_strategy_performance_full(
    strategies: dict[str, dict],
) -> tuple[list[str], list[list[str]]]:
    """Section B — Full Strategy Performance.

    Every strategy in the cache, sorted by Sharpe descending so the
    headline ordering matches the dashboard's strategy table. The
    benchmark sits in the same table (not in a separate row) so a
    reader can read every column side-by-side.

    June 28 2026 -- "Excess Return vs Benchmark" column inserted
    after CAGR. Value sourced from r.get('excess_return'), which
    is annualised CAGR alpha relative to BENCHMARK computed at
    backtester time (tools/backtester.py:537 stores it as
    round(cagr - bm_cagr, 4) on every strategy result). The
    surrounding appendix text already claims this column is
    present as a Part I required metric; this restores the
    schema match.
    """
    headers = ["Strategy", "Sharpe", "CAGR",
               "Excess Return vs Benchmark",
               "Volatility", "Sortino", "Calmar", "Max DD"]
    items = list(strategies.items())
    items.sort(
        key=lambda kv: -float(kv[1].get("sharpe_ratio") or 0))
    rows = []
    for name, r in items:
        rows.append([
            str(name),
            format_metric(r.get("sharpe_ratio"), "sharpe_ratio"),
            format_metric(r.get("cagr"), "cagr"),
            format_metric(r.get("excess_return"), "excess_return"),
            format_metric(r.get("volatility"), "volatility"),
            format_metric(r.get("sortino_ratio"), "sortino_ratio"),
            format_metric(r.get("calmar_ratio"), "calmar_ratio"),
            format_metric(r.get("max_drawdown"), "max_drawdown"),
        ])
    return headers, rows


def table_statistical_tests(
    strategies: dict[str, dict],
) -> tuple[list[str], list[list[str]]]:
    """Section C — Statistical Tests.

    Surface every statistical figure the strategy result carries:
    paired-t p-value, FDR-corrected p-value, Deflated Sharpe Ratio
    p-value, Probabilistic Sharpe Ratio, and the SPA gate. Skips
    BENCHMARK (a self-vs-self test is trivially 1.0 and adds no
    information).
    """
    headers = ["Strategy", "p (paired t)", "p (FDR-adj)", "DSR p",
               "PSR", "SPA pass"]
    rows = []
    for name, r in strategies.items():
        if name == "BENCHMARK":
            continue
        spa = r.get("passes_spa")
        rows.append([
            str(name),
            format_metric(r.get("p_value_ttest"), "p_value"),
            format_metric(r.get("p_value_corrected"), "p_value"),
            format_metric(r.get("dsr_p_value"), "p_value"),
            format_metric(r.get("probabilistic_sharpe_ratio"),
                          "sharpe_ratio"),
            ("yes" if spa is True else
             "no" if spa is False else "—"),
        ])
    return headers, rows


def table_bootstrap_ci(
    rows_in: list[dict],
) -> tuple[list[str], list[list[str]]]:
    """Section D — Bootstrap Confidence Intervals on Sharpe.

    rows_in is the `bootstrap_ci_sharpe` payload from the
    academic_analytics metric: each entry carries a `strategy`,
    `sharpe`, `ci_low`, `ci_high`, and an `overlaps_benchmark` flag
    (true when the CI brackets the benchmark Sharpe).
    """
    headers = ["Strategy", "Sharpe", "95% CI low", "95% CI high",
               "Overlaps benchmark"]
    rows = []
    for r in rows_in or []:
        rows.append([
            str(r.get("strategy", "—")),
            format_metric(r.get("sharpe"), "sharpe_ratio"),
            format_metric(r.get("ci_low"), "sharpe_ratio"),
            format_metric(r.get("ci_high"), "sharpe_ratio"),
            ("yes" if r.get("overlaps_benchmark") is True else
             "no" if r.get("overlaps_benchmark") is False else "—"),
        ])
    return headers, rows


def table_crisis_performance(
    crisis_payload: dict | None,
) -> tuple[list[str], list[list[str]]]:
    """Section F — Crisis Window Performance.

    crisis_payload is the `crisis_performance` metric payload:
    {windows, rows} where rows maps strategy → {crisis_label →
    {cumulative_return, max_dd, sharpe, partial, n_months}}.

    Columns: Strategy + one column per crisis window, each cell the
    cumulative return through the window (the F3-fix headline, NOT
    the annualised CAGR). Partial-overlap windows are flagged with a
    trailing † so a reader sees the strategy started mid-window.
    """
    if not crisis_payload or "rows" not in crisis_payload:
        return ["Strategy", "(no crisis data)"], []
    windows = list((crisis_payload.get("windows") or {}).keys())
    headers = ["Strategy"] + windows
    rows = []
    for strategy, by_crisis in (crisis_payload.get("rows") or {}).items():
        row = [str(strategy)]
        for w in windows:
            cell = (by_crisis or {}).get(w) or {}
            cum = cell.get("cumulative_return")
            partial = bool(cell.get("partial"))
            txt = format_metric(cum, "cagr")  # render as %, 4dp
            if partial and txt != "—":
                txt = txt + " †"
            row.append(txt)
        rows.append(row)
    return headers, rows


def table_cost_sensitivity(
    cost_payload: dict | None,
) -> tuple[list[str], list[list[str]]]:
    """Section G — Transaction Cost Sensitivity.

    cost_payload is the `oos_cost_sensitivity` metric payload, one
    row per cost assumption (10/15/20 bps). vs_benchmark_pct is a
    fractional figure (e.g. 0.0532 = +5.32% relative to benchmark
    Sharpe); the formatter renders it as a percent at 2dp because the
    headline figure on the dashboard's Net of Switching Costs table
    uses 2dp.
    """
    if not cost_payload or "scenarios" not in cost_payload:
        return (["Bps per rebalance", "Net Sharpe", "vs Benchmark",
                 "Material rebalances"], [])
    headers = ["Bps per rebalance", "Net Sharpe", "vs Benchmark",
               "Material rebalances"]
    n_rebal = cost_payload.get("n_rebalances")
    rows = []
    for s in (cost_payload.get("scenarios") or []):
        vs = s.get("vs_benchmark_pct")
        vs_txt = (f"{vs * 100:+.2f}%" if isinstance(vs, (int, float))
                  else "—")
        rows.append([
            str(s.get("bps", "—")),
            format_metric(s.get("net_sharpe"), "sharpe_ratio"),
            vs_txt,
            (str(n_rebal) if n_rebal is not None else "—"),
        ])
    return headers, rows


def table_invariant_summary(
    invariant_payload: dict | None,
) -> tuple[list[str], list[list[str]]]:
    """Section H — Validation Audit Summary (the invariant verdict
    component). Reads the `invariant_summary` metric written by
    set_strategy_cache on every warm. Empty if the cache row hasn't
    landed yet (cold deploy)."""
    headers = ["Field", "Value"]
    if not invariant_payload:
        return headers, [["status", "no invariant run on record"]]
    passed = invariant_payload.get("passed")
    hf = invariant_payload.get("hard_failures", 0)
    sw = invariant_payload.get("soft_warnings", 0)
    cr = invariant_payload.get("checks_run", 0)
    ran_at = invariant_payload.get("ran_at", "—")
    rows = [
        ["Status", "PASS" if passed else "FAIL"],
        ["Checks run", str(cr)],
        ["Hard failures", str(hf)],
        ["Soft warnings", str(sw)],
        ["Ran at (UTC)", str(ran_at)],
    ]
    return headers, rows


# ── Analytical Appendix data gather (June 2 2026) ─────────────────────────────


async def gather_analytical_appendix_data(
    data_hash: str | None = None,
) -> dict[str, Any]:
    """
    Assembles the data bundle behind the eight-section analytical
    appendix. Builds on gather_document_data() (which already produces
    summary_statistics, regime_conditional, drawdown_comparison,
    factor_loadings, strategy_results, and the audit_disclosures
    bundle) and ADDS four cache reads the appendix needs but the
    other generators don't:

      - bootstrap_ci_sharpe        from academic_analytics metric
      - crisis_performance         from crisis_performance metric
      - oos_cost_sensitivity       from oos_cost_sensitivity metric
      - invariant_summary          from invariant_summary metric
                                   (PR #252 writes this on every warm)
      - data_hash                  the strategy_results_cache hash,
                                   rendered in the appendix footer for
                                   reproducibility

    data_hash -- June 27 2026 (PR 1 v3, LEAK 1 closer). Threaded
    through to gather_document_data so the strategy_results_cache
    read is hash-aware. Under freeze, a miss on the freeze hash
    raises StrategyCacheMissingForHashError. The four additional
    appendix-specific reads (bootstrap_ci_sharpe, crisis_performance,
    oos_cost_sensitivity, invariant_summary) below STILL use
    get_latest_metric -- those are a SEPARATE freeze leak class
    (same architectural shape as the load_substitution_metric_sources
    fix from PR 1 v1) that a follow-up PR can close once the
    operator confirms the appendix-specific metric tokens are also
    in freeze scope.

    Every cache read is fail-open — a missing row leaves the field
    None and the DOCX builder degrades that section to a "no data on
    record" line. The appendix is always assemblable.
    """
    bundle = await gather_document_data(data_hash=data_hash)

    # ── Bootstrap CI table — lives inside the academic_analytics row.
    try:
        from tools.precomputed_analytics import get_latest_metric
        academic = await get_latest_metric("academic_analytics") or {}
        bundle["bootstrap_ci_sharpe"] = (
            academic.get("bootstrap_ci_sharpe") or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("appendix_bootstrap_read_failed", error=str(exc))
        bundle["bootstrap_ci_sharpe"] = []

    # ── Crisis performance.
    try:
        from tools.precomputed_analytics import get_latest_metric
        bundle["crisis_performance"] = (
            await get_latest_metric("crisis_performance"))
    except Exception as exc:  # noqa: BLE001
        log.warning("appendix_crisis_read_failed", error=str(exc))
        bundle["crisis_performance"] = None

    # ── Transaction-cost sensitivity.
    try:
        from tools.regime_meta_validation import get_cached_cost_sensitivity
        bundle["cost_sensitivity"] = await get_cached_cost_sensitivity()
    except Exception as exc:  # noqa: BLE001
        log.warning("appendix_cost_sensitivity_read_failed",
                    error=str(exc))
        bundle["cost_sensitivity"] = None

    # ── Invariant summary — written on every warm by PR #252.
    try:
        from tools.precomputed_analytics import get_latest_metric
        bundle["invariant_summary"] = (
            await get_latest_metric("invariant_summary"))
    except Exception as exc:  # noqa: BLE001
        log.warning("appendix_invariant_read_failed", error=str(exc))
        bundle["invariant_summary"] = None

    # ── Data hash for the footer. The strategy_results_cache hash is
    #    the right anchor: every appendix figure traces back to a
    #    strategy results row (either directly or via the analytics
    #    metric that was refreshed alongside it).
    #
    # June 28 2026 -- when data_hash is supplied (the 3 doc generators
    # always supply it post-PR-1-v3), use the FREEZE-EFFECTIVE hash
    # the caller threaded through. Without this, the reproducibility
    # line (_add_reproducibility_line at academic_docx.py:1731) wrote
    # the LIVE hash (d0b1339e) to the appendix footer under freeze --
    # while strategy headlines + inline {{DATA_HASH}} captions
    # correctly showed the freeze hash. The two values disagreed in
    # the same document. The inline {{DATA_HASH}} token already flows
    # through the substitution table (hash-aware); only the footer
    # reproducibility line needed this fix.
    if data_hash:
        bundle["data_hash"] = data_hash
    else:
        try:
            from tools.cache import get_latest_strategy_hash
            bundle["data_hash"] = await get_latest_strategy_hash()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "appendix_data_hash_read_failed", error=str(exc))
            bundle["data_hash"] = None

    # June 29 2026 -- SUBMISSION SCOPE filter. Same central
    # filter the brief bundle applies; restricts every
    # strategy-bearing surface (strategy_results,
    # summary_statistics, regime_conditional, factor_loadings,
    # crisis_performance, etc.) to BENCHMARK + CLASSIC_60_40 +
    # REGIME_SWITCHING.
    bundle = _filter_to_submission_scope(bundle)

    return bundle
