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


async def gather_document_data() -> dict[str, Any]:
    """
    Assembles the full data bundle the document builders consume.

    Never raises — every failure mode degrades to available=False with
    empty collections, so a caller can build a structurally complete
    document carrying [DATA PENDING] markers in place of live figures.
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
            get_ff_factors, get_latest_strategy_cache, get_monthly_returns,
        )
        from tools import analytics as an

        monthly = await get_monthly_returns()
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
                OOS_SHARPE_BENCHMARK, OOS_SHARPE_EQUAL_WEIGHT,
                OOS_SHARPE_REGIME_CONDITIONAL,
                OOS_WINDOW_MONTHS, OOS_WINDOW_PCT_OF_STUDY,
                PLAY_BY_PLAY_ADD_VALUE, PLAY_BY_PLAY_EVENTS,
            )
            validated_constants = {
                "oos_sharpe_regime_conditional":
                    OOS_SHARPE_REGIME_CONDITIONAL,
                "oos_sharpe_benchmark":      OOS_SHARPE_BENCHMARK,
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
) -> list[str]:
    """Returns the list of token strings in prose that are NOT in
    the anchor_values set (within _STORY_PLAN_TOLERANCE) and NOT
    year-like in citation parens. Used by harness_narrative's
    post-pass check; mirrors the logic in
    document_audit.check_brief_story_plan_violations but on a
    per-section scope.

    Empty list = no unauthorized numbers -> no retry needed."""
    if not prose or not anchor_values:
        return []
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
                        final_text, anchor_values)
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
                            retry_clean, anchor_values)
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
    Drops a leading 'AI DRAFT — REQUIRES HUMAN REVIEW' line if the model
    emitted one. The .docx/.pptx builders add the banner themselves (on
    every page / slide), so an inline copy would only be a duplicate.
    """
    out = (text or "").strip()
    lines = out.split("\n")
    while lines and ("AI DRAFT" in lines[0].upper() or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines).strip()


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
    """
    headers = ["Strategy", "Sharpe", "CAGR", "Volatility",
               "Sortino", "Calmar", "Max DD"]
    items = list(strategies.items())
    items.sort(
        key=lambda kv: -float(kv[1].get("sharpe_ratio") or 0))
    rows = []
    for name, r in items:
        rows.append([
            str(name),
            format_metric(r.get("sharpe_ratio"), "sharpe_ratio"),
            format_metric(r.get("cagr"), "cagr"),
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


async def gather_analytical_appendix_data() -> dict[str, Any]:
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

    Every cache read is fail-open — a missing row leaves the field
    None and the DOCX builder degrades that section to a "no data on
    record" line. The appendix is always assemblable.
    """
    bundle = await gather_document_data()

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
    try:
        from tools.cache import get_latest_strategy_hash
        bundle["data_hash"] = await get_latest_strategy_hash()
    except Exception as exc:  # noqa: BLE001
        log.warning("appendix_data_hash_read_failed", error=str(exc))
        bundle["data_hash"] = None

    return bundle
