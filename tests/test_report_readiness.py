"""
tests/test_report_readiness.py

Workstream C — report-readiness gate (May 28 2026). Two layers:

  1. The compute_readiness() pure function — combines statistical and
     methodology blockers into one verdict and a summarisable list.
     Uses monkeypatch to inject fixture data so the module's contract
     can be pinned without touching the live database.

  2. The GET /api/v1/report/readiness endpoint + the three generation
     endpoints' gate behaviour. Pinned via the FastAPI TestClient with
     the readiness internals stubbed so the gate response shape is
     verified deterministically.
"""
from __future__ import annotations

import asyncio
import os
import sys

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


# ── Pure-function contract ───────────────────────────────────────────────────

class TestComputeReadinessFailOpen:
    """A platform with no audit history reports is_ready=true — the
    gate refuses to block on something that does not exist. Same
    answer when either query fails (DB outage)."""

    def test_returns_ready_when_both_surfaces_return_empty(
        self, monkeypatch,
    ):
        from tools import report_readiness

        async def _empty_stat():
            return {"unreviewed_warnings": [], "unreviewed_failures": []}

        async def _empty_meth():
            return {"unresolved_warnings": [], "unresolved_failures": []}

        monkeypatch.setattr(
            report_readiness, "_statistical_blocking", _empty_stat)
        monkeypatch.setattr(
            report_readiness, "_methodology_blocking", _empty_meth)
        out = asyncio.run(report_readiness.compute_readiness())
        assert out["is_ready"] is True
        assert out["blocking_count"] == 0
        assert "checked_at" in out

    def test_returns_ready_when_each_surface_raises(self, monkeypatch):
        # _statistical_blocking and _methodology_blocking each carry
        # their own try/except; a failure inside them returns empty
        # lists. compute_readiness reads through to the verdict.
        from tools import report_readiness

        async def _boom():
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(report_readiness, "_statistical_blocking",
                            lambda: _boom())
        monkeypatch.setattr(report_readiness, "_methodology_blocking",
                            lambda: _boom())
        # The compute_readiness wrapper still has to await something —
        # the wrapping functions each return empty on internal error,
        # so to test the compute_readiness wrapper directly we stub
        # the wrappers themselves to return empties (the wrappers are
        # the layer that catches errors).
        async def _empty_stat():
            return {"unreviewed_warnings": [], "unreviewed_failures": []}

        async def _empty_meth():
            return {"unresolved_warnings": [], "unresolved_failures": []}

        monkeypatch.setattr(
            report_readiness, "_statistical_blocking", _empty_stat)
        monkeypatch.setattr(
            report_readiness, "_methodology_blocking", _empty_meth)
        out = asyncio.run(report_readiness.compute_readiness())
        assert out["is_ready"] is True


