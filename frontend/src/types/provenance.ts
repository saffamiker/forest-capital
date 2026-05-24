/**
 * frontend/src/types/provenance.ts
 *
 * TypeScript types mirroring the backend data_series_registry table and the
 * /api/v1/provenance response.  Strict typing here ensures the frontend can
 * never silently display a label for a source it doesn't recognise.
 *
 * CHART_PROVENANCE_REGISTRY is the only provenance constant in the frontend —
 * it maps chart IDs to series IDs.  All series metadata comes from the API,
 * never from a hardcoded label.  This is the design the spec requires:
 * "The frontend NEVER declares provenance — it only displays what the API returns."
 */

// ── Source types — must match backend data_series_registry.source_type ──────

export type SourceType =
  | "excel_provided"
  | "yfinance"
  | "fred_api"
  | "ken_french"          // legacy pandas-datareader (deprecated)
  | "ken_french_direct"   // Sprint 6: direct HTTP zip fetch from Dartmouth
  | "constant";

export type ValidationStatus = "pass" | "warn" | "fail";

// ── Source detail schemas — one per SourceType ────────────────────────────────

export interface ExcelProvidedDetail {
  file: string;
  sheet: string;
  provided_by: string;
  original_source: string;
}

export interface YfinanceDetail {
  ticker: string;
  auto_adjust: boolean;
  interval: string;
  fetched_at: string;
}

export interface FredApiDetail {
  series_id: string;
  fetched_at: string;
  fred_url: string;
}

export interface KenFrenchDetail {
  dataset: string;
  fetched_at: string;
  url: string;
}

export interface ConstantDetail {
  value: Record<string, number>;
  justification: string;
  used_by: string;
}

export type SourceDetail =
  | ExcelProvidedDetail
  | YfinanceDetail
  | FredApiDetail
  | KenFrenchDetail
  | ConstantDetail;

// ── Data series registry entry ────────────────────────────────────────────────

export interface DataSeriesRecord {
  series_id: string;
  display_name: string;
  source_type: SourceType;
  source_detail: SourceDetail;
  frequency: "daily" | "monthly" | "quarterly";
  date_range_start: string | null;
  date_range_end: string | null;
  row_count: number | null;
  loaded_at: string;
  last_validated: string | null;
  validation_status: ValidationStatus | null;
}

// ── Cross-validation block (nested in provenance response) ───────────────────

export interface EquityCrossValidation {
  series_a: string;
  series_b: string;
  n_months_compared: number;
  n_green: number;
  n_amber: number;
  n_red: number;
  max_discrepancy_pct: number;
  mean_discrepancy_pct: number;
  worst_month: string;
  status: ValidationStatus;
  authoritative: string;
}

export interface BondInternalValidation {
  bnd_gaps_found: number;
  bnd_outliers_found: number;
  hy_index_positive: boolean;
  hy_gfc_drawdown_pct: number;
  status: ValidationStatus;
}

export interface CrossValidationBlock {
  equity: EquityCrossValidation;
  bond_internal: BondInternalValidation;
}

// ── Full /api/v1/provenance response ─────────────────────────────────────────

export interface ProvenanceResponse {
  series: DataSeriesRecord[];
  cross_validation: CrossValidationBlock | Record<string, never>;
  last_pipeline_run: string | null;
}

// ── Chart provenance registry — the ONLY provenance constant in the frontend ─

/**
 * Maps chart IDs to the series IDs they display.
 * These series IDs must match keys in data_series_registry from the API.
 * If a series ID isn't in the registry, the sources line shows "Unknown".
 */
export const CHART_PROVENANCE_REGISTRY: Record<string, string[]> = {
  cumulative_returns: [
    "equity_monthly",
    "ig_monthly_bnd",
    "hy_monthly_baml",
    "risk_free_dtb3",
  ],
  regime_timeline: [
    "vix_daily",
    "yield_curve_10y2y",
    "hy_spread_baml",
    "gdp_real_gdpc1",
  ],
  factor_exposure_heatmap: ["ff_factors_monthly"],
  stress_test_comparison: [
    "equity_monthly",
    "ig_monthly_bnd",
    "hy_monthly_baml",
    "risk_free_dtb3",
  ],
  correlation_breakdown: [
    "equity_monthly",
    "ig_monthly_bnd",
    "hy_monthly_baml",
  ],
  significance_journey_matrix: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml"],
  rolling_correlation: ["equity_daily_spy", "ig_daily_bnd", "hy_daily_baml"],
  // May 24 2026 (UAT ID 286) — diversification suite charts.
  // All seven derive from monthly strategy returns, which themselves
  // are computed from the four core return series + the risk-free
  // rate. Adding the registry entries makes ChartCommentStrip render
  // the Sources line on every diversification chart wrapped in one.
  correlation_heatmap: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml"],
  tail_risk: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml", "risk_free_dtb3"],
  capture_ratios: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml"],
  drawdown_duration: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml"],
  crisis_performance: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml", "risk_free_dtb3"],
  marginal_contribution_to_risk: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml"],
  return_distribution: ["equity_monthly", "ig_monthly_bnd", "hy_monthly_baml"],
};

// ── Display helper ────────────────────────────────────────────────────────────

/**
 * Formats a source label for the ChartCommentStrip sources line.
 * Called by the frontend — matches the formatSource spec in CLAUDE.md.
 */
export function formatSource(sourceType: SourceType, detail: SourceDetail): string {
  switch (sourceType) {
    case "excel_provided":
      return "Excel (provided by Dr. Panttser)";
    case "yfinance":
      return `yfinance — ${(detail as YfinanceDetail).ticker}`;
    case "fred_api":
      return `FRED API — ${(detail as FredApiDetail).series_id}`;
    case "ken_french":
      return "Ken French data library";
    case "ken_french_direct":
      return "Ken French data library (direct HTTP)";
    case "constant":
      return "Fixed assumption (documented)";
  }
}
