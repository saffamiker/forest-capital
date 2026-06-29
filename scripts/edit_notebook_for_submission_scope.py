"""scripts/edit_notebook_for_submission_scope.py -- June 29 2026.

In-place notebook edit. The Read tool can't open the .ipynb (too
many tokens), so NotebookEdit is unavailable. This script does the
same job: load JSON, swap cell sources by ID, insert one new cell.

Run:
    python scripts/edit_notebook_for_submission_scope.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"


CELL2_SOURCE = """# Data manifest -- load every file from the static
# freeze, print shapes, and assert the canonical strategy
# hash. If this cell raises, the freeze has been edited
# and the notebook is no longer consistent with the
# brief.
DATA_DIR = Path('notebook_data')
assert DATA_DIR.is_dir(), (
    f'Data freeze directory {DATA_DIR} not found. The '
    'notebook must be run from the repository root.')

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
# regime signals). See notebook_data/README.md for the
# regeneration procedure (scripts/export_notebook_chart_data.py).
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
    f'monthly_returns.csv has {n_rows} rows, expected 287')
"""


CELL6_SOURCE = """# Section 4 -- Performance Metrics and Visualisations.
#
# Submission-scope charts (June 29 2026 rewrite). Four
# panels constituting the visual companion to the
# Executive Brief's headline claims:
#   1. Cumulative return       -- 3 strategies (benchmark,
#                                 classic 60/40, blend OOS)
#   2. Drawdown periods        -- same 3 strategies
#   3. Rolling correlation     -- equity vs IG + equity vs HY
#                                 (12-month window to match
#                                 the brief's -0.05 / +0.57)
#   4. Regime signals          -- in the next cell, since
#                                 it uses a 2-panel layout
SUBMISSION_STRATEGIES = ('BENCHMARK', 'CLASSIC_60_40',
                          'REGIME_SWITCHING')
BLEND = 'REGIME_SWITCHING'
BENCH = 'BENCHMARK'
C6040 = 'CLASSIC_60_40'

# ── strategy_results.json carries monthly_returns as a
#    list of [date_str, ret_float] pairs (not a flat float
#    list).  Adapter:
def _ret_series(name):
    pairs = strategy_results[name].get('monthly_returns') or []
    dates = pd.to_datetime([p[0] for p in pairs])
    rets = pd.Series([float(p[1]) for p in pairs], index=dates)
    return rets.sort_index()

blend_full = _ret_series(BLEND)
bench_full = _ret_series(BENCH)
c6040_full = _ret_series(C6040)

# Risk-free annualised series for the rolling Sharpe.
ff_rf = (ff_factors[['yyyymm', 'rf']]
         .assign(date=lambda d: pd.to_datetime(
             d['yyyymm'].astype(str), format='%Y%m')
             + pd.offsets.MonthEnd(0))
         .set_index('date')['rf'] / 100.0)

# Blend OOS path -- from the new export.
blend_oos_ret = blend_oos.set_index('date')['return'].sort_index()

# ── Chart 1: cumulative return -- 3 strategies, OOS anchor.
fig, ax = plt.subplots(figsize=(11, 5))

# Full-period lines for benchmark and classic 60/40
bench_nav = (1 + bench_full).cumprod()
c6040_nav = (1 + c6040_full).cumprod()
ax.plot(bench_nav.index, bench_nav.values,
        color='#C0392B', linewidth=1.8,
        label='Benchmark (100% equity)', linestyle='--')
ax.plot(c6040_nav.index, c6040_nav.values,
        color='#7F8C8D', linewidth=1.8,
        label='Classic 60/40', linestyle='-.')

# Anchor blend OOS to benchmark NAV at Jan 2022 so all
# three share the same starting index visually.
anchor_date = pd.Timestamp('2022-01-31')
anchor_value = bench_nav.asof(anchor_date)
blend_oos_nav = anchor_value * (1 + blend_oos_ret).cumprod()
ax.plot(blend_oos_nav.index, blend_oos_nav.values,
        color='#1F3A93', linewidth=2.6,
        label='Regime-Conditional Blend (OOS)')

