"""Config resolution tests — env > file > defaults precedence.

The config module is small but its precedence rules are easy to
get subtly wrong, and the bridge's behaviour pivots on
worker_enabled. These tests pin that contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_bridge import config as config_mod


def _isolate_config_path(monkeypatch, tmp_path):
    """Redirect the config-file location so each test starts
    fresh without touching the developer's actual ~/.config."""
    fake = tmp_path / "mcp-bridge" / "config.json"
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", fake)
    return fake


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Every test gets a clean env so file/defaults are visible."""
    for k in (
        "MCP_BRIDGE_HOST", "MCP_BRIDGE_PORT", "MCP_BRIDGE_AUTH_TOKEN",
        "MCP_BRIDGE_DB_PATH", "MCP_BRIDGE_WORKER_ENABLED",
        "MCP_BRIDGE_POLL_INTERVAL_S", "MCP_BRIDGE_PROMPT_TIMEOUT_S",
        "MCP_BRIDGE_CLAUDE_BINARY", "MCP_BRIDGE_SESSION_ID",
        "MCP_BRIDGE_MAX_PROMPT_BYTES",
    ):
        monkeypatch.delenv(k, raising=False)


def test_defaults_are_safe(monkeypatch, tmp_path):
    _isolate_config_path(monkeypatch, tmp_path)
    cfg = config_mod.load_config()
    # Worker daemon MUST default off — the spec is explicit.
    assert cfg.worker_enabled is False
    # Bind to localhost by default — never expose the bridge.
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    # Empty auth token by default; the CLI's serve command
    # refuses to bind non-localhost without a token.
    assert cfg.auth_token == ""


def test_env_overrides_defaults(monkeypatch, tmp_path):
    _isolate_config_path(monkeypatch, tmp_path)
    monkeypatch.setenv("MCP_BRIDGE_PORT", "9999")
    monkeypatch.setenv("MCP_BRIDGE_AUTH_TOKEN", "env-token")
    monkeypatch.setenv("MCP_BRIDGE_WORKER_ENABLED", "true")
    cfg = config_mod.load_config()
    assert cfg.port == 9999
    assert cfg.auth_token == "env-token"
    assert cfg.worker_enabled is True


def test_file_overrides_defaults(monkeypatch, tmp_path):
    fake = _isolate_config_path(monkeypatch, tmp_path)
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(json.dumps({
        "auth_token": "file-token",
        "worker_enabled": True,
        "port": 7777,
    }))
    cfg = config_mod.load_config()
    assert cfg.auth_token == "file-token"
    assert cfg.worker_enabled is True
    assert cfg.port == 7777


def test_env_beats_file(monkeypatch, tmp_path):
    fake = _isolate_config_path(monkeypatch, tmp_path)
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(json.dumps({
        "auth_token": "file-token",
        "worker_enabled": True,
    }))
    monkeypatch.setenv("MCP_BRIDGE_AUTH_TOKEN", "env-token")
    monkeypatch.setenv("MCP_BRIDGE_WORKER_ENABLED", "0")
    cfg = config_mod.load_config()
    assert cfg.auth_token == "env-token"
    assert cfg.worker_enabled is False


def test_write_default_config_creates_file(monkeypatch, tmp_path):
    fake = _isolate_config_path(monkeypatch, tmp_path)
    p = config_mod.write_default_config(token="abc123")
    assert p == fake
    assert fake.exists()
    data = json.loads(fake.read_text())
    assert data["auth_token"] == "abc123"


def test_env_bool_accepts_common_truthy_values(monkeypatch, tmp_path):
    _isolate_config_path(monkeypatch, tmp_path)
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("MCP_BRIDGE_WORKER_ENABLED", truthy)
        cfg = config_mod.load_config()
        assert cfg.worker_enabled is True, truthy


def test_env_bool_rejects_garbage(monkeypatch, tmp_path):
    _isolate_config_path(monkeypatch, tmp_path)
    for falsy in ("0", "false", "no", "off", "", "garbage"):
        monkeypatch.setenv("MCP_BRIDGE_WORKER_ENABLED", falsy)
        cfg = config_mod.load_config()
        assert cfg.worker_enabled is False, falsy


def test_corrupt_config_file_is_ignored(monkeypatch, tmp_path):
    # A malformed config file must not crash load_config — the
    # bridge falls back to defaults so the operator can recover.
    fake = _isolate_config_path(monkeypatch, tmp_path)
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("not valid json {")
    cfg = config_mod.load_config()
    # All defaults — no fields overwritten by the bad file.
    assert cfg.host == "127.0.0.1"
    assert cfg.auth_token == ""
