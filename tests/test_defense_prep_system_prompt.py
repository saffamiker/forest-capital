"""Thesis Defense Prep — system-prompt contract.

The system prompt is the load-bearing surface for how the mock panel
answers questions about the uploaded document. This file pins the four
amendments that landed on May 30 2026 so a future edit cannot silently
drop any of them:

  A. RESPONSE BALANCE — narrative-first, technical only when needed;
     DO-NOT list; the audience clause.
  B. PEER FRAMING — MSFA audience, plain-English LANGUAGE RULES, and
     collegial TONE.
  C. TWO QUESTIONER LEVELS — Peer vs Professor; full technical depth
     on HMM / Correlation / Covariance / Skewness / Kurtosis.
  D. TECHNICAL PRIMERS — a 16-term reference block so the council can
     explain core finance terms from first principles when asked.
"""
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture(scope="module")
def prompt() -> str:
    from agents.peer_review import _defense_prep_system_prompt
    return _defense_prep_system_prompt()


@pytest.fixture(scope="module")
def full_primers() -> str:
    """The full 16-term primer reference text. Stored as a module-level
    constant rather than embedded in the system prompt so the initial
    generation context stays lean (~6.9 kB vs ~12.3 kB); the full text
    is injected only on a follow-up question that explicitly asks for
    a definition."""
    from agents.peer_review import _DEFENSE_PREP_FULL_PRIMERS
    return _DEFENSE_PREP_FULL_PRIMERS


# ── A. Response balance + DO NOT list + closing audience clause ────────────

def test_response_balance_two_levels_named(prompt):
    assert "NARRATIVE" in prompt
    assert "TECHNICAL DEFENCE" in prompt
    assert "Lead with the conceptual argument" in prompt
    # The closing clause exactly as the user wrote it.
    assert "may not know the platform's implementation details" in prompt


def test_do_not_list_present(prompt):
    assert "recite tables of numbers unprompted" in prompt
    assert "answer purely technically when the question is conceptual" in prompt
    assert "be vague when a specific methodological challenge" in prompt


# ── B. PEER framing — MSFA audience + LANGUAGE RULES + TONE ────────────────

def test_msfa_audience_named(prompt):
    assert "MSFA (Master of Science in Finance and Analytics)" in prompt
    # Audience boundaries the user spelled out verbatim.
    assert "not quants, not economists, and not math majors" in prompt


def test_language_replacements_present(prompt):
    # The user's exact substitution phrases.
    assert "in the worst scenarios" in prompt
    assert "the model's confidence that we are in a particular market regime" \
        in prompt
    assert "how sensitive the strategy is to broad market moves" in prompt
    assert "bonds stopped cushioning equity losses" in prompt
    # The p-value plain-language line.
    assert ("the outperformance is real but our sample is too small to prove "
            "it statistically") in prompt


def test_tone_rules_present(prompt):
    assert "collegial, not professorial" in prompt
    assert "never make the questioner feel like they asked a naive question" \
        in prompt


# ── C. Two questioner levels + Professor-depth topics ──────────────────────

def test_two_questioner_levels_named(prompt):
    assert "TWO QUESTIONER LEVELS" in prompt
    assert "PEER FRAMING" in prompt
    assert "PROFESSOR FRAMING" in prompt
    assert "Professor-level questions deserve professor-level answers" in prompt


def test_professor_topics_full_depth(prompt):
    # Each topic appears with its method / window / known-limitation depth.
    assert "Hidden Markov Model" in prompt
    assert "Baum-Welch" in prompt
    assert "500 iterations, tolerance 1e-5" in prompt
    assert "GaussianHMM with 3 states" in prompt
    # Pearson correlation + the central headline shift.
    assert "rolling pairwise Pearson correlation" in prompt
    assert "-0.05 pre-2022 to +0.57 post-2022" in prompt
    # 36-month covariance window + the 3-asset well-conditioned caveat.
    assert "rolling 36-month covariance matrix" in prompt
    assert "well-conditioned" in prompt
    # Skewness + kurtosis depth.
    assert "SKEWNESS" in prompt
    assert "KURTOSIS" in prompt
    assert "Sharpe assumes symmetric returns" in prompt


