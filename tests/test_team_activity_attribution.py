"""tests/test_team_activity_attribution.py — Step 3 join bug fix.

May 23 2026 — Pipeline Step 3 (Pull Team Activity) was returning
0 for every per-member count even though agent_interactions /
commit_activity / test_results rows existed for Bob (79 rows),
Molly (58), Michael (40).

Two bugs identified and fixed:

  1. audit_runs.statistical_status reference threw a column-
     not-found error. The whole fetch_team_activity body was
     wrapped in ONE try/except, so the error aborted every
     subsequent per-member query — they all returned 0 from the
     default dict. Fix: per-query isolation via _try_count + use
     the correct column name (layer_2_status).

  2. Michael's commits in commit_activity are authored under
     mikeruurds@gmail.com (git identity), not his platform email
     ruurdsm@queens.edu. The query keyed on the platform email
     only, so it never matched. Fix: query against
     _TEAM_GIT_EMAILS["michael"] which carries both identities.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


from tools import template_pipeline as tp  # noqa: E402


# ── Constants ────────────────────────────────────────────────────────────────


class TestTeamGitEmails:
    """The git email mapping is what makes Michael's commits
    attribute correctly. A drift here re-introduces the Step 3
    bug — guard the contract."""

    def test_michael_carries_both_platform_and_git_email(self):
        assert "ruurdsm@queens.edu" in tp._TEAM_GIT_EMAILS["michael"]
        assert "mikeruurds@gmail.com" in tp._TEAM_GIT_EMAILS["michael"]

    def test_bob_and_molly_use_their_platform_email(self):
        # Bob and Molly commit under their queens.edu address; the
        # list is a single element so the IN clause works either
        # way.
        assert tp._TEAM_GIT_EMAILS["bob"] == ["thaob@queens.edu"]
        assert tp._TEAM_GIT_EMAILS["molly"] == ["murdockm@queens.edu"]


# ── Per-query isolation contract ─────────────────────────────────────────────


class TestPerQueryIsolation:
    """The fix that unblocked Step 3: each per-member query has its
    own try/except so one failing query no longer aborts the rest.
    Source-level check — a regression that reverts to a single
    try/except would re-introduce the bug."""

    def test_try_count_helper_isolates_each_count(self):
        # The helper must catch every Exception and return 0,
        # logging the failure but not propagating.
        src = inspect.getsource(tp._try_count)
        assert "except Exception" in src
        assert "return 0" in src

    def test_fetch_team_activity_uses_the_helper(self):
        # The body must funnel every count through _try_count so a
        # column-not-found bug in ONE query does not blank every
        # subsequent count. Source-level check is reliable here —
        # we know the function structure.
        src = inspect.getsource(tp.fetch_team_activity)
        # The helper call appears once per count — at least 12
        # counts in the current body.
        assert src.count("_try_count(") >= 10, (
            "fetch_team_activity must call _try_count for every "
            "count; a regression that goes back to the shared "
            "try/except would re-blank every per-member count on "
            "any single SQL error.")

    def test_audit_runs_query_uses_layer_2_status(self):
        # The original bug: statistical_status was referenced but
        # the column does not exist. layer_2_status is the right
        # column for the audit-validation total. (The bug-history
        # comment in the body mentions statistical_status — strip
        # comments before asserting the SQL never references it.)
        src = inspect.getsource(tp.fetch_team_activity)
        sql_only = "\n".join(
            line for line in src.splitlines()
            if not line.lstrip().startswith("#"))
        assert "statistical_status" not in sql_only, (
            "audit_runs has no statistical_status column. Use "
            "layer_2_status — the recompute layer that maps to "
            "validated.")
        assert "layer_2_status = 'pass'" in src


# ── Commit attribution via git emails ────────────────────────────────────────


class TestCommitAttribution:
    """Michael's commits live under mikeruurds@gmail.com (his git
    identity), not ruurdsm@queens.edu (his platform identity).
    The Step 3 query must match against both."""

    def test_commit_query_uses_in_clause_for_michael(self):
        src = inspect.getsource(tp.fetch_team_activity)
        # The IN clause that admits both identities. Source-level
        # check — a regression to the old `= :e` keyed only on the
        # platform email would re-zero Michael's commits.
        assert "WHERE LOWER(author) IN" in src \
            or "WHERE author IN" in src, (
                "Michael's commits live under his git identity "
                "(mikeruurds@gmail.com), not his platform email — "
                "the commit_activity query must use an IN clause "
                "that admits both.")


class TestPRMergeAttribution:
    """Michael's merged-PR count was zero because the query
    counted pr_suggestions.reviewed_by — that table only carries
    PRs that reference a failure number (the triage workflow),
    not every merged PR. The fix counts merge commits in
    commit_activity matched on Michael's git identities."""

    def test_pr_query_reads_commit_activity_not_pr_suggestions(self):
        src = inspect.getsource(tp.fetch_team_activity)
        # The michael_prs_merged section must use commit_activity
        # with a merge-commit pattern, NOT pr_suggestions.
        # Locate the line that assigns to michael_prs_merged and
        # inspect the next ~600 chars of context.
        pr_section = src.split('out["michael_prs_merged"]')[1]
        ctx = pr_section[:600]
        assert "commit_activity" in ctx, (
            "michael_prs_merged must query commit_activity for "
            "merge commits, not pr_suggestions (which only carries "
            "failure-linked PRs).")
        assert "merge pull request" in ctx.lower(), (
            "The merge-commit message pattern must be present in "
            "the query — that's what identifies a PR-merge "
            "commit in commit_activity.")

    def test_pr_query_uses_ilike_with_wildcards(self):
        # Hotfix iteration 3: LIKE 'Merge pull request #%' was too
        # strict — case-sensitive AND prefix-anchored. Real-world
        # merge commits sometimes vary (leading whitespace, case
        # differences between gh pr merge vs GitHub UI vs older
        # squash flows). ILIKE with %merge pull request% on both
        # sides catches every variant.
        src = inspect.getsource(tp.fetch_team_activity)
        pr_section = src.split('out["michael_prs_merged"]')[1][:600]
        assert "ILIKE" in pr_section, (
            "Use ILIKE (case-insensitive) for the merge-commit "
            "message match — LIKE is case-sensitive and misses "
            "commits with non-canonical capitalisation.")
        assert "%merge pull request%" in pr_section.lower(), (
            "The pattern must have wildcards on BOTH sides of "
            "'merge pull request' — anchored prefix matching was "
            "the previous bug.")

    def test_pr_query_admits_michael_git_identities(self):
        src = inspect.getsource(tp.fetch_team_activity)
        pr_section = src.split('out["michael_prs_merged"]')[1][:600]
        # Same IN-clause shape the commits query uses — verifies the
        # author filter is applied to the PR merge count too.
        assert "LOWER(author) IN" in pr_section \
            or "author IN" in pr_section, (
                "michael_prs_merged must match against BOTH of "
                "Michael's git identities (the same "
                "_git_identities_for list the commit count uses), "
                "not just the platform email.")


