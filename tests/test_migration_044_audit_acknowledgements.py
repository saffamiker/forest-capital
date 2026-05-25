"""tests/test_migration_044_audit_acknowledgements.py

Schema-only migration (May 28 2026). The audit-ack workstream ships
the carry pass + endpoint + frontend + PDF in follow-up PRs; this
migration just lays the columns down. Tests here verify the chain
integrity + module shape — the column-level schema is enforced at
runtime by Postgres when the carry pass first writes to it.

The repo's existing migration-test pattern (e.g. TestMigration008
in test_academic_documents.py) is a single test class that imports
the migration module by path and asserts revision + down_revision +
the upgrade/downgrade entry points. Mirroring that.
"""
from __future__ import annotations

import importlib.util
import os


_MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "backend", "migrations", "versions",
    "044_audit_acknowledgements.py",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_044", _MIGRATION_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigration044:
    """Chain integrity + module shape."""

    def test_revision_chains_from_043(self):
        # 043 was the last migration on main when this work started
        # (citation_type addition for the multi-layered citation
        # sourcing workstream). 044 builds on it.
        mod = _load_migration()
        assert mod.revision == "044"
        assert mod.down_revision == "043"

    def test_upgrade_and_downgrade_callable(self):
        # Both entry points must exist — the changelog gate
        # (scripts/changelog_gate.py) enforces this as part of
        # the migration contract.
        mod = _load_migration()
        assert hasattr(mod, "upgrade")
        assert hasattr(mod, "downgrade")
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_changelog_insert_present(self):
        # The changelog contract requires every migration to insert
        # at least one row into the `changelog` table — enforced by
        # scripts/changelog_gate.py in CI and as a pre-commit hook.
        # The migration must use either op.bulk_insert(...) or an
        # `INSERT INTO changelog` SQL statement; the SQL form
        # matches 043's pattern. Check raw and upper for both.
        with open(_MIGRATION_PATH) as f:
            src = f.read()
        upper = src.upper().replace("\n", " ")
        assert "INSERT INTO CHANGELOG" in upper \
            or "BULK_INSERT" in upper \
            or "OP.BULK_INSERT" in upper

    def test_migration_declares_new_table(self):
        # The migration creates the new run-independent
        # acknowledgement table. Source-level check — we don't
        # execute upgrade() against a real DB here (the rest of the
        # test suite operates without postgres).
        with open(_MIGRATION_PATH) as f:
            src = f.read()
        assert 'create_table(' in src
        assert '"audit_acknowledgements"' in src

    def test_migration_adds_three_audit_findings_columns(self):
        # All three columns the next-PR carry pass + statistical
        # audit PDF disclosures section depend on must be added
        # together in this single migration.
        with open(_MIGRATION_PATH) as f:
            src = f.read()
        # add_column lines name the target table + column.
        assert 'add_column(\n        "audit_findings"' in src \
            or '"audit_findings",\n' in src
        for col in ("auto_acknowledged", "resolved_by", "resolved_at"):
            assert f'"{col}"' in src, f"Missing add_column for {col!r}"

    def test_audit_acknowledgements_carries_check_id(self):
        # check_id is the stable cross-run identifier the carry
        # pass uses to find a prior ack. It must be present and
        # non-nullable so a row without one cannot be inserted.
        with open(_MIGRATION_PATH) as f:
            src = f.read()
        # Defensive — match the canonical column declaration shape
        # the rest of the codebase uses.
        assert '"check_id"' in src
        # The next-PR carry pass queries
        # WHERE check_id = :c AND superseded = false; the index
        # on those two columns makes the lookup O(log n) instead
        # of a sequential scan on every re-run.
        assert "ix_audit_acknowledgements_check_active" in src

    def test_downgrade_reverses_in_order(self):
        # downgrade() must drop the new audit_findings columns AND
        # drop the new table so the migration is reversible. The
        # changelog row inserted by upgrade is removed too.
        with open(_MIGRATION_PATH) as f:
            src = f.read()
        # Split at the downgrade() definition to scope the assertions
        # to the downgrade body.
        _, _, downgrade_src = src.partition("def downgrade()")
        assert 'drop_table("audit_acknowledgements")' in downgrade_src
        for col in ("auto_acknowledged", "resolved_by", "resolved_at"):
            assert f'drop_column("audit_findings", "{col}")' in downgrade_src
        # The changelog gate's reverse-clause check: every migration
        # that INSERTs into changelog must also DELETE on downgrade.
        assert "DELETE FROM changelog" in downgrade_src
