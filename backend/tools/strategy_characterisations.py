"""tools/strategy_characterisations.py — per-strategy pre-computed profile.

Item 9 (May 22 2026). Backs the migration 029 strategy_characterisations
table. The refresh fires once when the strategy cache is written and
upserts ten rows (one per strategy). The endpoint, the Dashboard
strategy cards, the Portfolio Profile panel, and the agent context
injectors all read from this table.

Two compute paths per strategy:
  1. portfolio_characteristics — DETERMINISTIC. Derived from the
     backtest's weight_schedule + true_turnover + monthly_returns.
     Pure NumPy / Python; no AI involvement.
  2. construction_summary + behavioural_profile + regime_sensitivity +
     behavioural_tag — AI-GENERATED. One Claude Sonnet call per
     strategy, fed the strategy's metadata + portfolio_characteristics
     + factor loadings + regime-conditional performance + a snapshot
     of the strategy's headline numbers. The model returns a single
     JSON object the writer parses.

FAIL-OPEN end to end. A compute failure for one strategy logs and
proceeds to the next; a parse failure on the model output falls back
to a deterministic stub so the row still lands. Mirrors the
precomputed_analytics fail-open contract.

TEST ENVIRONMENT. ENVIRONMENT=test substitutes a deterministic mock
characterisation per strategy so the pytest suite never hits
Anthropic. Mirrors research_engine._mock_digest pattern.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── Persistence ───────────────────────────────────────────────────────────────


async def get_characterisation(
    strategy_id: str, data_hash: str,
) -> dict[str, Any] | None:
    """Returns the row for (strategy_id, data_hash) or None. Mirrors
    precomputed_analytics.get_metric — fail-open on DB unavailability."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT construction_summary, portfolio_characteristics, "
                " behavioural_profile, regime_sensitivity, "
                " behavioural_tag, computed_at "
                "FROM strategy_characterisations "
                "WHERE strategy_id = :s AND data_hash = :h LIMIT 1"
            ), {"s": strategy_id, "h": data_hash})
            found = row.fetchone()
            if not found:
                return None
            return _row_to_dict(strategy_id, found)
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_read_failed",
                    strategy_id=strategy_id, error=str(exc))
        return None


async def get_latest_characterisation(
    strategy_id: str,
) -> dict[str, Any] | None:
    """Returns the most-recently-written row for a strategy_id,
    regardless of data_hash. Used as the cold-deploy fallback when
    the current data_hash has not been refreshed yet — serving the
    last good characterisation is better than serving nothing."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT construction_summary, portfolio_characteristics, "
                " behavioural_profile, regime_sensitivity, "
                " behavioural_tag, computed_at, data_hash "
                "FROM strategy_characterisations "
                "WHERE strategy_id = :s "
                "ORDER BY computed_at DESC LIMIT 1"
            ), {"s": strategy_id})
            found = row.fetchone()
            if not found:
                return None
            out = _row_to_dict(strategy_id, found[:-1])
            out["_data_hash"] = found[-1]
            out["_stale"] = True
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_latest_failed",
                    strategy_id=strategy_id, error=str(exc))
        return None


async def get_all_characterisations(
    data_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Returns every characterisation for the given data_hash. When
    data_hash is None or no rows match, falls back to the most-recent
    characterisation per strategy (regardless of hash) so the dashboard
    and the Portfolio Profile panel always have something to render."""
    if data_hash:
        try:
            from sqlalchemy import text
            from database import AsyncSessionLocal
            if AsyncSessionLocal is not None:
                async with AsyncSessionLocal() as session:
                    rows = await session.execute(text(
                        "SELECT strategy_id, construction_summary, "
                        " portfolio_characteristics, behavioural_profile, "
                        " regime_sensitivity, behavioural_tag, "
                        " computed_at "
                        "FROM strategy_characterisations "
                        "WHERE data_hash = :h"
                    ), {"h": data_hash})
                    fetched = rows.fetchall()
                    if fetched:
                        return [_row_to_dict(r[0], r[1:]) for r in fetched]
        except Exception as exc:  # noqa: BLE001
            log.warning("strategy_characterisation_bulk_failed",
                        data_hash=data_hash[:8], error=str(exc))
    # Cold deploy / hash mismatch — pull the latest row per strategy.
    return await _get_latest_per_strategy()


