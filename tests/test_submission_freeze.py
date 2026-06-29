"""tests/test_submission_freeze.py -- Layer 4 freeze helpers + admin
endpoints (June 21 2026).

Three tiers, all monkeypatch-driven so they run without a live DB:

  1. The freeze-helper module's pure contract:
       - get_freeze_config fail-open on cold DB
       - set_freeze_config UPSERTs and reads back
       - get_effective_data_hash returns freeze_hash vs live_hash
         depending on the active flag

  2. The /api/v1/admin/submission-freeze POST endpoint:
       - 200 on valid activate with a hash that exists in the cache
       - 400 on activate without a hash, or with a hash not in cache
       - 200 on deactivate (no hash required)

  3. The /api/v1/admin/submission-status GET endpoint:
       - shape: freeze_active, freeze_hash, hash_drift, etc.
       - hash_drift = True when freeze_hash != live_hash
       - submission_ready logic across the four pre-conditions
       - document generation uses the EFFECTIVE hash, not live, when
         the freeze is on

The freeze module is fail-open by design (an unreadable freeze flag
treats as OFF), so the test env's no-DB short-circuit is the same
code path a cold deploy would exercise -- and these tests pin that
contract before the freeze flips for real on June 30.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _auth_headers() -> dict:
    from config import MASTER_API_KEY  # type: ignore[import]
    return {"X-API-Key": MASTER_API_KEY}


@pytest.fixture
def client() -> TestClient:
    from main import app  # noqa: WPS433
    return TestClient(app)


# ── 1. Helper-module contract ────────────────────────────────────────────────

class TestGetFreezeConfigFailOpen:
    """A cold DB / unseeded table / parse error → OFF-state default.
    Never throws."""

    def test_no_db_returns_default(self, monkeypatch):
        from tools import submission_freeze

        monkeypatch.setattr(submission_freeze, "_DB_AVAILABLE", False)
        out = asyncio.run(submission_freeze.get_freeze_config())
        assert out["active"] is False
        assert out["freeze_hash"] is None
        assert out["freeze_date"] is None

    def test_default_has_all_keys(self):
        from tools.submission_freeze import _default_config

        cfg = _default_config()
        # Callers expect every key to exist so config["x"] never KeyErrors
        for k in ("active", "freeze_hash", "freeze_date",
                  "activated_by", "activated_at"):
            assert k in cfg


class TestSetFreezeConfigRoundTrip:
    """set + get round-trip via a fake session factory so we never
    need a live DB. The fake captures the bind values and replays
    them on the next read so the contract -- UPSERT + read -- is
    pinned end-to-end."""

    def test_activate_then_read_returns_active_state(self, monkeypatch):
        from tools import submission_freeze

        # Fake DB: a single in-memory dict that the fake session
        # mutates on INSERT and queries on SELECT.
        store: dict = {}

        class FakeResult:
            def __init__(self, rows):
                self._rows = list(rows)

            def fetchone(self):
                return self._rows[0] if self._rows else None

        class FakeSession:
            async def execute(self, stmt, params=None):
                sql = str(stmt)
                params = params or {}
                if "INSERT INTO platform_config" in sql:
                    import json
                    store[params["k"]] = json.loads(params["v"])
                    return FakeResult([])
                if "SELECT value FROM platform_config" in sql:
                    val = store.get(params["k"])
                    return FakeResult([(val,)] if val is not None else [])
                return FakeResult([])

            async def commit(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        def _fake_factory():
            return FakeSession()

        monkeypatch.setattr(submission_freeze, "_DB_AVAILABLE", True)
        monkeypatch.setattr(
            submission_freeze, "AsyncSessionLocal", _fake_factory)

        # Activate
        new_cfg = asyncio.run(submission_freeze.set_freeze_config(
            active=True, freeze_hash="c421fb89abc12345",
            activated_by="ruurdsm@queens.edu"))
        assert new_cfg["active"] is True
        assert new_cfg["freeze_hash"] == "c421fb89abc12345"
        assert new_cfg["activated_by"] == "ruurdsm@queens.edu"
        assert new_cfg["freeze_date"] is not None

        # Read back
        read = asyncio.run(submission_freeze.get_freeze_config())
        assert read["active"] is True
        assert read["freeze_hash"] == "c421fb89abc12345"

    def test_activate_requires_freeze_hash(self, monkeypatch):
        from tools import submission_freeze

        monkeypatch.setattr(submission_freeze, "_DB_AVAILABLE", True)

        with pytest.raises(ValueError):
            asyncio.run(submission_freeze.set_freeze_config(
                active=True, freeze_hash=None))

    def test_deactivate_clears_hash_and_date(self, monkeypatch):
        from tools import submission_freeze

        captured: dict = {}

        class FakeSession:
            async def execute(self, stmt, params=None):
                import json
                if params and "v" in params:
                    captured.update(json.loads(params["v"]))

                class _R:
                    def fetchone(self):
                        return None
                return _R()

            async def commit(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        monkeypatch.setattr(submission_freeze, "_DB_AVAILABLE", True)
        monkeypatch.setattr(
            submission_freeze, "AsyncSessionLocal",
            lambda: FakeSession())

        out = asyncio.run(submission_freeze.set_freeze_config(active=False))
        assert out["active"] is False
        assert out["freeze_hash"] is None
        assert out["freeze_date"] is None
        # And the bound payload also cleared
        assert captured.get("freeze_hash") is None


class TestGetEffectiveDataHash:
    """The single call site that gates document generation."""

    def test_returns_freeze_hash_when_active(self, monkeypatch):
        from tools import submission_freeze

        async def _config():
            return {"active": True, "freeze_hash": "FROZEN12345"}

        monkeypatch.setattr(
            submission_freeze, "get_freeze_config", _config)
        out = asyncio.run(
            submission_freeze.get_effective_data_hash("LIVEHASH98765"))
        assert out == "FROZEN12345"

    def test_returns_live_hash_when_inactive(self, monkeypatch):
        from tools import submission_freeze

        async def _config():
            return {"active": False, "freeze_hash": None}

        monkeypatch.setattr(
            submission_freeze, "get_freeze_config", _config)
        out = asyncio.run(
            submission_freeze.get_effective_data_hash("LIVEHASH98765"))
        assert out == "LIVEHASH98765"

    def test_returns_live_hash_when_active_but_no_freeze_hash(
        self, monkeypatch,
    ):
        # An active flag with a null freeze_hash is a degenerate
        # state (the admin endpoint would never allow it, but a
        # manually edited row could). Falls through to live_hash so
        # generation does not silently substitute "" everywhere.
        from tools import submission_freeze

        async def _config():
            return {"active": True, "freeze_hash": None}

        monkeypatch.setattr(
            submission_freeze, "get_freeze_config", _config)
        out = asyncio.run(
            submission_freeze.get_effective_data_hash("LIVE"))
        assert out == "LIVE"

    def test_returns_live_hash_on_internal_error(self, monkeypatch):
        from tools import submission_freeze

        async def _boom():
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(
            submission_freeze, "get_freeze_config", _boom)
        out = asyncio.run(
            submission_freeze.get_effective_data_hash("LIVE"))
        # Fail-open: a read failure during generation falls back to
        # the live hash rather than locking the document against ""
        assert out == "LIVE"


# ── 2. POST /api/v1/admin/submission-freeze ─────────────────────────────────

class TestActivateEndpoint:
    """The activate path needs auth, a hash, and validation that the
    hash exists in strategy_results_cache."""

    def test_activate_without_hash_returns_400(self, client):
        resp = client.post(
            "/api/v1/admin/submission-freeze",
            json={"active": True},
            headers=_auth_headers())
        assert resp.status_code == 400
        assert "freeze_hash" in resp.json()["detail"].lower()

    def test_activate_with_valid_hash_returns_200(
        self, client, monkeypatch,
    ):
        # Stub the hash-existence check so it returns a row, and
        # stub set_freeze_config to a no-op that returns the
        # activated payload.
        from tools import submission_freeze

        async def _fake_set(**kwargs):
            return {
                "active": True,
                "freeze_hash": kwargs["freeze_hash"],
                "freeze_date": "2026-06-30",
                "activated_by": kwargs.get("activated_by"),
                "activated_at": "2026-06-30T12:00:00+00:00",
            }

        monkeypatch.setattr(
            submission_freeze, "set_freeze_config", _fake_set)

        # Stub the cache-validation DB read (the endpoint short-
        # circuits when AsyncSessionLocal is None, treating absence
        # as "DB unreachable; skip validation").
        import database
        monkeypatch.setattr(database, "AsyncSessionLocal", None)

        resp = client.post(
            "/api/v1/admin/submission-freeze",
            json={"active": True, "freeze_hash": "abc12345def67890"},
            headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["freeze_hash"] == "abc12345def67890"

    def test_activate_with_invalid_hash_returns_400(
        self, client, monkeypatch,
    ):
        # Stub a fake DB where the validation query returns no row.
        class FakeResult:
            def fetchone(self):
                return None

        class FakeSession:
            async def execute(self, stmt, params=None):
                return FakeResult()

            async def commit(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        # Patch the in-endpoint import of AsyncSessionLocal
        import database
        monkeypatch.setattr(
            database, "AsyncSessionLocal", lambda: FakeSession())

        resp = client.post(
            "/api/v1/admin/submission-freeze",
            json={"active": True, "freeze_hash": "deadbeefdeadbeef"},
            headers=_auth_headers())
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()

    def test_deactivate_returns_cleared_state(
        self, client, monkeypatch,
    ):
        from tools import submission_freeze

        async def _fake_set(**kwargs):
            assert kwargs.get("active") is False
            return {
                "active": False, "freeze_hash": None,
                "freeze_date": None, "activated_by": None,
                "activated_at": None,
            }

        monkeypatch.setattr(
            submission_freeze, "set_freeze_config", _fake_set)

        resp = client.post(
            "/api/v1/admin/submission-freeze",
            json={"active": False},
            headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is False
        assert body["freeze_hash"] is None


# ── 3. GET /api/v1/admin/submission-status ───────────────────────────────────

class TestSubmissionStatusEndpoint:
    """Read-only view. Available to any authenticated user (Bob and
    Molly need to see whether the freeze is on). All DB-touching
    helpers are stubbed so this runs in CI without Postgres."""

    def _stub_helpers(
        self, monkeypatch, *,
        freeze_config: dict,
        live_hash: str = "LIVE12345abcdef",
        drafts: dict | None = None,
    ):
        from tools import submission_freeze
        from tools import editor_drafts
        import main as main_module

        drafts = drafts or {}

        async def _config():
            return freeze_config

        async def _live():
            return live_hash

        async def _draft(email, doc_type):
            return drafts.get(doc_type)

        monkeypatch.setattr(
            submission_freeze, "get_freeze_config", _config)
        # June 21 2026 -- submission-status reads strategy_hash from
        # strategy_results_cache directly via _read_latest_strategy_
        # hash, NOT from tools.audit_assembler.current_data_hash.
        # Stub the new helper so the test fixture controls live_hash
        # without monkeypatching AsyncSessionLocal.
        monkeypatch.setattr(
            main_module, "_read_latest_strategy_hash", _live)
        # June 21 2026 -- endpoint switched to the Layer-3 variant of
        # get_current_draft so export_verification flows through.
        monkeypatch.setattr(
            editor_drafts, "get_current_draft_with_layer3", _draft)

    def test_status_with_freeze_off_returns_expected_shape(
        self, client, monkeypatch,
    ):
        self._stub_helpers(
            monkeypatch,
            freeze_config={
                "active": False, "freeze_hash": None,
                "freeze_date": None,
                "activated_by": None, "activated_at": None,
            })

        resp = client.get(
            "/api/v1/admin/submission-status",
            headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["freeze_active"] is False
        assert body["freeze_hash"] is None
        assert body["hash_drift"] is False
        assert body["submission_ready"] is False
        assert "frozen_documents" in body
        assert set(body["frozen_documents"].keys()) == {
            "brief", "deck", "appendix"}
        assert body["submission_recommendation"].lower().startswith(
            "activate the submission freeze")

    def test_status_with_freeze_active_and_matching_hash(
        self, client, monkeypatch,
    ):
        self._stub_helpers(
            monkeypatch,
            freeze_config={
                "active": True,
                "freeze_hash": "MATCH12345abcdef",
                "freeze_date": "2026-06-30",
                "activated_by": "ruurdsm@queens.edu",
                "activated_at": "2026-06-30T12:00:00+00:00",
            },
            live_hash="MATCH12345abcdef")

        resp = client.get(
            "/api/v1/admin/submission-status",
            headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["freeze_active"] is True
        assert body["freeze_hash"] == "MATCH12345abcdef"
        # Frozen hash == live hash -> no drift
        assert body["hash_drift"] is False

    def test_status_with_hash_drift_reports_drift_true(
        self, client, monkeypatch,
    ):
        self._stub_helpers(
            monkeypatch,
            freeze_config={
                "active": True,
                "freeze_hash": "FROZEN_FAR_BACK",
                "freeze_date": "2026-06-30",
                "activated_by": None, "activated_at": None,
            },
            live_hash="LIVE_NEW_INGEST")

        resp = client.get(
            "/api/v1/admin/submission-status",
            headers=_auth_headers())
        body = resp.json()
        assert body["hash_drift"] is True
        assert body["submission_ready"] is False

    def test_submission_ready_false_when_documents_not_exported(
        self, client, monkeypatch,
    ):
        # Drafts exist for all three types but none have an
        # export_verification entry -> exported=False -> not ready.
        drafts = {
            "executive_brief": {
                "id": 1, "export_verification": None},
            "presentation_deck": {
                "id": 2, "export_verification": None},
            "analytical_appendix": {
                "id": 3, "export_verification": None},
        }
        self._stub_helpers(
            monkeypatch,
            freeze_config={
                "active": True, "freeze_hash": "HASH123",
                "freeze_date": "2026-06-30",
                "activated_by": None, "activated_at": None,
            },
            live_hash="HASH123",
            drafts=drafts)

        resp = client.get(
            "/api/v1/admin/submission-status",
            headers=_auth_headers())
        body = resp.json()
        assert body["submission_ready"] is False
        # Each document is "generated" but not "exported"
        for k in ("brief", "deck", "appendix"):
            assert body["frozen_documents"][k]["generated"] is True
            assert body["frozen_documents"][k]["exported"] is False
        assert "export" in body["submission_recommendation"].lower()

    def test_submission_ready_true_when_all_pre_conditions_met(
        self, client, monkeypatch,
    ):
        drafts = {
            doc_type: {
                "id": i + 1,
                "export_verification": {"passed": True},
            }
            for i, doc_type in enumerate((
                "executive_brief",
                "presentation_deck",
                "analytical_appendix",
            ))
        }
        self._stub_helpers(
            monkeypatch,
            freeze_config={
                "active": True, "freeze_hash": "FROZEN",
                "freeze_date": "2026-06-30",
                "activated_by": "ruurdsm@queens.edu",
                "activated_at": "2026-06-30T12:00:00+00:00",
            },
            live_hash="FROZEN",
            drafts=drafts)

        resp = client.get(
            "/api/v1/admin/submission-status",
            headers=_auth_headers())
        body = resp.json()
        assert body["submission_ready"] is True
        assert body["hash_drift"] is False
        for k in ("brief", "deck", "appendix"):
            assert body["frozen_documents"][k]["exported"] is True
            assert body["frozen_documents"][k]["export_verified"] is True
        assert "safe to submit" in body["submission_recommendation"].lower()

    def test_current_live_hash_reads_strategy_results_cache(
        self, client, monkeypatch,
    ):
        """June 21 2026 -- the canonical hash for freeze validation
        is strategy_results_cache.strategy_hash, NOT
        current_data_hash() (a SHA256 of platform-level row counts
        + max dates). This test pins that the endpoint reads from
        the right source: the stubbed live hash flows through to
        the response as current_live_hash."""
        self._stub_helpers(
            monkeypatch,
            freeze_config={
                "active": False, "freeze_hash": None,
                "freeze_date": None,
                "activated_by": None, "activated_at": None,
            },
            live_hash="c421fb895347f924")
        resp = client.get(
            "/api/v1/admin/submission-status",
            headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        # The endpoint should surface the value our stub returned --
        # demonstrating the read path goes through _read_latest_
        # strategy_hash, not current_data_hash.
        assert body["current_live_hash"] == "c421fb895347f924"

    def test_endpoint_returns_200_when_draft_read_raises(
        self, client, monkeypatch,
    ):
        """The draft read used to bleed transaction-aborted state
        between per-document calls when one of the SELECTs hit a
        pre-Layer-3 schema column-missing error. The endpoint
        must remain 200 even if every draft read raises; per-
        document state degrades to {generated: False} rather than
        crashing the whole response."""
        from tools import submission_freeze
        from tools import editor_drafts
        import main as main_module

        async def _config():
            return {"active": False, "freeze_hash": None,
                    "freeze_date": None}

        async def _live():
            return "LIVE_HASH"

        async def _draft_raises(email, doc_type):
            raise RuntimeError(
                "InFailedSQLTransactionError: simulated")

        monkeypatch.setattr(
            submission_freeze, "get_freeze_config", _config)
        monkeypatch.setattr(
            main_module, "_read_latest_strategy_hash", _live)
        monkeypatch.setattr(
            editor_drafts, "get_current_draft_with_layer3",
            _draft_raises)

        resp = client.get(
            "/api/v1/admin/submission-status",
            headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        # Every doc degrades to "not generated" rather than the
        # endpoint 500ing.
        for k in ("brief", "deck", "appendix"):
            assert body["frozen_documents"][k]["generated"] is False
            assert body["frozen_documents"][k]["exported"] is False


# ── 4. Document generation honours the EFFECTIVE hash ────────────────────────

class TestDocGenUsesEffectiveHash:
    """When the freeze is active, the document generators must call
    get_substitution_table with the FROZEN hash, not the live hash.
    Pin this contract via the freeze module's resolver (the same
    resolver the generators import). This is the single seam every
    generator routes through; verifying it once covers brief, deck,
    and appendix."""

    def test_effective_hash_is_frozen_when_freeze_active(
        self, monkeypatch,
    ):
        from tools import submission_freeze

        async def _frozen_config():
            return {"active": True, "freeze_hash": "FROZEN_FROM_MAY"}

        monkeypatch.setattr(
            submission_freeze, "get_freeze_config", _frozen_config)

        # Simulate the doc-gen call site
        live = "LIVE_FROM_JUNE_30"
        effective = asyncio.run(
            submission_freeze.get_effective_data_hash(live))
        # The substitution-table builder receives the FROZEN hash
        # -- this is the contract the brief / deck / appendix
        # generators depend on.
        assert effective == "FROZEN_FROM_MAY"
        assert effective != live