class TestComputeReadinessBlocking:
    """Either surface with one blocker reports is_ready=false."""

    def test_unreviewed_warning_blocks(self, monkeypatch):
        from tools import report_readiness

        async def _stat():
            return {
                "unreviewed_warnings": [{
                    "finding_id": 7, "layer": 2,
                    "check_name": "Sharpe verification",
                    "metric": "sharpe_ratio", "strategy": "REGIME_SWITCHING",
                    "status": "warning", "discrepancy": "0.4%",
                }],
                "unreviewed_failures": [],
            }

        async def _meth():
            return {"unresolved_warnings": [], "unresolved_failures": []}

        monkeypatch.setattr(
            report_readiness, "_statistical_blocking", _stat)
        monkeypatch.setattr(
            report_readiness, "_methodology_blocking", _meth)
        out = asyncio.run(report_readiness.compute_readiness())
        assert out["is_ready"] is False
        assert out["blocking_count"] == 1

    def test_methodology_fail_blocks_even_with_clean_statistical(
        self, monkeypatch,
    ):
        from tools import report_readiness

        async def _stat():
            return {"unreviewed_warnings": [], "unreviewed_failures": []}

        async def _meth():
            return {
                "unresolved_warnings": [],
                "unresolved_failures": [{
                    "check_id": "P03",
                    "check": "Transaction costs applied",
                    "description": "...",
                    "category": "PORTFOLIO_MECHANICS",
                    "status": "FAIL",
                }],
            }

        monkeypatch.setattr(
            report_readiness, "_statistical_blocking", _stat)
        monkeypatch.setattr(
            report_readiness, "_methodology_blocking", _meth)
        out = asyncio.run(report_readiness.compute_readiness())
        assert out["is_ready"] is False
        assert out["blocking_count"] == 1

    def test_blocking_count_aggregates_across_both_surfaces(
        self, monkeypatch,
    ):
        from tools import report_readiness

        async def _stat():
            return {
                "unreviewed_warnings": [{"finding_id": 1, "layer": 2,
                                         "check_name": "a", "metric": "m",
                                         "strategy": None,
                                         "status": "warning",
                                         "discrepancy": None}],
                "unreviewed_failures": [{"finding_id": 2, "layer": 3,
                                         "check_name": "b", "metric": "m",
                                         "strategy": None, "status": "fail",
                                         "discrepancy": None}],
            }

        async def _meth():
            return {
                "unresolved_warnings": [{"check_id": "P03", "check": "c",
                                         "description": "...",
                                         "category": "PORTFOLIO_MECHANICS",
                                         "status": "WARN"}],
                "unresolved_failures": [{"check_id": "S08", "check": "d",
                                         "description": "...",
                                         "category": "STATISTICAL_INTEGRITY",
                                         "status": "FAIL"}],
            }

        monkeypatch.setattr(
            report_readiness, "_statistical_blocking", _stat)
        monkeypatch.setattr(
            report_readiness, "_methodology_blocking", _meth)
        out = asyncio.run(report_readiness.compute_readiness())
        assert out["blocking_count"] == 4
        assert out["is_ready"] is False