# ── D. Technical primers — reference block for the 16 named terms ──────────

PRIMER_TERMS = [
    "SHARPE RATIO",
    "CVaR (Conditional Value-at-Risk)",
    "CAGR (Compound Annual Growth Rate)",
    "MAXIMUM DRAWDOWN",
    "CORRELATION",
    "COVARIANCE",
    "EFFICIENT FRONTIER",
    "BETA and FACTOR EXPOSURE",
    "MOMENTUM STRATEGY",
    "VOLATILITY TARGETING",
    "RISK PARITY",
    "REGIME SWITCHING",
    "REBALANCING",
    "BACKTESTING vs OUT-OF-SAMPLE TESTING",
    "STATISTICAL SIGNIFICANCE",
    "BLACK-LITTERMAN MODEL",
]


def test_primer_index_present_in_system_prompt(prompt):
    """The condensed INDEX is part of the initial-generation system
    prompt — the full per-term definitions are NOT (they live in
    _DEFENSE_PREP_FULL_PRIMERS, injected only on demand)."""
    assert "TECHNICAL PRIMERS — INDEX" in prompt
    # All 16 term names are listed in the index, comma-separated, so
    # the panel knows the briefed vocabulary without paying the full
    # context cost on every initial run.
    for term_phrase in (
        "Sharpe ratio", "CVaR", "CAGR", "maximum drawdown",
        "correlation", "covariance", "efficient frontier",
        "beta and factor exposure", "momentum strategy",
        "volatility targeting", "risk parity", "regime switching",
        "rebalancing", "backtesting vs out-of-sample testing",
        "statistical significance", "Black-Litterman model",
    ):
        assert term_phrase in prompt, \
            f"Index missing term phrase: {term_phrase!r}"
    # The system prompt does NOT carry the full reference block —
    # that lives in the on-demand constant.
    assert "TECHNICAL PRIMERS — FULL DEFINITIONS" not in prompt
    assert "Never define a term using the term itself" not in prompt


def test_full_primers_carry_shape_instruction(full_primers):
    """The shape instruction lives with the full definitions, since
    that is where each definition is written."""
    assert "TECHNICAL PRIMERS — FULL DEFINITIONS" in full_primers
    # The shape instruction names the never-defines-self rule.
    assert "never defines a term using the term itself" in full_primers


@pytest.mark.parametrize("term", PRIMER_TERMS)
def test_each_primer_term_has_a_primer(full_primers, term):
    """All 16 terms specified in the spec must have a primer entry
    in the on-demand reference block."""
    assert term in full_primers, f"Primer missing for: {term}"


def test_each_primer_carries_a_limitation(full_primers):
    """Every primer must include an honest limitation. We assert the
    presence of the cohort of limitation keywords the primers use —
    one strong signal each, not a per-term assertion."""
    for kw in (
        "assumes symmetric returns",          # Sharpe limitation
        "carries real uncertainty",           # CVaR limitation
        "hides the path",                     # CAGR limitation
        "single realised history",            # MaxDD + OOS limitations
        "tail-dependent comovement",          # Correlation limitation
        "estimated covariance is noisy",      # Covariance limitation
        "single-period mean-variance snapshot",  # Frontier limitation
        "drift through time",                 # Beta/factor limitation
        "vulnerable to sharp reversals",      # Momentum limitation
        "lags realised volatility shifts",    # Vol targeting limitation
        "moved sharply in 2022",              # Risk parity limitation
        "misclassified",                      # Regime-switching limitation
        "each rebalance carries a transaction cost",  # Rebalancing
        "too small to prove it statistically",  # Stat sig (peer + primer)
        "subjective",                         # Black-Litterman limitation
    ):
        assert kw in full_primers, f"Limitation keyword missing: {kw!r}"


def test_system_prompt_is_lean(prompt):
    """The initial-generation system prompt must stay under ~10 kB to
    keep Opus per-call tokens predictable. With full primers it was
    ~12.3 kB; with the condensed index it should be ~7 kB. Bound: 10."""
    assert len(prompt) < 10_000, (
        f"System prompt grew to {len(prompt)} chars — primers may have "
        f"crept back in. Keep the index, expand on demand only.")
