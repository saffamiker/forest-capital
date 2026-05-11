/**
 * frontend/src/stores/provenanceStore.ts
 *
 * Zustand store for runtime data provenance.  Populated from GET /api/v1/provenance
 * on app load.  All chart sources lines and the Data Sources panel read from here —
 * never from a hardcoded constant — so a label can never misrepresent the actual
 * origin of a number.  This is the runtime provenance integrity guarantee from
 * CLAUDE.md Section 4b.
 */

import { create } from "zustand";
import type {
  DataSeriesRecord,
  ProvenanceResponse,
  CrossValidationBlock,
} from "../types/provenance";

interface ProvenanceState {
  // Keyed by series_id for O(1) chart lookup
  series: Record<string, DataSeriesRecord>;
  crossValidation: CrossValidationBlock | null;
  lastPipelineRun: string | null;
  loading: boolean;
  error: string | null;

  // Actions
  fetchProvenance: (apiBase?: string) => Promise<void>;
  getSeriesForChart: (chartId: string, registry: Record<string, string[]>) => DataSeriesRecord[];
}

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export const useProvenanceStore = create<ProvenanceState>((set, get) => ({
  series: {},
  crossValidation: null,
  lastPipelineRun: null,
  loading: false,
  error: null,

  fetchProvenance: async (apiBase = API_BASE) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`${apiBase}/api/v1/provenance`);
      if (!res.ok) {
        throw new Error(`Provenance fetch failed: ${res.status}`);
      }
      const data: ProvenanceResponse = await res.json();

      // Index by series_id for O(1) lookup by charts
      const byId: Record<string, DataSeriesRecord> = {};
      for (const s of data.series) {
        byId[s.series_id] = s;
      }

      set({
        series: byId,
        crossValidation:
          Object.keys(data.cross_validation).length > 0
            ? (data.cross_validation as CrossValidationBlock)
            : null,
        lastPipelineRun: data.last_pipeline_run,
        loading: false,
      });
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : "Unknown error",
      });
    }
  },

  getSeriesForChart: (chartId, registry) => {
    const { series } = get();
    const seriesIds = registry[chartId] ?? [];
    return seriesIds
      .map((id) => series[id])
      .filter((s): s is DataSeriesRecord => s !== undefined);
  },
}));
