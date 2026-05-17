#!/usr/bin/env python3
"""
scripts/changelog_gate.py

Enforces the changelog contract: every database migration must include
a changelog INSERT. Run as a CI step (and a pre-commit hook).

Logic:
  1. Find the files changed in the last commit (HEAD~1..HEAD), with the
     first-commit edge case handled by diffing against the empty tree.
  2. If a new migration file was added under backend/migrations/versions/,
     verify it inserts at least one changelog row — exit 1 if not.
  3. If no new migration was added but feature files changed, exit 0
     with an advisory warning.

A migration "inserts a changelog row" when its source references the
changelog table and an insert (op.bulk_insert, INSERT INTO changelog,
or an .insert(...) on the table).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

# The git empty-tree object — diff target for the first commit.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_MIGRATIONS_DIR = "backend/migrations/versions/"


def _changed_files() -> list[tuple[str, str]]:
    """Returns [(status, path), ...] for the last commit. Status is git's
    A/M/D/... letter. Falls back to the empty tree when HEAD~1 is absent."""
    try:
        subprocess.run(["git", "rev-parse", "--verify", "HEAD~1"],
                       check=True, capture_output=True)
        base = "HEAD~1"
    except subprocess.CalledProcessError:
        base = _EMPTY_TREE   # first commit — diff against the empty tree

    out = subprocess.run(
        ["git", "diff", "--name-status", base, "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0].strip(), parts[-1].strip()))
    return rows


def _inserts_changelog(path: str) -> bool:
    """True when the migration file inserts at least one changelog row."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8").lower()
    except OSError:
        return False
    if "changelog" not in text:
        return False
    return (
        "bulk_insert" in text
        or "insert into changelog" in text
        or ".insert(" in text
    )


def main() -> int:
    changed = _changed_files()

    new_migrations = [
        path for (status, path) in changed
        if status.startswith("A")
        and path.startswith(_MIGRATIONS_DIR)
        and path.endswith(".py")
        and "__pycache__" not in path
    ]

    if new_migrations:
        offenders = [p for p in new_migrations if not _inserts_changelog(p)]
        if offenders:
            print("Migration added without changelog entry. Every migration "
                  "must include a changelog INSERT. See CLAUDE.md for the "
                  "changelog contract.")
            for o in offenders:
                print(f"  - {o}")
            return 1
        print(f"changelog gate: {len(new_migrations)} new migration(s) — "
              "all include a changelog entry.")
        return 0

    # No new migration. Warn if feature code changed — a user-facing change
    # may still warrant a changelog entry (via a follow-up migration).
    feature = [
        path for (_status, path) in changed
        if (path.startswith("backend/") or path.startswith("frontend/src/"))
        and "test" not in path.lower()
        and "__pycache__" not in path
    ]
    if feature:
        print("Warning: feature files changed without a new migration. If "
              "this is a user-facing change, consider whether a changelog "
              "entry is needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
