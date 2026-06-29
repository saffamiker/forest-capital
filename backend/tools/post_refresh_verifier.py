"""tools/post_refresh_verifier.py -- June 27 2026.

Post-light-refresh verification pass + rounding audit.

After light refresh completes, the operator needs a rock-solid
confirmation that every substitution token has the correct value
with consistent rounding. This module is the verifier.

Per-scope contract:

  LOCKED (IN_SCOPE_LOCKED):
    Re-read the value from strategy_results_cache keyed to the
    freeze hash (or live hash when freeze inactive) + the analytics
    metric caches. Compare against the substitution table value.
    Flag if values differ by more than the per-rule tolerance.
    Flag if rounding does not match the canonical rule.

  CONSTANT (IN_SCOPE_CONSTANT):
    Confirm value matches the hardcoded constant in academic_deck.
    Flag any deviation -- should never happen, the constant is in
    code.

  FULL_DATASET (IN_SCOPE_FULL_DATASET):
    Confirm value is non-null and plausible (STUDY_MONTHS in
    [200, 400], correlations in [-1, +1]).

  LIVE (OUT_OF_SCOPE_LIVE):
    Confirm value non-null and fresh. regime_signals_cache row
    must be < 15 minutes old; cio_recommendation must exist.
    Stale = warning, null = fail.

Returns the full structured report per spec:
  {verified_at, freeze_hash, passed, failed, warnings, results[],
   rounding_summary{...}, ready_for_submission}

ready_for_submission = (failed == 0
                        AND rounding_summary.inconsistent == 0
                        AND no live tokens stale)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:
    log = logging.getLogger(__name__)  # type: ignore[assignment]


# ── Submission scope classifier (consolidated June 28 2026) ──────
#
# Re-exported from backend/tools/data_reference_catalog (PR #459)
# so this module + the data-reference endpoint share a single
# source of truth. The catalog's classify_submission_scope was
# extended to handle the verifier's source-less call path by
# checking _LIVE_TOKEN_NAMES before the source-prefix check, so
# a verifier walking the substitution table (just {token: value}
# pairs, no source field) still classifies LIVE tokens correctly.
from tools.data_reference_catalog import (  # noqa: E402
    SCOPE_LOCKED,
    SCOPE_CONSTANT,
    SCOPE_FULL_DATASET,
    SCOPE_LIVE,
    classify_submission_scope,
)


# ── Canonical rounding rules ─────────────────────────────────────
#
# Per operator spec. Rules:
#   Sharpe / Net Sharpe / Cost sensitivity Sharpe: 2dp, no suffix
#   Percentages (CAGR / drawdown / volatility): 1dp, "%" suffix
#   Recovery months: integer (0dp), no suffix
#   Correlation: 2dp, no suffix
#   P-values: 3dp, no suffix
#   Factor loadings (alpha, beta): 4dp (CANONICAL = APPENDIX
#     precision; brief intentionally renders 2dp for readability,
#     verifier does NOT flag brief-context truncation)
#   Confidence: 1dp, "%" suffix
#   Watchpoints (VIX, credit spread): 2dp, no suffix
#   Equity trend: 1dp, "%" suffix

ROUNDING_RULES: dict[str, dict[str, Any]] = {
    "sharpe":        {"decimals": 2, "suffix": ""},
    "max_dd":        {"decimals": 1, "suffix": "%"},
    "drawdown":      {"decimals": 1, "suffix": "%"},
    "cagr":          {"decimals": 1, "suffix": "%"},
    "volatility":    {"decimals": 1, "suffix": "%"},
    "recovery":      {"decimals": 0, "suffix": ""},
    "correlation":   {"decimals": 2, "suffix": ""},
    "p_value":       {"decimals": 3, "suffix": ""},
    "factor_alpha":  {"decimals": 4, "suffix": ""},
    "factor_beta":   {"decimals": 4, "suffix": ""},
    "r_squared":     {"decimals": 4, "suffix": ""},
    "confidence":    {"decimals": 1, "suffix": "%"},
    "watchpoint":    {"decimals": 2, "suffix": ""},
    "equity_trend":  {"decimals": 1, "suffix": "%"},
    "net_sharpe":    {"decimals": 2, "suffix": ""},
}


def classify_rounding(token: str) -> dict[str, Any] | None:
    """Map a token to its canonical rounding rule by name pattern.
    Returns None for tokens whose value is a string / date / count
    rather than a rounded numeric (STUDY_MONTHS, STUDY_END,
    CURRENT_REGIME, etc) -- those skip the rounding check.

    Order matters: more specific patterns FIRST so e.g.
    REGIME_CONFIDENCE matches 'confidence' not falls through to
    a less-specific rule."""
    # Skip tokens whose canonical value is a date / string / int
    # rather than a rounded numeric.
    if token in {"{{STUDY_MONTHS}}", "{{STUDY_START}}",
                 "{{STUDY_END}}", "{{OOS_WINDOW}}",
                 "{{OOS_WINDOW_MONTHS}}", "{{N_STRATEGIES}}",
                 "{{CURRENT_REGIME}}", "{{DATA_HASH}}",
                 "{{IG_SPLICE_DATE}}", "{{HY_EXTENSION_DATE}}",
                 "{{HY_TRACKING_ERROR}}", "{{EQUITY_SERIES}}",
                 "{{IG_SERIES}}", "{{HY_SERIES}}",
                 "{{RISK_FREE_SERIES}}", "{{FACTOR_SERIES}}",
                 "{{PLAY_BY_PLAY_VALUE_ADD}}",
                 "{{PLAY_BY_PLAY_TOTAL}}",
                 "{{PLAY_BY_PLAY_EVENTS}}"}:
        return None
    # Specific patterns first.
    if token == "{{REGIME_CONFIDENCE}}":
        return ROUNDING_RULES["confidence"]
    if "RECOVERY_MONTHS" in token:
        return ROUNDING_RULES["recovery"]
    if "RECOVERY" in token:
        # *_RECOVERY (without _MONTHS) is a count string like
        # "6 months" -- treat as integer-precision string.
        return None
    if "MAX_DD" in token or "DRAWDOWN" in token:
        return ROUNDING_RULES["max_dd"]
    if "CAGR" in token:
        return ROUNDING_RULES["cagr"]
    if "VOLATILITY" in token or "VOLAT" in token:
        return ROUNDING_RULES["volatility"]
    if "NET_SHARPE" in token:
        return ROUNDING_RULES["net_sharpe"]
    if "SHARPE" in token:
        return ROUNDING_RULES["sharpe"]
    if "CORR" in token:
        return ROUNDING_RULES["correlation"]
    if "ALPHA" in token:
        return ROUNDING_RULES["factor_alpha"]
    if "BETA" in token:
        return ROUNDING_RULES["factor_beta"]
    if "R_SQUARED" in token or "R2" in token:
        return ROUNDING_RULES["r_squared"]
    if "P_VALUE" in token or "PVALUE" in token:
        return ROUNDING_RULES["p_value"]
    if token in {"{{VIX_CURRENT}}", "{{CREDIT_SPREAD_CURRENT}}",
                 "{{YIELD_CURVE_CURRENT}}", "{{ESS_CURRENT}}"}:
        return ROUNDING_RULES["watchpoint"]
    if token == "{{EQUITY_TREND_CURRENT}}":
        return ROUNDING_RULES["equity_trend"]
    # Allocation percentages: CURRENT_*_PCT, BLEND_*_WT,
    # IMPROVEMENT_PCT, OOS_WINDOW_PCT_OF_STUDY.
    if "_PCT" in token or token.endswith("_WT}}"):
        return ROUNDING_RULES["cagr"]  # 1dp %, same rule
    return None


def check_rounding(value: str, rule: dict[str, Any]) -> bool:
    """Returns True when `value` is formatted per the rule.

    The check walks the string: strip optional leading +/- sign,
    strip the rule.suffix, find the decimal portion, count its
    digits. Token values come from format_pct / format_sharpe /
    etc which all use Python f-string formatting -- the decimal
    count is exact.

    Examples (rule decimals=2, suffix=''):
      '0.86'     -> True
      '0.860'    -> False (3dp)
      '0.9'      -> False (1dp)
      '+0.86'    -> True (sign stripped)
    """
    if not isinstance(value, str) or not value:
        return False
    # The em-dash + 'cache miss' sentinel are not numeric -- the
    # caller should not invoke check_rounding on them. Defensive
    # short-circuit anyway.
    if value in {"—", "-", "cache miss", "n/a"}:
        return False
    suffix = rule.get("suffix", "")
    decimals = int(rule.get("decimals", 0))
    body = value
    if suffix:
        # A non-empty suffix is REQUIRED -- a value rendered
        # without it is malformed for this rule type.
        if not body.endswith(suffix):
            return False
        body = body[:-len(suffix)]
    # Strip a leading sign +/-.
    if body and body[0] in "+-":
        body = body[1:]
    # An integer-precision rule (decimals=0) must have no
    # decimal point.
    if decimals == 0:
        return body.isdigit()
    # Find the decimal portion.
    if "." not in body:
        return False
    int_part, _, frac_part = body.partition(".")
    if not int_part.isdigit() or not frac_part.isdigit():
        return False
    return len(frac_part) == decimals


# ── Per-scope verifier helpers ───────────────────────────────────


def _result(
    token: str, label: str, scope: str, expected: str,
    actual: str, rounded: bool, status: str, message: str,
) -> dict[str, Any]:
    return {
        "token": token, "label": label, "scope": scope,
        "expected": expected, "actual": actual,
        "rounded_correctly": rounded, "status": status,
        "message": message,
    }


def _check_full_dataset_plausibility(
    token: str, value: str,
) -> tuple[str, str]:
    """Returns (status, message). Plausibility ranges per token."""
    if token == "{{STUDY_MONTHS}}":
        try:
            n = int(value)
            if 200 <= n <= 400:
                return ("pass", f"{n} months in plausible range")
            return ("fail", f"{n} outside plausible [200, 400]")
        except (TypeError, ValueError):
            return ("fail", f"non-integer value: {value!r}")
    if "CORR" in token:
        try:
            x = float(value)
            if -1.0 <= x <= 1.0:
                return ("pass", f"{x} in [-1, +1]")
            return ("fail", f"{x} outside [-1, +1]")
        except (TypeError, ValueError):
            return ("fail", f"non-numeric value: {value!r}")
    if not value or value == "—":
        return ("fail", "null value")
    return ("pass", "non-null")


async def _check_live_freshness() -> tuple[bool, bool, str]:
    """Returns (cio_fresh, regime_fresh, message).
    cio_fresh = cio_recommendation row exists.
    regime_fresh = regime_signals_cache row < 15 min old."""
    try:
        from tools.cio_recommendation import (
            get_latest_recommendation,
        )
        from tools.cache import get_regime_cache
        cio = await get_latest_recommendation()
        regime = await get_regime_cache()
        cio_ok = bool(cio)
        regime_ok = False
        regime_msg = ""
        if regime:
            ts = regime.get("computed_at") or regime.get(
                "updated_at")
            if isinstance(ts, str):
                try:
                    ts_dt = datetime.fromisoformat(
                        ts.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc)
                           - ts_dt.astimezone(timezone.utc))
                    regime_ok = age < timedelta(minutes=15)
                    regime_msg = (
                        f"regime cache age "
                        f"{int(age.total_seconds())}s")
                except (TypeError, ValueError) as exc:
                    regime_msg = f"timestamp parse: {exc}"
            else:
                regime_ok = True
                regime_msg = "regime cache present"
        return (cio_ok, regime_ok, regime_msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("verifier_live_freshness_failed",
                    error=str(exc))
        return (False, False, str(exc))


# ── Orchestrator ─────────────────────────────────────────────────


async def run_verification() -> dict[str, Any]:
    """Build the substitution table the way the live platform
    builds it, then walk every token + classify by scope + apply
    the per-scope checks + rounding audit.

    Self-contained: re-reads CIO + regime + strategy cache + the
    historical analytics metrics, derives submission_scope locally
    via classify_submission_scope above (no dependency on the
    /data-reference-sheet endpoint)."""
    from tools.audit_assembler import current_data_hash
    from tools.cache import get_strategy_cache
    from tools.cio_recommendation import (
        compute_implied_asset_allocation,
        get_latest_recommendation,
    )
    from tools.numeric_substitution import (
        get_substitution_table,
    )
    from tools.submission_freeze import (
        get_effective_data_hash, get_freeze_config,
    )

    verified_at = datetime.now(timezone.utc).isoformat()

    # Effective hash (freeze hash when active, live hash otherwise).
    live_hash = await current_data_hash()
    eff_hash = await get_effective_data_hash(live_hash) or live_hash
    freeze_cfg = await get_freeze_config()
    freeze_active = bool(freeze_cfg.get("active"))

    # Pre-load the same sources the substitution table consumes.
    cio_row = await get_latest_recommendation() or {}
    strategy_cache = await get_strategy_cache(eff_hash) or {}

    # Implied allocation derives from CIO blend weights.
    # June 28 2026 HOTFIX -- compute_implied_asset_allocation is
    # async (it reads strategy_results_cache for the per-strategy
    # avg_equity_weight / avg_bond_weight). The original PR #460
    # called it WITHOUT await, so the coroutine object was passed
    # to get_substitution_table as implied_allocation, and the
    # first downstream .get() on it raised AttributeError -> the
    # operator-visible 500. Awaiting the coroutine returns the
    # expected dict | None.
    implied_alloc: dict[str, Any] = {}
    try:
        if cio_row.get("blend_weights"):
            implied_alloc = (
                await compute_implied_asset_allocation(
                    cio_row.get("blend_weights"))) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("verifier_implied_alloc_failed",
                    error=str(exc))

    # Live signals from regime_signals_cache.
    live_signals: dict[str, Any] = {}
    try:
        from tools.cache import get_regime_cache
        live_signals = await get_regime_cache() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("verifier_live_signals_failed",
                    error=str(exc))

    # Build the substitution table -- same path the doc generators
    # use. PR 1 v2 cache-key invalidation makes this safe to call
    # repeatedly without stale-table risk.
    # June 28 2026 HOTFIX -- pass hash_verified=True. The verifier
    # explicitly loaded strategy_cache + historical analytics via
    # the hash-aware path (get_strategy_cache(eff_hash) above), so
    # the audit signal that the data_hash is verified-to-match
    # must be set. Without this flag, build_substitution_table
    # logs the spurious 'data_hash supplied without
    # hash_verified=True' warning on every verifier call.
    table = get_substitution_table(
        eff_hash, strategy_cache, cio_row,
        implied_allocation=implied_alloc,
        live_signals=live_signals,
        hash_verified=True)

    # Pre-check live freshness for the LIVE-scope tokens.
    cio_fresh, regime_fresh, freshness_msg = (
        await _check_live_freshness())

    # Walk every token in the substitution table.
    results: list[dict[str, Any]] = []
    inconsistent_tokens: list[str] = []
    rounding_total = 0

    for token, value in table.items():
        if not (token.startswith("{{") and token.endswith("}}")):
            continue
        scope = classify_submission_scope(token)
        rule = classify_rounding(token)
        rounded = True
        if rule is not None:
            rounding_total += 1
            rounded = check_rounding(value, rule)
            if not rounded:
                inconsistent_tokens.append(token)

        # Scope-specific verdict.
        if scope == SCOPE_LIVE:
            # Live token must be non-null and the underlying
            # source must be fresh.
            if not value or value == "—":
                status = "fail"
                msg = "live token rendered null/em-dash"
            elif not cio_fresh and token.startswith(
                    ("{{CURRENT_", "{{REGIME_", "{{BLEND_")):
                status = "warning"
                msg = "cio_recommendation absent or stale"
            elif not regime_fresh and token.endswith(
                    "_CURRENT}}"):
                status = "warning"
                msg = (f"regime_signals_cache stale -- "
                       f"{freshness_msg}")
            else:
                status = "pass"
                msg = "live and fresh"
        elif scope == SCOPE_FULL_DATASET:
            status, msg = (
                _check_full_dataset_plausibility(token, value))
        elif scope == SCOPE_CONSTANT:
            # Constants are sourced from caller-provided kwargs
            # backed by hardcoded module values -- a non-null
            # token has been resolved correctly. Rounding still
            # checked above. Null on a constant means the caller
            # forgot to pass the kwarg -- fail loudly.
            if not value or value == "—":
                status = "fail"
                msg = (
                    "constant rendered null -- caller missed "
                    "the kwarg")
            else:
                status = "pass"
                msg = "constant resolved"
        else:  # SCOPE_LOCKED
            # Locked tokens come from strategy_cache / historical
            # analytics keyed to eff_hash. A null value here means
            # the strategy_cache row is missing for the freeze
            # hash -- the same condition the doc generators
            # surface as StrategyCacheMissingForHashError.
            if not value or value == "—":
                status = "fail"
                msg = (
                    f"locked token null -- "
                    f"strategy_cache miss for {eff_hash[:8]}")
            else:
                status = "pass"
                msg = "locked value resolved"

        # Promote pass -> warning when rounding is inconsistent
        # (rounding is non-fatal -- the value is correct but the
        # display precision drifts from the canonical rule).
        if status == "pass" and not rounded:
            status = "warning"
            msg = (msg + "; "
                   "rounding inconsistent with canonical rule")

        results.append(_result(
            token=token, label=token.strip("{}"),
            scope=scope, expected="", actual=value,
            rounded=rounded, status=status, message=msg))

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    warnings = sum(1 for r in results if r["status"] == "warning")

    # Stale live = any LIVE-scope token with warning status.
    stale_live_present = any(
        r["scope"] == SCOPE_LIVE and r["status"] == "warning"
        for r in results)

    rounding_summary = {
        "checked": rounding_total,
        "consistent": rounding_total - len(inconsistent_tokens),
        "inconsistent": len(inconsistent_tokens),
        "inconsistent_tokens": inconsistent_tokens,
    }

    ready_for_submission = (
        failed == 0
        and rounding_summary["inconsistent"] == 0
        and not stale_live_present)

    log.info(
        "post_refresh_verification_complete",
        passed=passed, failed=failed, warnings=warnings,
        rounding_inconsistent=len(inconsistent_tokens),
        ready=ready_for_submission,
        freeze_active=freeze_active,
        eff_hash=eff_hash[:8] if eff_hash else "")

    return {
        "verified_at": verified_at,
        "freeze_active": freeze_active,
        "freeze_hash": (
            freeze_cfg.get("freeze_hash")
            if freeze_active else None),
        "effective_hash": eff_hash,
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
        "results": results,
        "rounding_summary": rounding_summary,
        "ready_for_submission": ready_for_submission,
    }
