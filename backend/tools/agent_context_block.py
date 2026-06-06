"""Shared context-block formatter for agent prompts.

The CIO, the dissenters (Gemini / Grok), and the academic advisor each
receive a `live_context` dict assembled by tools.cio_recommendation.
compute_context() and (optionally) a macro digest. Each call site used
to inline its own JSON dump, which left the regime label, ESS warning,
and top blend weights buried in nested fields — easy for the LLM to
miss.

This helper renders the regime + blend snapshot as a compact prose
block the model reads at a glance, and (separately) renders a macro
summary line. It is a formatter only — no fetches, no transforms — so
the call site retains control over what to thread in and the helper
keeps the rendered prompt deterministic.

The block is intentionally short. Long context wastes the token
budget and dilutes the signal the dissenter is supposed to
challenge.
"""
from __future__ import annotations

from typing import Any


def _safe_pct(value: Any) -> str | None:
    try:
        if value is None:
            return None
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return None


def format_live_context_block(
    live_context: dict[str, Any] | None,
) -> str:
    """Render the regime + blend + posterior + ESS snapshot as a
    compact prose block. Returns "" when live_context is missing so
    the caller can prepend or skip with a single check.

    Fields read:
      regime              -- daily HMM regime label
      monthly_regime      -- monthly HMM regime label (added in fix #3)
      hmm_models_agree    -- True when daily and monthly fits agree
      probability         -- posterior probability of the active regime
      ess                 -- Kish effective sample size for that regime
      ess_warning         -- True when ESS is below the floor
      blend_weights       -- dict[strategy -> weight]; top 3 are surfaced
      posterior           -- full posterior dict (rendered as summary)
    """
    if not live_context:
        return ""

    lines: list[str] = ["LIVE REGIME + BLEND STATE:"]

    regime = live_context.get("regime")
    monthly_regime = live_context.get("monthly_regime")
    hmm_models_agree = live_context.get("hmm_models_agree", True)
    prob_str = _safe_pct(live_context.get("probability"))
    ess = live_context.get("ess")
    ess_warning = live_context.get("ess_warning")

    if regime:
        regime_line = f"  Regime (daily HMM): {regime}"
        if prob_str:
            regime_line += f" (posterior {prob_str})"
        lines.append(regime_line)

    if monthly_regime and monthly_regime != regime:
        # Surface the divergence prominently when models disagree.
        # The disclosure is already in the recommendation prose; the
        # dissenter must see it too so its objection accounts for
        # the daily/monthly split.
        lines.append(f"  Regime (monthly HMM): {monthly_regime}")
        if not hmm_models_agree:
            lines.append(
                "  *** MODEL DIVERGENCE: daily and monthly HMM fits "
                "disagree. The live label reflects the daily model; "
                "the blend weights below reflect the monthly model. "
                "Account for this in any objection.")

    if ess is not None:
        ess_line = f"  ESS (Kish): {ess:.2f}"
        if ess_warning:
            ess_line += " (BELOW FLOOR — weights weakly determined)"
        lines.append(ess_line)

    blend = live_context.get("blend_weights") or {}
    if blend:
        top = sorted(
            ((n, float(w)) for n, w in blend.items()
             if isinstance(w, (int, float)) and float(w) > 0),
            key=lambda kv: -kv[1])[:3]
        if top:
            weight_str = ", ".join(
                f"{name} {weight * 100:.0f}%" for name, weight in top)
            lines.append(f"  Top blend weights: {weight_str}")

    if len(lines) == 1:
        # No fields populated -- bail to empty string so the caller
        # does not prepend a stub header.
        return ""
    return "\n".join(lines)


def format_macro_context_line(macro: str | None) -> str:
    """Render the macro digest as a single tagged line. Returns "" when
    the macro string is empty or whitespace-only.

    The macro context is already inserted into the system prompt by
    tools.macro_context.inject_macro_context for several agents; this
    helper is for call sites that want to surface it in the USER
    message instead (so it's grounded against the same evidence
    block)."""
    if not macro or not macro.strip():
        return ""
    return f"MACRO CONTEXT:\n{macro.strip()}"
