"""tests/test_deck_slide_guidance.py -- slide guidance upload
validator + template builder + merge helper (June 22 2026).

The validator is rigid by design: exact key set, all values
strings, all 12 slide numbers, version + generated_from must
match, length limits per field. These tests pin every rule so
a future loosening of the validator (e.g. accepting partial
uploads) breaks the test rather than silently shipping.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

from tools.deck_slide_guidance import (  # noqa: E402
    TEMPLATE_VERSION,
    build_default_template,
    count_overridden_slides,
    merge_guidance_into_slide_plan_entry,
    validate_guidance,
)


# ── Template builder ────────────────────────────────────────────────


class TestBuildDefaultTemplate:

    def test_template_carries_required_top_level_fields(self):
        t = build_default_template()
        assert t["version"] == TEMPLATE_VERSION
        assert isinstance(t["generated_from"], str)
        assert t["generated_from"]
        assert isinstance(t["slides"], dict)

    def test_all_12_slides_present(self):
        t = build_default_template()
        keys = set(t["slides"].keys())
        assert keys == {str(i) for i in range(1, 13)}

    def test_each_slide_has_all_5_overridable_fields(self):
        t = build_default_template()
        for n in range(1, 13):
            slide = t["slides"][str(n)]
            for field_name in (
                    "title", "so_what", "max_bullets",
                    "bullet_guidance",
                    "speaker_note_directive"):
                assert field_name in slide, (
                    f"slide {n}: {field_name} missing")
                assert isinstance(slide[field_name], str), (
                    f"slide {n}: {field_name} must be string")

    def test_table_heavy_slides_default_max_bullets_2(self):
        t = build_default_template()
        for n in (4, 6, 7, 8, 9, 12):
            assert t["slides"][str(n)]["max_bullets"] == "2"

    def test_non_table_slides_default_max_bullets_3(self):
        t = build_default_template()
        for n in (1, 2, 3, 5, 10, 11):
            assert t["slides"][str(n)]["max_bullets"] == "3"

    def test_template_round_trips_through_validator(self):
        """The downloaded template MUST validate as-is. If it
        doesn't, Molly's first edit-and-upload cycle fails for
        a non-obvious reason."""
        t = build_default_template()
        clean, error = validate_guidance(t)
        assert error is None, (
            f"default template failed validation: {error}")
        assert clean == t


# ── Validator -- top-level ──────────────────────────────────────────


class TestValidatorTopLevel:

    def _good(self) -> dict:
        return build_default_template()

    def test_non_dict_rejected(self):
        _, err = validate_guidance("not a dict")
        assert err is not None
        assert "JSON object" in err

    def test_missing_version_rejected(self):
        payload = self._good()
        del payload["version"]
        _, err = validate_guidance(payload)
        assert err is not None
        assert "version" in err

    def test_missing_generated_from_rejected(self):
        payload = self._good()
        del payload["generated_from"]
        _, err = validate_guidance(payload)
        assert err is not None
        assert "generated_from" in err

    def test_missing_slides_rejected(self):
        payload = self._good()
        del payload["slides"]
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides" in err

    def test_unexpected_top_level_field_rejected(self):
        payload = self._good()
        payload["extra"] = "nope"
        _, err = validate_guidance(payload)
        assert err is not None
        assert "extra" in err

    def test_version_must_be_int(self):
        payload = self._good()
        payload["version"] = "1"
        _, err = validate_guidance(payload)
        assert err is not None
        assert "version" in err and "integer" in err

    def test_version_mismatch_returns_download_link_hint(self):
        """The user requirement: version mismatch error must
        include the GET /api/v1/deck/slide-guidance/template
        download link so Molly knows how to recover."""
        payload = self._good()
        payload["version"] = TEMPLATE_VERSION + 1
        _, err = validate_guidance(payload)
        assert err is not None
        assert "version mismatch" in err
        assert "/api/v1/deck/slide-guidance/template" in err
        assert "Download" in err or "download" in err


# ── Validator -- slides dict ────────────────────────────────────────


class TestValidatorSlidesDict:

    def _good(self) -> dict:
        return build_default_template()

    def test_extra_slide_number_rejected(self):
        payload = self._good()
        payload["slides"]["13"] = payload["slides"]["1"]
        _, err = validate_guidance(payload)
        assert err is not None
        assert "13" in err
        assert "1" in err and "12" in err

    def test_partial_upload_rejected(self):
        """No partial uploads -- all 12 slides must be
        present."""
        payload = self._good()
        del payload["slides"]["7"]
        _, err = validate_guidance(payload)
        assert err is not None
        assert "missing slide" in err
        assert "7" in err

    def test_slides_must_be_dict(self):
        payload = self._good()
        payload["slides"] = []
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides" in err and "object" in err


# ── Validator -- per-slide rigid fields ─────────────────────────────


class TestValidatorPerSlideFields:

    def _good(self) -> dict:
        return build_default_template()

    def test_missing_field_returns_exact_path(self):
        """User spec: 'slides.7.bullet_guidance is missing'."""
        payload = self._good()
        del payload["slides"]["7"]["bullet_guidance"]
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.7" in err
        assert "bullet_guidance" in err

    def test_unexpected_field_rejected(self):
        payload = self._good()
        payload["slides"]["3"]["extra_field"] = "nope"
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.3" in err
        assert "extra_field" in err

    def test_non_string_value_rejected_with_field_path(self):
        payload = self._good()
        payload["slides"]["5"]["max_bullets"] = 2  # int, not str
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.5.max_bullets" in err
        assert "string" in err

    def test_title_length_limit_120(self):
        """User spec: 'slides.3.title exceeds 120 character
        limit'."""
        payload = self._good()
        payload["slides"]["3"]["title"] = "x" * 121
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.3.title" in err
        assert "120" in err

    def test_so_what_length_limit_200(self):
        payload = self._good()
        payload["slides"]["1"]["so_what"] = "x" * 201
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.1.so_what" in err
        assert "200" in err

    def test_bullet_guidance_length_limit_300(self):
        payload = self._good()
        payload["slides"]["1"]["bullet_guidance"] = "x" * 301
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.1.bullet_guidance" in err
        assert "300" in err

    def test_speaker_note_directive_length_limit_300(self):
        payload = self._good()
        payload["slides"]["1"]["speaker_note_directive"] = (
            "x" * 301)
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.1.speaker_note_directive" in err
        assert "300" in err

    def test_max_bullets_must_be_numeric_string_in_range(self):
        payload = self._good()
        payload["slides"]["1"]["max_bullets"] = "abc"
        _, err = validate_guidance(payload)
        assert err is not None
        assert "slides.1.max_bullets" in err
        assert ("numeric" in err.lower()
                or "0" in err)

    def test_max_bullets_out_of_range_rejected(self):
        payload = self._good()
        payload["slides"]["1"]["max_bullets"] = "5"
        _, err = validate_guidance(payload)
        assert err is not None
        assert "max_bullets" in err

    def test_good_payload_passes(self):
        clean, err = validate_guidance(self._good())
        assert err is None
        assert clean is not None


