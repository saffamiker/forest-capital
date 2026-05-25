"""
tests/test_audit_carry.py — Workstream A (May 28 2026).

Audit-acknowledgement auto-carry. Three layers of coverage:

  1. The check_id composition helper — stable across re-runs, bounded
     by the migration-044 schema's 120-character column limit.
  2. The numeric / string value-match logic that decides whether a
     prior ack should carry forward.
  3. The end-to-end live-DB carry pass — seed an ack, persist a fresh
     finding, run apply_carry, verify the finding is now resolved
     with auto_acknowledged=true. Live-DB tests skip without a
     reachable Postgres.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")


def _db_ready() -> bool:
    try:
        from database import AsyncSessionLocal
        return AsyncSessionLocal is not None
    except Exception:  # noqa: BLE001
        return False


# ── Pure helpers ─────────────────────────────────────────────────────────────

class TestComposeCheckId:
    """Workstream A — the stable cross-run identifier used as the join
    key between audit_findings and audit_acknowledgements."""

    def test_includes_layer_metric_strategy(self):
        from tools.audit_carry import compose_check_id

        out = compose_check_id({
            "layer": 2, "metric": "cagr", "strategy": "REGIME_SWITCHING",
        })
        assert out == "L2.cagr.REGIME_SWITCHING"

    def test_missing_strategy_uses_underscore_slot(self):
        from tools.audit_carry import compose_check_id

        # Layer 1 / Layer 3 cross-platform checks have no strategy —
        # the underscore keeps the identifier well-formed and
        # unambiguous (a real strategy can never be "_").
        out = compose_check_id({
            "layer": 1, "metric": "benchmark_cagr", "strategy": None,
        })
        assert out == "L1.benchmark_cagr._"

    def test_missing_metric_uses_underscore_slot(self):
        from tools.audit_carry import compose_check_id

        out = compose_check_id({
            "layer": 3, "metric": "", "strategy": "EQUITY",
        })
        assert out == "L3._.EQUITY"

    def test_is_bounded_by_120_chars(self):
        from tools.audit_carry import compose_check_id

        # Schema column is varchar(120); a long composition truncates.
        out = compose_check_id({
            "layer": 2,
            "metric": "x" * 200,
            "strategy": "y" * 200,
        })
        assert len(out) == 120


class TestValueMatchesWithinTolerance:
    """The carry pass uses this to decide whether a prior ack should
    apply to the current finding. Numeric values within 0.5% of the
    snapshot match; non-numeric values must match exactly."""

    def test_numeric_within_tolerance_matches(self):
        from tools.audit_carry import value_matches_within_tolerance

        # 0.1% off → within tolerance.
        assert value_matches_within_tolerance(
            prev_numeric=10.0, prev_raw=None,
            current_value="10.01", tolerance=0.005,
        ) is True

    def test_numeric_outside_tolerance_does_not_match(self):
        from tools.audit_carry import value_matches_within_tolerance

        # 1% off → outside tolerance.
        assert value_matches_within_tolerance(
            prev_numeric=10.0, prev_raw=None,
            current_value="10.10", tolerance=0.005,
        ) is False

    def test_numeric_zero_prev_uses_absolute_tolerance(self):
        from tools.audit_carry import value_matches_within_tolerance

        # Prev value 0 — relative tolerance undefined, fall back to
        # the 1e-4 absolute window.
        assert value_matches_within_tolerance(
            prev_numeric=0.0, prev_raw=None,
            current_value="0.00005", tolerance=0.005,
        ) is True
        assert value_matches_within_tolerance(
            prev_numeric=0.0, prev_raw=None,
            current_value="0.001", tolerance=0.005,
        ) is False

    def test_percent_sign_stripped(self):
        from tools.audit_carry import value_matches_within_tolerance

        assert value_matches_within_tolerance(
            prev_numeric=4.5, prev_raw=None,
            current_value="4.51%", tolerance=0.005,
        ) is True

    def test_non_numeric_path_requires_exact_string_match(self):
        from tools.audit_carry import value_matches_within_tolerance

        # Neither side parses — fall back to exact equality.
        assert value_matches_within_tolerance(
            prev_numeric=None,
            prev_raw="5 strategies × 282 months",
            current_value="5 strategies × 282 months",
            tolerance=0.005,
        ) is True
        assert value_matches_within_tolerance(
            prev_numeric=None,
            prev_raw="5 strategies × 282 months",
            current_value="6 strategies × 282 months",
            tolerance=0.005,
        ) is False

    def test_neither_path_resolves_returns_false(self):
        from tools.audit_carry import value_matches_within_tolerance

        # Prev had no numeric AND no raw — the carry cannot decide.
        assert value_matches_within_tolerance(
            prev_numeric=None, prev_raw=None,
            current_value="anything",
        ) is False


# ── Live-DB carry pass ───────────────────────────────────────────────────────

class TestApplyCarryEndToEnd:
    """End-to-end live-DB tests. apply_carry reads audit_findings +
    audit_acknowledgements and updates findings in place when prior
    acks still apply. Each test seeds its own run and ack rows and
    cleans them up afterwards."""

    def _setup(self, finding_value: str, ack_numeric: float | None,
               ack_raw: str | None, layer: int = 2,
               metric: str = "sharpe_ratio",
               strategy: str = "REGIME_SWITCHING") -> tuple[int, int, int]:
        """Inserts an audit_runs row, an audit_findings row in WARN
        state, and an audit_acknowledgements row for the same check_id.
        Returns (run_id, finding_id, ack_id)."""
        from sqlalchemy import text

        from database import AsyncSessionLocal

        async def _go() -> tuple[int, int, int]:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status) "
                    "VALUES ('manual', 'complete') RETURNING id"))
                run_id = r.fetchone()[0]
                fr = await s.execute(text(
                    "INSERT INTO audit_findings (audit_run_id, layer, "
                    "check_name, metric, strategy, severity, status, "
                    "platform_value) VALUES (:rid, :l, 'CarryTest', "
                    ":m, :st, 'warning', 'warning', :v) RETURNING id"),
                    {"rid": run_id, "l": layer, "m": metric,
                     "st": strategy, "v": finding_value})
                finding_id = fr.fetchone()[0]
                # check_id composes from the fields the finding carries.
                check_id = f"L{layer}.{metric}.{strategy}"
                ar = await s.execute(text(
                    "INSERT INTO audit_acknowledgements "
                    "(check_id, verdict_at_ack, "
                    " platform_value_at_ack, platform_value_raw, "
                    " resolution_note, acknowledged_by) "
                    "VALUES (:c, 'warning', :n, :r, "
                    " 'CARRYNOTETOKEN previously reviewed', "
                    " 'bob@queens.edu') RETURNING id"),
                    {"c": check_id, "n": ack_numeric, "r": ack_raw})
                ack_id = ar.fetchone()[0]
                await s.commit()
            return run_id, finding_id, ack_id

        return asyncio.run(_go())

    def _cleanup(self, run_id: int) -> None:
        from sqlalchemy import text

        from database import AsyncSessionLocal

        async def _go() -> None:
            async with AsyncSessionLocal() as s:
                # audit_findings cascades from audit_runs.
                await s.execute(text(
                    "DELETE FROM audit_runs WHERE id = :id"),
                    {"id": run_id})
                # audit_acknowledgements is independent — purge by
                # check_id so the seeded rows don't accumulate across
                # the test suite.
                await s.execute(text(
                    "DELETE FROM audit_acknowledgements "
                    "WHERE check_id LIKE 'L%' "
                    "  AND acknowledged_by = 'bob@queens.edu' "
                    "  AND resolution_note LIKE 'CARRYNOTETOKEN%'"))
                await s.commit()

        asyncio.run(_go())

    def _finding_row(self, fid: int) -> dict:
        from sqlalchemy import text

        from database import AsyncSessionLocal

        async def _go() -> dict:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "SELECT resolved, resolution_note, resolved_by, "
                    " resolved_at, auto_acknowledged "
                    "FROM audit_findings WHERE id = :id"),
                    {"id": fid})
                row = r.fetchone()
            return {
                "resolved": row[0], "resolution_note": row[1],
                "resolved_by": row[2], "resolved_at": row[3],
                "auto_acknowledged": row[4],
            }

        return asyncio.run(_go())

    def _ack_row(self, ack_id: int) -> dict:
        from sqlalchemy import text

        from database import AsyncSessionLocal

        async def _go() -> dict:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "SELECT superseded, superseded_at "
                    "FROM audit_acknowledgements WHERE id = :id"),
                    {"id": ack_id})
                row = r.fetchone()
            return {"superseded": row[0], "superseded_at": row[1]}

        return asyncio.run(_go())

    def test_carries_an_ack_when_value_unchanged(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.audit_carry import apply_carry

        # Same value on both sides — within tolerance.
        run_id, fid, ack_id = self._setup(
            finding_value="0.629", ack_numeric=0.629, ack_raw=None)
        try:
            counts = asyncio.run(apply_carry(run_id))
            assert counts["carried"] == 1
            assert counts["value_changed"] == 0

            f = self._finding_row(fid)
            assert f["resolved"] is True
            assert f["auto_acknowledged"] is True
            assert f["resolved_by"] == "auto_carry"
            assert "CARRYNOTETOKEN" in (f["resolution_note"] or "")
            assert f["resolved_at"] is not None
        finally:
            self._cleanup(run_id)

    def test_carries_when_value_drifts_within_tolerance(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.audit_carry import apply_carry

        # 0.3% drift — within the 0.5% tolerance.
        run_id, fid, ack_id = self._setup(
            finding_value="0.6309", ack_numeric=0.629, ack_raw=None)
        try:
            counts = asyncio.run(apply_carry(run_id))
            assert counts["carried"] == 1
            f = self._finding_row(fid)
            assert f["resolved"] is True
            assert f["auto_acknowledged"] is True
            ack = self._ack_row(ack_id)
            assert ack["superseded"] is False
        finally:
            self._cleanup(run_id)

    def test_supersedes_ack_when_value_changes_materially(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.audit_carry import apply_carry

        # 2% drift — well outside the 0.5% tolerance.
        run_id, fid, ack_id = self._setup(
            finding_value="0.642", ack_numeric=0.629, ack_raw=None)
        try:
            counts = asyncio.run(apply_carry(run_id))
            assert counts["carried"] == 0
            assert counts["value_changed"] == 1

            f = self._finding_row(fid)
            # Finding stays unreviewed — the team must re-evaluate.
            assert f["resolved"] is False
            assert f["auto_acknowledged"] is False
            ack = self._ack_row(ack_id)
            # The ack is superseded so a future re-run with the same
            # drift cannot accidentally carry it forward later.
            assert ack["superseded"] is True
            assert ack["superseded_at"] is not None
        finally:
            self._cleanup(run_id)

    def test_no_prior_ack_leaves_finding_alone(self):
        if not _db_ready():
            pytest.skip("no live database")
        from sqlalchemy import text

        from database import AsyncSessionLocal
        from tools.audit_carry import apply_carry

        async def _seed() -> tuple[int, int]:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status) "
                    "VALUES ('manual', 'complete') RETURNING id"))
                run_id = r.fetchone()[0]
                fr = await s.execute(text(
                    "INSERT INTO audit_findings (audit_run_id, layer, "
                    "check_name, metric, strategy, severity, status, "
                    "platform_value) VALUES (:rid, 2, 'NoAckCheck', "
                    "'sortino_ratio', 'EQUAL_WEIGHT', 'warning', "
                    "'warning', '1.23') RETURNING id"),
                    {"rid": run_id})
                finding_id = fr.fetchone()[0]
                await s.commit()
            return run_id, finding_id

        run_id, fid = asyncio.run(_seed())
        try:
            counts = asyncio.run(apply_carry(run_id))
            assert counts["carried"] == 0
            assert counts["no_prior_ack"] >= 1

            from sqlalchemy import text as _text

            async def _read() -> dict:
                async with AsyncSessionLocal() as s:
                    r = await s.execute(_text(
                        "SELECT resolved, auto_acknowledged "
                        "FROM audit_findings WHERE id = :id"),
                        {"id": fid})
                    row = r.fetchone()
                return {"resolved": row[0], "auto_acknowledged": row[1]}

            f = asyncio.run(_read())
            assert f["resolved"] is False
            assert f["auto_acknowledged"] is False
        finally:
            from sqlalchemy import text as _text

            async def _clean() -> None:
                async with AsyncSessionLocal() as s:
                    await s.execute(_text(
                        "DELETE FROM audit_runs WHERE id = :id"),
                        {"id": run_id})
                    await s.commit()
            asyncio.run(_clean())


class TestResolveFindingRecordsAck:
    """Workstream A — resolve_finding's success path now writes an
    audit_acknowledgements row alongside the audit_findings UPDATE,
    so the next re-run can carry the review forward via apply_carry."""

    def test_resolve_writes_an_unsuperseded_ack_row(self):
        if not _db_ready():
            pytest.skip("no live database")
        from sqlalchemy import text

        from database import AsyncSessionLocal
        from tools.audit_engine import resolve_finding

        async def _seed() -> tuple[int, int]:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status) "
                    "VALUES ('manual', 'complete') RETURNING id"))
                run_id = r.fetchone()[0]
                fr = await s.execute(text(
                    "INSERT INTO audit_findings (audit_run_id, layer, "
                    "check_name, metric, strategy, severity, status, "
                    "platform_value) VALUES (:rid, 2, 'WriteAckCheck', "
                    "'alpha', 'VOL_TARGETING', 'warning', 'warning', "
                    "'0.05') RETURNING id"),
                    {"rid": run_id})
                finding_id = fr.fetchone()[0]
                await s.commit()
            return run_id, finding_id

        async def _read_ack(check_id: str) -> dict | None:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "SELECT resolution_note, acknowledged_by, "
                    "       platform_value_at_ack, superseded "
                    "FROM audit_acknowledgements "
                    "WHERE check_id = :c AND superseded = false"),
                    {"c": check_id})
                row = r.fetchone()
            if row is None:
                return None
            return {
                "resolution_note": row[0],
                "acknowledged_by": row[1],
                "platform_value_at_ack": row[2],
                "superseded": row[3],
            }

        run_id, fid = asyncio.run(_seed())
        check_id = "L2.alpha.VOL_TARGETING"
        try:
            # Manually ack the finding through the engine. The endpoint
            # in main.py does the same thing — pass resolved_by.
            asyncio.run(resolve_finding(
                fid, True, "MANUALACKTOKEN — reviewed.",
                resolved_by="ruurdsm@queens.edu"))
            ack = asyncio.run(_read_ack(check_id))
            assert ack is not None
            assert "MANUALACKTOKEN" in (ack["resolution_note"] or "")
            assert ack["acknowledged_by"] == "ruurdsm@queens.edu"
            # The numeric value was snapped at ack time.
            assert ack["platform_value_at_ack"] is not None
            assert abs(ack["platform_value_at_ack"] - 0.05) < 1e-9
        finally:
            from sqlalchemy import text as _text

            async def _clean() -> None:
                async with AsyncSessionLocal() as s:
                    await s.execute(_text(
                        "DELETE FROM audit_acknowledgements "
                        "WHERE check_id = :c"), {"c": check_id})
                    await s.execute(_text(
                        "DELETE FROM audit_runs WHERE id = :id"),
                        {"id": run_id})
                    await s.commit()
            asyncio.run(_clean())

    def test_revoke_supersedes_the_ack_row(self):
        if not _db_ready():
            pytest.skip("no live database")
        from sqlalchemy import text

        from database import AsyncSessionLocal
        from tools.audit_engine import resolve_finding

        async def _seed() -> tuple[int, int, str]:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status) "
                    "VALUES ('manual', 'complete') RETURNING id"))
                run_id = r.fetchone()[0]
                fr = await s.execute(text(
                    "INSERT INTO audit_findings (audit_run_id, layer, "
                    "check_name, metric, strategy, severity, status, "
                    "platform_value) VALUES (:rid, 2, 'RevokeAckCheck', "
                    "'beta', 'MIN_VARIANCE', 'warning', 'warning', "
                    "'0.97') RETURNING id"),
                    {"rid": run_id})
                finding_id = fr.fetchone()[0]
                await s.commit()
            return run_id, finding_id, "L2.beta.MIN_VARIANCE"

        run_id, fid, check_id = asyncio.run(_seed())
        try:
            # Ack, then revoke — the ack row must end up superseded.
            asyncio.run(resolve_finding(
                fid, True, "SOONREVOKEDTOKEN", resolved_by="bob@queens.edu"))
            asyncio.run(resolve_finding(fid, False, None, resolved_by=None))

            async def _read() -> dict:
                async with AsyncSessionLocal() as s:
                    r = await s.execute(text(
                        "SELECT COUNT(*) FILTER (WHERE superseded), "
                        "       COUNT(*) "
                        "FROM audit_acknowledgements "
                        "WHERE check_id = :c"), {"c": check_id})
                    row = r.fetchone()
                return {"superseded": row[0], "total": row[1]}

            counts = asyncio.run(_read())
            # The single row exists and is superseded.
            assert counts["total"] >= 1
            assert counts["superseded"] == counts["total"]
        finally:
            from sqlalchemy import text as _text

            async def _clean() -> None:
                async with AsyncSessionLocal() as s:
                    await s.execute(_text(
                        "DELETE FROM audit_acknowledgements "
                        "WHERE check_id = :c"), {"c": check_id})
                    await s.execute(_text(
                        "DELETE FROM audit_runs WHERE id = :id"),
                        {"id": run_id})
                    await s.commit()
            asyncio.run(_clean())