class TestGitIdentitiesHelper:
    """The _git_identities_for helper drives the author-filter for
    BOTH the commits count and the merged-PR count. It pulls each
    user's git identities from platform_users (email +
    github_email) so the query is data-driven, not hardcoded."""

    def test_helper_exists_and_is_async(self):
        assert callable(tp._git_identities_for)
        assert inspect.iscoroutinefunction(tp._git_identities_for)

    def test_helper_returns_platform_email_lower_on_no_db(self, monkeypatch):
        # No DB → fall back to the platform email lower-cased so
        # the IN clause has at least one entry to match against.
        # This is the fail-open contract.
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        from sqlalchemy import text as _text  # noqa: F401

        # We can't easily construct a session without DB, so call
        # directly with a None-like session that raises on use.
        class _BoomSession:
            async def execute(self, *a, **kw):
                raise RuntimeError("DB unavailable")
        out = asyncio.run(tp._git_identities_for(
            _BoomSession(), "ruurdsm@queens.edu"))
        assert out == ["ruurdsm@queens.edu"]

    def test_michael_block_uses_data_driven_identities(self):
        # The Michael block must NOT hardcode the git emails in a
        # Python literal — it must fetch them from platform_users
        # via _git_identities_for so populating github_email on
        # any future team member auto-attributes their activity.
        src = inspect.getsource(tp.fetch_team_activity)
        michael_block_start = src.index("# ── Michael")
        bob_block_start = src.index("# ── Bob")
        michael_block = src[michael_block_start:bob_block_start]
        assert "_git_identities_for" in michael_block, (
            "Michael's commit + PR queries must source his git "
            "identities from platform_users via "
            "_git_identities_for so the github_email column "
            "(migration 038) is the canonical lookup.")