async def _get_latest_per_strategy() -> list[dict[str, Any]]:
    """One row per strategy_id — the most recent computed_at for each.
    Falls back when no row matches the current data_hash."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                "SELECT DISTINCT ON (strategy_id) "
                " strategy_id, construction_summary, "
                " portfolio_characteristics, behavioural_profile, "
                " regime_sensitivity, behavioural_tag, "
                " computed_at, data_hash "
                "FROM strategy_characterisations "
                "ORDER BY strategy_id, computed_at DESC"))
            fetched = rows.fetchall()
            out: list[dict[str, Any]] = []
            for r in fetched:
                row = _row_to_dict(r[0], r[1:-1])
                row["_data_hash"] = r[-1]
                row["_stale"] = True
                out.append(row)
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_latest_bulk_failed",
                    error=str(exc))
        return []


def _row_to_dict(
    strategy_id: str, fields: "tuple[Any, ...]",
) -> dict[str, Any]:
    """Row-to-payload mapper. fields is the SELECT projection in
    schema order: construction_summary, portfolio_characteristics,
    behavioural_profile, regime_sensitivity, behavioural_tag,
    computed_at."""
    pc, bp = fields[1], fields[2]
    # asyncpg may return JSONB pre-parsed (dict) or as a string in some
    # configurations — accept both.
    if isinstance(pc, str):
        try:
            pc = json.loads(pc)
        except json.JSONDecodeError:
            pc = {}
    if isinstance(bp, str):
        try:
            bp = json.loads(bp)
        except json.JSONDecodeError:
            bp = {}
    return {
        "strategy_id":              strategy_id,
        "construction_summary":     fields[0] or "",
        "portfolio_characteristics": pc or {},
        "behavioural_profile":      bp or {},
        "regime_sensitivity":       fields[3] or "",
        "behavioural_tag":          fields[4] or "",
        "_computed_at":             (
            fields[5].isoformat() if fields[5] else None),
    }


async def upsert_characterisation(
    strategy_id: str,
    data_hash: str,
    *,
    construction_summary: str,
    portfolio_characteristics: dict[str, Any],
    behavioural_profile: dict[str, Any],
    regime_sensitivity: str,
    behavioural_tag: str,
) -> None:
    """Writes a row. Idempotent — ON CONFLICT on
    (strategy_id, data_hash) DO UPDATE."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "INSERT INTO strategy_characterisations "
                "(strategy_id, data_hash, construction_summary, "
                " portfolio_characteristics, behavioural_profile, "
                " regime_sensitivity, behavioural_tag) "
                "VALUES (:s, :h, :cs, :pc, :bp, :rs, :tag) "
                "ON CONFLICT (strategy_id, data_hash) DO UPDATE SET "
                " construction_summary = EXCLUDED.construction_summary, "
                " portfolio_characteristics = EXCLUDED.portfolio_characteristics, "
                " behavioural_profile = EXCLUDED.behavioural_profile, "
                " regime_sensitivity = EXCLUDED.regime_sensitivity, "
                " behavioural_tag = EXCLUDED.behavioural_tag, "
                " computed_at = now()"
            ), {
                "s": strategy_id, "h": data_hash,
                "cs": construction_summary,
                "pc": json.dumps(portfolio_characteristics),
                "bp": json.dumps(behavioural_profile),
                "rs": regime_sensitivity,
                "tag": behavioural_tag,
            })
            await session.commit()
        log.info("strategy_characterisation_written",
                 strategy_id=strategy_id, data_hash=data_hash[:8])
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_write_failed",
                    strategy_id=strategy_id, error=str(exc))


# ── Deterministic portfolio_characteristics ───────────────────────────────────


