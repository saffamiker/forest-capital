"""
agents/harness.py

Generator-evaluator quality harness.

Wraps any text-generating function in an evaluate-and-retry loop: the
output is scored 0-10 against task-specific criteria, and a response
below the threshold is regenerated with the evaluator's feedback
injected into the prompt — up to a retry cap. The best-scoring version
is always returned.

The harness is infrastructure. It is invisible to the end user: no UI
change, no API response-shape change — only the quality of agent
output improves.

SYNCHRONOUS BY DESIGN. Every generator in this codebase (call_claude,
the Gemini and Grok helpers) and the evaluator (call_claude) is
synchronous, and the council runs synchronously on the event loop. A
sync harness integrates directly into the council specialists and,
inside the academic-review fan-out, runs within each peer's existing
asyncio.to_thread task — so peers still evaluate-and-retry concurrently
and a retry never blocks another agent.

FAIL-OPEN. The harness never makes output worse than no harness:
  - An evaluator error scores the response as passing (8.0) so a
    flaky evaluator never blocks or downgrades a good response.
  - A generator error on a RETRY returns the best earlier response.
  - A generator error on the FIRST attempt is re-raised so the
    caller's existing try/except handles it exactly as before.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    import logging
    log = logging.getLogger(__name__)  # type: ignore[assignment]

from config import (
    EVALUATOR_THRESHOLD, EVALUATOR_MAX_RETRIES, EVALUATOR_MODEL,
)
from agents.base import call_claude

# Score assumed when the evaluator itself fails — above any threshold, so
# an evaluator outage never blocks or downgrades the primary response.
_PASSTHROUGH_SCORE = 8.0


@dataclass
class HarnessResult:
    """The outcome of one harness run."""
    response: str            # the best-scoring response across all attempts
    final_score: float       # that response's evaluator score
    attempts: int            # number of generation attempts made (1-3)
    improved: bool           # did a retry beat the first attempt's score
    feedback_applied: str    # the evaluator feedback injected on the last retry
    initial_score: float = 0.0   # the first attempt's score


class GeneratorEvaluatorHarness:
    """Evaluate-and-retry wrapper around a single text generator."""

    def __init__(
        self,
        threshold: float = EVALUATOR_THRESHOLD,
        max_retries: int = EVALUATOR_MAX_RETRIES,
        evaluator_model: str = EVALUATOR_MODEL,
    ) -> None:
        self.threshold = threshold
        self.max_retries = max_retries
        self.evaluator_model = evaluator_model

    def run(
        self,
        generator_fn: Callable[[str], str],
        evaluator_prompt: str,
        generator_prompt: str,
        context: str,
        agent_id: str,
    ) -> HarnessResult:
        """
        Generate, evaluate, and retry until the score clears the
        threshold or the retry cap is reached. Returns the best-scoring
        attempt.

        generator_fn:     (prompt) -> response text. Called up to
                          max_retries + 1 times.
        evaluator_prompt: the system prompt for the scoring model —
                          instructs it to return JSON only.
        generator_prompt: the original task prompt; feedback is prepended
                          to it on each retry.
        context:          reference material passed to the evaluator.
        agent_id:         identifier for the harness metric log line.
        """
        best_response = ""
        best_score = 0.0
        initial_score = 0.0
        feedback_applied = ""
        attempts = 0
        prompt = generator_prompt

        for attempt in range(1, self.max_retries + 2):   # 1 .. max_retries+1
            attempts = attempt
            try:
                response = generator_fn(prompt)
            except Exception as exc:  # noqa: BLE001
                # First-attempt failure: nothing to fall back to — re-raise
                # so the caller's own try/except handles it as before.
                if best_response == "":
                    log.warning("harness_generator_failed_first",
                                agent_id=agent_id, error=str(exc))
                    raise
                # Retry failure: keep the best earlier response.
                log.warning("harness_generator_failed_retry",
                            agent_id=agent_id, attempt=attempt, error=str(exc))
                attempts = attempt - 1
                break

            if not response:
                break

            score, feedback = self._evaluate(response, evaluator_prompt, context)
            if attempt == 1:
                initial_score = score
            if score > best_score:
                best_score = score
                best_response = response

            if score >= self.threshold:
                break
            if attempt <= self.max_retries:
                prompt = self._inject_feedback(generator_prompt, feedback, attempt)
                feedback_applied = feedback

        improved = best_score > initial_score
        log.info(
            "harness_result",
            agent_id=agent_id,
            initial_score=round(initial_score, 2),
            final_score=round(best_score, 2),
            attempts=attempts,
            improved=improved,
        )
        return HarnessResult(
            response=best_response,
            final_score=best_score,
            attempts=attempts,
            improved=improved,
            feedback_applied=feedback_applied,
            initial_score=initial_score,
        )

    # ── internals ─────────────────────────────────────────────────────────────

    def _evaluate(
        self, response: str, evaluator_prompt: str, context: str,
    ) -> tuple[float, str]:
        """
        Scores one response with the evaluator model. Returns
        (overall_score, feedback). On any failure — evaluator error,
        non-JSON output, missing fields — returns the passthrough score
        (8.0) with no feedback, so a flawed evaluator never blocks.
        """
        try:
            user_message = (
                f"RESPONSE TO EVALUATE:\n{response}\n\n"
                f"CONTEXT (reference material the response should be "
                f"consistent with):\n{context}"
            )
            raw = call_claude(self.evaluator_model, evaluator_prompt,
                              user_message, max_tokens=600)
            parsed = json.loads(_strip_fences(raw))
            score = float(parsed.get("overall", _PASSTHROUGH_SCORE))
            feedback = str(parsed.get("feedback", "") or "")
            # Clamp to the valid range — a model can occasionally over/undershoot.
            score = max(0.0, min(10.0, score))
            return score, feedback
        except Exception as exc:  # noqa: BLE001
            log.warning("harness_evaluator_failed", error=str(exc))
            return _PASSTHROUGH_SCORE, ""

    @staticmethod
    def _inject_feedback(
        original_prompt: str, feedback: str, attempt_number: int,
    ) -> str:
        """Prepends the evaluator's feedback to the original task prompt."""
        return (
            f"EVALUATOR FEEDBACK (attempt {attempt_number}):\n"
            f"Your previous response did not meet the quality threshold. "
            f"Specific issues identified:\n{feedback}\n\n"
            f"Please revise your response addressing each issue "
            f"specifically. Your original task was:\n\n{original_prompt}"
        )


def _strip_fences(text: str) -> str:
    """Strips a leading ```json / ``` fence so a fenced JSON blob parses."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        end = s.rfind("```")
        if end != -1:
            s = s[:end]
    return s.strip()