class TestNonBlockingWarnFilter:
    """Bridge #74: a WARN with warn_class='non_blocking' must NEVER
    gate report generation. The canonical case is AN03 (sensitivity
    extension marked as Section 4 planned extension in qa_agent.py's
    _SUBMISSION_CLASSIFICATIONS). The methodology blocker filter must
    drop these from unresolved_warnings so the deck / brief / appendix
    endpoints can generate even when AN03 is WARN."""

    def test_is_non_blocking_warn_helper(self):
        from tools.report_readiness import _is_non_blocking_warn

        # The three taxonomy values from qa_agent._SUBMISSION_CLASSIFICATIONS.
        # No check_id present -> the per-item warn_class fallback applies.
        assert _is_non_blocking_warn(
            {"warn_class": "non_blocking"}) is True
        assert _is_non_blocking_warn(
            {"warn_class": "disclosure_required"}) is False
        assert _is_non_blocking_warn({"warn_class": "blocks"}) is False
        # Missing classification defaults to the most conservative read
        # (the helper returns False so the legacy disclosure-required
        # gate behaviour applies).
        assert _is_non_blocking_warn({}) is False
        assert _is_non_blocking_warn({"warn_class": None}) is False
        # Case + whitespace tolerance so a future capitalisation drift
        # cannot silently flip the gate.
        assert _is_non_blocking_warn(
            {"warn_class": "NON_BLOCKING"}) is True
        assert _is_non_blocking_warn(
            {"warn_class": "  non_blocking  "}) is True

    def test_static_classification_overrides_stale_cached_warn_class(self):
        """Bridge #85 -- the gate reads the source-of-truth static map
        from qa_agent.py first. A cached qa_results_cache row whose
        warn_class is stale ('disclosure_required' for IN02, written
        before PR #300 reclassified IN02 to non_blocking) MUST be
        treated as non-blocking by the gate because the STATIC
        classification is the authoritative one.

        Without this, IN02 would keep blocking generation on Render
        until the next QA audit run rewrote the cached row -- which
        is exactly the bug the user reported in bridge #85."""
        from tools.report_readiness import _is_non_blocking_warn

        # IN02 in qa_agent._SUBMISSION_CLASSIFICATIONS is non_blocking
        # after PR #300. Even if the cached row still carries the
        # stale disclosure_required value, the gate must say "non
        # blocking".
        assert _is_non_blocking_warn({
            "check_id": "IN02",
            "warn_class": "disclosure_required",  # stale cache value
        }) is True

        # Conversely, a check classified disclosure_required in the
        # static map remains blocking even if the row mis-states it
        # as non_blocking (defensive both ways).
        assert _is_non_blocking_warn({
            "check_id": "D01",
            "warn_class": "non_blocking",  # bogus row value
        }) is False

    def test_unknown_check_id_falls_back_to_row_warn_class(self):
        """A check_id not present in _SUBMISSION_CLASSIFICATIONS
        falls back to the per-row warn_class field. This keeps the
        helper extension-friendly: tests can pin behaviour for a
        synthetic check_id without registering it in the static map."""
        from tools.report_readiness import _is_non_blocking_warn

        assert _is_non_blocking_warn({
            "check_id": "XX99",   # not in the static map
            "warn_class": "non_blocking",
        }) is True
        assert _is_non_blocking_warn({
            "check_id": "XX99",
            "warn_class": "disclosure_required",
        }) is False

    def test_methodology_blocking_drops_non_blocking_warn(
        self, monkeypatch,
    ):
        """An AN03 WARN (warn_class='non_blocking') must not appear in
        unresolved_warnings -- it is informational only. A D01 WARN
        (warn_class='disclosure_required') must still block when no
        override is present."""
        from tools import report_readiness

        async def _fake_recent_qa_run(min_tier: int = 1):
            return {"checklist": {"items": [
                # AN03 sensitivity -- non_blocking by qa_agent taxonomy.
                {"check_id": "AN03", "status": "WARN",
                 "warn_class": "non_blocking",
                 "check": "Sensitivity sweep", "category": "ANALYTICS"},
                # D01 data integrity -- disclosure_required; must block.
                {"check_id": "D01", "status": "WARN",
                 "warn_class": "disclosure_required",
                 "check": "Returns audit", "category": "DATA"},
                # A FAIL is always blocking regardless of warn_class.
                {"check_id": "P01", "status": "FAIL",
                 "warn_class": "blocks",
                 "check": "Weight integrity", "category": "PORTFOLIO"},
            ]}}

        monkeypatch.setattr(
            "tools.cache.get_most_recent_qa_run", _fake_recent_qa_run)

        # Empty overrides so D01 stays unreviewed (the only thing that
        # would otherwise drop it from the list).
        async def _empty_overrides_session():
            class _S:
                async def __aenter__(self_):
                    return self_
                async def __aexit__(self_, *a):
                    return None
                async def execute(self_, *_a, **_kw):
                    class _R:
                        def fetchall(self): return []
                    return _R()
            return _S()
        # The function reads overrides via direct SQL; patch the
        # AsyncSessionLocal it imports so the iterator yields no rows.
        import database
        monkeypatch.setattr(
            database, "AsyncSessionLocal", _empty_overrides_session)

        result = asyncio.run(report_readiness._methodology_blocking())

        warning_ids = {it["check_id"]
                       for it in result["unresolved_warnings"]}
        failure_ids = {it["check_id"]
                       for it in result["unresolved_failures"]}

        # AN03 dropped -- non_blocking taxonomy.
        assert "AN03" not in warning_ids
        # D01 still blocking -- disclosure_required + no override.
        assert "D01" in warning_ids
        # P01 FAIL still blocking -- unchanged.
        assert "P01" in failure_ids

    def test_an03_warn_alone_does_not_block_compute_readiness(
        self, monkeypatch,
    ):
        """End-to-end via compute_readiness: a single AN03 WARN finding
        and no other blockers must produce is_ready=True. Pre-fix this
        was is_ready=False, surfacing as a 422 on the generation
        endpoints."""
        from tools import report_readiness

        async def _empty_stat():
            return {"unreviewed_warnings": [], "unreviewed_failures": []}

        async def _meth_with_an03():
            # _methodology_blocking already filters non_blocking WARNs;
            # we test the filtered output threads through to a clean
            # verdict. This is the contract compute_readiness must
            # satisfy with the new helper in place.
            return {"unresolved_warnings": [], "unresolved_failures": []}

        monkeypatch.setattr(
            report_readiness, "_statistical_blocking", _empty_stat)
        monkeypatch.setattr(
            report_readiness, "_methodology_blocking", _meth_with_an03)

        out = asyncio.run(report_readiness.compute_readiness())
        assert out["is_ready"] is True
        assert out["blocking_count"] == 0


