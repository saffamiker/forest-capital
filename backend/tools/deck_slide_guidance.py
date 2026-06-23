"""tools/deck_slide_guidance.py -- per-user slide guidance for the
presentation deck (June 22 2026).

Molly uploads a JSON file via the platform UI; the deck generation
pipeline merges per-slide overrides on top of the hardcoded defaults
in SLIDE_SPECIFICATIONS at generation time. Lets the team iterate on
slide tone, framing, and bullet caps without code changes.

CONTRACT

  TEMPLATE_VERSION
    Integer; the version of the template schema. When the schema
    changes (e.g. a new overridable field), bump this constant +
    the regenerated template's version field. Old uploads are
    rejected with an error pointing to the download link.

  build_default_template(data_hash) -> dict
    Generates the canonical default guidance from SLIDE_TITLES +
    SLIDE_SPECIFICATIONS. Returns the EXACT shape Molly downloads,
    edits, and re-uploads. Every field she might edit is already
    populated with the current default value so she edits strings
    only.

  validate_guidance(payload) -> (clean_payload, error_message)
    Rigid validator. Returns (clean_payload, None) on pass,
    (None, error_message_str) on failure. Rules:
      - Exact key set: every key in the template must be present,
        no additional keys.
      - All values must be strings.
      - All 12 slide numbers ("1" through "12") must be present.
      - version + generated_from match TEMPLATE_VERSION / current
        template build identifier.
      - String length limits per field.
    Error message names the exact field path and what went wrong,
    in plain English.

  merge_guidance(slide_number, default_entry, guidance) -> dict
    Overlays the per-slide guidance on top of the default entry.
    Non-overridable fields (numeric_anchors, chart_references,
    substitution_tokens) are NOT touched.

  Async DB helpers:
    set_active_guidance(owner_email, payload)
    get_active_guidance(owner_email) -> dict | None
    clear_active_guidance(owner_email) -> bool
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Bump when the overridable field set changes. The downloaded
# template carries this version; uploads with a mismatched version
# are rejected with a "download fresh template" hint.
TEMPLATE_VERSION = 1


# Slides 1-12 (matches DECK_SLIDE_COUNT).
_SLIDE_NUMBERS: tuple[str, ...] = tuple(str(i) for i in range(1, 13))

# Overridable field names per slide. Fixed set -- adding a field
# requires bumping TEMPLATE_VERSION.
_OVERRIDABLE_FIELDS: tuple[str, ...] = (
    "title",
    "so_what",
    "max_bullets",
    "bullet_guidance",
    "speaker_note_directive",
)

# Max character lengths per field. Enforced strictly; an overrun
# returns a 422 with the exact field path.
_LENGTH_LIMITS: dict[str, int] = {
    "title": 120,
    "so_what": 200,
    # max_bullets is a numeric string ("0".."3"); 4 chars is plenty
    "max_bullets": 4,
    "bullet_guidance": 300,
    "speaker_note_directive": 300,
}

# Top-level keys the template carries. Same rigidity as the per-
# slide fields: every key required, no additional keys.
_REQUIRED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "version", "generated_from", "slides",
})


def _default_so_what_for_slide(slide_number: int) -> str:
    """The SO WHAT framing default for each slide. Mirrors the
    locked titles -- every entry is the short answer to "what
    does this slide prove?" Used to seed the template so Molly
    has a starting point rather than a blank field."""
    defaults = {
        1: ("Headline proof point: dynamic regime-conditional "
            "beats 100% equity on OOS Sharpe."),
        2: ("Structural agenda walking the panel through the "
            "deck sections."),
        3: ("Frame the three strategies and the central "
            "question they answer."),
        4: ("Pin the canonical numbers behind the headline: "
            "0.86 / 0.43 across 53 OOS months."),
        5: ("Identify the 2022 correlation break as the "
            "diagnosis for static allocation underperformance."),
        6: ("Drawdown depth and recovery time -- the capital "
            "preservation story."),
        7: ("Confirm the OOS result holds up across the test "
            "window (no in-sample fit, no overfitting)."),
        8: ("Live regime watchpoints update at generation "
            "time -- VIX, yield curve, credit spread, equity "
            "trend."),
        9: ("Honest disclosure: 2 of 9 stress scenarios show "
            "the model degrading -- capital preservation, not "
            "market timing."),
        10: ("How the AI council was used + what the methodology "
             "passes that worked and didn't."),
        11: ("Open the live platform and walk through the "
             "regime classification + recommendation flow."),
        12: ("The investable recommendation, the conditions on "
             "it, and the failure modes to monitor."),
    }
    return defaults.get(slide_number, "")


def _default_bullet_guidance_for_slide(slide_number: int) -> str:
    """The bullet-writing guidance default. The BULLET DISCIPLINE
    block in SLIDE_SPECIFICATIONS already says "no more than N"
    -- this guidance lets Molly add slide-specific tone (e.g.
    "lead with the panel's likely first question")."""
    if slide_number in (4, 6, 7, 8, 9, 12):
        return (
            "Table-heavy slide. Bullets above the table are "
            "ORIENTING only -- name what the table is and why it "
            "matters. Do not re-state any cell value as a bullet.")
    return (
        "Bullets are \"because\" or \"which means\" -- never "
        "\"what\". The title states the what; the bullets "
        "interpret. Cut any bullet that does not add meaning.")


def _default_speaker_note_directive_for_slide(
        slide_number: int) -> str:
    """The speaker_note_directive seeds tone/cue for the speaker
    notes pass. Per-slide so the speaker can rehearse from the
    panel-facing prose without re-deriving the talking points."""
    if slide_number == 11:
        return (
            "Live demo step-by-step script -- four steps total, "
            "~50s each. Open analyticsdesk.app and narrate.")
    if slide_number == 12:
        return (
            "Recommendation framing: state the answer, state "
            "the conditions, name the failure modes the panel "
            "would ask about.")
    return (
        "Two-paragraph notes: paragraph 1 names the finding "
        "with one number; paragraph 2 says why it matters to "
        "the panel.")


def _default_max_bullets_for_slide(slide_number: int) -> str:
    """Default max_bullets ceiling per slide. Table-heavy slides
    get 2; non-table slides get 3. String-typed because all
    template values must be strings; coerced to int on read."""
    return "2" if slide_number in (4, 6, 7, 8, 9, 12) else "3"


def build_default_template(
    generated_from: str | None = None,
) -> dict[str, Any]:
    """Generate the canonical default guidance.

    Reads SLIDE_TITLES at call time so the template auto-tracks
    any title change (the PR-#384 locked titles flow through
    automatically once that PR lands).

    `generated_from` -- a build identifier the validator uses to
    reject uploads from a stale template. Defaults to
    "v{TEMPLATE_VERSION}-defaults" when omitted. Pass a deploy
    sha at call time if you want stricter "must come from this
    deploy" enforcement.
    """
    try:
        from tools.academic_deck import SLIDE_TITLES
    except Exception:  # noqa: BLE001
        # Test environment / circular import safety -- fall back
        # to the empty title list rather than raising; the caller
        # gets a structurally-valid template with empty titles.
        SLIDE_TITLES = [""] * 12
    if generated_from is None:
        generated_from = f"v{TEMPLATE_VERSION}-defaults"
    slides: dict[str, dict[str, str]] = {}
    for i, title in enumerate(SLIDE_TITLES[:12], start=1):
        slides[str(i)] = {
            "title": title or "",
            "so_what": _default_so_what_for_slide(i),
            "max_bullets": _default_max_bullets_for_slide(i),
            "bullet_guidance":
                _default_bullet_guidance_for_slide(i),
            "speaker_note_directive":
                _default_speaker_note_directive_for_slide(i),
        }
    return {
        "version": TEMPLATE_VERSION,
        "generated_from": generated_from,
        "slides": slides,
    }


def validate_guidance(
    payload: Any,
) -> tuple[dict | None, str | None]:
    """Rigid validator. Returns (clean_payload, None) on pass,
    (None, error_message) on failure.

    error_message names the exact field path that failed and
    what went wrong in plain English. The endpoint surfaces this
    string directly in the 422 response body so Molly sees
    exactly which field to edit.
    """
    if not isinstance(payload, dict):
        return None, "uploaded file must be a JSON object"

    # Top-level key set: exact match, no missing, no extras.
    extra = set(payload.keys()) - _REQUIRED_TOP_LEVEL_KEYS
    if extra:
        return None, (
            f"unexpected top-level field(s): "
            f"{', '.join(sorted(extra))}. "
            "Allowed: version, generated_from, slides.")
    missing = _REQUIRED_TOP_LEVEL_KEYS - set(payload.keys())
    if missing:
        return None, (
            f"missing required top-level field(s): "
            f"{', '.join(sorted(missing))}")

    # version: int matching TEMPLATE_VERSION.
    version = payload.get("version")
    if not isinstance(version, int):
        return None, (
            "version must be an integer; got "
            f"{type(version).__name__}")
    if version != TEMPLATE_VERSION:
        return None, (
            f"version mismatch: file is v{version}, current "
            f"template is v{TEMPLATE_VERSION}. Download the "
            "current template from "
            "GET /api/v1/deck/slide-guidance/template and "
            "re-apply your edits.")

    # generated_from: present (any string value is fine; we don't
    # enforce a specific build identifier today, just that the
    # field is present and non-empty so a future deploy-sha pin
    # can layer in without a schema bump).
    generated_from = payload.get("generated_from")
    if not isinstance(generated_from, str) or not generated_from:
        return None, (
            "generated_from must be a non-empty string -- copy "
            "this verbatim from the downloaded template")

    # slides: dict with all 12 keys "1".."12".
    slides = payload.get("slides")
    if not isinstance(slides, dict):
        return None, (
            "slides must be an object keyed by slide number "
            "string (\"1\" through \"12\")")
    extra_slides = set(slides.keys()) - set(_SLIDE_NUMBERS)
    if extra_slides:
        return None, (
            "unexpected slide number(s): "
            f"{', '.join(sorted(extra_slides))}. "
            "Valid keys are \"1\" through \"12\".")
    missing_slides = set(_SLIDE_NUMBERS) - set(slides.keys())
    if missing_slides:
        return None, (
            "missing slide(s): "
            f"{', '.join(sorted(missing_slides))}. "
            "All 12 slide numbers must be present -- partial "
            "uploads are not accepted.")

    # Per-slide fields: exact key set + types + length limits.
    for slide_number in _SLIDE_NUMBERS:
        slide = slides[slide_number]
        if not isinstance(slide, dict):
            return None, (
                f"slides.{slide_number} must be an object")
        extra_fields = set(slide.keys()) - set(_OVERRIDABLE_FIELDS)
        if extra_fields:
            return None, (
                f"slides.{slide_number}: unexpected field(s) "
                f"{', '.join(sorted(extra_fields))}. "
                f"Allowed: {', '.join(_OVERRIDABLE_FIELDS)}.")
        missing_fields = (
            set(_OVERRIDABLE_FIELDS) - set(slide.keys()))
        if missing_fields:
            return None, (
                f"slides.{slide_number}: missing field(s) "
                f"{', '.join(sorted(missing_fields))}. "
                "All overridable fields must be present.")
        for field_name in _OVERRIDABLE_FIELDS:
            value = slide[field_name]
            if not isinstance(value, str):
                return None, (
                    f"slides.{slide_number}.{field_name} "
                    "must be a string; got "
                    f"{type(value).__name__}")
            limit = _LENGTH_LIMITS.get(field_name)
            if limit is not None and len(value) > limit:
                return None, (
                    f"slides.{slide_number}.{field_name} "
                    f"exceeds {limit} character limit "
                    f"(currently {len(value)} chars)")
            # max_bullets must be a numeric string in [0, 3].
            if field_name == "max_bullets":
                try:
                    cap = int(value)
                    if not 0 <= cap <= 3:
                        raise ValueError
                except (TypeError, ValueError):
                    return None, (
                        f"slides.{slide_number}.max_bullets "
                        "must be a numeric string between "
                        f"\"0\" and \"3\"; got {value!r}")

    return payload, None


def merge_guidance_into_slide_plan_entry(
    slide_plan_entry: dict | None,
    slide_number: int,
    guidance: dict | None,
) -> dict | None:
    """Overlay the per-slide guidance overrides on top of a
    slide_plan_entry. Returns a NEW dict; never mutates the input.

    Non-overridable fields (numeric_anchors, chart_references,
    substitution_tokens, key_visual, slide_bullets,
    transition_to_next, headline) are preserved verbatim from
    the input plan_entry. Only the five overridable fields are
    touched.

    `max_bullets` is coerced from string to int on read since the
    plan_entry consumer (`_generate_one_deck_slide`) expects int.
    """
    if not guidance:
        return slide_plan_entry
    slides = (guidance.get("slides") or {})
    override = slides.get(str(slide_number))
    if not isinstance(override, dict):
        return slide_plan_entry
    merged = dict(slide_plan_entry or {})
    if "title" in override and override["title"]:
        merged["title"] = override["title"]
    if "max_bullets" in override:
        try:
            merged["max_bullets"] = int(override["max_bullets"])
        except (TypeError, ValueError):
            pass
    # The remaining fields are surfaced under a _guidance sub-key
    # the per-slide spec block reads. The plan_entry consumer
    # doesn't have explicit slots for so_what / bullet_guidance /
    # speaker_note_directive -- they flow into the prompt as
    # additional spec text the LLM follows.
    guidance_block: dict[str, str] = {}
    for field_name in (
            "so_what", "bullet_guidance",
            "speaker_note_directive"):
        if field_name in override and override[field_name]:
            guidance_block[field_name] = override[field_name]
    if guidance_block:
        merged["_user_guidance"] = guidance_block
    return merged


def count_overridden_slides(guidance: dict | None) -> int:
    """How many slides have at least one non-empty overridable
    field. Used by the deck_slide_guidance_applied log event so
    the operator can see what's in effect at a glance."""
    if not guidance:
        return 0
    slides = guidance.get("slides") or {}
    count = 0
    for slide_payload in slides.values():
        if not isinstance(slide_payload, dict):
            continue
        for field_name in _OVERRIDABLE_FIELDS:
            v = slide_payload.get(field_name)
            if isinstance(v, str) and v.strip():
                count += 1
                break
    return count


