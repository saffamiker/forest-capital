"""tools/editor_save_numeric_check.py -- June 28 2026.

Touchpoint 5 of the hard-lock numeric guardrail. Runs on every
PATCH /api/v1/documents/drafts/{draft_id} save call. Scans the
incoming content_json (TipTap doc tree) for free-text numerics
not backed by a {{TOKEN}} from the substitution table.

Different from the harness-time check at touchpoints 1-4:
  * WARN, do not BLOCK -- the operator is in the editor + the
    save must always succeed. The warning surfaces in the UI
    as a dismissible banner.
  * Logs every offender to editor_numeric_overrides for the
    permanent audit trail (migration 066).
  * Token_value nodes (dual-mode storage from PR-DM-Lite) are
    SKIPPED -- the value inside attrs.resolved is by definition
    token-backed via attrs.token. Only PLAIN TEXT nodes feed
    the scanner.

Returns a list of warning records the endpoint includes in its
response. The frontend reads response.warnings + renders the
dismissible banner.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:
    log = logging.getLogger(__name__)  # type: ignore[assignment]


# ── TipTap text extraction (token_value-aware) ─────────────────


def _extract_plain_text(node: Any) -> str:
    """Flatten a TipTap subtree to plain text, SKIPPING
    token_value node text entirely. Only text the operator could
    have typed (plain text leaves outside token_value wrappers)
    feeds the scanner. This is the key difference from the
    harness-time check, which scans the raw substitution-bearing
    prose; here we have already-rendered content_json that may
    carry token_value nodes for the platform-managed values."""
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == "token_value":
        # Skip entirely -- the value inside is token-backed.
        return ""
    if node.get("text"):
        return str(node["text"])
    parts = [
        _extract_plain_text(c)
        for c in (node.get("content") or [])
    ]
    return "".join(parts)


# ── Scanner entry point ─────────────────────────────────────────


def scan_editor_save_for_untoken_numerics(
    content_json: dict[str, Any] | None,
    substitution_table: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Walks content_json + returns one warning dict per
    untoken-backed numeric found in plain-text nodes.

    Each warning carries:
      {
        offending_value: str    -- the raw numeric string
        sentence:        str    -- 200-char surrounding context
        suggested_token: str|None -- closest matching {{TOKEN}}
                                     if value is in substitution
                                     table; None otherwise
        severity:        str    -- 'token_available' / 'unsupported'
      }

    Empty list when content is clean. Fail-open on any error
    (the save must always succeed; warning is best-effort).
    """
    if not isinstance(content_json, dict):
        return []
    if not substitution_table:
        # Without a substitution table, the scanner has no way to
        # know what's token-backed. Fail-open with no warnings.
        return []

    try:
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = _extract_plain_text(content_json)
        if not text:
            return []
        violations = find_untoken_backed_numerics(
            text, substitution_table)
        out: list[dict[str, Any]] = []
        for v in violations:
            out.append({
                "offending_value": v.raw_value,
                "sentence":        v.sentence,
                "suggested_token": v.suggested_token,
                "severity":        v.severity,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "editor_save_numeric_check_failed",
            error=str(exc))
        return []


# ── Persist to audit trail ─────────────────────────────────────


async def log_editor_overrides(
    draft_id: int,
    document_type: str | None,
    user_email: str,
    warnings: list[dict[str, Any]],
) -> int:
    """Insert one row per warning into editor_numeric_overrides
    (migration 066). Returns the count of rows inserted.

    Fail-open: a DB error here never breaks the save. The
    warning still surfaces in the response so the operator
    sees it even when persistence fails."""
    if not warnings:
        return 0
    try:
        from database import AsyncSessionLocal
        from sqlalchemy import text as _text
        if AsyncSessionLocal is None:
            return 0
        async with AsyncSessionLocal() as s:
            for w in warnings:
                await s.execute(_text(
                    "INSERT INTO editor_numeric_overrides "
                    "(draft_id, document_type, user_email, "
                    " offending_value, sentence_context, "
                    " suggested_token, saved_at) "
                    "VALUES (:d, :dt, :u, :v, :s, :t, :ts)"),
                    {
                        "d":  draft_id,
                        "dt": document_type,
                        "u":  user_email,
                        "v":  str(w.get("offending_value", ""))[:64],
                        "s":  str(w.get("sentence", ""))[:1000],
                        "t":  (w.get("suggested_token")
                               if w.get("suggested_token")
                               else None),
                        "ts": datetime.now(timezone.utc),
                    })
            await s.commit()
        log.info(
            "editor_numeric_overrides_logged",
            draft_id=draft_id,
            user_email=user_email,
            count=len(warnings))
        return len(warnings)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "editor_numeric_overrides_persist_failed",
            draft_id=draft_id,
            error=str(exc))
        return 0
