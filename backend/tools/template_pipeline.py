"""tools/template_pipeline.py — the verified-data report generation pipeline.

May 22 2026 (item 12 — consolidated spec). Seven discrete steps wrap
template-based document generation:

  STEP 1  Live data pull from the analytics endpoints / DB caches.
  STEP 1B Source citations — web search per concept_id against the
          trusted-domain allowlist; no hardcoded slot details.
  STEP 1C Team activity pull — per-member + platform-wide counts.
  STEP 2  Cross-check live values against the staged findings.
          Mismatches → flag with [DATA MISMATCH live=X staged=Y];
          tolerance: ratios 0.01, percentages 0.1pp.
  STEP 6  Thesis validation gate — three required conditions on the
          live data. A failure BLOCKS generation.
  STEP 7  Finding strength ranking — order findings HIGH > MEDIUM >
          LOW, then by magnitude within tier. Section 2 leads with
          ranked_findings[0] — not a predetermined finding.
  STEP 4  Placeholder substitution — {verified_data},
          {ranked_findings}, {citations_cache}, {team_activity},
          {validation_summary} swapped into the template prompt.
  STEP 5  Post-check — regex scan for numbers not in verified_data,
          inline citations without References entries, sections over
          word budget.

FAIL-OPEN end to end. Each step produces a flag on the affected
field rather than aborting the run; the human reviewer resolves
flags before submission. Step 6 (thesis validation) is the
exception — a failure here BLOCKS Step 4 because the central
argument is no longer supported by the data.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Callable

import structlog

log = structlog.get_logger(__name__)


# ── Tolerances ───────────────────────────────────────────────────────────────


_RATIO_TOLERANCE = 0.01      # two decimal places
_PERCENT_TOLERANCE = 0.001   # 0.1 percentage points (decimal form: 0.001)


_FIELD_TOLERANCE: dict[str, str] = {
    "benchmark_sharpe":         "ratio",
    "regime_switching_sharpe":  "ratio",
    "sharpe_delta":             "ratio",
    "equity_ig_corr_pre_2022":  "ratio",
    "equity_ig_corr_post_2022": "ratio",
    "corr_shift":               "ratio",
    "benchmark_cvar_99":        "percent",
    "vol_targeting_cvar_99":    "percent",
    "cvar_ratio":               "ratio",
    "min_pairwise_corr":        "ratio",
    "equal_weight_sharpe":      "ratio",
    "benchmark_max_dd":         "percent",
    "equal_weight_max_dd":      "percent",
    "max_dd_reduction_pp":      "percent",
    "benchmark_covid_recovery": "percent",
}


def _within_tolerance(live: float, staged: float, kind: str) -> bool:
    tol = _PERCENT_TOLERANCE if kind == "percent" else _RATIO_TOLERANCE
    return abs(live - staged) <= tol


# ── STEP 1 — live data pull ─────────────────────────────────────────────────


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def live_from_payload(payload: dict) -> dict[str, Any]:
    """Build the live-data side of verified_data from a payload dict
    matching the shape gather_payload_from_db() returns.

    Pure compute so the test suite can stub the payload directly."""
    out: dict[str, Any] = {}
    strategies = payload.get("strategies") or {}
    bench = strategies.get("BENCHMARK") or {}
    rs = strategies.get("REGIME_SWITCHING") or {}

    academic = payload.get("academic") or {}
    period = academic.get("study_period") or {}
    out["study_period_start"] = period.get("start")
    out["study_period_end"] = period.get("end")
    out["n_months"] = period.get("n_months")

    out["benchmark_sharpe"] = _safe_float(bench.get("sharpe_ratio"))
    out["regime_switching_sharpe"] = _safe_float(rs.get("sharpe_ratio"))
    if (out["benchmark_sharpe"] is not None
            and out["regime_switching_sharpe"] is not None):
        out["sharpe_delta"] = round(
            out["regime_switching_sharpe"] - out["benchmark_sharpe"], 4)
    else:
        out["sharpe_delta"] = None

    # Benchmark Sharpe rank — needed for the thesis validation gate
    # condition 1 (benchmark_not_first). Computed here against the
    # full strategies dict so the value lives in verified_data and
    # validate_thesis doesn't have to scrape it back out of a finding.
    # Rank 1 = highest Sharpe; rank > 1 means at least one strategy
    # beats the benchmark on Sharpe (the desired pass condition).
    bench_sharpe = out["benchmark_sharpe"]
    if bench_sharpe is not None:
        better = 0
        n_ranked = 0
        for name, result in (strategies or {}).items():
            s = _safe_float((result or {}).get("sharpe_ratio"))
            if s is None:
                continue
            n_ranked += 1
            if name != "BENCHMARK" and s > bench_sharpe:
                better += 1
        out["benchmark_sharpe_rank"] = better + 1
        out["n_strategies_ranked"] = n_ranked
    else:
        out["benchmark_sharpe_rank"] = None
        out["n_strategies_ranked"] = 0

    pre_avg, post_avg = _equity_ig_corr_split(academic)
    out["equity_ig_corr_pre_2022"] = pre_avg
    out["equity_ig_corr_post_2022"] = post_avg
    if pre_avg is not None and post_avg is not None:
        out["corr_shift"] = round(post_avg - pre_avg, 4)
    else:
        out["corr_shift"] = None

    tail = payload.get("tail_risk") or {}
    cvar = {r["strategy"]: r for r in (tail.get("strategies") or [])
            if isinstance(r, dict) and r.get("strategy")}
    out["benchmark_cvar_99"] = _safe_float(
        (cvar.get("BENCHMARK") or {}).get("cvar_99_annual"))
    out["vol_targeting_cvar_99"] = _safe_float(
        (cvar.get("VOL_TARGETING") or {}).get("cvar_99_annual"))
    if (out["benchmark_cvar_99"] is not None
            and out["vol_targeting_cvar_99"] is not None
            and out["benchmark_cvar_99"] != 0):
        out["cvar_ratio"] = round(
            abs(out["vol_targeting_cvar_99"])
            / abs(out["benchmark_cvar_99"]), 4)
    else:
        out["cvar_ratio"] = None

    corr = payload.get("correlation") or {}
    labels = corr.get("labels") or []
    matrix = corr.get("full") or []
    pair = _lowest_off_diagonal(labels, matrix)
    if pair:
        a, b, r = pair
        out["min_pairwise_corr"] = round(r, 4)
        out["min_corr_pair"] = f"{a} and {b}"
    else:
        out["min_pairwise_corr"] = None
        out["min_corr_pair"] = None

    eq, bench_metrics = _equal_weight_blend(strategies)
    out["equal_weight_sharpe"] = eq.get("sharpe")
    out["equal_weight_max_dd"] = eq.get("max_dd")
    out["benchmark_max_dd"] = bench_metrics.get("max_dd")
    if (out["equal_weight_max_dd"] is not None
            and out["benchmark_max_dd"] is not None):
        out["max_dd_reduction_pp"] = round(
            out["benchmark_max_dd"] - out["equal_weight_max_dd"], 4)
    else:
        out["max_dd_reduction_pp"] = None

    crisis = payload.get("crisis") or {}
    crisis_rows = crisis.get("rows") or {}
    bench_recovery = (
        (crisis_rows.get("BENCHMARK") or {})
        .get("COVID_Recovery") or {})
    out["benchmark_covid_recovery"] = _safe_float(
        bench_recovery.get("cagr"))

    macro = payload.get("macro_digest") or {}
    out["macro_summary"] = (macro.get("summary_text") or "")[:500]
    out["macro_regime_implication"] = (
        macro.get("regime_implication") or "")[:500]
    return out


def _equity_ig_corr_split(academic: dict) -> tuple[float | None, float | None]:
    rc = (academic or {}).get("rolling_correlation") or {}
    pts = rc.get("points") or []
    pre, post = [], []
    for p in pts:
        d = p.get("date") or ""
        v = p.get("equity_ig")
        if v is None:
            continue
        if d >= "2022":
            post.append(float(v))
        else:
            pre.append(float(v))
    pre_avg = round(sum(pre) / len(pre), 4) if pre else None
    post_avg = round(sum(post) / len(post), 4) if post else None
    return pre_avg, post_avg


def _lowest_off_diagonal(
    labels: list[str], matrix: list[list[Any]],
) -> tuple[str, str, float] | None:
    best: tuple[str, str, float] | None = None
    for i, row in enumerate(matrix):
        for j in range(i + 1, len(row)):
            v = row[j] if j < len(row) else None
            if v is None or i >= len(labels) or j >= len(labels):
                continue
            try:
                vf = float(v)
            except (TypeError, ValueError):
                continue
            if best is None or vf < best[2]:
                best = (labels[i], labels[j], vf)
    return best


def _strategy_monthly_returns(result: dict) -> list[tuple[str, float]]:
    raw = (result or {}).get("monthly_returns") or []
    out: list[tuple[str, float]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            d, v = entry[0], entry[1]
        elif isinstance(entry, dict):
            d = entry.get("date") or entry.get("month")
            v = entry.get("return") or entry.get("value")
        else:
            continue
        if d is None or v is None:
            continue
        try:
            out.append((str(d), float(v)))
        except (TypeError, ValueError):
            continue
    return out


def _annualised_sharpe(rets: list[float]) -> float | None:
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return round((mean / math.sqrt(var)) * math.sqrt(12), 4)


def _max_drawdown(rets: list[float]) -> float | None:
    if not rets:
        return None
    cum = 1.0
    peak = 1.0
    worst = 0.0
    for r in rets:
        cum *= 1.0 + r
        peak = max(peak, cum)
        worst = min(worst, cum / peak - 1.0)
    return round(worst, 4)


def _equal_weight_blend(strategies: dict) -> tuple[dict, dict]:
    series_by_name = {
        name: _strategy_monthly_returns(res)
        for name, res in (strategies or {}).items()
        if name != "BENCHMARK"
    }
    bench_series = _strategy_monthly_returns(
        (strategies or {}).get("BENCHMARK") or {})
    if not series_by_name or not bench_series:
        return {}, {}
    bench_dates = {d for d, _ in bench_series}
    common = bench_dates
    for s in series_by_name.values():
        common &= {d for d, _ in s}
    common_sorted = sorted(common)
    if not common_sorted:
        return {}, {}
    blend_rets: list[float] = []
    for d in common_sorted:
        vals = []
        for s in series_by_name.values():
            m = dict(s)
            if d in m:
                vals.append(m[d])
        if vals:
            blend_rets.append(sum(vals) / len(vals))
    bench_rets = [v for d, v in bench_series if d in common]
    return (
        {"sharpe": _annualised_sharpe(blend_rets),
         "max_dd": _max_drawdown(blend_rets)},
        {"sharpe": _annualised_sharpe(bench_rets),
         "max_dd": _max_drawdown(bench_rets)},
    )


# ── STEP 1C — team activity ──────────────────────────────────────────────────


_TEAM_EMAILS = {
    "michael":  "ruurdsm@queens.edu",
    "bob":      "thaob@queens.edu",
    "molly":    "murdockm@queens.edu",
}


async def fetch_team_activity() -> dict[str, Any]:
    """Per-member + platform-wide counts pulled from the existing
    activity tables (test_results, test_feedback, agent_interactions,
    commit_activity). Mirrors the activity_log read patterns.

    Returns the verified_data field names directly so the live-pull
    merge is a plain dict.update(). Every count fails open to 0 on a
    missing table / DB error.
    """
    out = {
        "team_total_uat_steps": 0,
        "team_total_failure_reports": 0,
        "team_total_failure_reports_resolved": 0,
        "team_total_council_sessions": 0,
        "team_total_audit_validations": 0,
        "michael_commits": 0, "michael_prs_merged": 0,
        "michael_migrations_deployed": 0,
        "michael_failure_reports_resolved": 0,
        "bob_uat_steps": 0, "bob_council_sessions": 0,
        "bob_academic_review_runs": 0, "bob_report_drafts": 0,
        "molly_uat_steps": 0, "molly_failure_reports_filed": 0,
        "molly_feedback_items": 0,
    }
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return out
        async with AsyncSessionLocal() as s:
            # Platform-wide
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_results "
                "WHERE result IN ('pass','fail')"))
            out["team_total_uat_steps"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_results WHERE result = 'fail'"))
            out["team_total_failure_reports"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_results "
                "WHERE result = 'fail' AND resolved_at IS NOT NULL"))
            out["team_total_failure_reports_resolved"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM agent_interactions "
                "WHERE interaction_type = 'council'"))
            out["team_total_council_sessions"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM audit_runs "
                "WHERE statistical_status = 'pass' "
                " OR layer_2_status = 'pass'"))
            out["team_total_audit_validations"] = int(r.scalar() or 0)

            # Michael
            r = await s.execute(text(
                "SELECT COUNT(*) FROM commit_activity WHERE author = :e"),
                {"e": _TEAM_EMAILS["michael"]})
            out["michael_commits"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM pr_suggestions "
                "WHERE reviewed_by = :e"),
                {"e": _TEAM_EMAILS["michael"]})
            out["michael_prs_merged"] = int(r.scalar() or 0)
            try:
                from pathlib import Path
                mig_dir = (Path(__file__).resolve().parents[1]
                           / "migrations" / "versions")
                out["michael_migrations_deployed"] = sum(
                    1 for p in mig_dir.glob("*.py")
                    if p.name[0].isdigit())
            except Exception:  # noqa: BLE001
                pass
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_results "
                "WHERE result = 'fail' AND resolved_by = :e"),
                {"e": _TEAM_EMAILS["michael"]})
            out["michael_failure_reports_resolved"] = int(r.scalar() or 0)

            # Bob
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_results "
                "WHERE user_email = :e AND result IN ('pass','fail')"),
                {"e": _TEAM_EMAILS["bob"]})
            out["bob_uat_steps"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM agent_interactions "
                "WHERE user_email = :e AND interaction_type = 'council'"),
                {"e": _TEAM_EMAILS["bob"]})
            out["bob_council_sessions"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM agent_interactions "
                "WHERE user_email = :e "
                " AND interaction_type = 'academic_review'"),
                {"e": _TEAM_EMAILS["bob"]})
            out["bob_academic_review_runs"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM editor_drafts "
                "WHERE owner_email = :e"),
                {"e": _TEAM_EMAILS["bob"]})
            out["bob_report_drafts"] = int(r.scalar() or 0)

            # Molly
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_results "
                "WHERE user_email = :e AND result IN ('pass','fail')"),
                {"e": _TEAM_EMAILS["molly"]})
            out["molly_uat_steps"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_results "
                "WHERE user_email = :e AND result = 'fail'"),
                {"e": _TEAM_EMAILS["molly"]})
            out["molly_failure_reports_filed"] = int(r.scalar() or 0)
            r = await s.execute(text(
                "SELECT COUNT(*) FROM test_feedback WHERE user_email = :e"),
                {"e": _TEAM_EMAILS["molly"]})
            out["molly_feedback_items"] = int(r.scalar() or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("team_activity_fetch_failed", error=str(exc))
    return out


def cross_check_team_activity(activity: dict) -> list[str]:
    """Activity cross-check from the spec: Bob UAT + Molly UAT must
    equal the platform total. Returns a list of flag strings;
    empty when everything reconciles."""
    bob = int(activity.get("bob_uat_steps") or 0)
    molly = int(activity.get("molly_uat_steps") or 0)
    total = int(activity.get("team_total_uat_steps") or 0)
    if bob + molly != total:
        return [(
            f"[ACTIVITY CROSS-CHECK MISMATCH: "
            f"Bob {bob} + Molly {molly} = {bob + molly} ≠ "
            f"platform total {total} — verify before submission]")]
    return []


# ── STEP 2 — cross-check live values against staged findings ─────────────────


_STAGED_NUMBER_RE = re.compile(r"(-?\d+(?:\.\d+)?)(%)?")


def _extract_numbers_from_findings(findings_md: str) -> list[float]:
    out: list[float] = []
    for m in _STAGED_NUMBER_RE.finditer(findings_md or ""):
        try:
            v = float(m.group(1))
            if m.group(2) == "%":
                v = v / 100.0
            out.append(v)
        except (TypeError, ValueError):
            continue
    return out


def _staged_field_match(
    field: str, live_value: Any, staged_numbers: list[float],
) -> tuple[bool, Any]:
    if not isinstance(live_value, (int, float)):
        return True, None
    kind = _FIELD_TOLERANCE.get(field, "ratio")
    for s in staged_numbers:
        if _within_tolerance(float(live_value), s, kind):
            return True, None
    return False, {"live": live_value, "staged": None, "field": field}


def cross_check(
    live: dict[str, Any], staged_md: str,
) -> tuple[dict[str, Any], list[dict]]:
    staged_numbers = _extract_numbers_from_findings(staged_md)
    verified: dict[str, Any] = {}
    mismatches: list[dict] = []
    for field, live_value in (live or {}).items():
        if not isinstance(live_value, (int, float)):
            verified[field] = live_value
            continue
        matched, payload = _staged_field_match(
            field, live_value, staged_numbers)
        if matched:
            verified[field] = live_value
        else:
            verified[field] = (
                f"[DATA MISMATCH: live={live_value} "
                f"staged=not-found — verify before submission]")
            mismatches.append(payload or {})
    return verified, mismatches


# ── STEP 1B — citation finder (concept-driven, no hardcoded slots) ───────────


# ── 7-state citation machine ─────────────────────────────────────────────────
#
# The Analytical Appendix grade requires every citation to be either
# explicitly verified or explicitly excluded — never silently missing.
# These seven states capture every legitimate position a citation can
# be in. The legacy "untrusted_source" state is retained for backwards
# compatibility (rows written by the pre-review-workflow code path)
# and treated as equivalent to PENDING_REVIEW everywhere downstream.
#
#   not_found          search returned nothing usable on any pass
#   pending_review     search returned a candidate but trust is unclear;
#                      waiting on a human decision
#   verified           auto-verified, trusted domain — needs no review
#   human_verified     reviewer accepted a pending_review citation
#                      via the accept_untrusted action
#   search_selected    reviewer picked an alternative from pass 2 or 3
#                      via the select_alternative action
#   manually_added     reviewer entered the citation by hand via the
#                      manual_add action — no search source attached
#   rejected           reviewer rejected — the concept is dropped from
#                      the references list; the inline marker is replaced
#                      with the concept's natural-language description
#                      instead of an APA citation
CITATION_STATE_NOT_FOUND       = "not_found"
CITATION_STATE_PENDING_REVIEW  = "pending_review"
CITATION_STATE_VERIFIED        = "verified"
CITATION_STATE_HUMAN_VERIFIED  = "human_verified"
CITATION_STATE_SEARCH_SELECTED = "search_selected"
CITATION_STATE_MANUALLY_ADDED  = "manually_added"
CITATION_STATE_REJECTED        = "rejected"

# Frozenset for membership checks in citation_quality and elsewhere.
# These map to the "passes" bucket — any state in this set counts as
# a real citation for quality colouring and downstream rendering.
CITATION_VERIFIED_STATES: frozenset[str] = frozenset({
    CITATION_STATE_VERIFIED,
    CITATION_STATE_HUMAN_VERIFIED,
    CITATION_STATE_SEARCH_SELECTED,
    CITATION_STATE_MANUALLY_ADDED,
})

# States that require human action before the paper is presentation-ready.
CITATION_NEEDS_REVIEW_STATES: frozenset[str] = frozenset({
    CITATION_STATE_PENDING_REVIEW,
    "untrusted_source",  # legacy alias — same meaning, older rows
    CITATION_STATE_NOT_FOUND,
})

# Reviewer actions accepted by the /review endpoint.
CITATION_REVIEW_ACTIONS: frozenset[str] = frozenset({
    "accept_untrusted",     # pending_review → human_verified
    "select_alternative",   # any → search_selected (with the picked entry)
    "reject",               # any → rejected
    "manual_add",           # any → manually_added (with entered citation)
})


# Search pass 1 — strictly trusted domains. These are the journals,
# central banks, institutions, and research firms whose citations the
# Analytical Appendix can defend without further review.
_TRUSTED_DOMAINS = (
    "jstor.org", "ssrn.com", "papers.ssrn.com", "nber.org",
    "federalreserve.gov", "bis.org", "imf.org", "ecb.europa.eu",
    "aqr.com", "dimensional.com", "cfainstitute.org",
    "onlinelibrary.wiley.com",
    "academic.oup.com",
    "sciencedirect.com",
    "pm-research.com",
    "scholar.google.com",
)


# Search pass 2 — wider academic / quasi-academic. Working papers,
# university hosting, regional Fed banks, professional bodies. A
# reviewer must accept these before they count as verified; the
# search itself flags them as pending_review.
_ACADEMIC_DOMAINS = (
    ".edu/", ".edu.", ".ac.uk", ".ac.au",
    "stlouisfed.org", "newyorkfed.org", "minneapolisfed.org",
    "chicagofed.org", "philadelphiafed.org", "atlantafed.org",
    "kansascityfed.org", "dallasfed.org", "richmondfed.org",
    "sanfranciscofed.org", "bostonfed.org", "clevelandfed.org",
    "sec.gov", "treasury.gov", "europa.eu", "oecd.org",
    "worldbank.org", "tandfonline.com",
    "cambridge.org", "springer.com", "mitpressjournals.org",
)


# Never returned, ever — even from pass 3. These are popular finance
# blogs that look authoritative on first read but do not meet the
# citation bar.
_NEVER_DOMAINS = (
    "investopedia.com", "wikipedia.org", "wikipedia.com",
    "medium.com", "linkedin.com", "seekingalpha.com",
    "fool.com",
)


def _is_trusted_url(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    if any(d in url_lower for d in _NEVER_DOMAINS):
        return False
    return any(d in url_lower for d in _TRUSTED_DOMAINS)


def _is_academic_url(url: str) -> bool:
    """True for pass 2 — wider academic / quasi-academic sources.
    Distinct from _is_trusted_url: an academic URL is publishable but
    requires a human accept before it counts as verified."""
    if not url:
        return False
    url_lower = url.lower()
    if any(d in url_lower for d in _NEVER_DOMAINS):
        return False
    if _is_trusted_url(url_lower):
        # Already trusted — counts as pass 1, not pass 2.
        return False
    return any(d in url_lower for d in _ACADEMIC_DOMAINS)


def _is_publishable_url(url: str) -> bool:
    """True for pass 3 — widest acceptable. Anything that isn't on
    the never-list AND has a domain that looks like an org / academic
    / governmental source. This is the last fallback so a not_found
    result is genuinely rare. The reviewer still has to accept it."""
    if not url:
        return False
    url_lower = url.lower()
    if any(d in url_lower for d in _NEVER_DOMAINS):
        return False
    # Any URL with a publishable-looking TLD passes pass 3 — the
    # reviewer's job is to filter from here.
    publishable_suffixes = (
        ".org/", ".org.", ".gov/", ".gov.", ".edu/", ".edu.",
        ".int/", ".int.",
    )
    return any(s in url_lower for s in publishable_suffixes)


def _parse_citation_json(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*)```\s*$", s, flags=re.DOTALL)
    if m:
        s = m.group(1).strip()
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def _format_citation(c: dict) -> str:
    """APA 7th edition reference list formatter.

    Journal article:
      Author, A. A., & Author, B. B. (year). Title of article in
      sentence case. Journal Name in Title Case, volume(issue),
      pages. https://doi.org/xxxxx

    Working paper / report:
      Author, A. A. (year). Title of paper. Institution Name. URL

    The .docx renderer wraps the result in a hanging-indent
    paragraph so the visual format matches the APA convention.

    Inputs:
      author              — 'Surname, A. A.' or
                            'Surname1, A. A., & Surname2, B. B.'
      year                — '1994' or '2018'
      title               — sentence case title of the work
      journal_or_institution — italics-wrapped at render time
                            (the .docx renderer can't see asterisks
                            inside table cells, so we use '*Journal*'
                            markers that _split_inline interprets)
      volume_issue_pages  — '15(2), 3-44' or '9(3), 203-228'
      url                 — DOI URL or institution URL
    """
    author = (c.get("author") or "").strip()
    year = c.get("year")
    if year is not None:
        year = str(year).strip()
    title = (c.get("title") or "").strip()
    journal = (c.get("journal_or_institution") or "").strip()
    vol = (c.get("volume_issue_pages") or "").strip()
    url = (c.get("url") or "").strip()
    parts: list[str] = []
    if author:
        # Author block ends with a period only when no year follows.
        parts.append(author.rstrip(".") + ".")
    if year:
        parts.append(f"({year}).")
    if title:
        # APA 7th sentence case: trailing period.
        parts.append(title.rstrip(".") + ".")
    if journal:
        # Journal name is italicised in APA — emit Markdown asterisks
        # so the docx renderer's _split_inline reads the italics.
        if vol:
            parts.append(f"*{journal}*, {vol}.")
        else:
            parts.append(f"*{journal}*.")
    if url:
        parts.append(url)
    return " ".join(parts).strip()


async def source_citations(
    concepts: list[dict],
) -> dict[str, dict]:
    """STEP 1B — find one citation per concept_id via a 3-pass web
    search.

    PASS 1 — trusted domain (Journal of Finance, NBER, BIS, Fed, AQR,
            CFA Institute, SSRN). A hit here goes straight to
            CITATION_STATE_VERIFIED.
    PASS 2 — wider academic. .edu, regional Feds, sec.gov, treasury.gov,
            publishing houses (Cambridge, Springer, MIT Press). Stored
            as CITATION_STATE_PENDING_REVIEW so the reviewer can accept
            with one click.
    PASS 3 — widest publishable. Any .org / .gov / .edu / .int that
            isn't on the never-list. Also CITATION_STATE_PENDING_REVIEW
            but flagged as a wider-pass result so the reviewer knows
            the source has lower priors of being right.

    Every pass that fires stores its result in `alternatives` so the
    reviewer can pick from any of the three passes via the
    select_alternative action. The primary entry is whichever pass
    produced the first viable citation; the other passes' results are
    appended as alternatives.

    Each entry returned:
      concept_id, author, year, title, journal_or_institution,
      volume_issue_pages, url, verification_status,
      search_query_used, formatted, alternatives (list of dicts),
      passes_run (int 1-3).

    Fail-open: when ENVIRONMENT=test or no Anthropic key is configured,
    every concept is marked CITATION_STATE_NOT_FOUND. The downstream
    pipeline writes [CITATION REQUIRED] inline for unverified slots —
    never invents one.
    """
    out: dict[str, dict] = {}
    if os.getenv("ENVIRONMENT", "").lower() == "test":
        for c in concepts:
            cid = c.get("concept_id", "")
            out[cid] = _empty_citation_entry(
                cid, c.get("search_query", ""))
        return out

    try:
        from agents.base import call_claude, SONNET_MODEL
    except Exception:  # noqa: BLE001
        for c in concepts:
            cid = c.get("concept_id", "")
            out[cid] = _empty_citation_entry(
                cid, c.get("search_query", ""))
        return out

    # Parallelise across concepts. Each _one_concept runs up to three
    # search passes serially (so pass 2 only fires if pass 1 failed,
    # pass 3 only if pass 2 also failed) — but different concepts run
    # in parallel via the bounded semaphore.
    import asyncio
    sem = asyncio.Semaphore(10)

    def _one_concept(c: dict) -> tuple[str, dict[str, Any]]:
        cid = c.get("concept_id", "")
        query = c.get("search_query", "")
        entry: dict[str, Any] = _empty_citation_entry(cid, query)
        alternatives: list[dict[str, Any]] = []

        try:
            # ── Pass 1 — trusted domains only ───────────────────────────────
            primary = _run_citation_pass(
                call_claude, SONNET_MODEL,
                query=query, concept_id=cid, pass_index=1)
            entry["passes_run"] = 1
            if primary and primary.get("verification_status") == \
                    CITATION_STATE_VERIFIED:
                entry.update(primary)
                entry["formatted"] = _format_citation(entry)
                return cid, entry
            # Pass 1 returned a candidate but on an untrusted domain —
            # capture it as the primary pending_review hit AND keep
            # searching for a better one.
            if primary and primary.get("url"):
                primary["pass_source"] = "pass_1_off_trusted"
                alternatives.append(primary)

            # ── Pass 2 — wider academic ─────────────────────────────────────
            wider = _run_citation_pass(
                call_claude, SONNET_MODEL,
                query=query, concept_id=cid, pass_index=2)
            entry["passes_run"] = 2
            if wider and wider.get("url"):
                wider["pass_source"] = "pass_2_academic"
                # If pass 2 found an academic-domain hit, promote it
                # to the primary entry as pending_review.
                if _is_academic_url(wider.get("url") or ""):
                    entry.update(wider)
                    entry["verification_status"] = (
                        CITATION_STATE_PENDING_REVIEW)
                    entry["formatted"] = _format_citation(entry)
                    entry["alternatives"] = alternatives
                    return cid, entry
                alternatives.append(wider)

            # ── Pass 3 — widest publishable ─────────────────────────────────
            widest = _run_citation_pass(
                call_claude, SONNET_MODEL,
                query=query, concept_id=cid, pass_index=3)
            entry["passes_run"] = 3
            if widest and widest.get("url") \
                    and _is_publishable_url(widest.get("url") or ""):
                widest["pass_source"] = "pass_3_widest"
                entry.update(widest)
                entry["verification_status"] = (
                    CITATION_STATE_PENDING_REVIEW)
                entry["formatted"] = _format_citation(entry)
                entry["alternatives"] = alternatives
                return cid, entry
            if widest and widest.get("url"):
                widest["pass_source"] = "pass_3_off_publishable"
                alternatives.append(widest)

            # No pass produced a viable primary — but we might have
            # alternatives the reviewer can promote. Promote the
            # first alternative as a pending_review primary if any
            # alternative had a URL; otherwise leave as not_found.
            if alternatives:
                first = alternatives.pop(0)
                entry.update({k: v for k, v in first.items()
                              if k not in ("pass_source",)})
                entry["verification_status"] = (
                    CITATION_STATE_PENDING_REVIEW)
                entry["formatted"] = _format_citation(entry)
                entry["alternatives"] = alternatives
                return cid, entry

            entry["verification_status"] = CITATION_STATE_NOT_FOUND
            entry["alternatives"] = []
            return cid, entry
        except Exception as exc:  # noqa: BLE001
            log.warning("citation_search_failed",
                        concept_id=cid, error=str(exc))
            entry["verification_status"] = CITATION_STATE_NOT_FOUND
            return cid, entry

    async def _bounded(c: dict) -> tuple[str, dict[str, Any]]:
        async with sem:
            return await asyncio.to_thread(_one_concept, c)

    results = await asyncio.gather(
        *[_bounded(c) for c in concepts],
        return_exceptions=False)
    for cid, entry in results:
        out[cid] = entry
    return out


def _empty_citation_entry(cid: str, query: str) -> dict[str, Any]:
    """A fresh citation entry with every field initialised. Used as
    the starting point for every search and as the fail-open shape."""
    return {
        "concept_id":              cid,
        "verification_status":     CITATION_STATE_NOT_FOUND,
        "search_query_used":       query,
        "author":                  None,
        "year":                    None,
        "title":                   None,
        "journal_or_institution":  None,
        "volume_issue_pages":      None,
        "url":                     None,
        "formatted":               None,
        "alternatives":            [],
        "passes_run":              0,
    }


def _run_citation_pass(
    call_claude_fn,
    model: str,
    *,
    query: str,
    concept_id: str,
    pass_index: int,
) -> dict[str, Any] | None:
    """Runs ONE citation search pass with a pass-specific system
    prompt that names the acceptable domain set. Returns the parsed
    citation dict (with author/year/title/journal/url) on success,
    or None on a failed parse / unverified flag. The CALLER is
    responsible for deciding the resulting verification_status based
    on the URL the search returned — this function just runs the
    search and parses the result.

    `call_claude_fn` is the Anthropic call wrapper; passed in so the
    caller can mock it in tests without monkeypatching the import.
    """
    web_search_tool = {
        "type":     "web_search_20250305",
        "name":     "web_search",
        "max_uses": 2,
    }
    pass_instructions = {
        1: (
            "use web_search to find the most appropriate academic "
            "citation from a TRUSTED domain only — Journal of Finance, "
            "Journal of Financial Economics, Review of Financial Studies, "
            "NBER working papers, BIS, the Federal Reserve Board, AQR, "
            "CFA Institute, SSRN, JSTOR. Return ONLY a JSON object."),
        2: (
            "the trusted-domain search returned nothing. Now run a WIDER "
            "search. ACCEPTABLE sources for this pass: university-hosted "
            "papers (.edu domains), regional Federal Reserve banks, the "
            "SEC, Treasury, OECD, World Bank, ECB, Cambridge / Springer / "
            "MIT Press journals. The result will be flagged for human "
            "review — pick the most authoritative hit you find. Return "
            "ONLY a JSON object."),
        3: (
            "neither the trusted nor the academic search returned a "
            "result. Run the WIDEST acceptable search now. Anything on "
            "a .org / .gov / .edu / .int domain that isn't a popular "
            "finance blog (no Investopedia, Wikipedia, Medium, Seeking "
            "Alpha, Motley Fool, LinkedIn) is acceptable for this pass. "
            "The reviewer will decide whether to use it. Return ONLY a "
            "JSON object."),
    }
    sys_prompt = (
        "You are a citation finder. " + pass_instructions[pass_index]
        + " Required JSON fields: author, year, title, "
        "journal_or_institution, volume_issue_pages, url. Author in "
        "'Surname, Initials' format; multiple authors joined with "
        "' and '. If no source is found at all, return "
        "{\"unverified\": true}. NEVER invent details — only what the "
        "search results support.")

    try:
        raw = call_claude_fn(
            model=model,
            system_prompt=sys_prompt,
            user_message=(
                f"concept: {concept_id}\n"
                f"search query: {query}\n"
                f"pass: {pass_index}/3\n\n"
                "Use web_search now. Return ONLY the JSON."),
            max_tokens=512,
            tools=[web_search_tool],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_pass_call_failed",
                    concept_id=concept_id,
                    pass_index=pass_index, error=str(exc))
        return None

    parsed = _parse_citation_json(raw)
    if not parsed or parsed.get("unverified"):
        return None

    # Per-pass URL classification: pass 1 only counts as verified if
    # the URL is on the trusted list; pass 2 only counts as a primary
    # if academic; pass 3 only if publishable. The CALLER then makes
    # the verification_status decision.
    url = parsed.get("url") or ""
    if pass_index == 1 and _is_trusted_url(url):
        parsed["verification_status"] = CITATION_STATE_VERIFIED
    else:
        parsed["verification_status"] = CITATION_STATE_PENDING_REVIEW
    return parsed


def citation_quality(citations: dict) -> str:
    """Green / amber / red indicator per the spec.

    Updated May 23 2026 (user request): green 8-10 verified,
    amber 5-7 verified, red fewer than 5 verified. Every state in
    CITATION_VERIFIED_STATES counts toward the verified total —
    auto-verified, human-accepted, alternative-selected, and
    manually-added all qualify. The needs-review states (not_found,
    pending_review, the legacy untrusted_source) do not."""
    verified = sum(
        1 for v in (citations or {}).values()
        if isinstance(v, dict)
        and v.get("verification_status") in CITATION_VERIFIED_STATES)
    if verified >= 8:
        return "green"
    if verified >= 5:
        return "amber"
    return "red"


# ── STEP 6 — Thesis validation gate ──────────────────────────────────────────


_THESIS_CONDITIONS = [
    {
        "id": "benchmark_not_first",
        "description": (
            "At least one strategy beats the benchmark on Sharpe "
            "(benchmark rank > 1)."),
        "field": "benchmark_sharpe_rank",
        "test": "gt",
        "threshold": 1,
    },
    {
        "id": "material_corr_shift",
        "description": (
            "Post-2022 equity-IG correlation shift exceeds 0.30."),
        "field": "corr_shift",
        "test": "gt",
        "threshold": 0.30,
    },
    {
        "id": "meaningful_dd_reduction",
        "description": (
            "Equal-weight blend reduces drawdown by at least "
            "10 percentage points vs benchmark."),
        "field": "max_dd_reduction_pp_abs",
        "test": "gt",
        "threshold": 0.10,
    },
]


def validate_thesis(
    verified_data: dict[str, Any], ranked_findings: list[dict],
) -> dict[str, Any]:
    """STEP 6 — three required conditions. A failure on any blocks
    generation. Returns a dict {passed, conditions, blocker_reasons}.

    The pipeline computes the inputs from verified_data and the
    F1 finding (for benchmark_sharpe_rank). The drawdown reduction
    is stored as a negative value in verified_data
    (max_dd_reduction_pp <= 0 when bench DD is worse); we test the
    absolute magnitude.
    """
    # Source of truth: live_from_payload computes benchmark_sharpe_rank
    # against the strategies dict and writes it into verified_data, so
    # the validation never depends on a finding being staged with the
    # right shape. The finding evidence is a legacy fallback for rows
    # generated before this fix landed.
    bench_rank = None
    vd_rank = verified_data.get("benchmark_sharpe_rank")
    if isinstance(vd_rank, int):
        bench_rank = vd_rank
    elif isinstance(vd_rank, float) and not (vd_rank != vd_rank):
        bench_rank = int(vd_rank)
    else:
        f1 = next(
            (f for f in (ranked_findings or [])
             if f.get("title") == "BENCHMARK COMPETITIVENESS"), None)
        if f1 and isinstance(f1.get("benchmark_rank"), int):
            bench_rank = f1["benchmark_rank"]
        elif f1 and isinstance(f1.get("evidence"), list):
            # Extract 'BENCHMARK Sharpe rank: N of M' from evidence text.
            for line in f1["evidence"]:
                m = re.search(r"BENCHMARK Sharpe rank: (\d+) of", line)
                if m:
                    try:
                        bench_rank = int(m.group(1))
                        break
                    except ValueError:
                        pass

    corr_shift = verified_data.get("corr_shift")
    if not isinstance(corr_shift, (int, float)):
        corr_shift = None

    dd_reduction = verified_data.get("max_dd_reduction_pp")
    # Reduction is stored as bench_max_dd - equal_max_dd; when the blend
    # is shallower, dd_reduction is positive (bench more negative).
    dd_abs = (abs(dd_reduction)
              if isinstance(dd_reduction, (int, float)) else None)

    results = []
    blocker_reasons: list[str] = []
    for cond in _THESIS_CONDITIONS:
        cid = cond["id"]
        thresh = cond["threshold"]
        if cid == "benchmark_not_first":
            value = bench_rank
        elif cid == "material_corr_shift":
            value = corr_shift
        elif cid == "meaningful_dd_reduction":
            value = dd_abs
        else:
            value = None
        passed = (
            value is not None
            and isinstance(value, (int, float))
            and value > thresh)
        results.append({
            "id": cid,
            "description": cond["description"],
            "field": cond["field"],
            "threshold": thresh,
            "value": value,
            "passed": bool(passed),
        })
        if not passed:
            blocker_reasons.append(
                f"[{cid}] {cond['description']} "
                f"value={value}, threshold>{thresh}")
    return {
        "passed": all(r["passed"] for r in results),
        "conditions": results,
        "blocker_reasons": blocker_reasons,
    }


# ── STEP 7 — Finding strength ranking ────────────────────────────────────────


_STRENGTH_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _finding_magnitude(f: dict) -> float:
    """Heuristic magnitude per finding so the within-tier ordering is
    deterministic and meaningful. We pull a representative numeric
    from the finding's evidence + a per-title bonus for canonical
    headline findings. Pure compute; the per-title bonus is the only
    thing that codes 'F2 is the project's central finding' into the
    ranking without forcing a fixed ordering."""
    title = f.get("title", "")
    bonus = {
        "REGIME SHIFT EVIDENCE":           2.0,
        "BENCHMARK COMPETITIVENESS":       1.5,
        "TAIL RISK DIVERGENCE":            1.4,
        "CRISIS PERFORMANCE":              1.3,
        "DIVERSIFICATION BENEFIT":         1.2,
        "NATURAL COMPLEMENTS":             1.0,
        "FACTOR EXPOSURE":                 0.9,
        "MOMENTUM VS MEAN REVERSION":      0.8,
        "EFFICIENT FRONTIER SHIFT":        0.7,
        "MACRO CONTEXT ALIGNMENT":         0.6,
        "SURPRISES":                       0.5,
    }.get(title, 0.0)
    # First numeric token in the first evidence bullet — a proxy for
    # the magnitude of the finding's headline number.
    ev = f.get("evidence") or []
    first_num = 0.0
    if ev:
        m = re.search(r"(-?\d+(?:\.\d+)?)", str(ev[0]))
        if m:
            try:
                first_num = abs(float(m.group(1)))
            except (TypeError, ValueError):
                first_num = 0.0
    return bonus + min(first_num / 10.0, 1.0)


def rank_findings(findings: list[dict]) -> list[dict]:
    """Order findings HIGH > MEDIUM > LOW, then by magnitude desc
    within each tier. Returns a NEW list so the caller can persist it
    alongside the raw findings."""
    return sorted(
        list(findings or []),
        key=lambda f: (
            _STRENGTH_RANK.get(f.get("nugget_strength", "LOW"), 9),
            -_finding_magnitude(f)))


def macro_validated(macro_summary: str | None) -> bool:
    """Cleanliness check for the latest macro digest. Returns True
    only when the summary_text is non-empty, does not contain agent
    planning prose ('I'll start by...', 'Let me search', 'Now I have
    sufficient...', etc.), and parses as natural sentences rather
    than a raw JSON code fence. The validator is intentionally
    conservative — a False here only omits the macro paragraph from
    Section 2, never blocks generation."""
    s = (macro_summary or "").strip()
    if not s:
        return False
    bad_signals = (
        "i'll start", "i will start", "let me search",
        "let me fetch", "now i have", "i have sufficient",
        "```json", "```", "compiling now",
    )
    lower = s.lower()
    if any(b in lower for b in bad_signals):
        return False
    if len(s) < 50:
        return False
    return True


# ── STEP 4 — placeholder substitution ────────────────────────────────────────


_LEGACY_PLACEHOLDER_RE = re.compile(
    r"\{\{verified_data\.([a-zA-Z0-9_]+)\}\}")


def _fmt_value(v: Any) -> str:
    if v is None:
        return "[DATA REQUIRED]"
    if isinstance(v, float):
        return f"{v}"
    if isinstance(v, (list, dict)):
        return json.dumps(v, default=str)
    return str(v)


def substitute_prompt(
    template_prompt: str,
    verified_data: dict[str, Any],
    ranked_findings: list[dict],
    citations: dict[str, dict],
    team_activity: dict[str, Any],
    validation_summary: dict[str, Any],
) -> str:
    """STEP 4 — substitute every named block in the template prompt.

    The seeded prompt uses bare {verified_data}, {ranked_findings},
    {citations_cache}, {team_activity}, {validation_summary} block
    placeholders. Inline {{verified_data.field}} references (carried
    over from earlier drafts) are also resolved against the
    verified_data dict so the substitution is forward-compatible.
    """
    vd_lines = "\n".join(
        f"  - {k}: {_fmt_value(v)}"
        for k, v in sorted(verified_data.items()))
    rf_lines = "\n".join(
        f"  {i + 1}. {f.get('title')} "
        f"({f.get('nugget_strength', 'LOW')}) — "
        f"{f.get('finding', '')}"
        for i, f in enumerate(ranked_findings or []))
    cit_lines = "\n".join(
        f"  - {cid}: "
        + (f"{c.get('formatted')}"
           if c.get("verification_status") == "verified"
           else f"[{c.get('verification_status', 'not_found')}]")
        for cid, c in (citations or {}).items())
    ta_lines = "\n".join(
        f"  - {k}: {v}"
        for k, v in sorted((team_activity or {}).items()))
    vs_lines = json.dumps(validation_summary or {}, indent=2,
                           default=str)

    prompt = template_prompt
    prompt = prompt.replace("{verified_data}", vd_lines or "(empty)")
    prompt = prompt.replace("{ranked_findings}",
                              rf_lines or "(no findings staged)")
    prompt = prompt.replace("{citations_cache}",
                              cit_lines or "(no citations sourced)")
    prompt = prompt.replace("{team_activity}",
                              ta_lines or "(no activity recorded)")
    prompt = prompt.replace("{validation_summary}",
                              vs_lines or "(no validation run)")

    def repl(m: re.Match) -> str:
        field = m.group(1)
        if field not in verified_data:
            return f"[DATA REQUIRED — {field}]"
        return _fmt_value(verified_data[field])
    prompt = _LEGACY_PLACEHOLDER_RE.sub(repl, prompt)
    return prompt


# ── STEP 5 — post-generation checks ──────────────────────────────────────────


_NUMBER_IN_DRAFT_RE = re.compile(
    r"(?<![A-Za-z0-9])(-?\d+\.\d+)(?!\d)")


def post_check_numbers(
    draft: str, verified_data: dict[str, Any],
) -> list[dict]:
    if not draft:
        return []
    verified_numerics = [
        float(v) for v in verified_data.values()
        if isinstance(v, (int, float))
        and not (isinstance(v, float)
                 and (math.isnan(v) or math.isinf(v)))]
    flagged: list[dict] = []
    for m in _NUMBER_IN_DRAFT_RE.finditer(draft):
        try:
            v = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if any(_within_tolerance(v, w, "ratio")
                or _within_tolerance(v, w, "percent")
                for w in verified_numerics):
            continue
        flagged.append({"value": v, "position": m.start()})
    return flagged


_INLINE_CITATION_RE = re.compile(
    r"\(([A-Z][A-Za-z\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z\-]+)?"
    r"(?:\s+et al\.)?),\s*(\d{4})\)")


def post_check_citations(
    draft: str, citations: dict,
) -> tuple[list[str], list[str]]:
    """Returns (inline_only, references_only). inline_only =
    citations that appear inline but have no verified citations_cache
    entry. references_only = verified entries with no inline
    citation."""
    inline_keys: set[tuple[str, str]] = set()
    for m in _INLINE_CITATION_RE.finditer(draft or ""):
        inline_keys.add((m.group(1).lower(), m.group(2)))
    ref_keys: set[tuple[str, str]] = set()
    for c in (citations or {}).values():
        if c.get("verification_status") not in CITATION_VERIFIED_STATES:
            continue
        author = (c.get("author") or "").strip().lower()
        surname = author.split(",")[0].strip() if author else ""
        year = str(c.get("year") or "")
        if surname and year:
            ref_keys.add((surname, year))
    inline_only = [
        f"({k[0]}, {k[1]})" for k in inline_keys - ref_keys]
    refs_only = [
        f"({k[0]}, {k[1]})" for k in ref_keys - inline_keys]
    return inline_only, refs_only


# ── Word-count enforcement ───────────────────────────────────────────────────


_SECTION_BUDGETS = {1: 250, 2: 300, 3: 150, 4: 125}
_TOTAL_BUDGET = 825


_SECTION_HEADER_RE = re.compile(
    r"(?m)^#{1,3}\s*(?:SECTION\s+)?(\d)\.?", re.IGNORECASE)


def split_by_section(draft: str) -> dict[int, str]:
    out: dict[int, str] = {0: ""}
    cur = 0
    pos = 0
    for m in _SECTION_HEADER_RE.finditer(draft or ""):
        out[cur] = (out.get(cur, "")
                    + (draft[pos:m.start()] if draft else ""))
        try:
            cur = int(m.group(1))
        except ValueError:
            continue
        out.setdefault(cur, "")
        pos = m.start()
    if draft:
        out[cur] = out.get(cur, "") + draft[pos:]
    return out


def word_count_report(draft: str) -> dict[str, Any]:
    sections = split_by_section(draft)
    per_section: dict[int, dict] = {}
    total = 0
    for sec_num, budget in _SECTION_BUDGETS.items():
        text = sections.get(sec_num) or ""
        count = len(text.split())
        total += count
        status = "green"
        if count > budget * 1.10:
            status = "red"
        elif count > budget:
            status = "amber"
        per_section[sec_num] = {
            "words": count, "budget": budget, "status": status,
        }
    total_status = "green"
    if total > _TOTAL_BUDGET * 1.10:
        total_status = "red"
    elif total > _TOTAL_BUDGET:
        total_status = "amber"
    return {
        "per_section": per_section,
        "total": {"words": total, "budget": _TOTAL_BUDGET,
                  "status": total_status},
    }


# ── Persistence helpers ──────────────────────────────────────────────────────


async def persist_citations(
    citations: dict[str, dict],
    generation_id: int | None = None,
) -> list[int]:
    """Writes each citation row to citations_cache. Returns the
    inserted row ids so the caller can persist citations_cache_ids
    on report_generations.

    Includes the new (May 23 2026) alternatives column populated from
    the 3-pass search. The reviewer_email / reviewed_at / review_action
    columns are left NULL on initial insert — they get filled by
    apply_citation_review() when Bob reviews each citation."""
    ids: list[int] = []
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as s:
            for cid, c in (citations or {}).items():
                alts = c.get("alternatives") or []
                row = await s.execute(text(
                    "INSERT INTO citations_cache "
                    "(generation_id, concept_id, author, year, title, "
                    " journal_or_institution, volume_issue_pages, "
                    " url, verification_status, search_query_used, "
                    " alternatives) "
                    "VALUES (:g, :c, :au, :y, :t, :j, :v, :u, :s, :q, "
                    " CAST(:alts AS JSONB)) "
                    "RETURNING id"
                ), {
                    "g":    generation_id,
                    "c":    cid,
                    "au":   c.get("author"),
                    "y":    c.get("year"),
                    "t":    c.get("title"),
                    "j":    c.get("journal_or_institution"),
                    "v":    c.get("volume_issue_pages"),
                    "u":    c.get("url"),
                    "s":    c.get("verification_status"),
                    "q":    c.get("search_query_used"),
                    "alts": json.dumps(alts) if alts else None,
                })
                new_id = row.scalar()
                if new_id is not None:
                    ids.append(int(new_id))
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("persist_citations_failed", error=str(exc))
    return ids


# ── Reviewer-action helpers ──────────────────────────────────────────────────


async def get_citations_for_generation(
    generation_id: int,
) -> list[dict[str, Any]]:
    """Returns every citation row for a generation_id, ordered by
    concept_id. Fail-open: a database error returns []."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as s:
            rows = await s.execute(text(
                "SELECT id, concept_id, author, year, title, "
                " journal_or_institution, volume_issue_pages, url, "
                " verification_status, search_query_used, "
                " alternatives, reviewer_email, reviewed_at, "
                " review_action "
                "FROM citations_cache "
                "WHERE generation_id = :g "
                "ORDER BY concept_id"
            ), {"g": int(generation_id)})
            out: list[dict[str, Any]] = []
            for r in rows.fetchall():
                alts = r[10]
                if isinstance(alts, str):
                    try:
                        alts = json.loads(alts)
                    except json.JSONDecodeError:
                        alts = []
                out.append({
                    "id":                     int(r[0]),
                    "concept_id":             r[1],
                    "author":                 r[2],
                    "year":                   r[3],
                    "title":                  r[4],
                    "journal_or_institution": r[5],
                    "volume_issue_pages":     r[6],
                    "url":                    r[7],
                    "verification_status":    r[8],
                    "search_query_used":      r[9],
                    "alternatives":           alts or [],
                    "reviewer_email":         r[11],
                    "reviewed_at":            (r[12].isoformat()
                                                if r[12] else None),
                    "review_action":          r[13],
                    "formatted":              _format_citation({
                        "author": r[2], "year": r[3], "title": r[4],
                        "journal_or_institution": r[5],
                        "volume_issue_pages": r[6], "url": r[7],
                    }),
                })
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("get_citations_failed", error=str(exc),
                    generation_id=generation_id)
        return []


async def apply_citation_review(
    citation_id: int,
    action: str,
    reviewer_email: str,
    *,
    selected_alternative: dict[str, Any] | None = None,
    manual_citation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Apply a reviewer action to a citations_cache row, transitioning
    its state per the 7-state machine.

      accept_untrusted   — pending_review → human_verified
                            (keeps the existing search result)
      select_alternative — any → search_selected
                            (caller supplies the picked entry as
                            selected_alternative; the row's primary
                            citation fields are overwritten)
      reject             — any → rejected
                            (clears the citation fields; the inline
                            marker will fall back to the concept's
                            description)
      manual_add         — any → manually_added
                            (caller supplies manual_citation; the
                            row's fields are overwritten)

    Returns the updated row as a dict, or None on a database error /
    unknown action / unknown citation_id.
    """
    if action not in CITATION_REVIEW_ACTIONS:
        log.warning("citation_review_unknown_action",
                    citation_id=citation_id, action=action)
        return None

    # Decide the new state + which fields to overwrite based on action.
    overwrite: dict[str, Any] = {}
    if action == "accept_untrusted":
        new_state = CITATION_STATE_HUMAN_VERIFIED
    elif action == "select_alternative":
        if not selected_alternative:
            log.warning("citation_review_select_alternative_missing_payload",
                        citation_id=citation_id)
            return None
        new_state = CITATION_STATE_SEARCH_SELECTED
        overwrite = {
            "author":                 selected_alternative.get("author"),
            "year":                   selected_alternative.get("year"),
            "title":                  selected_alternative.get("title"),
            "journal_or_institution":
                selected_alternative.get("journal_or_institution"),
            "volume_issue_pages":
                selected_alternative.get("volume_issue_pages"),
            "url":                    selected_alternative.get("url"),
        }
    elif action == "reject":
        new_state = CITATION_STATE_REJECTED
        # Clear the citation fields — the references list will skip
        # this concept entirely.
        overwrite = {
            "author":                 None,
            "year":                   None,
            "title":                  None,
            "journal_or_institution": None,
            "volume_issue_pages":     None,
            "url":                    None,
        }
    elif action == "manual_add":
        if not manual_citation:
            log.warning("citation_review_manual_add_missing_payload",
                        citation_id=citation_id)
            return None
        new_state = CITATION_STATE_MANUALLY_ADDED
        overwrite = {
            "author":                 manual_citation.get("author"),
            "year":                   manual_citation.get("year"),
            "title":                  manual_citation.get("title"),
            "journal_or_institution":
                manual_citation.get("journal_or_institution"),
            "volume_issue_pages":
                manual_citation.get("volume_issue_pages"),
            "url":                    manual_citation.get("url"),
        }
    else:  # pragma: no cover — covered by the membership check above
        return None

    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            # Build the UPDATE dynamically so we only touch the
            # overwrite columns for the actions that need them.
            set_clauses = [
                "verification_status = :state",
                "reviewer_email     = :rev",
                "reviewed_at        = now()",
                "review_action      = :act",
            ]
            params: dict[str, Any] = {
                "state": new_state,
                "rev":   reviewer_email,
                "act":   action,
                "id":    int(citation_id),
            }
            for col, val in overwrite.items():
                set_clauses.append(f"{col} = :{col}")
                params[col] = val

            sql = (
                "UPDATE citations_cache SET "
                + ", ".join(set_clauses)
                + " WHERE id = :id RETURNING id"
            )
            res = await s.execute(text(sql), params)
            row = res.fetchone()
            if not row:
                return None
            await s.commit()
            # Read back via the canonical accessor so the shape
            # matches every other read path.
            rows = await s.execute(text(
                "SELECT id, concept_id, author, year, title, "
                " journal_or_institution, volume_issue_pages, url, "
                " verification_status, search_query_used, "
                " alternatives, reviewer_email, reviewed_at, "
                " review_action, generation_id "
                "FROM citations_cache WHERE id = :id"
            ), {"id": int(citation_id)})
            r = rows.fetchone()
            if not r:
                return None
            alts = r[10]
            if isinstance(alts, str):
                try:
                    alts = json.loads(alts)
                except json.JSONDecodeError:
                    alts = []
            return {
                "id":                     int(r[0]),
                "concept_id":             r[1],
                "author":                 r[2],
                "year":                   r[3],
                "title":                  r[4],
                "journal_or_institution": r[5],
                "volume_issue_pages":     r[6],
                "url":                    r[7],
                "verification_status":    r[8],
                "search_query_used":      r[9],
                "alternatives":           alts or [],
                "reviewer_email":         r[11],
                "reviewed_at":            (r[12].isoformat()
                                            if r[12] else None),
                "review_action":          r[13],
                "generation_id":          (int(r[14])
                                            if r[14] is not None else None),
                "formatted":              _format_citation({
                    "author": r[2], "year": r[3], "title": r[4],
                    "journal_or_institution": r[5],
                    "volume_issue_pages": r[6], "url": r[7],
                }),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("apply_citation_review_failed", error=str(exc),
                    citation_id=citation_id, action=action)
        return None
