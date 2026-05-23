"""mcp_bridge.worker — autonomous queue consumer.

Off by default per the May 23 2026 spec. Enable via
`worker_enabled: true` in the config OR
`MCP_BRIDGE_WORKER_ENABLED=1` in the environment.

Loop:
  1. Sleep poll_interval_s
  2. Claim the next pending prompt (claim_next('worker'))
  3. Subprocess: claude -p "<prompt>" --output-format json [--resume <session>]
  4. Capture stdout (with timeout); parse JSON if present;
     fall back to raw text otherwise.
  5. post_result(prompt_id, result=...) or post_result(...,
     error=...) on failure.
  6. Repeat.

Failure modes the worker handles cleanly:
  - claude binary missing on PATH        → row marked failed
  - subprocess exits non-zero            → row marked failed with
                                              stderr captured
  - subprocess hangs > timeout            → row marked failed with
                                              "timed out after Ns"
  - SQLite locked momentarily             → log + retry next tick
  - any other exception                  → log + continue (no
                                              uncaught exit)

Stop signals: SIGINT / SIGTERM. The worker finishes the current
prompt (if mid-flight), posts the result, and exits cleanly.
"""
from __future__ import annotations

import json
import signal
import subprocess
import time
from typing import Any

import structlog

from .config import BridgeConfig, load_config
from .queue import Queue


log = structlog.get_logger(__name__) if hasattr(structlog,
    "get_logger") else None  # type: ignore[truthy-bool]


# Worker identifier written into claimed_by. Distinct from the
# "live" value the slash command uses so the queue rows show
# which consumer ran each prompt.
_WORKER_ID = "worker"


def _log(event: str, **kw: Any) -> None:
    """Tiny logging shim — uses structlog when available, falls
    back to print so the worker still emits diagnostics when
    structlog isn't installed (the bridge ships its own minimal
    requirements.txt that DOES include structlog, but a user
    could install just FastAPI and run the server manually)."""
    if log is not None:
        log.info(event, **kw)  # type: ignore[union-attr]
    else:
        # Keep the line short — the worker is a long-running
        # daemon and verbose lines clutter the terminal.
        kvs = " ".join(f"{k}={v!r}" for k, v in kw.items())
        print(f"[mcp-bridge.worker] {event} {kvs}", flush=True)


class Worker:
    """One worker instance owns one Queue handle and one stop
    flag. Multiple workers can run against the same queue file —
    claim_next is atomic — but the bridge's normal mode is a
    single worker on Michael's desktop."""

    def __init__(self, cfg: BridgeConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self.queue = Queue(self.cfg.db_path)
        self._stop = False

    def request_stop(self, *_args: Any) -> None:
        """Signal handler — sets the stop flag. The main loop
        checks it after each prompt so the current job finishes
        cleanly before exit."""
        self._stop = True
        _log("worker_stop_requested")

    def run(self) -> int:
        """Main loop. Returns the exit code the CLI should
        propagate (0 on a clean stop, 2 on a config error
        detected at start)."""
        if not self.cfg.worker_enabled:
            _log("worker_disabled_in_config",
                 hint=("Set worker_enabled: true in the config "
                       "or MCP_BRIDGE_WORKER_ENABLED=1 in env."))
            return 2
        # Wire signal handlers so a Ctrl-C / kill cleanly stops
        # the loop after the current prompt.
        signal.signal(signal.SIGINT, self.request_stop)
        try:
            signal.signal(signal.SIGTERM, self.request_stop)
        except (AttributeError, ValueError):
            # Windows ignores SIGTERM; SIGINT is still wired so
            # Ctrl-C works in the terminal.
            pass

        _log("worker_started",
             db_path=self.cfg.db_path,
             poll_interval_s=self.cfg.worker_poll_interval_s,
             session_id=self.cfg.worker_session_id or "<fresh>",
             timeout_s=self.cfg.worker_prompt_timeout_s)
        while not self._stop:
            try:
                handled = self._tick()
            except Exception as exc:  # noqa: BLE001
                _log("worker_tick_failed", error=str(exc))
                handled = False
            if not handled:
                # No work this tick — sleep for the configured
                # interval. A claimed prompt skips the sleep so
                # the next prompt fires immediately.
                time.sleep(self.cfg.worker_poll_interval_s)
        _log("worker_stopped")
        return 0

    def _tick(self) -> bool:
        """One queue iteration. Returns True when a prompt was
        processed (so the loop should skip its sleep), False when
        the queue was empty."""
        row = self.queue.claim_next(_WORKER_ID)
        if row is None:
            return False
        prompt_id = int(row["id"])
        prompt = row["prompt"]
        _log("worker_claimed_prompt",
             prompt_id=prompt_id,
             prompt_len=len(prompt))
        try:
            result_text = self._run_claude(prompt)
            self.queue.post_result(prompt_id, result=result_text)
            _log("worker_prompt_complete",
                 prompt_id=prompt_id,
                 result_len=len(result_text))
        except subprocess.TimeoutExpired:
            self.queue.post_result(
                prompt_id,
                error=(f"timed out after "
                       f"{self.cfg.worker_prompt_timeout_s}s"))
            _log("worker_prompt_timed_out", prompt_id=prompt_id)
        except FileNotFoundError as exc:
            # claude binary missing — a fatal config issue, but the
            # worker doesn't abort. Mark this prompt failed and
            # keep running; the next claim will fail the same way
            # until the operator fixes the path.
            self.queue.post_result(
                prompt_id,
                error=f"claude binary not found: {exc}")
            _log("worker_prompt_no_binary",
                 prompt_id=prompt_id, error=str(exc))
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or "").strip() or str(exc)
            self.queue.post_result(prompt_id, error=err)
            _log("worker_prompt_nonzero_exit",
                 prompt_id=prompt_id, error=err)
        except Exception as exc:  # noqa: BLE001
            # Belt-and-braces — never leave a row stuck in
            # running. Any unexpected failure becomes a failed
            # row with the exception text.
            self.queue.post_result(
                prompt_id, error=f"worker exception: {exc}")
            _log("worker_prompt_exception",
                 prompt_id=prompt_id, error=str(exc))
        return True

    def _run_claude(self, prompt: str) -> str:
        """Shells out to `claude -p "<prompt>"`. Returns the
        captured stdout — JSON-parsed result string when
        --output-format json gives us a structured blob, raw
        stdout otherwise. Caller catches TimeoutExpired /
        CalledProcessError / FileNotFoundError."""
        argv: list[str] = [self.cfg.claude_binary, "-p", prompt,
                           "--output-format", "json"]
        if self.cfg.worker_session_id:
            argv += ["--resume", self.cfg.worker_session_id]
        argv += list(self.cfg.claude_extra_args or [])

        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=self.cfg.worker_prompt_timeout_s,
            check=True,
        )
        stdout = completed.stdout or ""
        # Try to lift the response text out of the JSON envelope
        # claude prints under --output-format json. The exact
        # shape can vary across versions, so we fall back to
        # the raw stdout when the JSON doesn't have a clear
        # "response" / "result" field.
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout.strip()
        if isinstance(data, dict):
            for k in ("response", "result", "text", "content"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    return v
            # No known field — return the whole JSON so the
            # mobile client at least sees the structured output.
            return json.dumps(data, indent=2)
        return stdout.strip()
