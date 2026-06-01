"""Email alert (Component 2) — synthetic payload + content contract.

The alert assembler must:
  - return None when the invariant result has no violations
  - lead with the severity (hard subject vs warning subject)
  - render every violation's code, entity, expected, actual, detail
  - mark warm_aborted explicitly when not res.passed
  - include the "stop receiving alerts" unsubscribe line per the spec

No real Resend calls — the helper short-circuits in test env.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

from tools.email_alert import (  # noqa: E402
    build_alert_email, send_alert, send_test_alert,
)


def _hard_failure_payload() -> dict:
    return {
        "passed":        False,
        "checks_run":    20,
        "hard_failures": 1,
        "soft_warnings": 0,
        "ran_at":        "2026-05-31T10:00:00+00:00",
        "violations": [
            {"code": "1a", "severity": "hard", "category": 1,
             "entity": "BENCHMARK/COVID_Crash_2020",
             "metric": "cumulative_return",
             "expected": "|return| <= |full-period max DD| = 0.5256",
             "actual": "|-0.7353| = 0.7353",
             "detail": "A crisis-window cumulative loss cannot exceed "
                       "the strategy's worst-ever loss across the full "
                       "sample."},
        ],
    }


def _soft_only_payload() -> dict:
    return {
        "passed":        True,    # passed → not aborted
        "checks_run":    20,
        "hard_failures": 0,
        "soft_warnings": 1,
        "ran_at":        "2026-05-31T10:00:00+00:00",
        "violations": [
            {"code": "4a", "severity": "soft", "category": 4,
             "entity": "VOL_TARGETING/GFC_2008-2009",
             "metric": "cumulative_return",
             "expected": "VOL_TARGETING return > benchmark -0.4566",
             "actual": "-0.5000",
             "detail": "A volatility-targeting strategy lost more than "
                       "the benchmark in a crash window."},
        ],
    }


def test_build_returns_none_on_clean_result():
    """No violations -> nothing to alert about, nothing to assemble."""
    out = build_alert_email({
        "passed":        True, "checks_run": 20,
        "hard_failures": 0, "soft_warnings": 0,
        "violations":    [],
    })
    assert out is None


def test_hard_failure_subject_leads_with_alert():
    subject, html, text = build_alert_email(_hard_failure_payload())
    assert subject.startswith("[ALERT]")
    assert "action required" in subject


def test_soft_only_subject_uses_warning_prefix():
    subject, html, text = build_alert_email(_soft_only_payload())
    assert subject.startswith("[WARNING]")


def test_html_carries_severity_and_violation_fields():
    _, html, _text = build_alert_email(_hard_failure_payload())
    assert "1a" in html
    assert "BENCHMARK/COVID_Crash_2020" in html
    assert "cumulative_return" in html
    assert "0.7353" in html
    # warm aborted state visible.
    assert "ABORTED" in html


def test_text_fallback_is_plain_and_complete():
    _, _html, text = build_alert_email(_hard_failure_payload())
    assert "[1a]" in text
    assert "HARD" in text
    assert "Expected:" in text
    assert "Actual:" in text
    assert "Detail:" in text
    # The unsubscribe line per the spec.
    assert "stop receiving alerts" in text.lower()


def test_warm_aborted_inferred_from_passed_flag():
    """warm_aborted=None defaults to `not passed`."""
    _, html_hard, _ = build_alert_email(_hard_failure_payload())
    assert "ABORTED" in html_hard
    _, html_soft, _ = build_alert_email(_soft_only_payload())
    assert "ABORTED" not in html_soft
    assert "COMPLETED" in html_soft


def test_send_alert_returns_dev_message_id_in_test_env(monkeypatch):
    """Test env short-circuits send_email() to a dev- message id —
    confirms the wire path is exercised without a real Resend call."""
    monkeypatch.setenv("ALERT_RECIPIENT", "test-michael@example.com")
    result = send_alert(_hard_failure_payload())
    assert result["sent"] is True
    assert result["message_id"].startswith("dev-")
    assert result["recipient"] == "test-michael@example.com"


def test_send_alert_returns_skipped_when_no_recipient(monkeypatch):
    """Without ALERT_RECIPIENT (and outside test env) the alert
    helper should fail open with a clear `sent=False` reason. In
    test env, the email_resend helper falls back to a placeholder
    address, so we can only assert the fallback path lands a send
    rather than the no-recipient branch."""
    monkeypatch.setenv("ALERT_RECIPIENT", "")
    # In test env: the placeholder kicks in. The send still goes.
    result = send_alert(_hard_failure_payload())
    assert result["sent"] is True


def test_send_alert_skips_when_no_violations():
    result = send_alert({
        "passed": True, "checks_run": 20, "hard_failures": 0,
        "soft_warnings": 0, "violations": [],
    })
    assert result["sent"] is False
    assert "no violations" in result.get("reason", "").lower()


def test_send_test_alert_uses_synthetic_payload(monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENT", "test-michael@example.com")
    result = send_test_alert()
    assert result["sent"] is True
    assert "[ALERT]" in result["subject"]
