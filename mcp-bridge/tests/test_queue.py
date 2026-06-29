"""Queue state-machine tests — every transition + the atomicity
contract that two consumers can't claim the same row.
"""
from __future__ import annotations

import threading

import pytest

from mcp_bridge.queue import Queue


def test_enqueue_returns_increasing_ids(tmp_queue_path):
    q = Queue(tmp_queue_path)
    a = q.enqueue("first prompt")
    b = q.enqueue("second prompt")
    assert b > a
    row = q.get(a)
    assert row is not None
    assert row["prompt"] == "first prompt"
    assert row["status"] == "pending"
    assert row["created_at"] is not None


def test_claim_next_returns_oldest_pending(tmp_queue_path):
    q = Queue(tmp_queue_path)
    a = q.enqueue("first")
    b = q.enqueue("second")
    claimed = q.claim_next("worker")
    assert claimed is not None
    assert claimed["id"] == a, "FIFO order — oldest is claimed first"
    assert claimed["status"] == "running"
    assert claimed["claimed_by"] == "worker"
    # Second claim returns the second row.
    claimed_b = q.claim_next("worker")
    assert claimed_b is not None
    assert claimed_b["id"] == b


def test_claim_next_returns_none_when_empty(tmp_queue_path):
    q = Queue(tmp_queue_path)
    assert q.claim_next("worker") is None


def test_post_result_success_transitions_to_complete(tmp_queue_path):
    q = Queue(tmp_queue_path)
    pid = q.enqueue("p")
    q.claim_next("worker")
    ok = q.post_result(pid, result="hello world")
    assert ok is True
    row = q.get(pid)
    assert row is not None
    assert row["status"] == "complete"
    assert row["result"] == "hello world"
    assert row["error"] is None
    assert row["completed_at"] is not None


def test_post_result_error_transitions_to_failed(tmp_queue_path):
    q = Queue(tmp_queue_path)
    pid = q.enqueue("p")
    q.claim_next("worker")
    ok = q.post_result(pid, error="something broke")
    assert ok is True
    row = q.get(pid)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "something broke"
    assert row["result"] is None


def test_post_result_requires_exactly_one_of_result_or_error(
    tmp_queue_path,
):
    q = Queue(tmp_queue_path)
    pid = q.enqueue("p")
    with pytest.raises(ValueError):
        q.post_result(pid)  # neither
    with pytest.raises(ValueError):
        q.post_result(pid, result="r", error="e")  # both


def test_post_result_returns_false_for_unknown_id(tmp_queue_path):
    q = Queue(tmp_queue_path)
    assert q.post_result(9999, result="x") is False


def test_post_result_refuses_to_overwrite_completed_row(tmp_queue_path):
    # Once a row is complete, a second post_result must NOT
    # silently flip it. Returns False so the caller knows.
    q = Queue(tmp_queue_path)
    pid = q.enqueue("p")
    q.claim_next("worker")
    assert q.post_result(pid, result="first") is True
    assert q.post_result(pid, result="second") is False
    row = q.get(pid)
    assert row["result"] == "first"
    assert row["status"] == "complete"


def test_status_snapshot_counts_by_state(tmp_queue_path):
    q = Queue(tmp_queue_path)
    # One pending, one running, one complete, one failed.
    p1 = q.enqueue("pending only")
    p2 = q.enqueue("about to run")
    q.claim_next("worker")  # claims p1 (oldest)
    _ = p1  # pylint
    p3 = q.enqueue("about to complete")
    q.claim_next("worker")  # claims p2
    q.post_result(p2, result="done")
    p4 = q.enqueue("about to fail")
    q.claim_next("worker")  # claims p3
    q.post_result(p3, error="boom")
    _ = p4  # pending
    snap = q.status_snapshot()
    # p1 = running, p2 = complete, p3 = failed, p4 = pending.
    assert snap["counts"]["pending"] == 1
    assert snap["counts"]["running"] == 1
    assert snap["counts"]["complete"] == 1
    assert snap["counts"]["failed"] == 1
    assert snap["last_completed_at"] is not None


