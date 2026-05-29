"""Tests for the Team Activity plain-English layer: the GitHub merged-PR
count and the commit-summary cache. Both are fail-open — no network or DB
is required for these to pass."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


def _run(coro):
    return asyncio.run(coro)


def test_fetch_merged_pr_count_none_without_token():
    # No GITHUB_TOKEN → None (the caller shows a dash, never a wrong number).
    from tools.github_sync import fetch_merged_pr_count
    assert _run(fetch_merged_pr_count("saffamiker/forest-capital", "")) is None


def test_summarize_commits_empty_input():
    from tools.commit_summaries import summarize_commits
    assert _run(summarize_commits([])) == {}


def test_summarize_commits_skips_generation_in_test_env():
    # In the test environment no Anthropic call is made; with no cached rows
    # the result is empty and the report falls back to the technical message.
    from tools.commit_summaries import summarize_commits
    out = _run(summarize_commits([{"sha": "abc1234def", "message": "fix: x"}]))
    assert out == {}


def test_get_merged_pr_count_caches_and_fails_open():
    # With no token configured the count is None and nothing is cached as a
    # real value — get_merged_pr_count returns None without raising.
    from tools.activity_log import get_merged_pr_count
    assert _run(get_merged_pr_count()) is None
