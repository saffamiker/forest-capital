"""scripts/apply_section6_edits.py -- June 29 2026.

Three edits to Section 6 (Cell 14, markdown):

1. 6.1 hash framing -- the prior copy says "December 2025
   submission freeze". The actual freeze date is June 2026
   (commit 5a49169 on 2026-06-21). Replace + tighten the
   dual-hash explanation.

2. 6.2 REGIME_SWITCHING vs regime-conditional blend --
   the second item says "The blend (REGIME_SWITCHING)
   passes 3/5". REGIME_SWITCHING is the underlying HMM
   strategy; the "blend" is the regime-conditional weighted
   combination. Rename to "REGIME_SWITCHING strategy".

3. 6.3 unverified Sharpe figures -- drop the per-event
   Sharpe values (+0.33, +0.04, -2.31) and replace with
   directional language per operator spec. Clarify that
   the 2/9 scorecard tracks the regime-conditional blend
   rebalance events, not REGIME_SWITCHING in isolation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "279ddc31"


NEW_SOURCE = '''## 6. AI Usage Discussion

The FNA 670 syllabus requires that every team document the role artificial intelligence played in producing the submission. This section addresses that requirement in six parts: the overall architecture, where the agent council added analytical value, where human judgment overrode the system, the council's own track record on the nine rebalance events we observed, limitations, and what we learned about AI in financial analysis.

### 6.1 System architecture in brief

The Forest Capital platform is a multi-agent system. A generator (Sonnet 4.6) drafts narrative content; an evaluator (Opus 4.7) scores each draft against an explicit per-section rubric (numeric anchoring, citation presence, length compliance, audience register) and returns a structured pass / regenerate / accept-with-revisions verdict. A third role, the arbiter, mediates disagreement between the council's regime-switching strategies and emits the council's chosen blend weights at each rebalance. Every numerical claim in the brief and the deck is locked to two hashes from the same June 2026 data freeze (commit `5a49169`, 2026-06-21):

- `f2e87dec7dcabe71` -- the **notebook strategy-cache key** (the hash this notebook asserts in Cell 3). Computed as `sha256(f"{n_rows}:{last_date}:{n_strategies}").hexdigest()[:16]` from the strategy backtest output.
- `c421fb895347f924` -- the **platform fingerprint** (the value referenced by the brief / DOCX appendix / deck). Computed as `sha256(...)` over the source-table row counts + max dates + last_updated timestamps.

Both hashes identify the same data state under different hashing schemes (the notebook's hash is downstream of the strategy backtest; the platform's hash is upstream of it, fingerprinting the raw market-data tables). The four submission deliverables are consistent because they all derive from the same Jun 21 2026 freeze; the notebook's manifest cell rejects any divergence.

A substitution-table layer rejects any generated number that is not in the locked cache, so the prose can never accidentally cite a value the data does not support.

### 6.2 Where the council added analytical value

Three specific instances stand out, all visible in the strategy_results.json freeze this notebook reads from:

1. **Tier-1 significance gating.** Every strategy's `significance_summary` field records the outcome of five statistical gates: t-test, FDR-corrected t-test, deflated Sharpe ratio, out-of-sample walk-forward, and cross-validation stability. The REGIME_SWITCHING strategy passes 3/5 significance gates -- not the high-watermark of 5/5 we would have wanted, but already a more honest assessment than the in-sample Sharpe ratio alone would suggest. The council surfaces this verdict in the brief verbatim rather than burying the gate failures. (NB: REGIME_SWITCHING is the underlying HMM strategy that drives one of the council inputs; the regime-conditional blend is the posterior-weighted combination across the full strategy set, with its own OOS Sharpe of 0.90.)
2. **Drawdown reconciliation.** During the audit phase the evaluator caught a definition mismatch in the recovery-month metric: the brief expressed it in trading-day-months (calendar days / 21) while an earlier draft implicitly used calendar months. The notebook documents both definitions in Cell 8a and reconciles to the brief's 32 vs 71 figure explicitly. A human reviewer would not have caught the ~30% definitional gap on a casual read.
3. **Numeric grounding gate.** Every number that appears in the brief is either a substitution-table token (replaced from the cache at render time) or a locked academic constant. A separate post-generation audit (`check_brief_story_plan_violations`) counts numbers in the prose that are NOT in the section's locked anchor set -- if more than three appear, the harness retries with explicit feedback. The version of the brief in this submission has zero unauthorised numbers.

### 6.3 The council's track record on nine events

`rebalance_events.csv` carries nine real rebalance events from 2023-03 through 2025-04. Each event records the council's verdict on the regime-conditional blend -- whether the chosen blend weights added value over the next 90 days versus what the static benchmark would have produced. The verdict is directional, measured against the benchmark over a 90-day rolling window.

**Two of nine events added value.** The December 2023 dovish-pivot rally and the November 2024 US-election repricing, both BULL regimes where the council's tilt toward risk-on assets captured a portion of the move.

**Seven of nine did not add value.** The largest single value-detraction was the April 2025 reciprocal-tariff event (Liberation Day), where the council shifted to a defensive posture immediately before a sharp equity recovery.

We do not bury this. The brief, the deck, and this appendix all report 2/9, not 9/9. The council is a statistically powered hypothesis generator, not a track record -- the 2/9 score covers REBALANCE EVENTS on the regime-conditional blend (specific, dated, individual decisions), not the blend's overall OOS performance, which carries the canonical Sharpe of 0.90 over the 53-month OOS window. The Tier-1 gates exist to prevent any in-sample Sharpe from being mistaken for out-of-sample skill.

### 6.4 Where human judgment overrode the council

Bob Thao (analyst) reviewed every draft section of the Executive Brief before sign-off, with the explicit scope to (a) reject any sentence whose causal claim was not supportable from the data, (b) rewrite any segment where the model's register drifted into marketing prose, and (c) override any quantitative claim where the cache and the narrative disagreed. The most material override this round was rewording several findings to soften causal language about regime detection ('the model DETECTED the regime' -> 'the model's posterior shifted in time with the regime') after Bob noted that the directional posterior moves and the realised regime are not statistically independent in the training window.

### 6.5 Limitations

Three things the AI did not do well and that human reviewers had to compensate for:

- The generator drifted toward expanding prose when an upstream document (the brief excerpt) was provided as alignment context; explicit hard word-count caps were necessary at each section boundary.
- The evaluator's per-section rubric checked positive presence of anchored numbers but not negative absence of unauthorised numbers; a separate post-pass audit had to be added to close that gap.
- Citation freezing required a registry-only constraint (`data/references.json`) -- without it the model would fabricate references with plausible authors and DOIs.

### 6.6 Net assessment

The agent council made the documentation phase of this project tractable on a part-time schedule. It did not do the underlying research or design the strategies. The honest read of the council's track record on the nine rebalance events is that the council's value-add IS the documentation, audit, and reproducibility chain that produces this submission -- not the per-event strategy-selection verdicts. Future work would focus on making the regime-detector itself more responsive to non-stationary correlation regimes like the post-2022 break documented in Cell 13.'''


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        c["source"] = [
            s + "\n" for s in NEW_SOURCE.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        print(f"REPLACED Section 6 markdown (id={CELL_ID})")
        break
    else:
        print(f"FATAL: cell {CELL_ID} not found")
        return 1

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
