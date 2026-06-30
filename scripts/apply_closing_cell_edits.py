"""scripts/apply_closing_cell_edits.py -- June 29 2026.

Four edits to the closing markdown cell (id=5449d008):

1. "December 2025 submission freeze" -> "June 2026 submission freeze"
2. "6-slide PPTX" -> "11-slide PPTX"
3. Replace the two-line dual-hash framing with the operator's
   sharper description that calls out the different inputs.
4. Append an hmmlearn / Python 3.14 caveat to the re-execution
   instructions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "5449d008"


NEW_SOURCE = '''## Closing

Every metric in this notebook is reproducible end-to-end from the static data freeze at `notebook_data/`. The manifest in Cell 3 asserts the strategy hash; Cell 6 reproduces every cached metric within 1e-3 tolerance and raises if any value diverges; subsequent cells visualise and stress-test those metrics.

**To re-execute the notebook:**

```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
pip install pandas numpy scipy matplotlib jupyter
jupyter nbconvert --execute --to notebook --inplace \\
  analytical_appendix.ipynb

# Note: hmmlearn is not available for Python 3.14.
# The notebook loads pre-exported regime_classification.csv
# rather than refitting the HMM. To regenerate canonical
# regime classifications, run on Render (Python 3.12,
# hmmlearn installed):
#   python scripts/export_notebook_chart_data.py
```

A green run with no exceptions raised is the proof. The freeze is committed at `notebook_data/`; restore from the `notebook-data-export` branch if the files have been edited.

**Companion documents in the submission:**
- Executive Brief (5-page DOCX, Bob Thao authored, Michael Ruurds chart support)
- Final Presentation Deck (11-slide PPTX, Molly Murdock authored)
- This Analytical Appendix notebook (Michael Ruurds authored, Bob Thao narrative review)

**Notebook strategy-cache key:** `f2e87dec7dcabe71` for the 287-month study. The brief / DOCX appendix / deck additionally reference the platform fingerprint `c421fb895347f924` per the June 2026 submission freeze. These hashes cover different inputs by design. `f2e87dec7dcabe71` is the strategy cache key (SHA256 of backtest run parameters). `c421fb895347f924` is the market data fingerprint (SHA256 of `market_data_monthly` + `ff_factors_monthly` row counts and timestamps). See `README.md` for the dual-hash architecture. Cache integrity is guarded at the platform level by the pre-flight hash-matched gate introduced in PR #366 and the platform-fingerprint cleanup in PR #367.'''


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        c["source"] = [
            s + "\n" for s in NEW_SOURCE.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        print(f"REPLACED Closing markdown (id={CELL_ID})")
        break
    else:
        print(f"FATAL: cell {CELL_ID} not found")
        return 1

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