def compute_portfolio_characteristics(strategy_result: dict) -> dict[str, Any]:
    """Derived from the backtest's weight_schedule + true_turnover +
    monthly_returns. Pure compute; no AI.

    Concentration note: the spec called for "top 5 holdings as % of
    portfolio". This project's universe is three asset classes (equity,
    IG, HY), so top-5 is trivially 100%. We report the largest single
    holding instead — averaged across rebalances — which is the
    meaningful concentration measure on a 3-asset universe. The frontend
    surfaces this as 'Largest holding (avg)' so the metric label
    accurately reflects what is computed.

    Returns the same shape on every strategy so the JSONB column has a
    uniform schema:
      avg_holdings:       average number of non-zero holdings per rebalance
      avg_turnover_pct:   annualised one-way turnover (already in the
                          backtest result), converted to a percent
      avg_concentration:  average max-weight per rebalance, as a percent
      rebalance_frequency: 'buy and hold' | 'monthly' | 'quarterly'
                          | 'signal-driven'
    """
    schedule = strategy_result.get("weight_schedule") or []
    monthly_returns = strategy_result.get("monthly_returns") or []
    true_turnover = strategy_result.get("true_turnover") or 0.0

    if not schedule:
        return {
            "avg_holdings":         None,
            "avg_turnover_pct":     round(true_turnover * 100, 1),
            "avg_concentration":    None,
            "rebalance_frequency":  "unknown",
        }

    holdings_counts: list[int] = []
    concentrations: list[float] = []
    for entry in schedule:
        weights = entry.get("weights") or {}
        # Treat anything ≥ 0.1% as a held position; below that is
        # rounding noise that the backtester emits.
        nonzero = [v for v in weights.values()
                   if isinstance(v, (int, float)) and v >= 0.001]
        holdings_counts.append(len(nonzero))
        if nonzero:
            concentrations.append(max(nonzero))

    avg_holdings = (
        sum(holdings_counts) / len(holdings_counts)
        if holdings_counts else None)
    avg_concentration = (
        sum(concentrations) / len(concentrations)
        if concentrations else None)

    n_rebalances = len(schedule)
    n_months = len(monthly_returns)
    if n_months < 12 or n_rebalances <= 1:
        rebalance_frequency = "buy and hold"
    else:
        per_year = n_rebalances / (n_months / 12.0)
        if per_year >= 9:
            rebalance_frequency = "monthly"
        elif per_year >= 3:
            rebalance_frequency = "quarterly"
        else:
            rebalance_frequency = "signal-driven"

    return {
        "avg_holdings":
            round(avg_holdings, 1) if avg_holdings is not None else None,
        "avg_turnover_pct":     round(true_turnover * 100, 1),
        "avg_concentration":
            round(avg_concentration * 100, 1)
            if avg_concentration is not None else None,
        "rebalance_frequency":  rebalance_frequency,
    }


def derive_primary_risk_factor(
    factor_row: dict | None,
) -> str:
    """The Carhart factor with the largest absolute beta on this
    strategy. Returns the factor's display name, or 'Market exposure'
    as a graceful default when the regression row is missing or
    degenerate."""
    if not factor_row:
        return "Market exposure"
    candidates = [
        ("Market (MKT-RF)", factor_row.get("mkt_rf")),
        ("Size (SMB)",      factor_row.get("smb")),
        ("Value (HML)",     factor_row.get("hml")),
        ("Momentum (MOM)",  factor_row.get("mom")),
    ]
    valid = [(label, abs(float(b))) for label, b in candidates
             if isinstance(b, (int, float))]
    if not valid:
        return "Market exposure"
    return max(valid, key=lambda x: x[1])[0]


# ── AI generation ─────────────────────────────────────────────────────────────


def _is_test_env() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() == "test"


