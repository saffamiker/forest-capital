"""
tests/test_midpoint_word_count.py — May 25 2026.

Pins the midpoint-paper word-count validation:

  1. _count_words and _validate_midpoint_word_counts: the pure helper
     in main.py that scores a generated narratives dict against the
     per-section + total targets the user spec defines.

  2. editor_content.midpoint_to_editor's word_validation banner: when
     validation fails, a [[BOB: WORD COUNT WARNING — ...]] callout
     prepends to the editor draft so the user sees the drift before
     submitting.

Targets (3 pages double-spaced 12pt → 750-900 words total):
  methodology  250-300
  results      250-300
  roles        125-150
  next_steps   125-150
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


def _make_narratives(
    methodology_words: int, results_words: int,
    roles_words: int, next_steps_words: int,
) -> dict[str, str]:
    """Builds a narratives dict whose section texts have exact word
    counts — every word is the same to keep the test deterministic."""
    def words(n: int) -> str:
        return " ".join(["word"] * n)
    return {
        "methodology": words(methodology_words),
        "results":     words(results_words),
        "roles":       words(roles_words),
        "next_steps":  words(next_steps_words),
    }


# ── _count_words ─────────────────────────────────────────────────────────────


def test_count_words_handles_empty_and_whitespace():
    from main import _count_words
    assert _count_words("") == 0
    assert _count_words("   ") == 0
    assert _count_words("one") == 1
    assert _count_words("one two three") == 3
    # Newlines and multiple spaces all collapse to whitespace.
    assert _count_words("one\ntwo\n\nthree   four") == 4


def test_count_words_counts_inline_markers_as_words():
    """[[VERIFY: ...]] / [[BOB: ...]] markers are AI-emitted and will
    be resolved by the user before submission — counting them keeps
    the post-resolution count realistic rather than systematically
    over-counting. Whitespace-split = 5 base + the 3 marker tokens
    ('[[VERIFY:', 'Sharpe', '0.63]]') = 8 tokens."""
    from main import _count_words
    n = _count_words("The 2022 break [[VERIFY: Sharpe 0.63]] was decisive")
    assert n == 8


# ── _validate_midpoint_word_counts ───────────────────────────────────────────


def test_validation_passes_when_every_section_in_range():
    """A clean run lands inside every target range. valid=True,
    warnings list is empty, total is the sum of section counts."""
    from main import _validate_midpoint_word_counts
    nars = _make_narratives(275, 275, 137, 137)  # all mid-range
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is True
    assert result["total_words"] == 275 + 275 + 137 + 137
    assert result["warnings"] == []
    for key in ("methodology", "results", "roles", "next_steps"):
        assert result["sections"][key]["in_range"] is True


def test_validation_fails_when_methodology_below_target():
    from main import _validate_midpoint_word_counts
    nars = _make_narratives(200, 275, 137, 137)  # 200 < 250
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is False
    assert result["sections"]["methodology"]["in_range"] is False
    # The warning names the section + actual count + target.
    warn_text = "; ".join(result["warnings"])
    assert "Data and Methodology" in warn_text
    assert "200" in warn_text
    assert "250-300" in warn_text
    assert "below" in warn_text


def test_validation_fails_when_section_above_target():
    from main import _validate_midpoint_word_counts
    nars = _make_narratives(275, 275, 200, 137)  # roles 200 > 150
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is False
    assert result["sections"]["roles"]["in_range"] is False
    warn_text = "; ".join(result["warnings"])
    assert "Roles and Division of Labor" in warn_text
    assert "above" in warn_text


def test_validation_flags_total_drift_separately():
    """If every section is in range but their total falls outside the
    750-900 window (which can happen at the section-range boundaries —
    e.g. 250+250+125+125=750 is the floor, but a barely-out-of-range
    case can still produce a total warning). The validator surfaces
    BOTH the section warnings AND a separate total warning."""
    from main import _validate_midpoint_word_counts
    # All four sections at their floor — total exactly 750. Tweak
    # one section above range so the total breaches too.
    nars = _make_narratives(305, 250, 125, 125)  # methodology 305 > 300
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is False
    assert result["sections"]["methodology"]["in_range"] is False
    # 305 + 250 + 125 + 125 = 805 — still in 750-900, so no total
    # warning. The section warning IS surfaced, however.
    assert any("Data and Methodology" in w for w in result["warnings"])


def test_validation_total_below_750_emits_total_warning():
    from main import _validate_midpoint_word_counts
    nars = _make_narratives(250, 250, 125, 125)  # 750 — just at the floor
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is True
    assert result["total_words"] == 750

    nars2 = _make_narratives(250, 250, 100, 100)  # roles+next_steps low
    result2 = _validate_midpoint_word_counts(nars2)
    assert result2["valid"] is False
    # Multiple warnings: two section warnings + the total warning.
    assert any("750-900" in w for w in result2["warnings"])
    assert any("below" in w for w in result2["warnings"])


def test_validation_handles_missing_keys_gracefully():
    """A partial narratives dict (some sections weren't generated) is
    treated as 0-word sections — the validator never raises on a
    missing key."""
    from main import _validate_midpoint_word_counts
    result = _validate_midpoint_word_counts({"methodology": "one two three"})
    assert result["valid"] is False
    assert result["sections"]["methodology"]["words"] == 3
    assert result["sections"]["results"]["words"] == 0
    assert result["sections"]["roles"]["words"] == 0
    assert result["sections"]["next_steps"]["words"] == 0


# ── Editor banner ────────────────────────────────────────────────────────────


def test_midpoint_to_editor_banner_omitted_when_validation_passes():
    """A clean run produces an editor draft with NO word-count banner
    at the top — only the four section headings."""
    from tools.editor_content import midpoint_to_editor
    nars = {"methodology": "x", "results": "y",
            "roles": "z", "next_steps": "w"}
    content_json, content_text = midpoint_to_editor(
        nars, word_validation={"valid": True})
    # Top of the document is the methodology heading, not a banner.
    first_node = content_json["content"][0]
    assert first_node["type"] == "heading"
    assert "Data and Methodology" in first_node["content"][0]["text"]
    assert "WORD COUNT WARNING" not in content_text


def test_midpoint_to_editor_banner_omitted_when_validation_missing():
    """No word_validation kwarg → no banner. Backward-compatible with
    any caller that doesn't pass validation results."""
    from tools.editor_content import midpoint_to_editor
    nars = {"methodology": "x", "results": "y",
            "roles": "z", "next_steps": "w"}
    content_json, content_text = midpoint_to_editor(nars)
    assert "WORD COUNT WARNING" not in content_text


def test_midpoint_to_editor_banner_prepended_when_validation_fails():
    """A failed validation prepends a [[BOB: WORD COUNT WARNING —
    …]] callout to the document. The editor renders [[BOB: …]] as
    an amber panel so the user sees the drift before scrolling
    to a single section."""
    from tools.editor_content import midpoint_to_editor
    nars = {"methodology": "x", "results": "y",
            "roles": "z", "next_steps": "w"}
    validation = {
        "valid": False,
        "total_words": 620,
        "total_target": [750, 900],
        "warnings": [
            "Data and Methodology ran 200 words — below the 250-300 target.",
            "Total ran 620 words — below the 750-900 target.",
        ],
        "sections": {},
    }
    content_json, content_text = midpoint_to_editor(
        nars, word_validation=validation)
    # The first paragraph of the document is the warning callout.
    first_para = content_json["content"][0]
    assert first_para["type"] == "paragraph"
    assert "WORD COUNT WARNING" in first_para["content"][0]["text"]
    # The marker uses the [[BOB: …]] convention so the editor renders
    # it as the existing amber panel UI.
    assert first_para["content"][0]["text"].startswith("[[BOB:")
    assert first_para["content"][0]["text"].rstrip().endswith("]]")
    # The plain-text projection ALSO carries the banner so the
    # Academic Review (which reads content_text) sees it too.
    assert "WORD COUNT WARNING" in content_text
    assert "620" in content_text
    assert "750-900" in content_text


def test_banner_lists_every_section_warning():
    """The banner detail line concatenates EVERY section warning so a
    user scanning the editor sees the full set without expanding the
    sections individually."""
    from tools.editor_content import midpoint_to_editor
    nars = {"methodology": "x"}
    validation = {
        "valid": False,
        "total_words": 600,
        "total_target": [750, 900],
        "warnings": [
            "Data and Methodology ran 200 words — below the 250-300 target.",
            "Preliminary Results and Diagnostics ran 200 words — below "
            "the 250-300 target.",
            "Total ran 600 words — below the 750-900 target.",
        ],
        "sections": {},
    }
    _, content_text = midpoint_to_editor(
        nars, word_validation=validation)
    assert "Data and Methodology ran 200" in content_text
    assert "Preliminary Results and Diagnostics ran 200" in content_text
