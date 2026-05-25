"""
tests/test_resend_error_detail.py — May 25 2026.

Coverage for the _resend_error_detail() helper that enriches the
magic_link_send_failed / welcome_email_send_failed log events after
the SendGrid → Resend swap.

Resend's exception model exposes structured fields directly on the
exception (.code, .message, .suggested_action, .error_type, .headers)
rather than a raw response body. The helper surfaces each field as
resend_<name> so a log filter can pivot on the resend_code or
resend_error_type directly — distinct root causes (invalid_api_key,
rate_limit_exceeded, validation_error, etc.) each emit a distinct
error_type string.

Tests use the REAL resend.exceptions classes from the installed SDK
(>=2.0.0) where shape matters, and small fake exceptions for the
edge cases (pathological accessors, non-Resend exceptions).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


def test_extracts_code_error_type_message_from_real_invalid_api_key():
    """The canonical 401 case the swap is meant to triage. Uses the
    actual resend.exceptions.InvalidApiKeyError class so the helper
    is exercised against the real wire shape, not a guess."""
    from resend.exceptions import InvalidApiKeyError

    from auth import _resend_error_detail

    exc = InvalidApiKeyError(
        message="API key is invalid",
        error_type="invalid_api_key",
        code=401,
        headers={"X-Trace-Id": "trace-abcd"},
    )
    detail = _resend_error_detail(exc)
    assert detail["error_type"] == "InvalidApiKeyError"
    # `code` carries the HTTP status as int for HTTP-level errors.
    assert detail["resend_code"] == 401
    assert detail["resend_error_type"] == "invalid_api_key"
    assert detail["resend_message"] == "API key is invalid"
    assert detail["resend_headers"]["X-Trace-Id"] == "trace-abcd"


def test_extracts_fields_from_real_rate_limit_error():
    """A second real Resend class — confirms the helper isn't pinned
    to one specific subclass and that domain codes (str) surface as
    cleanly as HTTP codes (int)."""
    from resend.exceptions import RateLimitError

    from auth import _resend_error_detail

    exc = RateLimitError(
        message="Too many requests",
        error_type="rate_limit_exceeded",
        code=429,
    )
    detail = _resend_error_detail(exc)
    assert detail["error_type"] == "RateLimitError"
    assert detail["resend_code"] == 429
    assert detail["resend_error_type"] == "rate_limit_exceeded"
    assert detail["resend_message"] == "Too many requests"


def test_real_invalid_api_key_carries_a_suggested_action():
    """The installed Resend SDK populates suggested_action with a
    sensible default for InvalidApiKeyError ('Generate a new API
    key…') — surface it under resend_suggested_action so the log
    line carries the operator hint Resend supplied."""
    from resend.exceptions import InvalidApiKeyError

    from auth import _resend_error_detail

    exc = InvalidApiKeyError(
        message="x", error_type="invalid_api_key", code=401)
    detail = _resend_error_detail(exc)
    assert isinstance(detail.get("resend_suggested_action"), str)
    assert "API key" in detail["resend_suggested_action"]


def test_empty_string_field_is_skipped():
    """Defensive: a custom exception that leaves a string field as ''
    (e.g. a future SDK version, a subclass that overrides defaults)
    should NOT appear as an empty string in the log line — the helper
    filters those out so the resend_<name> set stays informative."""
    from auth import _resend_error_detail

    class _MinimalResendError(Exception):
        def __init__(self):
            super().__init__("boom")
            self.code = 422
            self.error_type = ""           # empty — should be skipped
            self.message = "boom"
            self.suggested_action = ""     # empty — should be skipped

    detail = _resend_error_detail(_MinimalResendError())
    assert detail["resend_code"] == 422
    assert detail["resend_message"] == "boom"
    assert "resend_error_type" not in detail
    assert "resend_suggested_action" not in detail


def test_string_fields_truncated_at_2000_chars():
    """A misbehaving Resend message must not flood the log line."""
    from resend.exceptions import ApplicationError

    from auth import _resend_error_detail

    huge = "x" * 3000
    exc = ApplicationError(
        message=huge, error_type="application_error", code=500)
    detail = _resend_error_detail(exc)
    message_field = detail["resend_message"]
    assert isinstance(message_field, str)
    assert len(message_field) <= 2000 + len("…(truncated)")
    assert message_field.endswith("…(truncated)")


def test_headers_capped_at_50_entries():
    from resend.exceptions import InvalidApiKeyError

    from auth import _resend_error_detail

    huge_headers = {f"X-Header-{i}": f"v{i}" for i in range(200)}
    exc = InvalidApiKeyError(
        message="x", error_type="invalid_api_key", code=401,
        headers=huge_headers)
    detail = _resend_error_detail(exc)
    assert isinstance(detail["resend_headers"], dict)
    assert len(detail["resend_headers"]) == 50


def test_non_resend_exception_returns_only_type_and_message():
    """A connection error / DNS failure / TypeError raised before the
    HTTP round-trip has no Resend-shaped fields. The helper still
    returns a dict — never raises on missing attrs."""
    from auth import _resend_error_detail

    exc = ConnectionError("DNS resolution failed")
    detail = _resend_error_detail(exc)
    assert detail["error_type"] == "ConnectionError"
    assert detail["error_message"] == "DNS resolution failed"
    # Optional fields absent — no resend_* keys at all.
    for absent in ("resend_code", "resend_message",
                   "resend_suggested_action", "resend_error_type",
                   "resend_headers"):
        assert absent not in detail


def test_helper_never_raises_on_pathological_input():
    """An exception with a @property that raises on access (or any
    other descriptor side-effect) must NOT crash the helper. Every
    field access goes through getattr-in-try."""
    from auth import _resend_error_detail

    class _Broken(Exception):
        code = 401

        @property
        def message(self):  # type: ignore[override]
            raise RuntimeError("broken message accessor")

        @property
        def error_type(self):  # type: ignore[override]
            raise RuntimeError("broken error_type accessor")

    detail = _resend_error_detail(_Broken("boom"))
    # Field that didn't blow up still surfaces.
    assert detail["resend_code"] == 401
    # Fields that DID raise are silently skipped, not exposed as None.
    assert "resend_message" not in detail
    assert "resend_error_type" not in detail


def test_legacy_sendgrid_alias_points_to_resend_helper():
    """The single-commit SendGrid → Resend swap aliases the old
    _sendgrid_error_detail name to the new _resend_error_detail
    function. The alias exists so any import that hasn't been renamed
    yet still works — both names should produce the same output."""
    from resend.exceptions import InvalidApiKeyError

    from auth import _resend_error_detail, _sendgrid_error_detail

    exc = InvalidApiKeyError(
        message="hi", error_type="invalid_api_key", code=401)
    assert _sendgrid_error_detail(exc) == _resend_error_detail(exc)
