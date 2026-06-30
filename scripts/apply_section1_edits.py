"""scripts/apply_section1_edits.py -- June 29 2026.

Two markdown edits to Cell 4 (Section 1 -- Data Sources):

  1. Section 1.1 risk-free-rate paragraph -- append a note
     about the DTB3 vs Ken French rf source difference so
     the brief vs notebook Sharpe gap is documented.

  2. Section 1.2 last paragraph -- replace the incorrect
     "Executive Brief does not require Carhart specifically"
     claim with the operator's framing that calls out the
     known scope gap.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "25fbbcc2"


OLD_RF_PARA = (
    "**Risk-free rate.** Used in Sharpe ratio computation. Sourced "
    "from the `rf` column of `ff_factors.csv` (1-month T-bill from "
    "Kenneth French data library, in percent). Converted to monthly "
    "decimal before use."
)
NEW_RF_PARA = (
    "**Risk-free rate.** Used in Sharpe ratio computation. Sourced "
    "from the `rf` column of `ff_factors.csv` (1-month T-bill from "
    "Kenneth French data library, in percent). Converted to monthly "
    "decimal before use.\n\n"
    "Note: the platform computes OOS Sharpe ratios using DTB3 "
    "(3-month Treasury bill, FRED), converted to monthly frequency. "
    "The `ff_factors.csv` rf column uses the 1-month T-bill from "
    "Kenneth French. The difference is immaterial at monthly "
    "frequency but explains minor Sharpe differences between the "
    "notebook's full-period figures and the platform's rf-adjusted "
    "OOS Sharpe reported in the brief."
)

OLD_MOM_LINE = (
    "**Momentum factor (UMD/MOM) is NOT in the freeze.** The "
    "Kenneth French momentum factor would be required for a strict "
    "Carhart (1997) four-factor regression. We run the Fama-French "
    "three-factor model instead -- cell 8 is explicit about this "
    "scope decision. The Executive Brief does not require Carhart "
    "specifically; the three-factor alpha is the headline "
    "regression result."
)
NEW_MOM_LINE = (
    "**Momentum factor (UMD/MOM) is NOT in the freeze.** The "
    "Kenneth French momentum factor would be required for a strict "
    "Carhart (1997) four-factor regression. The notebook's factor-"
    "regression cell tries to download the MOM series from Ken "
    "French's data library at runtime so the notebook can run the "
    "canonical Carhart model; if the download fails (air-gapped "
    "environment, rate limit), the cell falls back to the Fama-"
    "French three-factor model and labels the output accordingly.\n\n"
    "Note: the Executive Brief and Analytical Appendix both cite "
    "Carhart (1997) and Table E1 reports MOM coefficients. The "
    "notebook runs the Fama-French three-factor model as a "
    "limitation of the data freeze when MOM is not downloadable -- "
    "UMD/MOM is not included in `ff_factors.csv`. The Carhart four-"
    "factor regression would require downloading the momentum "
    "factor separately. This is a known scope gap in the notebook "
    "relative to the submitted documents."
)


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        src = "".join(c["source"])
        if OLD_RF_PARA not in src:
            print("FATAL: rf paragraph not found")
            return 1
        if OLD_MOM_LINE not in src:
            print("FATAL: mom paragraph not found")
            return 1
        src = src.replace(OLD_RF_PARA, NEW_RF_PARA)
        src = src.replace(OLD_MOM_LINE, NEW_MOM_LINE)
        c["source"] = [s + "\n" for s in src.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        print(f"PATCHED Section 1 markdown (id={CELL_ID})")
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
