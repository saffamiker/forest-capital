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
  methodology  235-285
  results      235-285
  roles        110-135
  next_steps   110-135

May 25 2026 — shaved 15 from each section's range. The original
250-300/125-150 split summed to 750-900 with no headroom; the new
ranges sum to 690-840 so the 60-100 words of empirical-citation
overhead (4 findings × ~15-25 words per inline citation) lives in
the gap between section sums and the unchanged 750-900 total.
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
    warnings list is empty, total is the sum of section counts.
    Mid-range numbers picked so the total (260+260+122+122=764)
    also falls inside 750-900 — both gates pass independently."""
    from main import _validate_midpoint_word_counts
    nars = _make_narratives(260, 260, 122, 122)  # all mid-range
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is True
    assert result["total_words"] == 260 + 260 + 122 + 122
    assert result["warnings"] == []
    for key in ("methodology", "results", "roles", "next_steps"):
        assert result["sections"][key]["in_range"] is True


def test_validation_fails_when_methodology_below_target():
    from main import _validate_midpoint_word_counts
    nars = _make_narratives(200, 260, 122, 122)  # 200 < 235
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is False
    assert result["sections"]["methodology"]["in_range"] is False
    # The warning names the section + actual count + target.
    warn_text = "; ".join(result["warnings"])
    assert "Data and Methodology" in warn_text
    assert "200" in warn_text
    assert "235-285" in warn_text
    assert "below" in warn_text


def test_validation_fails_when_section_above_target():
    from main import _validate_midpoint_word_counts
    nars = _make_narratives(260, 260, 180, 122)  # roles 180 > 135
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is False
    assert result["sections"]["roles"]["in_range"] is False
    warn_text = "; ".join(result["warnings"])
    assert "Roles and Division of Labor" in warn_text
    assert "above" in warn_text


def test_validation_flags_section_drift_independent_of_total():
    """A section can be out of range while the total stays inside
    750-900 — the validator surfaces the section warning regardless."""
    from main import _validate_midpoint_word_counts
    # methodology 290 > 285 ceiling. 290+260+122+122=794 — total OK.
    nars = _make_narratives(290, 260, 122, 122)
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is False
    assert result["sections"]["methodology"]["in_range"] is False
    assert any("Data and Methodology" in w for w in result["warnings"])


def test_section_floor_with_citations_lands_at_total_floor():
    """The math behind the May 25 2026 shave: section sums at the
    floor (235+235+110+110=690) PLUS ~60 words of citation overhead =
    750, the total floor. The validator checks sections and total
    independently — a paper at section-floor without citations would
    flag a total-below-750 warning, which is the desired signal."""
    from main import _validate_midpoint_word_counts
    # Sections at their floors, no citation padding — total 690 below
    # the 750 floor. Sections pass; total warning fires.
    nars = _make_narratives(235, 235, 110, 110)
    result = _validate_midpoint_word_counts(nars)
    assert result["sections"]["methodology"]["in_range"] is True
    assert result["sections"]["results"]["in_range"] is True
    assert result["sections"]["roles"]["in_range"] is True
    assert result["sections"]["next_steps"]["in_range"] is True
    assert result["total_words"] == 690
    assert result["valid"] is False  # total under floor
    assert any("750-900" in w for w in result["warnings"])
    assert any("below" in w for w in result["warnings"])


def test_section_floor_plus_citation_padding_passes():
    """Sections at the floor PLUS realistic citation overhead lands
    at the total floor — every gate passes. Simulates a real run
    where the prose hits the section floor and 4 citations add ~60
    words of overhead distributed across the body."""
    from main import _validate_midpoint_word_counts
    # 250+250+110+110 = 720 — still below 750 floor (sections still
    # in range; methodology and results have absorbed ~15 words of
    # citation overhead each but the total hasn't yet reached 750).
    # 250+250+125+125 = 750 — exactly at the floor.
    nars = _make_narratives(250, 250, 125, 125)
    result = _validate_midpoint_word_counts(nars)
    assert result["valid"] is True
    assert result["total_words"] == 750


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
            "Data and Methodology ran 200 words — below the 235-285 target.",
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
            "Data and Methodology ran 200 words — below the 235-285 target.",
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
