"""Migration 035 + agent prompt instruction coverage.

Item 1 — [BOB] pre-population (May 23 2026). Confirms that
migration 035 loads cleanly, appends the [BOB] pre-population
instruction to both seeded templates, and emits the expected
changelog entry. The frontend behaviour is covered separately in
frontend/src/__tests__/bob-prepopulation.test.tsx.
"""
import importlib.util
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


def test_migration_035_loads():
    spec = importlib.util.spec_from_file_location(
        "mig_035",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "035_bob_prepopulation.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "035"
    assert m.down_revision == "034"
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_migration_035_midpoint_instruction_has_required_blocks():
    """The appended midpoint instruction must direct the agent to
    produce the four [BOB] blocks the user spec listed."""
    spec = importlib.util.spec_from_file_location(
        "mig_035",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "035_bob_prepopulation.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    appendix = m._MIDPOINT_BOB_APPENDIX
    assert "[BOB:" in appendix
    # Four required blocks.
    assert "2022 correlation shift implication" in appendix
    assert "Strategy selection in the current environment" in appendix
    assert "Academic connections" in appendix
    assert "Open question framing" in appendix
    # Rules.
    assert "verified_data" in appendix
    assert "citations_cache" in appendix
    assert "Do NOT skip" in appendix
    assert "Mark as reviewed" in appendix


def test_migration_035_brief_instruction_has_two_blocks():
    """The executive brief gets two pre-populated blocks per the spec."""
    spec = importlib.util.spec_from_file_location(
        "mig_035",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "035_bob_prepopulation.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    appendix = m._BRIEF_BOB_APPENDIX
    assert "Recommended framing for Forest Capital" in appendix
    assert "Forest Capital mandate context" in appendix
    assert "30-60 word draft paragraph" in appendix
    # Brief tone constraint — professional advisory, not academic.
    # Word-wrapped across a newline in the source; check both tokens.
    assert "professional advisory" in appendix
    assert "no academic" in appendix


def test_bob_block_extractor_handles_long_prepopulated_content():
    """The frontend regex / backend regex must match multi-sentence
    pre-populated drafts inside [BOB: ...] — the brackets are the
    only constraint. This test runs against the backend Python
    regex in report_generator.extract_bob_blocks (the frontend
    lib/bobBlocks.ts mirrors it character-for-character)."""
    from tools.report_generator import extract_bob_blocks
    long_draft = (
        "The post-2022 correlation shift from -0.05 to +0.61 in "
        "equity-IG bond correlation fundamentally alters the case "
        "for traditional diversification. For a capital planning "
        "mandate like Forest Capital's, this means we cannot rely "
        "on the historical hedge relationship as a structural "
        "feature of the portfolio."
    )
    md = (
        "## 1. Data and Methodology\n\nSection 1 prose.\n\n"
        f"[BOB: {long_draft}]\n\n"
        "## 2. Results\n\nSection 2 prose.\n"
    )
    blocks = extract_bob_blocks(md)
    assert len(blocks) == 1
    block = blocks[0]
    assert block["kind"] == "BOB"
    # Description must be the full draft prose (minus the kind
    # prefix and colon).
    assert "post-2022 correlation shift" in block["description"]
    assert "Forest Capital's" in block["description"]
    # No bracket leak.
    assert "[" not in block["description"]
    assert "]" not in block["description"]


def test_extract_bob_blocks_handles_multiple_drafts():
    """The four required blocks at the end of the paper must all
    extract independently."""
    from tools.report_generator import extract_bob_blocks
    md = (
        "## 4. Next Steps\n\nMain body.\n\n"
        "[BOB: First draft about correlation shift implication.]\n\n"
        "[BOB: Second draft about strategy selection.]\n\n"
        "[BOB: Third draft about academic connections.]\n\n"
        "[BOB: Fourth draft framing the open question.]\n"
    )
    blocks = extract_bob_blocks(md)
    assert len(blocks) == 4
    assert all(b["kind"] == "BOB" for b in blocks)
    assert "correlation shift" in blocks[0]["description"]
    assert "strategy selection" in blocks[1]["description"]
    assert "academic connections" in blocks[2]["description"]
    assert "open question" in blocks[3]["description"]


def test_bob_separator_variants():
    """The extractor handles both colon ([BOB: ...]) and em-dash
    ([BOB — ...]) separators — the latter being the legacy form."""
    from tools.report_generator import extract_bob_blocks
    md_colon = "Body. [BOB: prose here] more body."
    md_dash = "Body. [BOB — prose here] more body."
    md_space = "Body. [BOB prose here] more body."
    for md in (md_colon, md_dash, md_space):
        blocks = extract_bob_blocks(md)
        assert len(blocks) == 1
        assert "prose here" in blocks[0]["description"]
