"""
backend/scripts/stage_findings_from_api.py — one-off run.

Hits the production API (using MASTER_API_KEY) to pull every input the
compute_findings_from_payload function needs, then calls the shared
compute and writes the rendered markdown to
backend/reports/staging/analytical_findings.md.

The permanent path is POST /api/v1/reports/stage-findings (which runs
server-side against the live DB caches). This script is the one-off
equivalent that runs locally and writes a file the team can review
BEFORE the report-writer UI ships — see the May 22 2026 staging-report
brief.

Usage (from the repo root):
    python -m backend.scripts.stage_findings_from_api

Reads MASTER_API_KEY from backend/.env. API_URL defaults to the live
Render origin; override with the API_URL env var.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

try:
    from tools.analytical_findings import compute_findings_from_payload
except ImportError:
    # When invoked as `python backend/scripts/stage_findings_from_api.py`
    # without `-m`, the relative path inside the repo still resolves.
    sys.path.insert(0, str(REPO_ROOT))
    from backend.tools.analytical_findings import (  # type: ignore[no-redef]
        compute_findings_from_payload,
    )


API_URL = os.getenv("API_URL", "https://forest-capital.onrender.com")


def _read_master_key() -> str:
    """Read MASTER_API_KEY from backend/.env — the same place every
    production credential lives. The script will not run without it."""
    env_path = REPO_ROOT / "backend" / ".env"
    if not env_path.exists():
        raise SystemExit(
            f"Missing {env_path}. The script needs MASTER_API_KEY to "
            "hit the production API.")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MASTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(
        "MASTER_API_KEY not found in backend/.env.")


def _get(session, path: str) -> dict:
    """GET helper — returns parsed JSON or an empty dict on failure."""
    url = f"{API_URL}{path}"
    try:
        r = session.get(url, timeout=60)
        if r.status_code != 200:
            print(f"  [WARN] {path} -> HTTP {r.status_code}",
                  file=sys.stderr)
            return {}
        return r.json()
    except Exception as exc:
        print(f"  [WARN] {path} -> {exc}", file=sys.stderr)
        return {}


def fetch_payload() -> dict:
    """Builds the payload compute_findings_from_payload expects by
    calling the live analytics endpoints. Each fetch fails open to an
    empty dict; the per-finding fail-open contract handles the rest."""
    import requests
    session = requests.Session()
    session.headers["X-API-Key"] = _read_master_key()

    print(f"Fetching from {API_URL}...")

    strategies_raw = _get(session, "/api/backtest/compare")
    strategies_list = strategies_raw.get("strategies") or []
    # Normalise to {strategy_id: result_dict} — the shape the tool
    # expects (it indexes by the strategy_name key).
    strategies = {
        s.get("strategy_name") or s.get("name"): s
        for s in strategies_list if isinstance(s, dict)
        and (s.get("strategy_name") or s.get("name"))
    }
    print(f"  strategies: {len(strategies)}")

    correlation = _get(session, "/api/v1/analytics/correlation")
    print(f"  correlation labels: {len(correlation.get('labels') or [])}")

    tail_risk = _get(session, "/api/v1/analytics/tail-risk")
    print(f"  tail risk rows: "
          f"{len((tail_risk.get('strategies') or []))}")

    crisis = _get(session, "/api/v1/analytics/crisis-performance")
    print(f"  crisis windows: {len(crisis.get('windows') or {})}")

    risk_contribution = _get(session, "/api/v1/analytics/risk-contribution")
    has_tangency = bool(risk_contribution.get("tangency_weights"))
    print(f"  tangency weights present: {has_tangency}")

    capture = _get(session, "/api/v1/analytics/capture-ratios")
    print(f"  capture rows: {len(capture.get('strategies') or [])}")

    distribution = _get(session, "/api/v1/analytics/distribution")
    print(f"  distribution rows: "
          f"{len(distribution.get('strategies') or [])}")

    academic = _get(session, "/api/v1/analytics/academic")
    print(f"  academic regime rows: "
          f"{len(academic.get('regime_conditional') or [])}")

    research = _get(session, "/api/v1/research/latest")
    macro_digest = research.get("digest") or {}
    print(f"  macro digest signals: "
          f"{len(macro_digest.get('key_signals') or [])}")

    return {
        "strategies":        strategies,
        "correlation":       correlation,
        "tail_risk":         tail_risk,
        "crisis":            crisis,
        "risk_contribution": risk_contribution,
        "capture":           capture,
        "distribution":      distribution,
        "academic":          academic,
        "macro_digest":      macro_digest,
    }


def main() -> int:
    payload = fetch_payload()
    findings, markdown = compute_findings_from_payload(payload)
    out_path = (REPO_ROOT / "backend" / "reports"
                / "staging" / "analytical_findings.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    print()
    print(f"Wrote {out_path}")
    print(f"  findings:       {len(findings)}")
    print(f"  HIGH strength:  "
          f"{sum(1 for f in findings if f.get('nugget_strength') == 'HIGH')}")
    print(f"  surprises:      "
          f"{sum(1 for f in findings if f.get('surprise'))}")
    # Echo a JSON summary alongside the markdown for quick scripting.
    summary_path = out_path.with_suffix(".json")
    summary_path.write_text(
        json.dumps({"findings": findings}, indent=2, default=str),
        encoding="utf-8")
    print(f"  + JSON summary: {summary_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
