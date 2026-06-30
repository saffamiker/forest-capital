"""scripts/apply_strategy_desc_and_carhart.py -- June 29 2026.

Two more fixes from the operator's review:

  1. Cell 5 REGIME_SWITCHING description: rename "COUNCIL
     BLEND" to "HMM regime-conditional strategy" + add a
     trailing Note that distinguishes the underlying
     strategy from the submission blend.

  2. Cell 10 FF regression: try Carhart 4-factor via a
     runtime Kenneth French MOM download; gracefully
     fall back to 3-factor if no internet (Colab works,
     air-gapped runs degrade cleanly).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL5_ID = "cc801457"
CELL10_ID = "0b67cd07"


NEW_REGIME_DESC = """    'REGIME_SWITCHING': (
        'HMM regime-conditional strategy. A 3-state '
        'Gaussian Hidden Markov Model (BULL, BEAR, '
        'TRANSITION) classifies the market each month '
        'using equity return and absolute return as '
        'features. Allocation shifts to a fixed sleeve '
        'mix per regime, with smooth interpolation '
        'across transitions weighted by the posterior. '
        'The weight_schedule field in strategy_results.'
        'json carries the full rebalancing trajectory. '
        'Note: REGIME_SWITCHING is the underlying '
        'strategy. The regime-conditional blend '
        'submitted for FNA 670 is an HMM-posterior-'
        'weighted combination of all 10 strategies in '
        'this universe -- its OOS monthly returns are '
        'in blend_oos_monthly_returns.csv.'),"""

OLD_REGIME_DESC_MARKER_START = "'REGIME_SWITCHING':"
OLD_REGIME_DESC_MARKER_END = "'MOMENTUM_ROTATION':"


NEW_CELL10 = '''# Section 5a -- Factor regression (Carhart 4-factor with
#                Fama-French 3-factor fallback).
#
# The Executive Brief and Analytical Appendix Table E1
# cite Carhart (1997), so the canonical model is the
# 4-factor regression on (mkt_rf, smb, hml, mom). The
# notebook freeze ships only the 3-factor + rf series
# from Kenneth French's data library; the momentum (mom)
# factor is downloaded at runtime from Ken French's site
# and merged. If the download fails (e.g. an air-gapped
# environment or rate-limit), the cell degrades to a
# 3-factor regression and labels the output accordingly.

import urllib.request
import io
import zipfile

ff_decimal = ff.copy()
for col in ('mkt_rf', 'smb', 'hml', 'rf'):
    ff_decimal[col] = ff_decimal[col] / 100.0
ff_decimal = ff_decimal.set_index('date')[
    ['mkt_rf', 'smb', 'hml', 'rf']]

# Try to load MOM from Kenneth French's data library.
mom_decimal = None
try:
    _url = ("https://mba.tuck.dartmouth.edu/pages/faculty"
            "/ken.french/ftp/F-F_Momentum_Factor_CSV.zip")
    with urllib.request.urlopen(_url, timeout=15) as r:
        _zf = zipfile.ZipFile(io.BytesIO(r.read()))
        _fname = [n for n in _zf.namelist()
                  if n.upper().endswith('.CSV')][0]
        _raw = pd.read_csv(
            _zf.open(_fname), skiprows=13, header=0)
    _raw.columns = [c.strip() for c in _raw.columns]
    # First col is the date (header is usually blank); the
    # second is the MOM factor under names that vary by
    # vintage ('Mom', 'Mom   ', or 'MOM').
    _date_col = _raw.columns[0]
    _mom_col = [
        c for c in _raw.columns
        if c.lower().replace(' ', '') == 'mom'][0]
    _raw = _raw.rename(columns={
        _date_col: 'yyyymm', _mom_col: 'mom'})
    _raw = _raw[_raw['yyyymm']
                .astype(str).str.strip().str.len() == 6].copy()
    _raw['yyyymm'] = _raw['yyyymm'].astype(int)
    _raw['mom'] = pd.to_numeric(
        _raw['mom'], errors='coerce') / 100.0
    _raw = _raw[['yyyymm', 'mom']].dropna()
    _raw['date'] = (
        pd.to_datetime(_raw['yyyymm'].astype(str),
                       format='%Y%m')
        + pd.offsets.MonthEnd(0))
    mom_decimal = _raw.set_index('date')['mom']
    print(f"MOM factor loaded from Kenneth French: "
          f"{len(mom_decimal)} months "
          f"({mom_decimal.index[0].date()} -> "
          f"{mom_decimal.index[-1].date()})")
except Exception as _exc:
    print(f"MOM download unavailable ({type(_exc).__name__}: "
          f"{_exc}) -- falling back to Fama-French 3-factor.")

blend_full = strategy_returns(BLEND)
joined = pd.DataFrame({
    'blend': blend_full,
}).join(ff_decimal, how='inner').dropna()
if mom_decimal is not None:
    joined = joined.join(
        mom_decimal.rename('mom'), how='inner').dropna()
    factors = ['mkt_rf', 'smb', 'hml', 'mom']
    model_label = 'Carhart 4-factor'
else:
    factors = ['mkt_rf', 'smb', 'hml']
    model_label = 'Fama-French 3-factor'

joined['excess'] = joined['blend'] - joined['rf']
X = joined[factors].values
y = joined['excess'].values
X = np.column_stack([np.ones(len(X)), X])

beta, *_ = np.linalg.lstsq(X, y, rcond=None)
alpha_monthly = beta[0]
resid = y - X @ beta
n, k = X.shape
sigma2 = (resid @ resid) / (n - k)
cov = sigma2 * np.linalg.inv(X.T @ X)
se = np.sqrt(np.diag(cov))
t_stats = beta / se
p_vals = (
    2 * (1 - stats.t.cdf(np.abs(t_stats), df=n - k)))
r2 = 1 - resid.var(ddof=1) / y.var(ddof=1)

print()
print(f'{model_label} regression -- blend ({BLEND})')
print(f'  n_observations:  {n}  (FF coverage gap drops '
      f'{len(blend_full) - n} month(s))')
print()
print('  factor           coef         t       p-value')
print('  ' + '-' * 50)
for label, b, t, p in zip(
    ['alpha'] + factors, beta, t_stats, p_vals,
):
    print(f'  {label:14s} {b:>10.6f}  {t:>8.3f}  {p:>10.6f}')
alpha_bps_annual = alpha_monthly * 12 * 10000
print()
print(f'  alpha (annualised):  {alpha_bps_annual:.2f} bps')
print(f'  R-squared:           {r2:.4f}')

print()
print(f'  -- Cached (platform Carhart 4-factor) --')
print(f'  cached alpha (annualised):  '
      f'{strategy_results[BLEND]["alpha_bps"]} bps')
print(f'  cached market beta:         '
      f'{strategy_results[BLEND]["beta"]}')
print(f'  cached R-squared:           '
      f'{strategy_results[BLEND]["r_squared"]}')

print()
print(f'  Regression model: {model_label} (MKT-RF, SMB, '
      f'HML' + (', MOM)' if 'mom' in factors else ')'))
print(f'  MOM factor downloaded from Kenneth French\\'s data')
print(f'  library at runtime. Falls back to Fama-French')
print(f'  3-factor if MOM is unavailable.')
print(f'  The Executive Brief + Analytical Appendix Table E1')
print(f'  reference Carhart (1997).')'''


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    by_id = {c.get("id"): c for c in nb["cells"]}

    # Cell 5 REGIME_SWITCHING description swap.
    cell5 = by_id.get(CELL5_ID)
    if cell5 is None:
        print(f"FATAL: cell {CELL5_ID} not found")
        return 1
    src5 = "".join(cell5["source"])
    i = src5.find(OLD_REGIME_DESC_MARKER_START)
    j = src5.find(OLD_REGIME_DESC_MARKER_END)
    if i < 0 or j < 0:
        print("FATAL: REGIME_SWITCHING block not found in cell 5")
        return 1
    # Find the start of the line containing i (preserve indent).
    line_start = src5.rfind("\n", 0, i) + 1
    new_src5 = src5[:line_start] + NEW_REGIME_DESC + "\n    " + src5[j:]
    cell5["source"] = [s + "\n" for s in new_src5.splitlines()]
    if cell5["source"]:
        cell5["source"][-1] = cell5["source"][-1].rstrip("\n")
    print(f"REPLACED REGIME_SWITCHING description in cell {CELL5_ID}")

    # Cell 10 FF regression -- Carhart 4-factor with fallback.
    cell10 = by_id.get(CELL10_ID)
    if cell10 is None:
        print(f"FATAL: cell {CELL10_ID} not found")
        return 1
    cell10["source"] = [s + "\n" for s in NEW_CELL10.splitlines()]
    if cell10["source"]:
        cell10["source"][-1] = cell10["source"][-1].rstrip("\n")
    cell10["outputs"] = []
    cell10["execution_count"] = None
    print(f"REPLACED FF regression cell {CELL10_ID} with Carhart + fallback")

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
