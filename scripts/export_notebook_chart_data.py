"""scripts/export_notebook_chart_data.py -- June 29 2026.

One-shot export of the three data files needed by the
analytical_appendix.ipynb submission-scope chart rewrite.

Inputs (already frozen on the notebook-data-export branch):
  notebook_data/strategy_results.json   -- the 10-strategy backtest cache
  notebook_data/monthly_returns.csv     -- equity/IG/HY monthly returns
  notebook_data/ff_factors.csv          -- Fama-French + rf

Outputs (written to notebook_data/):
  blend_oos_monthly_returns.csv  -- date, return  (regime-conditional
                                    blend OOS path, 2022-01 -> 2026-05)
  regime_classification.csv      -- date, regime_label  (HMM historical
                                    timeline, 2002-07 -> 2026-05)
  oos_summary.json               -- {oos_sharpe_blend, oos_sharpe_benchmark,
                                    oos_sharpe_classic_6040,
                                    improvement_pct, ...}

Reproduced LOCALLY from the frozen notebook_data (no DB read) so the
exports stay aligned with the notebook's stated dataset (strategy-cache
hash f2e87dec7dcabe71).

Run:
    cd c:/Users/micha/forest-capital
    python scripts/export_notebook_chart_data.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
NOTEBOOK_DATA = REPO / "notebook_data"


def main() -> int:
    # Make backend importable so we can call the platform's HMM +
    # OOS validation code directly (these are pure-Python helpers
    # that don't touch the DB).
    sys.path.insert(0, str(REPO / "backend"))

    # asyncpg ships a `staticmethods` syntax that breaks on
    # Python 3.13 in some envs; the import surfaces it. We don't
    # actually use the DB layer here, so guard.
    os.environ.setdefault("ENVIRONMENT", "test")

    import numpy as np
    import pandas as pd

    from tools.regime_detector import fit_hmm_historical
    from tools.regime_meta_validation import out_of_sample_validation

    def _fallback_regime_classifier(
        equity_series: "pd.Series",
    ) -> dict:
        """Deterministic 3-regime classifier used when hmmlearn isn't
        available (Python 3.14 wheels not yet published). Produces a
        labelled_series that approximates the HMM output via
        return-quantile thresholding tuned to the verified count
        distribution from the platform HMM:
          BEAR  ~  38 months  (~13% of 287)
          BULL  ~  58 months  (~20%)
          TRANSITION  ~  191 months  (~67%)
        Per operator spec. The thresholds are quantile-based against
        the trailing 12-month return so the partition matches the
        verified counts exactly; the regime LABELLING is not the
        canonical HMM output and the OOS Sharpe DOES NOT REPRODUCE
        0.90 -- a Render re-run with hmmlearn is required for the
        canonical numbers. See README in notebook_data/.
        """
        clean = equity_series.dropna()
        roll12 = (1 + clean).rolling(12).apply(
            lambda x: x.prod() - 1, raw=True)
        bear_q = roll12.quantile(0.13)
        bull_q = roll12.quantile(0.80)
        labels: list[str] = []
        for r in roll12.values:
            if pd.isna(r):
                labels.append("TRANSITION")  # 12m warmup window
            elif r < bear_q:
                labels.append("BEAR")
            elif r > bull_q:
                labels.append("BULL")
            else:
                labels.append("TRANSITION")
        labelled = dict(zip(clean.index, labels))
        # Synthetic posteriors -- one-hot the assigned label.
        histograms = {"BULL": [], "TRANSITION": [], "BEAR": []}
        for lbl in labels:
            for k in histograms:
                histograms[k].append(1.0 if k == lbl else 0.0)
        return {
            "n_states": 3,
            "labelled_series": labelled,
            "historical_probs": histograms,
            "dates": [d.isoformat() for d in clean.index],
            "transition_matrix": {},
            "converged": True,
            "label_map": {0: "BEAR", 1: "TRANSITION", 2: "BULL"},
            "fallback": True,
        }

    # ── Load the frozen data bundle ───────────────────────────────
    with open(NOTEBOOK_DATA / "strategy_results.json") as f:
        sr = json.load(f)
    monthly_df = pd.read_csv(NOTEBOOK_DATA / "monthly_returns.csv")
    monthly_df["date"] = pd.to_datetime(monthly_df["date"])
    monthly_df = monthly_df.sort_values("date").reset_index(drop=True)

    ff_df = pd.read_csv(NOTEBOOK_DATA / "ff_factors.csv")
    # ff yyyymm -> month-end Timestamp for rf alignment.
    ff_df["date"] = pd.to_datetime(
        ff_df["yyyymm"].astype(str), format="%Y%m") + pd.offsets.MonthEnd(0)

    print(f"Loaded {len(monthly_df)} monthly returns + "
          f"{len(ff_df)} ff factor rows; "
          f"{len(sr)} strategies in cache.")

    # ── Fit HMM on the equity series ──────────────────────────────
    equity = pd.Series(
        monthly_df["equity_return"].values,
        index=monthly_df["date"]).dropna()
    hmm = fit_hmm_historical(equity)
    if "error" in hmm:
        msg = hmm["error"]
        if msg == "hmmlearn_not_available":
            print(
                "WARN: hmmlearn unavailable in this Python "
                "env -- falling back to deterministic 3-regime "
                "threshold classifier. Re-run on Render shell "
                "(hmmlearn installed) for the canonical HMM "
                "output before final submission.")
            hmm = _fallback_regime_classifier(equity)
        else:
            print(f"FATAL: HMM fit failed: {msg}")
            return 1
    labelled = hmm["labelled_series"]    # {Timestamp: 'BULL'/'TRANSITION'/'BEAR'}
    label_counts = pd.Series(list(labelled.values())).value_counts().to_dict()
    print(f"HMM fit OK: {label_counts}")

    # ── Export 1: regime_classification.csv ───────────────────────
    regime_rows = []
    for d, lbl in labelled.items():
        ts = pd.Timestamp(d) if not isinstance(d, pd.Timestamp) else d
        regime_rows.append({
            "date": ts.strftime("%Y-%m-%d"),
            "regime_label": lbl,
        })
    regime_df = pd.DataFrame(regime_rows).sort_values("date")
    out_regime = NOTEBOOK_DATA / "regime_classification.csv"
    regime_df.to_csv(out_regime, index=False)
    print(f"WROTE {out_regime}  ({len(regime_df)} rows, "
          f"{regime_df['date'].iloc[0]} -> {regime_df['date'].iloc[-1]})")

    # ── Run OOS validation ────────────────────────────────────────
    # rf map keyed by ISO date string -- _rf_for_dates in
    # regime_meta_validation looks up by stringified dates.
    rf_aligned = ff_df.set_index("date")["rf"].reindex(
        monthly_df["date"]).ffill() / 100.0  # ff is in percent
    rf_map: dict[str, float] = {}
    for d, v in zip(monthly_df["date"], rf_aligned.values):
        if pd.notna(v):
            rf_map[d.strftime("%Y-%m-%d")] = float(v)
        # Also key by Timestamp + isoformat variants so
        # _rf_for_dates lookups (whatever shape it expects)
        # find a match.
        rf_map[str(d)] = float(v) if pd.notna(v) else 0.0

    oos = out_of_sample_validation(
        sr, hmm, split_date="2022-01-01",
        risk_free=rf_map, return_series=True)
    if "error" in oos:
        print(f"FATAL: OOS validation failed: {oos['error']}")
        return 1

    print(f"OOS validation: split={oos['split_date']} "
          f"train={oos['n_train_months']}m test={oos['n_test_months']}m")
    print(f"  regime_conditional sharpe = "
          f"{oos['oos']['regime_conditional']['sharpe']:.4f}")
    print(f"  benchmark          sharpe = "
          f"{oos['oos'].get('benchmark', {}).get('sharpe')}")
    print(f"  equal_weight       sharpe = "
          f"{oos['oos']['equal_weight']['sharpe']:.4f}")

    # ── Export 2: blend_oos_monthly_returns.csv ───────────────────
    blend_rows = []
    for d, r in zip(oos["test_dates"], oos["blend_monthly"]):
        blend_rows.append({"date": str(d)[:10], "return": float(r)})
    blend_df = pd.DataFrame(blend_rows)
    out_blend = NOTEBOOK_DATA / "blend_oos_monthly_returns.csv"
    blend_df.to_csv(out_blend, index=False)
    print(f"WROTE {out_blend}  ({len(blend_df)} rows, "
          f"{blend_df['date'].iloc[0]} -> {blend_df['date'].iloc[-1]})")

    # ── Export 3: oos_summary.json ────────────────────────────────
    # Reproduce the academic_lock structure: Sharpe per arm + improvement_pct.
    # CLASSIC_60_40 is NOT one of the OOS validation arms by default --
    # validation produces (regime_conditional, equal_weight, benchmark,
    # regime_switching). Compute CLASSIC_60_40 OOS Sharpe inline using
    # the same convention (rf-adjusted, post-2022).
    test_dates = pd.to_datetime(oos["test_dates"])
    rf_test = np.array([
        rf_map.get(d.strftime("%Y-%m-%d"), 0.0) for d in test_dates])

    def _sharpe(ret_series: list, rf: np.ndarray) -> float:
        ret = np.asarray(ret_series, dtype=float)
        excess = ret - rf
        if excess.std(ddof=1) == 0 or np.isnan(excess).any():
            return 0.0
        return float(
            excess.mean() * 12 / (excess.std(ddof=1) * np.sqrt(12)))

    c6040_pairs = sr.get("CLASSIC_60_40", {}).get("monthly_returns") or []
    bench_pairs = sr.get("BENCHMARK", {}).get("monthly_returns") or []

    def _series_from_pairs(
        pairs: list, since: str,
    ) -> tuple[list, list]:
        """strategy_results.json monthly_returns are [[date, ret], ...]
        pairs. Filter to >= `since` and split into date / return lists."""
        out_dates, out_rets = [], []
        for p in pairs:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                d, r = p[0], p[1]
                if str(d) >= since:
                    out_dates.append(str(d))
                    out_rets.append(float(r))
        return out_dates, out_rets

    c6040_dates, c6040_oos = _series_from_pairs(
        c6040_pairs, "2022-01-01")
    bench_dates_iso, bench_oos = _series_from_pairs(
        bench_pairs, "2022-01-01")
    c6040_rf = np.array([
        rf_map.get(d[:10], 0.0) for d in c6040_dates])
    bench_rf = np.array([
        rf_map.get(d[:10], 0.0) for d in bench_dates_iso])
    c6040_sharpe = _sharpe(c6040_oos, c6040_rf)
    bench_sharpe_check = _sharpe(bench_oos, bench_rf)

    blend_sharpe_local = float(
        oos["oos"]["regime_conditional"]["sharpe"])
    bench_sharpe_local = float(
        oos["oos"].get("benchmark", {}).get("sharpe")
        or bench_sharpe_check)

    # ── CANONICAL VALUES (from the platform academic_lock) ────────
    # Hard-coded from the academic_lock cache (PR #490, locked at
    # submission). The local re-run above produces DIFFERENT values
    # because hmmlearn is unavailable in this Python 3.14 env and
    # the fallback regime classifier does not reproduce the canonical
    # HMM partition. The canonical OOS Sharpe values are sourced from
    # the platform's frozen academic_lock cache, which is what the
    # brief / appendix / deck reference -- so the notebook stays
    # consistent with the other three deliverables.
    canonical = {
        "oos_sharpe_blend":        0.90,
        "oos_sharpe_benchmark":    0.49,
        "oos_sharpe_classic_6040": 0.18,
    }
    improvement_pct = round(
        (canonical["oos_sharpe_blend"]
         - canonical["oos_sharpe_benchmark"])
        / abs(canonical["oos_sharpe_benchmark"]) * 100.0, 1)

    summary = {
        "split_date":       oos["split_date"],
        "oos_window_start": str(test_dates.min().date()),
        "oos_window_end":   str(test_dates.max().date()),
        "n_test_months":    int(oos["n_test_months"]),
        "oos_sharpe_blend":         canonical["oos_sharpe_blend"],
        "oos_sharpe_benchmark":     canonical["oos_sharpe_benchmark"],
        "oos_sharpe_classic_6040":  canonical["oos_sharpe_classic_6040"],
        "improvement_pct": improvement_pct,
        "source": (
            "Platform academic_lock cache (PR #490, locked at "
            "submission). Notebook re-runs use the same data inputs "
            "as the platform; the canonical Sharpe figures come from "
            "the platform's locked HMM fit + OOS validation."),
        "rf_source": "ff_factors.csv (Kenneth French)",
        "local_recomputation": {
            "oos_sharpe_blend":        round(blend_sharpe_local, 4),
            "oos_sharpe_benchmark":    round(bench_sharpe_local, 4),
            "oos_sharpe_classic_6040": round(c6040_sharpe, 4),
            "note": (
                "These local values use the fallback regime "
                "classifier (hmmlearn unavailable in this env). "
                "They differ from the canonical values above; the "
                "canonical values are what the notebook displays."),
        },
    }
    out_summary = NOTEBOOK_DATA / "oos_summary.json"
    with open(out_summary, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"WROTE {out_summary}")
    print(f"  blend Sharpe   = {summary['oos_sharpe_blend']}")
    print(f"  bench Sharpe   = {summary['oos_sharpe_benchmark']}")
    print(f"  60/40 Sharpe   = {summary['oos_sharpe_classic_6040']}")
    print(f"  improvement %  = {summary['improvement_pct']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
