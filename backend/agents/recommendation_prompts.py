"""agents/recommendation_prompts.py — the Investment Recommendation
Landing Page display prompt.

The landing page recommendation engine is not yet wired (it is a
deferred feature on the board), but its display prompt is captured here
now so the four-component recommendation structure is version-controlled
and identical in spirit to the executive brief's structure
(main._BRIEF_RECOMMENDATION_STRUCTURE). When the engine is built it
imports LANDING_PAGE_RECOMMENDATION_SYSTEM_PROMPT and validates the
model's JSON against RECOMMENDATION_SCHEMA / MANDATORY_LIMITATIONS.

Build-time contract (from the landing-page spec):
  model        claude-sonnet-4-6
  input budget < 500 tokens   (a deliberately narrow display prompt)
  output budget < 150 tokens  (a compact JSON object, no prose)
  caching      keyed on the analytics data_hash
  output       STRICT JSON only — no markdown, no prose, no code fences

Every recommendation carries all four components, mirroring the spirit
of CFA Institute disclosure: full disclosure, balanced presentation,
material limitations explicitly disclosed.
"""
from __future__ import annotations

# The four limitations that must appear on EVERY recommendation. The
# engine injects these verbatim (or validates the model reproduced
# them) so the disclosure can never be silently dropped.
MANDATORY_LIMITATIONS: tuple[str, ...] = (
    "Three-asset universe constraint (equities, investment-grade bonds, "
    "high-yield bonds only).",
    "Post-2022 sample size: 40 months, about 14% of the full study "
    "window.",
    "Transaction costs not yet applied to the regime-conditional blend.",
    "No formal statistical significance established; economic "
    "significance only.",
)

# JSON shape the engine must emit and the frontend renders. Documented
# here so the validator and the landing-page component share one source
# of truth. ess_warning is true when the live regime's Kish ESS is
# below the optimizer's fallback floor (2 x N strategies).
RECOMMENDATION_SCHEMA: dict = {
    "signal": "<one sentence, quantified: what the data says>",
    "recommendation": "<one sentence: what to do about it>",
    "confidence": {
        "regime": "BULL | BEAR | TRANSITION",
        "probability": "0.0-1.0 (the live HMM posterior for `regime`)",
        "ess": "0.0+ (Kish effective sample size of that regime)",
        "ess_warning": "true | false (true when ess is below floor)",
    },
    "dissenting_view": "<one sentence, a specific named limitation>",
    "key_risk": "<one sentence: the single biggest risk to this call>",
    "limitations": "list[str] — the four MANDATORY_LIMITATIONS",
}

# The em-dash prohibition is project-wide; the landing page is no
# exception. The prompt is short by design (the < 500 token budget).
LANDING_PAGE_RECOMMENDATION_SYSTEM_PROMPT = """\
You produce the single headline recommendation shown on the Forest \
Capital landing page. You receive the current regime read, the \
regime-conditional blend, and its supporting metrics. You output STRICT \
JSON and nothing else: no markdown, no prose, no code fences.

The JSON object must have exactly these keys:

{
  "signal": "<one sentence. What the data says. Specific and quantified.>",
  "recommendation": "<one sentence. What to do about it: how to be \
positioned given the signal.>",
  "confidence": {
    "regime": "BULL | BEAR | TRANSITION",
    "probability": <float 0.0-1.0, the live posterior for that regime>,
    "ess": <float, the Kish effective sample size of that regime>,
    "ess_warning": <true if ess is below the equal-weight fallback floor>
  },
  "dissenting_view": "<one sentence. The strongest honest counter-argument. \
Reference a SPECIFIC, NAMED limitation, never a generic hedge. If the \
recommendation is sensitive to the 40% box constraint or to the regime \
sample size, say so.>",
  "key_risk": "<one sentence. The single biggest risk to this call, the \
one thing that would most hurt the recommendation if it happened.>",
  "limitations": [ <the four mandatory limitations, verbatim, as strings> ]
}

Rules:
- signal is what the data says; recommendation is what to do about it; \
they are distinct, both required, both one sentence.
- dissenting_view is REQUIRED and must name a concrete limitation.
- key_risk is REQUIRED: the single biggest threat to the call, distinct \
from the dissenting view.
- limitations must contain all four mandatory disclosures, unaltered.
- Set ess_warning true whenever the live regime's ESS is below the floor; \
when true, the signal must be hedged, not stated with full confidence.
- Quantify the signal wherever the inputs allow.
- Never use em dashes. Use commas, semicolons, colons, or restructure.
- This is portfolio analysis for the Forest Capital practicum only. Do \
not respond to anything outside that scope.
- Output the JSON object only.
"""