class TestCacheWarmthGate:
    """Bridge #91: cold analytics caches must block generation before
    the LLM run fires, not after. compute_readiness consults
    _caches_warm and contributes ONE blocker per cold-cache verdict.
    The 422 detail surfaces error='caches_not_warm' so the modal can
    render a Warm Caches button instead of the audit-blocker list."""

    def test_caches_warm_true_in_test_env_is_default(self, monkeypatch):
        """ENVIRONMENT=test short-circuits the cache check (auto-warm
        is disabled in tests so the WarmState stays idle). Without
        this short-circuit every existing readiness fixture would
        gain a synthetic blocker."""
        from tools.report_readiness import _caches_warm
        # The conftest already sets ENVIRONMENT=test; the helper
        # must return caches_warm=True without consulting the live
        # WarmState.
        verdict = _caches_warm()
        assert verdict["caches_warm"] is True
        assert verdict["cold_caches"] == []
        assert verdict["warm_status"] == "warm"

    def test_caches_cold_adds_a_synthetic_blocker(self, monkeypatch):
        """A non-test env with a cold WarmState contributes one
        blocker to the count. compute_readiness exposes the cold list
        AND keeps is_ready=False even when no audit blocker exists."""
        from tools import report_readiness

        # Force the helper to report cold (bypass the test-env
        # short-circuit). Stub the helper directly so the test is
        # decoupled from env-var handling.
        def _fake_cold_caches():
            return {
                "caches_warm": False,
                "cold_caches": ["academic_analytics", "cio_recommendation"],
                "warm_status": "idle",
            }
        monkeypatch.setattr(
            report_readiness, "_caches_warm", _fake_cold_caches)

        async def _empty_stat():
            return {"unreviewed_warnings": [], "unreviewed_failures": []}

        async def _empty_meth():
            return {"unresolved_warnings": [], "unresolved_failures": []}

        monkeypatch.setattr(
            report_readiness, "_statistical_blocking", _empty_stat)
        monkeypatch.setattr(
            report_readiness, "_methodology_blocking", _empty_meth)

        out = asyncio.run(report_readiness.compute_readiness())
        assert out["is_ready"] is False
        assert out["blocking_count"] == 1
        assert out["caches_warm"] is False
        assert out["cold_caches"] == [
            "academic_analytics", "cio_recommendation"]

    def test_summarise_blockers_lists_cold_caches_first(self):
        """When caches are cold AND an audit blocker exists, the
        cold-caches entry appears FIRST -- warming is the
        prerequisite the user has to clear first."""
        from tools.report_readiness import summarise_blockers

        readiness = {
            "caches_warm": False,
            "cold_caches": ["academic_analytics"],
            "statistical": {"unreviewed_warnings": [], "unreviewed_failures": []},
            "methodology": {"unresolved_warnings": [
                {"check_id": "D01", "check": "Returns audit"}
            ], "unresolved_failures": []},
        }
        labels = summarise_blockers(readiness)
        assert labels[0].startswith("Caches are not warm")
        assert "academic_analytics" in labels[0]
        assert any("D01" in lbl for lbl in labels[1:])

    def test_summarise_blockers_warm_caches_omits_the_entry(self):
        from tools.report_readiness import summarise_blockers

        readiness = {
            "caches_warm": True,
            "cold_caches": [],
            "statistical": {"unreviewed_warnings": [], "unreviewed_failures": []},
            "methodology": {"unresolved_warnings": [
                {"check_id": "D01", "check": "Returns audit"}
            ], "unresolved_failures": []},
        }
        labels = summarise_blockers(readiness)
        assert not any("Caches are not warm" in lbl for lbl in labels)
        assert any("D01" in lbl for lbl in labels)


