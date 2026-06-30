"""Two fixes:

  1. Cell 12 cost sensitivity -- the _sharpe helper computed
     raw mean/std Sharpe. Table G1 reports rf-adjusted (excess
     return) Sharpe, so the local values overshot the target
     (1.16 / 1.14 / 1.11 vs target 0.85 / 0.83 / 0.80). Wrap
     the Sharpe to subtract rf consistent with the appendix
     methodology.

  2. Delete the final code cell (`2315a80d`) that calls
     jupyter nbconvert --to html via subprocess. Platform
     tooling; not needed for the Colab artifact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL12_ID = "2b8a37d6"
HTML_EXPORT_ID = "2315a80d"


OLD_SHARPE_HELPER = """def _sharpe(arr: np.ndarray) -> float:
    if arr.std(ddof=1) == 0:
        return 0.0
    return float(arr.mean() / arr.std(ddof=1) * np.sqrt(12))

bench_sharpe = _sharpe(bench_oos_arr)"""

NEW_SHARPE_HELPER = """# rf-aligned over the same OOS window so Sharpe is computed
# on excess return -- matches Table G1 methodology.
oos_dates_idx = pd.DatetimeIndex([
    pd.Timestamp(d) for d in bench_dates_iso
    if pd.Timestamp(d) >= oos_start])
rf_oos = ff_rf.reindex(oos_dates_idx).ffill().fillna(0.0).values

def _sharpe(arr: np.ndarray, rf: np.ndarray) -> float:
    excess = arr - rf
    if excess.std(ddof=1) == 0:
        return 0.0
    return float(
        excess.mean() / excess.std(ddof=1) * np.sqrt(12))

bench_sharpe = _sharpe(bench_oos_arr, rf_oos)"""

OLD_NET_CALL = """    net_returns = blend_returns_oos - monthly_cost_drag
    net_sharpe = _sharpe(net_returns)"""

NEW_NET_CALL = """    net_returns = blend_returns_oos - monthly_cost_drag
    net_sharpe = _sharpe(net_returns, rf_oos)"""


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    # Patch cell 12.
    for c in nb["cells"]:
        if c.get("id") != CELL12_ID:
            continue
        src = "".join(c["source"])
        if OLD_SHARPE_HELPER not in src:
            print("FATAL: cost-sharpe helper not found")
            return 1
        if OLD_NET_CALL not in src:
            print("FATAL: net-sharpe call not found")
            return 1
        src = src.replace(OLD_SHARPE_HELPER, NEW_SHARPE_HELPER)
        src = src.replace(OLD_NET_CALL, NEW_NET_CALL)
        c["source"] = [s + "\n" for s in src.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        c["outputs"] = []
        c["execution_count"] = None
        print("PATCHED cost sensitivity Sharpe (rf adjusted)")
        break
    else:
        print(f"FATAL: cell {CELL12_ID} not found")
        return 1

    # Delete HTML export cell.
    nb["cells"] = [c for c in nb["cells"]
                   if c.get("id") != HTML_EXPORT_ID]
    print(f"DELETED HTML export cell {HTML_EXPORT_ID}")

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
