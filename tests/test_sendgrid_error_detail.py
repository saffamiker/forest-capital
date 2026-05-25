"""
tests/test_sendgrid_error_detail.py — May 25 2026.

Coverage for the _sendgrid_error_detail() helper that enriches the
magic_link_send_failed / welcome_email_send_failed log events. SendGrid's
HTTPError exception carries .status_code, .body (raw response bytes),
and .headers — the previous log only rendered str(exc), which lost the
errors[] array describing WHY the call failed (invalid key, unverified
sender, suspended account, etc.). This file pins the helper's contract:

  - status_code surfaces as 'sendgrid_status' (typed int)
  - body surfaces as 'sendgrid_body' (str; bytes decoded; truncated > 2000)
  - JSON body with 'errors' surfaces as 'sendgrid_errors' (parsed list)
  - headers surface as 'sendgrid_headers' (string-keyed dict, cap 50)
  - exceptions without any of the fields fail gracefully (no raise)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


class _FakeHTTPError(Exception):
    """Mimics python_http_client.exceptions.HTTPError's interface:
    status_code, body (bytes), headers (dict-like)."""

    def __init__(self, status_code, body, headers, msg="HTTPError"):
        super().__init__(msg)
        self.status_code = status_code
        self.body = body
        self.headers = headers


def test_extracts_status_body_and_headers_from_sendgrid_httperror():
    from auth import _sendgrid_error_detail

    body = (b'{"errors":[{"message":"The provided authorization grant '
            b'is invalid, expired, or revoked","field":null,"help":null}]}')
    headers = {"X-Request-Id": "abcd-1234", "Content-Type": "application/json"}
    exc = _FakeHTTPError(401, body, headers, msg="HTTP Error 401: Unauthorized")
    detail = _sendgrid_error_detail(exc)
    assert detail["error_type"] == "_FakeHTTPError"
    assert "Unauthorized" in str(detail["error_message"])
    assert detail["sendgrid_status"] == 401
    # body decoded from bytes to str.
    assert "authorization grant is invalid" in detail["sendgrid_body"]
    # JSON body with errors[] parsed into a separate structured field.
    assert detail["sendgrid_errors"] == [{
        "message": ("The provided authorization grant is invalid, "
                    "expired, or revoked"),
        "field": None, "help": None,
    }]
    # Headers carried through as a string-keyed dict.
    assert detail["sendgrid_headers"]["X-Request-Id"] == "abcd-1234"


def test_body_truncated_at_2000_chars():
    from auth import _sendgrid_error_detail

    big_body = b'{"errors":[{"message":"' + (b"x" * 3000) + b'"}]}'
    exc = _FakeHTTPError(401, big_body, {})
    detail = _sendgrid_error_detail(exc)
    body_text = detail["sendgrid_body"]
    assert isinstance(body_text, str)
    assert len(body_text) <= 2000 + len("…(truncated)")
    assert body_text.endswith("…(truncated)")


def test_non_json_body_still_surfaces_as_text():
    from auth import _sendgrid_error_detail

    # SendGrid usually returns JSON but a misbehaving response or an
    # intermediate proxy might return plain text. The helper must
    # still surface it — log readers need SOMETHING to read.
    exc = _FakeHTTPError(503, b"Service Unavailable: gateway timeout", {})
    detail = _sendgrid_error_detail(exc)
    assert detail["sendgrid_status"] == 503
    assert detail["sendgrid_body"] == "Service Unavailable: gateway timeout"
    # No errors[] field — JSON parse silently skipped.
    assert "sendgrid_errors" not in detail


def test_str_body_decoded_unchanged():
    """Some SDK versions return body as str (not bytes). The helper
    handles both — the decode step is a no-op on a str."""
    from auth import _sendgrid_error_detail

    exc = _FakeHTTPError(429, '{"errors":[{"message":"Too many requests"}]}',
                          {"Retry-After": "30"})
    detail = _sendgrid_error_detail(exc)
    assert "Too many requests" in detail["sendgrid_body"]
    assert detail["sendgrid_errors"] == [{"message": "Too many requests"}]
    assert detail["sendgrid_headers"]["Retry-After"] == "30"


def test_undecodable_bytes_replaced_rather_than_raising():
    """A malformed UTF-8 byte in the body must not crash the log path —
    `errors="replace"` ensures the helper always returns a string."""
    from auth import _sendgrid_error_detail

    bad_bytes = b'\xff\xfe\x00 unauthorized'
    exc = _FakeHTTPError(401, bad_bytes, {})
    detail = _sendgrid_error_detail(exc)
    # No raise; the str is whatever 'replace' gave us — never empty.
    assert isinstance(detail["sendgrid_body"], str)
    assert "unauthorized" in detail["sendgrid_body"]


def test_headers_capped_at_50_entries():
    from auth import _sendgrid_error_detail

    huge_headers = {f"X-Header-{i}": f"v{i}" for i in range(200)}
    exc = _FakeHTTPError(401, b"{}", huge_headers)
    detail = _sendgrid_error_detail(exc)
    assert isinstance(detail["sendgrid_headers"], dict)
    assert len(detail["sendgrid_headers"]) == 50


def test_non_httperror_exception_returns_only_type_and_message():
    """A connection error or a TypeError raised before the HTTP round
    trip has no status / body / headers. The helper must still return
    a dict — never raise on the missing fields."""
    from auth import _sendgrid_error_detail

    exc = ConnectionError("DNS resolution failed")
    detail = _sendgrid_error_detail(exc)
    assert detail["error_type"] == "ConnectionError"
    assert detail["error_message"] == "DNS resolution failed"
    # Optional fields absent — no status / body / headers / errors.
    for absent in ("sendgrid_status", "sendgrid_body",
                   "sendgrid_headers", "sendgrid_errors"):
        assert absent not in detail


def test_helper_never_raises_on_pathological_input():
    """A truly broken exception — body raises an attribute error on
    access, headers is non-iterable — must not crash the helper."""
    from auth import _sendgrid_error_detail

    class _Broken(Exception):
        status_code = 401

        @property
        def body(self):  # type: ignore[override]
            raise RuntimeError("broken body accessor")

        @property
        def headers(self):  # type: ignore[override]
            return object()  # not dict-like; .items() will fail

    # No raise from the helper — defensive getattr/try wrap each
    # field access.
    detail = _sendgrid_error_detail(_Broken("boom"))
    assert detail["error_type"] == "_Broken"
    assert detail["sendgrid_status"] == 401
