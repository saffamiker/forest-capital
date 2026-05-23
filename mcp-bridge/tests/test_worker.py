"""Worker daemon tests — subprocess mocked so we never invoke a
real `claude` binary in CI.

Covers:
  - worker refuses to start when worker_enabled is False
  - one tick claims, runs (mocked), and posts the result
  - JSON-format claude output is unwrapped to the response field
  - non-JSON stdout passes through unchanged
  - subprocess timeout → row marked failed with timeout message
  - non-zero exit → row marked failed with stderr captured
  - claude binary missing → row marked failed with FileNotFoundError
  - unexpected exception → row marked failed with exception text
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from mcp_bridge.queue import Queue
from mcp_bridge.worker import Worker


# ── Gate behaviour ─────────────────────────────────────────────────────────


def test_worker_refuses_to_run_when_disabled(cfg, capsys):
    cfg.worker_enabled = False
    rc = Worker(cfg).run()
    assert rc == 2  # specific exit code so scripts can branch
    out = capsys.readouterr()
    # The hint mentions both ways to enable.
    assert "worker_disabled_in_config" in (out.out + out.err) \
        or "Set worker_enabled" in (out.out + out.err)


# ── Happy path: claim → run → post ─────────────────────────────────────────


def _make_completed(stdout: str, returncode: int = 0,
                     stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout,
        stderr=stderr)


def test_tick_runs_one_prompt_and_posts_result(cfg):
    cfg.worker_enabled = True
    q = Queue(cfg.db_path)
    pid = q.enqueue("what's 2+2?")
    worker = Worker(cfg)
    # subprocess.run returns a JSON envelope with a 'response' field.
    fake = _make_completed(
        stdout='{"response": "4 is the answer."}')
    with patch.object(subprocess, "run",
                       return_value=fake) as mock_run:
        handled = worker._tick()
    assert handled is True
    row = q.get(pid)
    assert row["status"] == "complete"
    assert row["result"] == "4 is the answer."
    # Worker invoked claude with --output-format json.
    invocation_argv = mock_run.call_args.args[0]
    assert cfg.claude_binary in invocation_argv
    assert "-p" in invocation_argv
    assert "--output-format" in invocation_argv
    assert "json" in invocation_argv


def test_tick_returns_false_when_queue_empty(cfg):
    cfg.worker_enabled = True
    worker = Worker(cfg)
    # No prompts queued — claim_next returns None and the tick
    # short-circuits without calling subprocess.
    with patch.object(subprocess, "run") as mock_run:
        handled = worker._tick()
    assert handled is False
    mock_run.assert_not_called()


# ── JSON shape variants ────────────────────────────────────────────────────


def test_unwraps_response_field(cfg):
    cfg.worker_enabled = True
    q = Queue(cfg.db_path)
    pid = q.enqueue("p")
    worker = Worker(cfg)
    with patch.object(subprocess, "run",
                       return_value=_make_completed(
                           '{"response": "first match"}')):
        worker._tick()
    assert q.get(pid)["result"] == "first match"


def test_unwraps_result_field_when_response_absent(cfg):
    cfg.worker_enabled = True
    q = Queue(cfg.db_path)
    pid = q.enqueue("p")
    worker = Worker(cfg)
    with patch.object(subprocess, "run",
                       return_value=_make_completed(
                           '{"result": "from result field"}')):
        worker._tick()
    assert q.get(pid)["result"] == "from result field"


def test_falls_back_to_raw_stdout_when_no_known_field(cfg):
    # Older claude versions / unfamiliar JSON shapes — we still
    # return SOMETHING the mobile can render. Whole JSON falls
    # through as a string.
    cfg.worker_enabled = True
    q = Queue(cfg.db_path)
    pid = q.enqueue("p")
    worker = Worker(cfg)
    with patch.object(subprocess, "run",
                       return_value=_make_completed(
                           '{"unknown_field": "x"}')):
        worker._tick()
    result = q.get(pid)["result"]
    assert "unknown_field" in result


def test_handles_non_json_stdout(cfg):
    cfg.worker_enabled = True
    q = Queue(cfg.db_path)
    pid = q.enqueue("p")
    worker = Worker(cfg)
    with patch.object(subprocess, "run",
                       return_value=_make_completed(
                           "plain text response\n")):
        worker._tick()
    assert q.get(pid)["result"] == "plain text response"


# ── Failure paths ──────────────────────────────────────────────────────────


def test_timeout_marks_failed_with_message(cfg):
    cfg.worker_enabled = True
    cfg.worker_prompt_timeout_s = 30
    q = Queue(cfg.db_path)
    pid = q.enqueue("slow prompt")
    worker = Worker(cfg)
    with patch.object(subprocess, "run",
                       side_effect=subprocess.TimeoutExpired(
                           cmd="claude", timeout=30)):
        worker._tick()
    row = q.get(pid)
    assert row["status"] == "failed"
    assert "timed out" in row["error"].lower()
    assert "30" in row["error"]


def test_nonzero_exit_marks_failed_with_stderr(cfg):
    cfg.worker_enabled = True
    q = Queue(cfg.db_path)
    pid = q.enqueue("bad prompt")
    worker = Worker(cfg)
    err = subprocess.CalledProcessError(
        returncode=1, cmd="claude", stderr="permission denied")
    with patch.object(subprocess, "run", side_effect=err):
        worker._tick()
    row = q.get(pid)
    assert row["status"] == "failed"
    assert "permission denied" in row["error"]


def test_missing_binary_marks_failed(cfg):
    cfg.worker_enabled = True
    cfg.claude_binary = "/nonexistent/claude"
    q = Queue(cfg.db_path)
    pid = q.enqueue("p")
    worker = Worker(cfg)
    with patch.object(subprocess, "run",
                       side_effect=FileNotFoundError(
                           "[Errno 2] No such file")):
        worker._tick()
    row = q.get(pid)
    assert row["status"] == "failed"
    assert "binary not found" in row["error"]


def test_unexpected_exception_marks_failed(cfg):
    # Belt-and-braces — the row must never get stuck in 'running'
    # even when subprocess raises something exotic.
    cfg.worker_enabled = True
    q = Queue(cfg.db_path)
    pid = q.enqueue("p")
    worker = Worker(cfg)
    with patch.object(subprocess, "run",
                       side_effect=RuntimeError("unexpected")):
        worker._tick()
    row = q.get(pid)
    assert row["status"] == "failed"
    assert "unexpected" in row["error"]


def test_session_id_threads_through_to_claude_argv(cfg):
    # When worker_session_id is set, the worker passes
    # --resume <session_id> so context continues across prompts.
    cfg.worker_enabled = True
    cfg.worker_session_id = "michael-session-xyz"
    q = Queue(cfg.db_path)
    q.enqueue("p")
    worker = Worker(cfg)
    fake = _make_completed('{"response": "ok"}')
    with patch.object(subprocess, "run",
                       return_value=fake) as mock_run:
        worker._tick()
    argv = mock_run.call_args.args[0]
    assert "--resume" in argv
    assert "michael-session-xyz" in argv


def test_extra_args_appended_to_claude_argv(cfg):
    cfg.worker_enabled = True
    cfg.claude_extra_args = ["--model", "claude-opus-4-7"]
    q = Queue(cfg.db_path)
    q.enqueue("p")
    worker = Worker(cfg)
    fake = _make_completed('{"response": "ok"}')
    with patch.object(subprocess, "run",
                       return_value=fake) as mock_run:
        worker._tick()
    argv = mock_run.call_args.args[0]
    assert "--model" in argv
    assert "claude-opus-4-7" in argv