def test_list_pending_returns_only_pending(tmp_queue_path):
    q = Queue(tmp_queue_path)
    p1 = q.enqueue("a")
    p2 = q.enqueue("b")
    q.claim_next("worker")  # p1 → running
    pending = q.list_pending()
    pending_ids = {row["id"] for row in pending}
    assert p2 in pending_ids
    assert p1 not in pending_ids


def test_two_workers_cannot_claim_same_row(tmp_queue_path):
    """The atomicity contract. Both threads race to claim_next;
    each row may be claimed AT MOST ONCE across the pair."""
    q = Queue(tmp_queue_path)
    # Pre-populate with N rows so the race has plenty of work.
    n_rows = 50
    for i in range(n_rows):
        q.enqueue(f"prompt {i}")

    claimed: list[dict] = []
    lock = threading.Lock()

    def claim_loop(worker_name: str) -> None:
        while True:
            row = q.claim_next(worker_name)
            if row is None:
                return
            with lock:
                claimed.append(row)

    t1 = threading.Thread(target=claim_loop, args=("worker-a",))
    t2 = threading.Thread(target=claim_loop, args=("worker-b",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Every row claimed exactly once.
    claimed_ids = [r["id"] for r in claimed]
    assert len(claimed_ids) == n_rows
    assert len(set(claimed_ids)) == n_rows, (
        "A row was claimed by both workers — atomicity broke.")


def test_get_returns_none_for_unknown_id(tmp_queue_path):
    q = Queue(tmp_queue_path)
    assert q.get(9999) is None


# ── purge_pending_and_running (June 3 2026) ───────────────────────────────


def test_purge_cancels_pending_rows(tmp_queue_path):
    """Pending rows flip to 'cancelled' and the returned count
    matches. Their completed_at gets stamped so the audit trail
    knows when the purge happened."""
    q = Queue(tmp_queue_path)
    a = q.enqueue("first")
    b = q.enqueue("second")
    n = q.purge_pending_and_running()
    assert n == 2
    for pid in (a, b):
        row = q.get(pid)
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["completed_at"] is not None


def test_purge_cancels_running_rows(tmp_queue_path):
    """A worker that died mid-prompt left its row in 'running'.
    The purge must clear it too — that's the whole point."""
    q = Queue(tmp_queue_path)
    pid = q.enqueue("stuck")
    q.claim_next("worker-that-died")
    # row is now running
    n = q.purge_pending_and_running()
    assert n == 1
    row = q.get(pid)
    assert row is not None
    assert row["status"] == "cancelled"


def test_purge_preserves_complete_and_failed(tmp_queue_path):
    """Terminal rows are part of the audit trail and must not be
    touched. Cancellation is for in-flight work only."""
    q = Queue(tmp_queue_path)
    done = q.enqueue("done")
    q.claim_next("w")
    q.post_result(done, result="ok")
    bad = q.enqueue("bad")
    q.claim_next("w")
    q.post_result(bad, error="boom")
    pending = q.enqueue("pending")
    n = q.purge_pending_and_running()
    assert n == 1   # only the pending row
    assert q.get(done)["status"] == "complete"
    assert q.get(bad)["status"] == "failed"
    assert q.get(pending)["status"] == "cancelled"


def test_purge_empty_queue_returns_zero(tmp_queue_path):
    """No pending or running rows → purge is a no-op that returns 0,
    never raises. The endpoint can be called repeatedly without harm."""
    q = Queue(tmp_queue_path)
    assert q.purge_pending_and_running() == 0


def test_purge_is_idempotent(tmp_queue_path):
    """A second call right after the first finds nothing to cancel —
    same contract as the empty case. The endpoint can be re-tried
    safely from a flaky network."""
    q = Queue(tmp_queue_path)
    q.enqueue("a")
    q.enqueue("b")
    assert q.purge_pending_and_running() == 2
    assert q.purge_pending_and_running() == 0


def test_status_snapshot_surfaces_cancelled_count(tmp_queue_path):
    """status_snapshot() picks up rows by GROUP BY status, so the
    'cancelled' total appears under its own key after a purge —
    same way it shows complete/failed today."""
    q = Queue(tmp_queue_path)
    q.enqueue("a")
    q.purge_pending_and_running()
    snap = q.status_snapshot()
    assert snap["counts"].get("cancelled") == 1