_SYSTEM_PROMPT = """You are a quantitative investment analyst writing concise,
plain-English summaries of portfolio strategies. You will be given one
strategy at a time — its construction rules, its current backtest
characteristics, its dominant Carhart factor exposure, and how it
performed pre- vs post-2022 — and you produce a single structured
JSON object.

OUTPUT CONTRACT (return ONLY a JSON object, no prose around it, no
markdown fences):
{
  "construction_summary": "One paragraph (60-90 words). Plain English. What signal drives the strategy, what it buys/avoids, rebalance frequency, and what makes it distinctive vs the other nine strategies. Specific, not generic.",
  "behavioural_profile": {
    "outperforms_when": "One short sentence — the conditions under which this strategy beats the benchmark. Cite the regime split or factor exposure when relevant.",
    "underperforms_when": "One short sentence — the conditions under which it lags. The flip side of outperforms_when.",
    "primary_risk_factor": "The factor name from the input — copy verbatim",
    "diversification_role": "One short sentence — what this strategy adds to a multi-strategy portfolio that the others don't."
  },
  "regime_sensitivity": "One sentence describing how this strategy responds to regime changes — bull/bear, low-vol/high-vol, rising-rate cycles.",
  "behavioural_tag": "A short descriptor (max 60 chars) for the dashboard card. e.g. 'Momentum-driven, performs in trending markets' or 'Static 60/40 balance — anchor allocation'."
}

CRITICAL RULES:
- Ground every claim in the input data. Cite the pre/post-2022 Sharpe
  numbers, the factor beta, the turnover and concentration figures
  when they support a point.
- Be specific. "It diversifies the portfolio" is not acceptable; "it
  contributes the only large momentum-factor exposure among the ten
  strategies" is.
- Plain English suitable for an MBA reader — not jargon-heavy.
- Return ONLY the JSON. No preamble, no explanation, no ``` fences."""


def _build_user_message(
    strategy_id: str,
    metadata: dict,
    portfolio_chars: dict,
    factor_row: dict | None,
    regime_row: dict | None,
    primary_risk_factor: str,
) -> str:
    """Assembles the per-strategy user message. The metadata + the
    pre-computed structural fields go in; the model returns the
    structured JSON described in _SYSTEM_PROMPT."""
    parts: list[str] = []
    parts.append(f"STRATEGY: {metadata.get('name') or strategy_id}")
    parts.append(f"TYPE: {metadata.get('type', 'unknown')}")
    parts.append(f"REBALANCING: {metadata.get('rebalancing', 'unknown')}")
    if metadata.get("weights"):
        w = metadata["weights"]
        parts.append(
            f"WEIGHTS: equity {w.get('equity', 0):.0%}, "
            f"IG {w.get('ig', 0):.0%}, HY {w.get('hy', 0):.0%}")
    else:
        parts.append("WEIGHTS: optimised — solved each rebalance, not fixed")
    if metadata.get("signal_logic"):
        parts.append(f"SIGNAL: {metadata['signal_logic']}")
    if metadata.get("economic_intuition"):
        parts.append(f"ECONOMIC INTUITION: {metadata['economic_intuition']}")
    if metadata.get("key_parameter") and metadata.get("parameter_value"):
        parts.append(
            f"KEY PARAMETER: {metadata['key_parameter']} = "
            f"{metadata['parameter_value']}")
    parts.append(f"CONSTRUCTION RATIONALE: {metadata.get('rationale', '')}")

    parts.append("")
    parts.append("BACKTEST CHARACTERISTICS:")
    parts.append(
        f"  avg holdings per rebalance:  "
        f"{portfolio_chars.get('avg_holdings')}")
    parts.append(
        f"  annualised turnover:         "
        f"{portfolio_chars.get('avg_turnover_pct')}%")
    parts.append(
        f"  avg concentration (largest position): "
        f"{portfolio_chars.get('avg_concentration')}%")
    parts.append(
        f"  rebalance frequency:         "
        f"{portfolio_chars.get('rebalance_frequency')}")

    parts.append("")
    parts.append("CARHART FACTOR EXPOSURE:")
    if factor_row:
        parts.append(
            f"  alpha (annualised): "
            f"{factor_row.get('alpha_annualised', 'n/a')}")
        parts.append(f"  MKT-RF beta:        {factor_row.get('mkt_rf', 'n/a')}")
        parts.append(f"  SMB beta:           {factor_row.get('smb', 'n/a')}")
        parts.append(f"  HML beta:           {factor_row.get('hml', 'n/a')}")
        parts.append(f"  MOM beta:           {factor_row.get('mom', 'n/a')}")
        parts.append(f"  R-squared:          {factor_row.get('r_squared', 'n/a')}")
    else:
        parts.append("  (factor regression unavailable)")
    parts.append(f"  → primary risk factor: {primary_risk_factor}")

    parts.append("")
    parts.append("REGIME-CONDITIONAL PERFORMANCE:")
    if regime_row:
        parts.append(
            f"  pre-2022 Sharpe:  {regime_row.get('pre_2022_sharpe', 'n/a')} "
            f"(over {regime_row.get('pre_2022_months', '?')} months)")
        parts.append(
            f"  post-2022 Sharpe: {regime_row.get('post_2022_sharpe', 'n/a')} "
            f"(over {regime_row.get('post_2022_months', '?')} months)")
        parts.append(
            f"  pre-2022 CAGR:    {regime_row.get('pre_2022_cagr', 'n/a')}")
        parts.append(
            f"  post-2022 CAGR:   {regime_row.get('post_2022_cagr', 'n/a')}")
    else:
        parts.append("  (regime split unavailable)")

    parts.append("")
    parts.append(
        "Now produce the structured JSON described in the system prompt.")
    return "\n".join(parts)


