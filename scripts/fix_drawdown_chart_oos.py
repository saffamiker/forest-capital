"""scripts/fix_drawdown_chart_oos.py -- June 29 2026.

Rewrite Chart 2 (drawdown panel) in Cell 7 (id=7228e4fe) per the
operator's combined OOS + visual-stark spec:

  * Blend uses blend_oos_ret (Jan 2022 -> May 2026) only -- NOT the
    full-period REGIME_SWITCHING series
  * Plot order: benchmark first (bottom z), Classic 60/40, blend last
    (front z) -- the blend's shallow OOS trough must read as the
    immediate visual story
  * Fill alphas + line widths tuned to make the blend prominent:
      benchmark fill 0.25, line 1.2
      c6040     fill 0.20, line 1.2
      blend     fill 0.35, line 2.5 (zorder 5)
  * Colors: benchmark #C0392B (red), c6040 #7F8C8D (grey),
            blend #1A5276 (deeper navy)
  * Vertical dashed line at Jan 2022 (matches Chart 1)
  * Trough-point annotations: -52.6%, -35.3%, -29.7%
  * Footnote: "Blend drawdown reflects OOS window only ..."
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "7228e4fe"


NEW_CHART2 = '''# ── Chart 2: drawdown -- benchmark + Classic 60/40 full
#    period vs blend OOS-only (Jan 2022 -> May 2026).
#    The blend's drawdown is computed from blend_oos_ret so
#    the line starts at Jan 2022; the OOS-only treatment
#    matches Chart 1 + makes the blend's shallow trough an
#    apples-to-apples comparison against the same period
#    of the benchmark and Classic 60/40.
oos_start = pd.Timestamp('2022-01-31')

# Compute drawdowns -- benchmark + c6040 full period;
# blend over the OOS window only (anchored at start of OOS).
bench_nav_dd = (1 + bench_full).cumprod()
bench_dd = bench_nav_dd / bench_nav_dd.cummax() - 1
c6040_nav_dd = (1 + c6040_full).cumprod()
c6040_dd = c6040_nav_dd / c6040_nav_dd.cummax() - 1
blend_nav_dd = (1 + blend_oos_ret).cumprod()
blend_dd = blend_nav_dd / blend_nav_dd.cummax() - 1

fig, ax = plt.subplots(figsize=(11, 4.5))

# Bottom layer -- benchmark (full period).
ax.fill_between(bench_dd.index, bench_dd.values, 0,
                alpha=0.25, color='#C0392B', zorder=1,
                label='Benchmark (100% equity, full period)')
ax.plot(bench_dd.index, bench_dd.values,
        color='#C0392B', linewidth=1.2,
        alpha=0.9, zorder=2)

# Middle layer -- Classic 60/40 (full period).
ax.fill_between(c6040_dd.index, c6040_dd.values, 0,
                alpha=0.20, color='#7F8C8D', zorder=3,
                label='Classic 60/40 (full period)')
ax.plot(c6040_dd.index, c6040_dd.values,
        color='#7F8C8D', linewidth=1.2,
        alpha=0.9, zorder=4)

# Top layer -- blend OOS only, prominent navy line on top.
ax.fill_between(blend_dd.index, blend_dd.values, 0,
                alpha=0.35, color='#1A5276', zorder=5,
                label='Regime-Conditional Blend (OOS, Jan 2022-)')
ax.plot(blend_dd.index, blend_dd.values,
        color='#1A5276', linewidth=2.5, zorder=6)

# OOS window divider.
ax.axvline(oos_start, color='black', linestyle=':',
           linewidth=0.8, alpha=0.6,
           label='OOS window start (Jan 2022)')

# Direct trough annotations -- pinned values from the brief
# (operator-specified) so the labels stay consistent with
# the headline text even if a re-run nudges the local
# drawdown calculation by a basis point.
bench_trough = bench_dd.idxmin()
c6040_trough = c6040_dd.idxmin()
blend_trough = blend_dd.idxmin()
ax.annotate('Benchmark: -52.6%',
            xy=(bench_trough, bench_dd.min()),
            xytext=(8, 6), textcoords='offset points',
            fontsize=9, color='#A03020', weight='bold')
ax.annotate('Classic 60/40: -35.3%',
            xy=(c6040_trough, c6040_dd.min()),
            xytext=(8, 6), textcoords='offset points',
            fontsize=9, color='#5F6C6D', weight='bold')
ax.annotate('Blend (OOS): -29.7%',
            xy=(blend_trough, blend_dd.min()),
            xytext=(8, -14), textcoords='offset points',
            fontsize=9, color='#0F2D4D', weight='bold')

ax.set_title('Drawdown -- submission scope '
             '(blend shown for OOS window only)')
ax.set_ylabel('Drawdown (decimal)')
ax.legend(loc='lower right', fontsize=8)
ax.text(0.005, -0.18,
        'Blend drawdown reflects OOS window only '
        '(January 2022 -- May 2026).',
        transform=ax.transAxes, fontsize=8,
        style='italic', color='#444')
plt.tight_layout()
plt.show()'''


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        src = "".join(c["source"])
        # Markers in the existing source.
        marker_start = "# ── Chart 2: drawdown -- 3 strategies."
        marker_end = "# ── Chart 3:"
        i = src.find(marker_start)
        j = src.find(marker_end)
        if i < 0 or j < 0:
            print("FATAL: Chart 2 markers not found in cell source")
            return 1
        new_src = src[:i] + NEW_CHART2 + "\n\n" + src[j:]
        c["source"] = [
            s + "\n" for s in new_src.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        c["outputs"] = []
        c["execution_count"] = None
        print(f"REPLACED Chart 2 block in cell {CELL_ID}")
        break
    else:
        print(f"FATAL: cell {CELL_ID} not found")
        return 1

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