# ── DB helpers ──────────────────────────────────────────────────────


async def set_active_guidance(
    owner_email: str,
    payload: dict,
) -> dict[str, Any]:
    """Persist an active guidance row for `owner_email`.

    Deactivates any prior active row in the same transaction
    before inserting the new one -- enforces the "only one
    active per user" invariant procedurally.

    Returns {ok: True, id, uploaded_at} on success. Fail-open:
    DB unavailability returns {ok: False, error: ...} rather
    than raising.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"ok": False, "error": "db_unavailable"}
        async with AsyncSessionLocal() as session:
            # Deactivate prior active row(s).
            await session.execute(
                text("UPDATE deck_slide_guidance "
                     "SET is_active = false "
                     "WHERE owner_email = :e AND is_active = true"),
                {"e": owner_email})
            # Insert the new active row.
            res = await session.execute(
                text("INSERT INTO deck_slide_guidance "
                     "(owner_email, guidance_json, is_active) "
                     "VALUES (:e, CAST(:p AS JSONB), true) "
                     "RETURNING id, uploaded_at"),
                {"e": owner_email,
                 "p": json.dumps(payload)})
            row = res.fetchone()
            await session.commit()
            if row:
                return {
                    "ok": True,
                    "id": int(row[0]),
                    "uploaded_at": (
                        row[1].isoformat()
                        if hasattr(row[1], "isoformat")
                        else str(row[1])),
                }
            return {"ok": True, "id": None,
                    "uploaded_at": None}
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "deck_slide_guidance_set_failed",
            owner_email=owner_email, error=str(exc))
        return {"ok": False, "error": str(exc)}


async def get_active_guidance(
    owner_email: str,
) -> dict[str, Any] | None:
    """Read the active guidance row for `owner_email`. Returns
    the persisted dict (including version + generated_from +
    slides + the row's uploaded_at), or None when no active row
    exists / DB is unavailable."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("SELECT id, guidance_json, uploaded_at "
                     "FROM deck_slide_guidance "
                     "WHERE owner_email = :e AND is_active = true "
                     "ORDER BY uploaded_at DESC LIMIT 1"),
                {"e": owner_email})
            row = res.fetchone()
            if not row:
                return None
            payload = row[1]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
            if not isinstance(payload, dict):
                return None
            return {
                "id": int(row[0]),
                "guidance": payload,
                "uploaded_at": (
                    row[2].isoformat()
                    if hasattr(row[2], "isoformat")
                    else str(row[2])),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "deck_slide_guidance_get_failed",
            owner_email=owner_email, error=str(exc))
        return None


async def clear_active_guidance(owner_email: str) -> bool:
    """Deactivate every active row for `owner_email`. Idempotent
    -- returns True even when there were no active rows. Returns
    False only on DB error."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE deck_slide_guidance "
                     "SET is_active = false "
                     "WHERE owner_email = :e AND is_active = true"),
                {"e": owner_email})
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "deck_slide_guidance_clear_failed",
            owner_email=owner_email, error=str(exc))
        return False
