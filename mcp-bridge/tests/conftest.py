"""Shared pytest fixtures for mcp-bridge tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Make the mcp_bridge package importable when pytest is run from
# the mcp-bridge directory directly (the common case during local
# development). Mirrors the sys.path tweak the backend tests use.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def tmp_queue_path(tmp_path: Path) -> str:
    """Per-test SQLite path so test runs don't share state. The
    queue module's __post_init__ creates the parent dir and the
    schema on first connect — nothing else to set up."""
    return str(tmp_path / "queue.db")


@pytest.fixture
def cfg(tmp_queue_path: str, monkeypatch: pytest.MonkeyPatch):
    """A BridgeConfig pointing at the per-test DB, with auth
    enabled (token = 'test-token'). Tests that want auth disabled
    can override cfg.auth_token = '' on the returned object."""
    from mcp_bridge.config import BridgeConfig
    # Clear any environment that load_config would pick up so each
    # test starts from a known baseline.
    for k in (
        "MCP_BRIDGE_HOST", "MCP_BRIDGE_PORT", "MCP_BRIDGE_AUTH_TOKEN",
        "MCP_BRIDGE_DB_PATH", "MCP_BRIDGE_WORKER_ENABLED",
        "MCP_BRIDGE_POLL_INTERVAL_S", "MCP_BRIDGE_PROMPT_TIMEOUT_S",
        "MCP_BRIDGE_CLAUDE_BINARY", "MCP_BRIDGE_SESSION_ID",
        "MCP_BRIDGE_MAX_PROMPT_BYTES",
    ):
        monkeypatch.delenv(k, raising=False)
    for k in (
        "MCP_BRIDGE_OAUTH_CLIENT_ID", "MCP_BRIDGE_OAUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)
    return BridgeConfig(
        db_path=tmp_queue_path,
        auth_token="test-token",
        oauth_client_id="test-client-id",
        oauth_client_secret="test-client-secret",
        worker_enabled=False,
        worker_poll_interval_s=0.01,
        worker_prompt_timeout_s=5,
    )