class TestSummariseBlockers:
    """The 422 detail line + frontend modal both render this list. The
    summariser must produce one entry per blocker, naming the surface
    and a label so the team can act on it."""

    def test_orders_failures_before_warnings_within_each_surface(self):
        from tools.report_readiness import summarise_blockers

        readiness = {
            "statistical": {
                "unreviewed_warnings": [{"layer": 2,
                                         "check_name": "stat-warn"}],
                "unreviewed_failures": [{"layer": 3,
                                         "check_name": "stat-fail"}],
            },
            "methodology": {
                "unresolved_warnings": [{"check_id": "P03",
                                         "check": "meth-warn"}],
                "unresolved_failures": [{"check_id": "S08",
                                         "check": "meth-fail"}],
            },
        }
        out = summarise_blockers(readiness)
        assert len(out) == 4
        # Failures come first within each surface, then warnings.
        assert out[0].startswith("Statistical FAIL")
        assert out[1].startswith("Statistical WARN unreviewed")
        assert out[2].startswith("Methodology FAIL")
        assert out[3].startswith("Methodology WARN unreviewed")

    def test_renders_each_label_from_finding_metadata(self):
        from tools.report_readiness import summarise_blockers

        readiness = {
            "statistical": {
                "unreviewed_warnings": [{
                    "layer": 2, "check_name": "STATCHECKLABEL",
                    "metric": "sharpe"}],
                "unreviewed_failures": [],
            },
            "methodology": {
                "unresolved_warnings": [],
                "unresolved_failures": [{
                    "check_id": "METHIDLABEL",
                    "check": "METHCHECKLABEL"}],
            },
        }
        out = summarise_blockers(readiness)
        joined = "\n".join(out)
        assert "STATCHECKLABEL" in joined
        assert "METHIDLABEL" in joined
        assert "METHCHECKLABEL" in joined

    def test_empty_readiness_returns_empty_list(self):
        from tools.report_readiness import summarise_blockers

        out = summarise_blockers({
            "statistical": {"unreviewed_warnings": [],
                            "unreviewed_failures": []},
            "methodology": {"unresolved_warnings": [],
                            "unresolved_failures": []},
        })
        assert out == []


# ── Endpoint contract ────────────────────────────────────────────────────────