# ── Merge ───────────────────────────────────────────────────────────


class TestMergeGuidance:

    def _guidance(self) -> dict:
        t = build_default_template()
        t["slides"]["3"]["title"] = "My Custom Slide 3 Title"
        t["slides"]["3"]["max_bullets"] = "1"
        t["slides"]["3"]["so_what"] = "Custom framing for 3"
        return t

    def test_no_guidance_passes_entry_through(self):
        entry = {"slide_number": 3, "title": "Default",
                 "max_bullets": 3}
        result = merge_guidance_into_slide_plan_entry(
            entry, 3, None)
        assert result is entry

    def test_title_override_applied(self):
        entry = {"slide_number": 3, "title": "Default"}
        result = merge_guidance_into_slide_plan_entry(
            entry, 3, self._guidance())
        assert result["title"] == "My Custom Slide 3 Title"

    def test_max_bullets_coerced_to_int(self):
        """The guidance file stores max_bullets as a string per
        the rigid schema; merge_guidance coerces to int because
        _generate_one_deck_slide expects int."""
        entry = {"slide_number": 3, "max_bullets": 3}
        result = merge_guidance_into_slide_plan_entry(
            entry, 3, self._guidance())
        assert result["max_bullets"] == 1
        assert isinstance(result["max_bullets"], int)

    def test_so_what_lands_in_user_guidance_subdict(self):
        """so_what / bullet_guidance / speaker_note_directive
        flow into a _user_guidance sub-dict the per-slide
        writer surfaces in the prompt -- the plan_entry has no
        explicit slot for them."""
        entry = {"slide_number": 3}
        result = merge_guidance_into_slide_plan_entry(
            entry, 3, self._guidance())
        assert "_user_guidance" in result
        assert (result["_user_guidance"]["so_what"]
                == "Custom framing for 3")

    def test_non_overridable_fields_preserved(self):
        """numeric_anchors, chart_references, headline,
        key_visual, slide_bullets stay untouched."""
        entry = {
            "slide_number": 3,
            "title": "Default",
            "headline": "Locked headline",
            "key_visual": "rolling_correlation",
            "numeric_anchors": {"x": 1, "y": 2},
            "slide_bullets": ["a", "b"],
        }
        result = merge_guidance_into_slide_plan_entry(
            entry, 3, self._guidance())
        # Title overridden.
        assert result["title"] == "My Custom Slide 3 Title"
        # Everything else preserved.
        assert result["headline"] == "Locked headline"
        assert result["key_visual"] == "rolling_correlation"
        assert result["numeric_anchors"] == {"x": 1, "y": 2}
        assert result["slide_bullets"] == ["a", "b"]

    def test_merge_returns_new_dict(self):
        """Never mutate the input plan entry."""
        entry = {"slide_number": 3, "title": "Default"}
        original_title = entry["title"]
        merge_guidance_into_slide_plan_entry(
            entry, 3, self._guidance())
        assert entry["title"] == original_title

    def test_every_slide_gets_template_values_applied(self):
        """The rigid schema requires all 12 slides to be present
        in every upload, so 'unrelated slide' doesn't exist --
        every slide gets its template title applied via merge.
        Even when the user only customised slide 3, slides 1-12
        all get the template's seeded values overlaid (which
        equal the defaults for the slides she didn't edit).
        That's correct behaviour: a user who downloads, edits
        slide 3, and re-uploads gets the same defaults for
        everything else."""
        entry = {"slide_number": 5, "title": "Existing"}
        result = merge_guidance_into_slide_plan_entry(
            entry, 5, self._guidance())
        # Slide 5's template default title flows in -- which
        # exactly matches what the user uploaded for slide 5
        # (the default she didn't edit).
        from tools.academic_deck import SLIDE_TITLES
        assert result.get("title") == SLIDE_TITLES[4]


# ── count_overridden_slides ─────────────────────────────────────────


class TestCountOverriddenSlides:

    def test_none_returns_zero(self):
        assert count_overridden_slides(None) == 0

    def test_default_template_has_overrides_seeded(self):
        """The default template seeds every slide with a so_what
        + bullet_guidance + speaker_note_directive + max_bullets
        + title -- so count_overridden_slides returns 12 even
        on an unmodified upload. The operator log surfaces the
        count so a zero-count log is the signal of an unwarranted
        upload."""
        t = build_default_template()
        assert count_overridden_slides(t) == 12

    def test_empty_string_fields_do_not_count(self):
        t = build_default_template()
        # Wipe every field on slide 3 to empty string.
        for f in t["slides"]["3"]:
            t["slides"]["3"][f] = ""
        # Slide 3 has no field with content; still 11 others.
        assert count_overridden_slides(t) == 11