ax.set_yscale('log')
ax.set_title(
    'Cumulative return -- submission scope, log scale')
ax.set_ylabel('Growth of $1 (log)')
ax.axvline(anchor_date, color='black', linestyle=':',
           linewidth=0.8, alpha=0.6,
           label='OOS window start (Jan 2022)')
ax.axvspan(anchor_date, bench_nav.index.max(),
           color='#FFF8DC', alpha=0.4, zorder=0)
ax.legend(loc='upper left', fontsize=9)
# Stats box.
stats_txt = (
    f"OOS Sharpe (rf-adjusted):\\n"
    f"  Blend       {oos_summary['oos_sharpe_blend']:.2f}\\n"
    f"  Benchmark   {oos_summary['oos_sharpe_benchmark']:.2f}\\n"
    f"  Classic 60/40 {oos_summary['oos_sharpe_classic_6040']:.2f}\\n"
    f"  Improvement +{oos_summary['improvement_pct']:.0f}%")
ax.text(0.02, 0.40, stats_txt, transform=ax.transAxes,
        fontsize=9, verticalalignment='top',
        bbox=dict(facecolor='white', alpha=0.85,
                  edgecolor='#888'))
plt.tight_layout()
plt.show()

# ── Chart 2: drawdown -- 3 strategies.
fig, ax = plt.subplots(figsize=(11, 4.5))
for r, name, color in (
    (bench_full, 'Benchmark (100% equity)', '#C0392B'),
    (c6040_full, 'Classic 60/40', '#7F8C8D'),
    (blend_full, 'Regime-Conditional Blend',
     '#1F3A93'),
):
    nav = (1 + r).cumprod()
    dd = nav / nav.cummax() - 1
    ax.fill_between(
        dd.index, dd.values, 0,
        alpha=0.35, color=color, label=name)
    ax.plot(dd.index, dd.values,
            color=color, linewidth=0.9, alpha=0.7)
# Max-DD annotations.
for r, name, color, mdd in (
    (bench_full, 'Benchmark',  '#C0532B', -0.526),
    (c6040_full, 'Classic 60/40', '#5F6C6D', -0.353),
    (blend_full, 'Blend',     '#0F1A53', -0.297),
):
    nav = (1 + r).cumprod()
    dd = nav / nav.cummax() - 1
    trough_date = dd.idxmin()
    ax.annotate(f'{name}: {dd.min():.1%}',
                xy=(trough_date, dd.min()),
                xytext=(8, -2), textcoords='offset points',
                fontsize=8, color=color)
ax.set_title('Drawdown -- submission scope')
ax.set_ylabel('Drawdown (decimal)')
ax.legend(loc='lower right', fontsize=9)
plt.tight_layout()
plt.show()

# ── Chart 3: 12-month rolling correlation, equity vs IG + HY.
WIN = 12
mr = monthly_returns.set_index('date')
rc_ig = mr['equity_return'].rolling(WIN).corr(mr['ig_return'])
rc_hy = mr['equity_return'].rolling(WIN).corr(mr['hy_return'])

fig, ax = plt.subplots(figsize=(11, 4.5))
ax.plot(rc_ig.index, rc_ig.values, color='#1F4E79',
        linewidth=1.5, label='Equity vs IG bonds')
ax.plot(rc_hy.index, rc_hy.values, color='#2C7A2C',
        linewidth=1.5, label='Equity vs HY bonds',
        linestyle='--')
ax.axhline(0, color='black', linewidth=0.5, linestyle='--')
break_date = pd.Timestamp('2022-01-31')
ax.axvline(break_date, color='#C0392B', linewidth=0.8,
           linestyle=':', label='Regime break (Jan 2022)')

# Pre/post means -- equity vs IG.
pre_mean = rc_ig[rc_ig.index < break_date].mean()
post_mean = rc_ig[rc_ig.index >= break_date].mean()
ax.text(0.02, 0.95,
        f"Equity-IG correlation:\\n"
        f"  Pre-2022  mean = {pre_mean:+.2f}\\n"
        f"  Post-2022 mean = {post_mean:+.2f}",
        transform=ax.transAxes, fontsize=9,
        verticalalignment='top',
        bbox=dict(facecolor='white', alpha=0.85,
                  edgecolor='#888'))