def _stub_characterisation(
    strategy_id: str,
    metadata: dict,
    primary_risk_factor: str,
) -> dict[str, Any]:
    """Deterministic mock used in ENVIRONMENT=test so the pytest suite
    never hits Anthropic. Also the fail-open fallback when the live
    Claude call errors or returns unparseable JSON in production —
    the row still lands with text the UI can render and the agents
    can inject."""
    name = metadata.get("name") or strategy_id
    is_dynamic = metadata.get("type") == "dynamic"
    return {
        "construction_summary": (
            f"{name} is a {metadata.get('type', 'static')} portfolio "
            f"strategy with {metadata.get('rebalancing', 'periodic')} "
            f"rebalancing. "
            + (metadata.get("signal_logic") or metadata.get("rationale")
               or "")
        ),
        "behavioural_profile": {
            "outperforms_when": (
                "Conditions aligned with its construction signal."
                if is_dynamic
                else "Its target asset mix is in favour."),
            "underperforms_when": (
                "The driving signal is degraded or absent."
                if is_dynamic
                else "Its target asset mix is out of favour."),
            "primary_risk_factor": primary_risk_factor,
            "diversification_role": (
                f"Adds the strategy-specific behaviour of {name} to a "
                "multi-strategy portfolio."),
        },
        "regime_sensitivity": (
            "Adapts allocation across regimes by construction."
            if is_dynamic
            else "Fixed allocation; regime sensitivity comes from the "
                 "underlying assets, not the strategy."),
        "behavioural_tag": (
            f"Dynamic — {metadata.get('key_parameter', 'signal-driven')}"
            if is_dynamic
            else f"Static — {metadata.get('rebalancing', 'fixed weights')}"
        ),
    }


def _parse_model_json(raw: str) -> dict[str, Any] | None:
    """Strip optional code fences and parse JSON. Returns None on a
    parse failure so the caller can fall back to the stub."""
    if not raw:
        return None
    s = raw.strip()
    # Strip ```json ... ``` and ``` ... ``` fences if the model added them.
    m = re.match(r"^```(?:json)?\s*\n?(.*)```\s*$", s, flags=re.DOTALL)
    if m:
        s = m.group(1).strip()
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def generate_characterisation_text(
    strategy_id: str,
    metadata: dict,
    portfolio_chars: dict,
    factor_row: dict | None,
    regime_row: dict | None,
) -> dict[str, Any]:
    """Returns the four AI-generated fields as a dict, falling back to
    the deterministic stub on any failure or in the test environment.

    Synchronous — the Anthropic SDK call_claude is synchronous. The
    refresh helper runs this inside an asyncio.to_thread shim so it
    does not block the event loop.
    """
    primary = derive_primary_risk_factor(factor_row)
    if _is_test_env():
        return _stub_characterisation(strategy_id, metadata, primary)
    try:
        from agents.base import call_claude, SONNET_MODEL
        user_msg = _build_user_message(
            strategy_id, metadata, portfolio_chars,
            factor_row, regime_row, primary)
        raw = call_claude(
            model=SONNET_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_msg,
            max_tokens=1024,
        )
        parsed = _parse_model_json(raw)
        if not parsed:
            log.warning("strategy_characterisation_unparseable",
                        strategy_id=strategy_id)
            return _stub_characterisation(strategy_id, metadata, primary)
        # Guard the structure — the model occasionally omits a key under
        # load. Backfill with the stub so the row still lands intact.
        stub = _stub_characterisation(strategy_id, metadata, primary)
        out = {
            "construction_summary":
                str(parsed.get("construction_summary")
                    or stub["construction_summary"])[:2000],
            "behavioural_profile": _validate_behavioural_profile(
                parsed.get("behavioural_profile"), stub, primary),
            "regime_sensitivity":
                str(parsed.get("regime_sensitivity")
                    or stub["regime_sensitivity"])[:500],
            "behavioural_tag":
                str(parsed.get("behavioural_tag")
                    or stub["behavioural_tag"])[:120],
        }
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_generation_failed",
                    strategy_id=strategy_id, error=str(exc))
        return _stub_characterisation(strategy_id, metadata, primary)


