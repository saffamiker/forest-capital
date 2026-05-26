"""tests/test_migration_046_access_test_panel.py — migration 046
back-fills the access_test_panel permission onto every active
team_member and sysadmin row.

The migration is a single-statement data fix for the omission that
left Bob, Molly and Michael unable to see the Test Administration
section in Settings (Failure Reports / Feedback Backlog / Issue
Tracker tabs). It mirrors migration 042's pattern exactly.

These tests pin the migration's metadata + SQL contract without
needing a live database — the SQL is exercised by the integration
suite that runs against Postgres in CI.
"""
from __future__ import annotations

import importlib.util
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _load_migration_046():
    spec = importlib.util.spec_from_file_location(
        "mig_046",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "046_access_test_panel_backfill.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_046_metadata():
    m = _load_migration_046()
    assert m.revision == "046"
    assert m.down_revision == "045"
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_back_fill_targets_team_and_sysadmin_only():
    """The UPDATE statement must restrict to role IN
    ('team_member', 'sysadmin'). Viewer rows are deliberately
    untouched — viewers don't need (and shouldn't see) the Test
    Administration panel. A regression that widened the role filter
    would surface the panel for guest reviewers."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "migrations",
        "versions", "046_access_test_panel_backfill.py")
    text = open(path, encoding="utf-8").read()
    # The upgrade UPDATE must name both roles and ONLY both roles.
    assert "role IN ('team_member', 'sysadmin')" in text


def test_back_fill_is_idempotent():
    """Re-running the migration on a row that already carries the
    permission must be a no-op. Enforced by the WHERE NOT IN clause
    that excludes already-permissioned rows."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "migrations",
        "versions", "046_access_test_panel_backfill.py")
    text = open(path, encoding="utf-8").read()
    assert "NOT ('access_test_panel' = ANY(permissions))" in text


def test_changelog_row_inserted():
    """Per the changelog contract: every migration from 011 onward
    must insert at least one changelog row. The CI changelog gate
    enforces this; the test pins the specific version + title so a
    refactor that drops the row is caught."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "migrations",
        "versions", "046_access_test_panel_backfill.py")
    text = open(path, encoding="utf-8").read()
    assert "INSERT INTO changelog" in text
    assert "v=65" in text or ":v" in text
    assert "Test Administration panel" in text


def test_downgrade_reverses_back_fill():
    """Downgrade must strip the permission from every row that has
    it, so a rollback is a true reverse. array_remove is idempotent
    (no-op on a row without the value), so the downgrade is safe to
    re-run."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "migrations",
        "versions", "046_access_test_panel_backfill.py")
    text = open(path, encoding="utf-8").read()
    assert "array_remove(permissions, 'access_test_panel')" in text
    # And the changelog row is dropped on downgrade so a re-upgrade
    # doesn't collide on (version).
    assert "DELETE FROM changelog WHERE version = 65" in text


def test_permission_exists_in_config():
    """The permission the migration grants must exist in
    config.PERMISSIONS. A typo'd permission name would be a no-op
    grant — the back-filled rows would carry a permission the
    application code never checks for."""
    from config import PERMISSIONS, ROLE_PRESETS
    assert "access_test_panel" in PERMISSIONS
    # And the role presets the migration targets must carry the
    # permission — otherwise the migration would re-add a permission
    # that newer rows do NOT carry, splitting old rows from new.
    assert "access_test_panel" in ROLE_PRESETS["team_member"]
    assert "access_test_panel" in ROLE_PRESETS["sysadmin"]
    # Viewer must NOT carry it — matches the migration's role filter.
    assert "access_test_panel" not in ROLE_PRESETS["viewer"]
