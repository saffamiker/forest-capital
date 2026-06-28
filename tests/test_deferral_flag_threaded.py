"""tests/test_deferral_flag_threaded.py -- June 28 2026.

Regression pins for the final Phase 2 deferral fix: the
DEFER_SUBSTITUTION_TO_EXPORT flag is now resolved ONCE in the
async caller (_generate_narratives) and threaded into
harness_narrative as a bool parameter. The worker thread never
queries the DB -- eliminates the 'Future attached to a
different loop' error operator observed on drafts 74/75.
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


class TestHarnessNarrativeSignature:

    def test_harness_narrative_accepts_defer_substitution_kwarg(
            self):
        """harness_narrative must accept defer_substitution as
        a kwarg so callers can pre-resolve the flag in async
        context and thread it through asyncio.to_thread."""
        from tools.academic_export import harness_narrative
        sig = inspect.signature(harness_narrative)
        assert "defer_substitution" in sig.parameters
        # Default is False -- preserves legacy behaviour when
        # the caller doesn't pre-resolve.
        param = sig.parameters["defer_substitution"]
        assert param.default is False

    def test_harness_narrative_does_NOT_call_sync_helper(self):
        """Source-pin: the body of harness_narrative must NOT
        invoke is_defer_substitution_enabled_sync() anywhere.
        The sync helper is the failure vector -- it raises
        'Future attached to a different loop' inside
        asyncio.to_thread because SQLAlchemy's async session is
        bound to the main loop."""
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        assert (
            "is_defer_substitution_enabled_sync" not in src), (
            "harness_narrative must not call the sync helper "
            "-- use the defer_substitution kwarg instead")

    def test_harness_narrative_uses_kwarg_at_swap_site(self):
        """Source-pin: the swap-gate's flag check reads from
        the kwarg-derived _flag_state, not from any DB query."""
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        assert "_flag_state = defer_substitution" in src


class TestGenerateNarrativesResolvesFlag:

    def test_signature_carries_defer_substitution_kwarg(self):
        from main import _generate_narratives
        sig = inspect.signature(_generate_narratives)
        assert "defer_substitution" in sig.parameters
        # Default None means "resolve from DB"; callers may
        # override with explicit bool to short-circuit.
        param = sig.parameters["defer_substitution"]
        assert param.default is None

    def test_resolves_flag_via_async_helper(self):
        """Source-pin: when defer_substitution is None,
        _generate_narratives awaits is_defer_substitution_enabled()
        BEFORE building any asyncio.to_thread jobs. This is the
        load-bearing semantic -- the flag is read in the async
        caller's loop context, not from the worker thread."""
        from main import _generate_narratives
        src = inspect.getsource(_generate_narratives)
        # Must contain the async-helper import + await.
        assert (
            "from tools.platform_flags import (" in src)
        assert "is_defer_substitution_enabled," in src
        assert (
            "await is_defer_substitution_enabled()" in src)
        # And the resolution must happen BEFORE the to_thread
        # jobs are built (loop check -> resolve -> dispatch).
        resolve_idx = src.find(
            "await is_defer_substitution_enabled()")
        dispatch_idx = src.find("asyncio.to_thread(")
        assert resolve_idx > -1
        assert dispatch_idx > -1
        assert resolve_idx < dispatch_idx, (
            "Flag must be resolved BEFORE dispatching jobs "
            "into asyncio.to_thread")

    def test_kwarg_threaded_into_harness_kwargs(self):
        """Source-pin: the resolved bool lands in the kwargs
        dict that flows into harness_narrative."""
        from main import _generate_narratives
        src = inspect.getsource(_generate_narratives)
        assert (
            'kwargs["defer_substitution"] = bool('
            "defer_substitution)" in src)


class TestNoSyncFlagCallInBriefPath:
    """Defensive coverage: scan academic_export.py for any
    remaining is_defer_substitution_enabled_sync() call inside
    a hot-path function. The sync helper still exists in
    platform_flags.py for any external caller that genuinely
    needs sync access, but the document-generation hot path
    must not use it."""

    def test_no_sync_helper_in_academic_export(self):
        import tools.academic_export as ae
        src = inspect.getsource(ae)
        # The IMPORT may still exist for legacy compat but no
        # actual call site must remain.
        assert (
            "is_defer_substitution_enabled_sync(" not in src), (
            "academic_export.py must not call the sync helper "
            "anywhere -- threading from the async caller is "
            "the contract")
