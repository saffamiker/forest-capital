"""Rebuild Cell 3 (manifest) cleanly. Removes the orphan
fragment line + the missing monthly_returns / ff_factors reads
that my prior strip lost, while keeping the new submission-
scope file loads and the strategy-hash assertion.

Cell 0 now owns DATA_DIR -- this cell just uses it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
MANIFEST_ID = "801d9305"


CELL3_SOURCE = '''# Data manifest -- load every file using DATA_DIR
# (set by Cell 0). Prints shapes + asserts the canonical
# strategy hash. If this cell raises, the freeze has been
# edited and the notebook is no longer consistent with the
# brief.

monthly_returns = pd.read_csv(
    DATA_DIR / 'monthly_returns.csv', parse_dates=['date'])
ff_factors = pd.read_csv(DATA_DIR / 'ff_factors.csv')
rebalance_events = pd.read_csv(
    DATA_DIR / 'rebalance_events.csv',
    parse_dates=['event_date'])
with open(DATA_DIR / 'strategy_results.json') as fh:
    strategy_results = json.load(fh)

# June 29 2026 -- submission-scope chart inputs. Three
# files added to support the four submission-scope
# charts (cumulative, drawdown, rolling correlation,
# regime signals). See README.md for the regeneration
# procedure (scripts/export_notebook_chart_data.py).
blend_oos = pd.read_csv(
    DATA_DIR / 'blend_oos_monthly_returns.csv',
    parse_dates=['date'])
regime_classification = pd.read_csv(
    DATA_DIR / 'regime_classification.csv',
    parse_dates=['date'])
with open(DATA_DIR / 'oos_summary.json') as fh:
    oos_summary = json.load(fh)

print('=' * 60)
print('DATA FREEZE MANIFEST')
print('=' * 60)
print(f"monthly_returns.csv             shape={monthly_returns.shape}")
print(f"ff_factors.csv                  shape={ff_factors.shape}")
print(f"rebalance_events.csv            shape={rebalance_events.shape}")
print(f"strategy_results.json           n_strategies={len(strategy_results)}")
print(f"blend_oos_monthly_returns.csv   shape={blend_oos.shape}")
print(f"regime_classification.csv       shape={regime_classification.shape}")
print(f"oos_summary.json                blend_sharpe={oos_summary['oos_sharpe_blend']}")

n_rows = len(monthly_returns)
last_date = monthly_returns['date'].iloc[-1].strftime(
    '%Y-%m-%d')
n_strategies = len(strategy_results)

# Canonical strategy hash (sha256 of "n:last_date:n_strats"
# truncated to 16 hex chars) -- must match
# f2e87dec7dcabe71 exactly -- the notebook strategy-cache
# key for the 287-month dataset. The brief / DOCX appendix /
# deck additionally reference the platform fingerprint
# c421fb895347f924 (December 2025 freeze); both hashes
# identify the same dataset under different schemes.
key = f'{n_rows}:{last_date}:{n_strategies}'
computed_hash = hashlib.sha256(
    key.encode()).hexdigest()[:16]
EXPECTED_HASH = 'f2e87dec7dcabe71'

print()
print(f"n_rows         = {n_rows}")
print(f"last_date      = {last_date}")
print(f"n_strategies   = {n_strategies}")
print(f"hash key       = '{key}'")
print(f"computed hash  = {computed_hash}")
print(f"expected hash  = {EXPECTED_HASH}")

assert computed_hash == EXPECTED_HASH, (
    f'Strategy hash mismatch: computed {computed_hash} but '
    f'expected {EXPECTED_HASH}. The notebook data freeze '
    f'has been edited or the notebook is out of sync. '
    f'Restore notebook_data/ from the committed snapshot '
    f'(branch notebook-data-export) before proceeding.')
assert n_rows == 287, (
    f'monthly_returns.csv has {n_rows} rows, expected 287')'''


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != MANIFEST_ID:
            continue
        c["source"] = [s + "\n" for s in CELL3_SOURCE.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        c["outputs"] = []
        c["execution_count"] = None
        print(f"REBUILT Cell 3 (id={MANIFEST_ID}) cleanly")
        break
    else:
        print(f"FATAL: cell {MANIFEST_ID} not found")
        return 1

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