class TestLocalPartFallback:
    """Hotfix iteration 4 (May 23 2026): GitHub UI / gh pr merge
    writes merge commits under the user's GitHub noreply email
    (mikeruurds@users.noreply.github.com or a numeric-id prefixed
    variant). Strict equality against {ruurdsm@queens.edu,
    mikeruurds@gmail.com} missed ~98 of ~100 PRs. The local-part
    OR-match (LOWER(author) LIKE 'mikeruurds@%') catches every
    noreply variant without needing the operator to know the
    numeric account ID."""

    def test_local_parts_helper_exists_and_is_async(self):
        assert callable(tp._git_author_local_parts_for)
        assert inspect.iscoroutinefunction(
            tp._git_author_local_parts_for)

    def test_local_parts_returns_locals_only(self, monkeypatch):
        # Mock _git_identities_for to return two emails; expect
        # the helper to return their local parts.
        async def _fake_identities(_s, _e):
            return ["ruurdsm@queens.edu", "mikeruurds@gmail.com"]
        monkeypatch.setattr(
            tp, "_git_identities_for", _fake_identities)

        class _StubSession: pass
        out = asyncio.run(tp._git_author_local_parts_for(
            _StubSession(), "ruurdsm@queens.edu"))
        assert "ruurdsm" in out
        assert "mikeruurds" in out
        # No duplicates and no full email strings.
        for p in out:
            assert "@" not in p

    def test_local_parts_deduplicates(self, monkeypatch):
        # A user whose platform and github email have the same
        # local part (rare but possible) should not see duplicates.
        async def _fake_identities(_s, _e):
            return ["foo@queens.edu", "foo@gmail.com"]
        monkeypatch.setattr(
            tp, "_git_identities_for", _fake_identities)

        class _StubSession: pass
        out = asyncio.run(tp._git_author_local_parts_for(
            _StubSession(), "foo@queens.edu"))
        assert out == ["foo"]

    def test_michael_pr_query_includes_local_part_like_match(self):
        # The PR query must OR-match against the local-part LIKE
        # clause. A regression to a strict-equality-only filter
        # would re-introduce the ~98-PR undercount.
        #
        # The local-part LIKE clause is built via an f-string above
        # the out["michael_prs_merged"] assignment, then OR'd into
        # the SQL via the {lp_likes} interpolation. Inspect the
        # full Michael block (from the # ── Michael banner to the
        # # ── Bob banner) so both the helper setup and the SQL
        # interpolation are visible.
        src = inspect.getsource(tp.fetch_team_activity)
        michael_block_start = src.index("# ── Michael")
        bob_block_start = src.index("# ── Bob")
        michael_block = src[michael_block_start:bob_block_start]
        # Local-part bind parameter convention is :mlpN; the LIKE
        # clause uses the '@%' suffix to anchor to the local part
        # so a match against the local part PRECEDES the @ sign.
        assert "LOWER(author) LIKE :mlp" in michael_block, (
            "michael_prs_merged must OR-match against the local "
            "part of Michael's known git identities ("
            "LOWER(author) LIKE :mlp0 || '@%') so GitHub noreply "
            "emails are caught.")
        assert "'@%'" in michael_block, (
            "The local-part LIKE pattern must anchor to '@%' so a "
            "match against the local PRECEDES the @ — preventing "
            "false positives where the local part happens to "
            "appear elsewhere in an unrelated email.")
        # Helper call must be present so the local parts are
        # actually fetched.
        assert "_git_author_local_parts_for" in michael_block, (
            "The local-part list must be sourced from "
            "_git_author_local_parts_for so populating "
            "github_email automatically yields the right local "
            "part — no hardcoded substring.")


# ── Fail-open contracts ──────────────────────────────────────────────────────


class TestFailOpenContracts:
    """The function returns a default-zero dict if AsyncSessionLocal
    is None (no DB). Future regressions that swallow the None case
    risk crashing the pipeline."""

    def test_no_db_returns_default_zeros(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(tp.fetch_team_activity())
        assert out["team_total_uat_steps"] == 0
        assert out["michael_commits"] == 0
        assert out["bob_uat_steps"] == 0
        assert out["molly_uat_steps"] == 0
        # The full dict shape is preserved on the no-DB path so the
        # report pipeline downstream can still consume it.
        for k in ("team_total_uat_steps", "michael_commits",
                  "bob_council_sessions", "molly_uat_steps",
                  "team_total_audit_validations"):
            assert k in out