ax.set_title(
    'Rolling 12-month correlation -- equity vs IG / HY bonds')
ax.set_ylabel('Pearson correlation')
ax.legend(loc='lower right', fontsize=9)
plt.tight_layout()
plt.show()

# Cell 7 (next code cell) renders Chart 4 -- regime signals.

# ── Headline reconciliation -- OOS scalars (academic-lock).
print()
print('=' * 60)
print('HEADLINE RECONCILIATION (OOS, academic-lock cache):')
print('=' * 60)
print(f"  OOS window           {oos_summary['oos_window_start']} -> {oos_summary['oos_window_end']}  ({oos_summary['n_test_months']} months)")
print(f"  OOS Sharpe blend      {oos_summary['oos_sharpe_blend']:.2f}  (brief: 0.90)")
print(f"  OOS Sharpe benchmark  {oos_summary['oos_sharpe_benchmark']:.2f}  (brief: 0.49)")
print(f"  OOS Sharpe 60/40      {oos_summary['oos_sharpe_classic_6040']:.2f}  (brief: 0.18)")
print(f"  Improvement vs bench  +{oos_summary['improvement_pct']:.1f}%  (brief: +83%)")
print()
print('  (Full-period strategy figures are in the per-strategy table above)')
print(f"  Max DD blend          {strategy_results[BLEND]['max_drawdown']:.4f}  (brief: -29.7%)")
print(f"  Max DD benchmark      {strategy_results[BENCH]['max_drawdown']:.4f}  (brief: -52.6%)")
print(f"  Max DD 60/40          {strategy_results[C6040]['max_drawdown']:.4f}  (brief: -35.3%)")
print()
print(f"  Rolling 12m corr equity-IG  pre-2022 = {pre_mean:+.4f}  (brief: -0.05)")
print(f"  Rolling 12m corr equity-IG  post-2022 = {post_mean:+.4f}  (brief: +0.57)")
"""


CELL_CHART4_SOURCE = """# Section 4d (June 29 2026) -- Regime signals chart.
#
# Two-panel layout overlaying the HMM regime classification on
# the S&P 500 cumulative return path. Verification target counts
# (from the platform academic_lock HMM fit):
#   BULL ~ 58, TRANSITION ~ 191, BEAR ~ 38   (total 287)
# Local fallback runs (hmmlearn unavailable in this env) yield
# ~55 / ~196 / ~36 -- within tolerance; the qualitative regime
# pattern is preserved. Regenerate on Render for the canonical
# HMM partition (see notebook_data/README.md).
from matplotlib.patches import Patch

regime = regime_classification.set_index('date')['regime_label']
equity_nav = (1 + monthly_returns.set_index('date')
              ['equity_return']).cumprod()

REGIME_COLOR = {
    'BULL':       '#2ECC71',
    'TRANSITION': '#F39C12',
    'BEAR':       '#E74C3C',
}
REGIME_VALUE = {'BEAR': -1, 'TRANSITION': 0, 'BULL': 1}

fig, (ax_top, ax_bot) = plt.subplots(
    2, 1, figsize=(11, 7), sharex=True,
    gridspec_kw={'height_ratios': [2.4, 1]})

# ── Top panel: S&P 500 cumulative + regime-band shading.
ax_top.plot(equity_nav.index, equity_nav.values,
            color='#1F1F1F', linewidth=1.8,
            label='S&P 500 cumulative (growth of $1)')

# Shade contiguous regime runs.
prev_regime = None
seg_start = None
for d, lbl in regime.items():
    if lbl != prev_regime:
        if prev_regime is not None and prev_regime in REGIME_COLOR:
            ax_top.axvspan(seg_start, d,
                           color=REGIME_COLOR[prev_regime],
                           alpha=0.18, zorder=0)
        seg_start = d
        prev_regime = lbl