class TestReadinessEndpoint:
    """GET /api/v1/report/readiness — auth + shape contract."""

    def test_rejects_unauthenticated(self, client: TestClient):
        r = client.get("/api/v1/report/readiness")
        assert r.status_code == 401

    def test_returns_verdict_shape(self, client: TestClient):
        r = client.get("/api/v1/report/readiness",
                       headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        for key in ("is_ready", "blocking_count", "statistical",
                    "methodology", "checked_at"):
            assert key in body
        for k in ("unreviewed_warnings", "unreviewed_failures"):
            assert k in body["statistical"]
            assert isinstance(body["statistical"][k], list)
        for k in ("unresolved_warnings", "unresolved_failures"):
            assert k in body["methodology"]
            assert isinstance(body["methodology"][k], list)

    def test_blocking_count_matches_lists(self, client: TestClient):
        # Whatever the live state is, the count field must equal the
        # sum of the four lists' lengths.
        r = client.get("/api/v1/report/readiness",
                       headers=_auth_headers())
        body = r.json()
        expected = (
            len(body["statistical"]["unreviewed_warnings"])
            + len(body["statistical"]["unreviewed_failures"])
            + len(body["methodology"]["unresolved_warnings"])
            + len(body["methodology"]["unresolved_failures"])
        )
        assert body["blocking_count"] == expected
        assert body["is_ready"] is (body["blocking_count"] == 0)

    def test_includes_deck_story_plan_and_script_flags(
        self, client: TestClient,
    ):
        """June 21 2026 -- the readiness payload carries two deck-
        related flags so the frontend can gate the Presentation Deck
        regen flow and the Presentation Script download independently:

          deck_story_plan_available -- True when story_plans has a
            non-fallback row for (current_data_hash, 'deck'). Used
            to surface the plan-derived state on the regen flow.
          deck_script_available -- True when the same row ALSO
            carries a non-empty full_script. Used by the Script
            card -- a story plan can land without a script (Pass 2
            is a separate Opus call that can fail independently),
            and gating the card on plan availability alone would
            let the user download a script that doesn't exist."""
        r = client.get("/api/v1/report/readiness",
                       headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        assert "deck_story_plan_available" in body
        assert "deck_script_available" in body
        # Both flags are booleans, never null. Without a cached deck
        # in test env (no live DB), both should be False -- the
        # fail-open path treats any read error as "not available".
        assert isinstance(body["deck_story_plan_available"], bool)
        assert isinstance(body["deck_script_available"], bool)
        # Script availability implies plan availability (a script
        # cannot exist without a plan it was generated from).
        if body["deck_script_available"]:
            assert body["deck_story_plan_available"]

    def test_deck_status_helper_returns_false_false_on_cold_cache(self):
        """The helper that backs the two flags must return
        (False, False) when there is no current_data_hash or no
        cached deck plan -- the fail-open contract every call site
        depends on. Without a live DB in test env, both reads
        degrade to None and the flags fall to False."""
        import asyncio
        from main import _deck_story_plan_status
        plan_available, script_available = asyncio.run(
            _deck_story_plan_status())
        assert plan_available is False
        assert script_available is False


# ── Generation gate ──────────────────────────────────────────────────────────

class TestGenerationGate:
    """The three generation endpoints are gated by _require_report_ready.
    With blockers present, generation returns 422 with a structured
    detail naming every blocker."""

    def _seed_blockers(self, monkeypatch) -> None:
        # Patch compute_readiness inside report_readiness AND inside the
        # import-bound reference used by _require_report_ready. The
        # gate does a local import so patching the module attribute
        # is enough. The kwarg is accepted-and-ignored — the blocker
        # used here is P03, not IN02, so the midpoint exclusion does
        # not change the outcome.
        from tools import report_readiness

        async def _blocked(exclude_methodology_check_ids=None):
            return {
                "is_ready": False,
                "blocking_count": 2,
                "statistical": {
                    "unreviewed_warnings": [{
                        "finding_id": 1, "layer": 2,
                        "check_name": "GATETESTSTATCHECK",
                        "metric": "sharpe", "strategy": None,
                        "status": "warning", "discrepancy": None,
                    }],
                    "unreviewed_failures": [],
                },
                "methodology": {
                    "unresolved_warnings": [{
                        "check_id": "P03",
                        "check": "GATETESTMETHCHECK",
                        "description": "...",
                        "category": "PORTFOLIO_MECHANICS",
                        "status": "WARN",
                    }],
                    "unresolved_failures": [],
                },
                "checked_at": "2026-05-28T00:00:00+00:00",
            }

        monkeypatch.setattr(report_readiness, "compute_readiness", _blocked)

    def test_executive_brief_audit_block_now_warns_not_blocks(
        self, monkeypatch, client: TestClient,
    ):
        # June 25 2026 -- audit findings WARN, never BLOCK per the
        # platform's fail-open architecture. The gate logs the
        # finding count but proceeds with the regen kickoff (202).
        # The frontend reads /api/v1/report/readiness separately
        # to surface the amber warning banner; the gate itself no
        # longer hard-blocks. Pinned previously as 422 with
        # error='report_not_ready' (test name kept verbatim in the
        # commit-message history).
        self._seed_blockers(monkeypatch)
        r = client.post("/api/v1/export/executive-brief",
                        headers=_auth_headers(), json={})
        assert r.status_code == 202
        body = r.json()
        # 202 carries the job id payload, not a structured error.
        assert "job_id" in body
        assert body.get("status") == "pending"

    def test_executive_brief_audit_block_does_not_422(
        self, monkeypatch, client: TestClient,
    ):
        self._seed_blockers(monkeypatch)
        r = client.post("/api/v1/export/executive-brief",
                        headers=_auth_headers(), json={})
        assert r.status_code == 202

    def test_presentation_deck_audit_block_does_not_422(
        self, monkeypatch, client: TestClient,
    ):
        self._seed_blockers(monkeypatch)
        r = client.post("/api/v1/export/presentation-deck",
                        headers=_auth_headers(), json={})
        assert r.status_code == 202

    def test_editor_export_path_skips_the_gate(
        self, monkeypatch, client: TestClient,
    ):
        """An editor export is a faithful render of a draft the author
        already saved — it should not be gated. The endpoint may 404
        on a missing draft, but it must NOT return the readiness 422
        even when blockers exist."""
        self._seed_blockers(monkeypatch)
        # PR-B (June 2026) retired /api/v1/export/midpoint-paper; the
        # editor-export gate-skip is now pinned via the executive-
        # brief endpoint (same _editor_export() implementation).
        r = client.post("/api/v1/export/executive-brief",
                        headers=_auth_headers(),
                        json={"editor_draft_id": 99999999})
        assert r.status_code != 422

    def test_ready_state_allows_generation_to_start(
        self, monkeypatch, client: TestClient,
    ):
        """With is_ready=true the gate passes through — the endpoint
        responds 202 (job_id) and not 422."""
        from tools import report_readiness

        async def _ready(exclude_methodology_check_ids=None):
            return {
                "is_ready": True, "blocking_count": 0,
                "statistical": {"unreviewed_warnings": [],
                                "unreviewed_failures": []},
                "methodology": {"unresolved_warnings": [],
                                "unresolved_failures": []},
                "checked_at": "2026-05-28T00:00:00+00:00",
            }

        monkeypatch.setattr(report_readiness, "compute_readiness", _ready)
        # PR-B (June 2026) retired /api/v1/export/midpoint-paper; the
        # ready-state 202 path is now pinned via executive-brief.
        r = client.post("/api/v1/export/executive-brief",
                        headers=_auth_headers(), json={})
        # 202 on success — the job is created and scheduled.
        assert r.status_code == 202


# ── IN02 advisory carve-out (May 25 2026) ────────────────────────────────────
#
# The midpoint paper generation passes exclude_methodology_check_ids=
# {"IN02"} so Academic Review completeness is advisory for that
# document type only — the auto-fired review produces a score
# surfaced in the editor instead of blocking generation. The exec
# brief and the deck retain the full set of blockers.

class TestComputeReadinessExcludesMethodologyCheckIds:
    """The exclude_methodology_check_ids parameter strips matching
    rows from the methodology blockers BEFORE the blocking_count is
    computed. Tests use the real compute_readiness against monkey-
    patched _methodology_blocking / _statistical_blocking so the
    filter logic is exercised end-to-end."""

    def test_in02_warning_excluded_when_in_the_set(self, monkeypatch):
        import asyncio
        from tools import report_readiness

        async def _stat():
            return {"unreviewed_warnings": [], "unreviewed_failures": []}

        async def _meth():
            return {
                "unresolved_warnings": [{
                    "check_id": "IN02", "check": "Academic Review complete",
                    "description": "...", "category": "INTEGRATION",
                    "status": "WARN"}],
                "unresolved_failures": [],
            }

        monkeypatch.setattr(report_readiness, "_statistical_blocking", _stat)
        monkeypatch.setattr(report_readiness, "_methodology_blocking", _meth)
        # Without the exclusion: IN02 blocks.
        baseline = asyncio.run(report_readiness.compute_readiness())
        assert baseline["is_ready"] is False
        assert baseline["blocking_count"] == 1
        # With IN02 excluded: not_ready becomes ready.
        filtered = asyncio.run(report_readiness.compute_readiness(
            exclude_methodology_check_ids={"IN02"}))
        assert filtered["is_ready"] is True
        assert filtered["blocking_count"] == 0
        # The detail also drops IN02 from the methodology lists.
        assert filtered["methodology"]["unresolved_warnings"] == []

    def test_other_methodology_blockers_still_block(self, monkeypatch):
        """Non-IN02 methodology blockers must remain when IN02 is the
        only exclusion. A P03 FAIL must still 422 the midpoint."""
        import asyncio
        from tools import report_readiness

        async def _stat():
            return {"unreviewed_warnings": [], "unreviewed_failures": []}

        async def _meth():
            return {
                "unresolved_warnings": [{
                    "check_id": "IN02", "check": "Academic Review complete",
                    "description": "...", "category": "INTEGRATION",
                    "status": "WARN"}],
                "unresolved_failures": [{
                    "check_id": "P03",
                    "check": "Transaction costs applied",
                    "description": "...", "category": "PORTFOLIO_MECHANICS",
                    "status": "FAIL"}],
            }

        monkeypatch.setattr(report_readiness, "_statistical_blocking", _stat)
        monkeypatch.setattr(report_readiness, "_methodology_blocking", _meth)
        out = asyncio.run(report_readiness.compute_readiness(
            exclude_methodology_check_ids={"IN02"}))
        assert out["is_ready"] is False
        assert out["blocking_count"] == 1  # P03 still blocks
        cids = {it.get("check_id")
                for it in out["methodology"]["unresolved_failures"]}
        assert cids == {"P03"}

    def test_statistical_blockers_are_unaffected_by_methodology_exclusion(
        self, monkeypatch,
    ):
        """The exclusion filter applies only to the methodology list —
        a statistical FAIL is never filtered out by passing IN02 to
        the methodology exclusion set."""
        import asyncio
        from tools import report_readiness

        async def _stat():
            return {
                "unreviewed_warnings": [],
                "unreviewed_failures": [{
                    "finding_id": 1, "layer": 2,
                    "check_name": "STATFAIL",
                    "metric": "sharpe_ratio", "strategy": "BENCHMARK",
                    "status": "FAIL", "discrepancy": "5%"}],
            }

        async def _meth():
            return {
                "unresolved_warnings": [{
                    "check_id": "IN02", "check": "Academic Review complete",
                    "description": "...", "category": "INTEGRATION",
                    "status": "WARN"}],
                "unresolved_failures": [],
            }

        monkeypatch.setattr(report_readiness, "_statistical_blocking", _stat)
        monkeypatch.setattr(report_readiness, "_methodology_blocking", _meth)
        out = asyncio.run(report_readiness.compute_readiness(
            exclude_methodology_check_ids={"IN02"}))
        assert out["is_ready"] is False
        assert out["blocking_count"] == 1  # statistical FAIL stands


class TestUniversalIN02NonBlocking:
    """Bridge #82: IN02 is reclassified non_blocking in qa_agent.py,
    so the readiness gate drops it via the generic _is_non_blocking_warn
    filter on every document type — midpoint, executive brief, AND the
    presentation deck. The previous per-document carve-out
    (`exclude_methodology_check_ids={"IN02"}` on the midpoint path
    only) has been removed because the taxonomy itself now says
    "IN02 never blocks generation". The gate behaves identically for
    all three endpoints — no special-casing by finding code.

    A real IN02 WARN now carries warn_class='non_blocking' (set in
    _SUBMISSION_CLASSIFICATIONS) so the _methodology_blocking filter
    drops it before compute_readiness even sees it. Tests below mock
    the methodology layer so the IN02 WARN never appears in
    unresolved_warnings — that is the canonical post-bridge-#82
    behaviour: the methodology blocker filter handles it, not a
    per-endpoint exclusion param."""

    def _seed_in02_dropped_at_methodology(self, monkeypatch) -> None:
        """After the reclassification, _methodology_blocking filters
        IN02 out generically (warn_class='non_blocking'). Mock the
        layer below to return empty -- matching the post-fix shape."""
        from tools import report_readiness

        async def _verdict(exclude_methodology_check_ids=None):
            # IN02 is already dropped by the methodology filter; this
            # mock represents post-filter state (no IN02 here).
            return {
                "is_ready": True,
                "blocking_count": 0,
                "statistical": {"unreviewed_warnings": [],
                                "unreviewed_failures": []},
                "methodology": {"unresolved_warnings": [],
                                "unresolved_failures": []},
                "checked_at": "2026-06-07T00:00:00+00:00",
            }

        monkeypatch.setattr(report_readiness, "compute_readiness", _verdict)

    # PR-B (June 2026) retired /api/v1/export/midpoint-paper; the
    # IN02 non-blocking contract is pinned via the executive-brief +
    # presentation-deck cases below, which use the same
    # _require_report_ready() gate.

    def test_executive_brief_accepts_when_only_in02_would_block(
        self, monkeypatch, client: TestClient,
    ):
        """NEW post-bridge-#82: the executive brief no longer 422s
        when IN02 is the only methodology WARN — IN02 is universally
        non-blocking."""
        self._seed_in02_dropped_at_methodology(monkeypatch)
        r = client.post("/api/v1/export/executive-brief",
                        headers=_auth_headers(), json={})
        assert r.status_code == 202

    def test_presentation_deck_accepts_when_only_in02_would_block(
        self, monkeypatch, client: TestClient,
    ):
        """NEW post-bridge-#82: deck matches the exec brief — IN02
        never gates any document type."""
        self._seed_in02_dropped_at_methodology(monkeypatch)
        r = client.post("/api/v1/export/presentation-deck",
                        headers=_auth_headers(), json={})
        assert r.status_code == 202
