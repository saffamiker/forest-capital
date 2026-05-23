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
        # with a merge-commit LIKE pattern, NOT pr_suggestions.
        # Locate the line that assigns to michael_prs_merged and
        # inspect the next ~6 lines of SQL.
        pr_section = src.split('out["michael_prs_merged"]')[1]
        # Inspect ~500 chars of context — well past the SQL block.
        ctx = pr_section[:500]
        assert "commit_activity" in ctx, (
            "michael_prs_merged must query commit_activity for "
            "merge commits, not pr_suggestions (which only carries "
            "failure-linked PRs).")
        assert "Merge pull request" in ctx, (
            "Merge commits start with 'Merge pull request #N' — "
            "the LIKE pattern that catches them must be present.")

    def test_pr_query_admits_michael_git_identities(self):
        src = inspect.getsource(tp.fetch_team_activity)
        pr_section = src.split('out["michael_prs_merged"]')[1][:500]
        # Same IN-clause shape the commits query uses — verifies the
        # author filter is applied to the PR merge count too.
        assert "LOWER(author) IN" in pr_section \
            or "author IN" in pr_section, (
                "michael_prs_merged must match against BOTH of "
                "Michael's git identities (the same _TEAM_GIT_EMAILS "
                "list the commit count uses), not just the platform "
                "email.")


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