def _validate_behavioural_profile(
    raw: Any, stub: dict, primary_risk_factor: str,
) -> dict[str, Any]:
    """Ensures all four sub-fields are present strings. A model that
    returned a malformed sub-structure falls back to the stub fields,
    so the cached row never carries partial JSON."""
    stub_bp = stub.get("behavioural_profile", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "outperforms_when":
            str(raw.get("outperforms_when") or stub_bp["outperforms_when"])[:400],
        "underperforms_when":
            str(raw.get("underperforms_when") or stub_bp["underperforms_when"])[:400],
        "primary_risk_factor":
            str(raw.get("primary_risk_factor") or primary_risk_factor)[:80],
        "diversification_role":
            str(raw.get("diversification_role") or stub_bp["diversification_role"])[:400],
    }


# ── Refresh orchestration ────────────────────────────────────────────────────


async def refresh_strategy_characterisations(data_hash: str) -> None:
    """Fires from refresh_all_analytics after every strategy_cache write.
    Computes portfolio_characteristics deterministically, generates the
    AI text fields, and upserts one row per strategy.

    Fail-open per strategy — one bad row does not block the others. The
    AI call runs in a thread (asyncio.to_thread) so the event loop is
    not blocked by Anthropic's network round-trip.
    """
    try:
        import asyncio
        import pandas as pd
        from tools.cache import (
            get_latest_strategy_cache, get_ff_factors, get_monthly_returns,
        )
        from tools import analytics as an
        from strategy_metadata import STRATEGY_METADATA

        strategies = await get_latest_strategy_cache()
        if not strategies:
            log.info("strategy_characterisation_refresh_no_strategies")
            return

        monthly = await get_monthly_returns()
        ff = await get_ff_factors()
        rf = None
        if monthly and monthly.get("dates") and monthly.get("rf"):
            try:
                idx = pd.to_datetime(monthly["dates"])
                rf = pd.Series(monthly["rf"], index=idx)
            except Exception:  # noqa: BLE001
                rf = None

        factor_rows = an.factor_loadings(strategies, ff or [])
        regime_rows = an.regime_conditional_performance(strategies, rf)
        # Both helpers key off result['strategy_name'] in their output;
        # build name→row maps so the per-strategy generation can index.
        factor_by_name = {r["strategy"]: r for r in factor_rows}
        regime_by_name = {r["strategy"]: r for r in regime_rows}

        meta_by_id = {m["id"]: m for m in STRATEGY_METADATA}

        for strategy_id, result in strategies.items():
            try:
                metadata = meta_by_id.get(strategy_id) or {}
                display_name = result.get("strategy_name") or strategy_id
                portfolio_chars = compute_portfolio_characteristics(result)
                factor_row = factor_by_name.get(display_name)
                regime_row = regime_by_name.get(display_name)
                # Run the AI generation off the event loop — call_claude
                # is sync and would otherwise block other endpoints.
                text_fields = await asyncio.to_thread(
                    generate_characterisation_text,
                    strategy_id, metadata, portfolio_chars,
                    factor_row, regime_row,
                )
                await upsert_characterisation(
                    strategy_id, data_hash,
                    construction_summary=text_fields["construction_summary"],
                    portfolio_characteristics=portfolio_chars,
                    behavioural_profile=text_fields["behavioural_profile"],
                    regime_sensitivity=text_fields["regime_sensitivity"],
                    behavioural_tag=text_fields["behavioural_tag"],
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("strategy_characterisation_row_failed",
                            strategy_id=strategy_id, error=str(exc))
        log.info("strategy_characterisation_refresh_complete",
                 data_hash=data_hash[:8] if data_hash else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_refresh_failed",
                    error=str(exc))