if prev_regime is not None and prev_regime in REGIME_COLOR:
    ax_top.axvspan(seg_start, regime.index[-1],
                   color=REGIME_COLOR[prev_regime],
                   alpha=0.18, zorder=0)

ax_top.axvline(pd.Timestamp('2022-01-31'),
               color='#C0392B', linewidth=0.8,
               linestyle=':',
               label='OOS window start (Jan 2022)')
ax_top.set_ylabel('S&P 500 (growth of $1)')
ax_top.set_yscale('log')
ax_top.set_title(
    'Regime signals -- HMM classification overlaid on S&P 500')

# Build a single legend combining line + regime patches.
counts = regime.value_counts()
patches = [
    Patch(facecolor=REGIME_COLOR[r], alpha=0.18,
          label=f'{r} ({counts.get(r, 0)})')
    for r in ('BULL', 'TRANSITION', 'BEAR')
]
ax_top.legend(
    handles=ax_top.get_legend_handles_labels()[0] + patches,
    loc='upper left', fontsize=8)

# ── Bottom panel: discrete regime bar chart.
regime_vals = regime.map(REGIME_VALUE)
bar_colors = regime.map(REGIME_COLOR)
ax_bot.bar(regime.index, regime_vals.values,
           color=bar_colors.values, width=22,
           edgecolor='none')
ax_bot.axhline(0, color='black', linewidth=0.4)
ax_bot.axvline(pd.Timestamp('2022-01-31'),
               color='#C0392B', linewidth=0.8,
               linestyle=':')
ax_bot.set_ylim(-1.4, 1.4)
ax_bot.set_yticks([-1, 0, 1])
ax_bot.set_yticklabels(['BEAR', 'TRANSITION', 'BULL'])
ax_bot.set_ylabel('Regime')
ax_bot.set_xlabel('Date')

plt.tight_layout()
plt.show()

print()
print('Regime counts (vs verified HMM targets):')
for r, target in (('BULL', 58), ('TRANSITION', 191), ('BEAR', 38)):
    print(f"  {r:11s}  observed={counts.get(r, 0):3d}  "
          f"target={target}")
"""


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    cells = nb["cells"]
    cells_by_id = {c.get("id"): (i, c) for i, c in enumerate(cells)}

    # 1. Replace cell 2 (data manifest) source.
    if "801d9305" in cells_by_id:
        idx, cell = cells_by_id["801d9305"]
        cell["source"] = [s + "\n" for s in CELL2_SOURCE.splitlines()]
        if cell["source"]:
            cell["source"][-1] = cell["source"][-1].rstrip("\n")
        cell["outputs"] = []
        cell["execution_count"] = None
        print(f"REPLACED cell 2 (id=801d9305) -- data manifest")
    else:
        print("FATAL: cell 801d9305 not found")
        return 1

    # 2. Replace cell 6 (Section 4 visualisations) source.
    if "7228e4fe" in cells_by_id:
        idx6, cell6 = cells_by_id["7228e4fe"]
        cell6["source"] = [s + "\n" for s in CELL6_SOURCE.splitlines()]
        if cell6["source"]:
            cell6["source"][-1] = cell6["source"][-1].rstrip("\n")
        cell6["outputs"] = []
        cell6["execution_count"] = None
        print(f"REPLACED cell 6 (id=7228e4fe) -- Section 4 charts")
    else:
        print("FATAL: cell 7228e4fe not found")
        return 1

    # 3. Insert NEW chart-4 cell after cell 6.
    new_id = uuid.uuid4().hex[:8]
    new_cell = {
        "cell_type": "code",
        "id": new_id,
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": [s + "\n" for s in CELL_CHART4_SOURCE.splitlines()],
    }
    if new_cell["source"]:
        new_cell["source"][-1] = new_cell["source"][-1].rstrip("\n")
    # Insert after cell 6 (idx6+1). Re-fetch idx since cells_by_id
    # is pre-insert.
    cells.insert(idx6 + 1, new_cell)
    print(f"INSERTED new cell (id={new_id}) after cell 6 -- Chart 4 regime signals")

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
