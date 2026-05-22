/**
 * frontend/src/stores/strategyCharacterisationsStore.ts
 *
 * Per-session store for the Portfolio Profile data shipped by the
 * Item 9 backend (GET /api/v1/strategies/characterisations).
 *
 * Returns one row per strategy with:
 *   - construction_summary           AI-generated paragraph
 *   - portfolio_characteristics      deterministic (avg_holdings, etc.)
 *   - behavioural_profile            AI-generated (outperforms_when, ...)
 *   - regime_sensitivity             AI-generated one-line summary
 *   - behavioural_tag                AI-generated short descriptor
 *
 * The store mirrors the regimeStore / dataStatusStore pattern: one
 * fetch per session, no TTL (the characterisations only change on
 * data ingestion, far rarer than the regime data's 15-minute window).
 * Both the PortfolioProfilePanel and the Dashboard behavioural_tag
 * read from the same cached payload — every consumer is a no-op fetch
 * once the first one has populated the store.
 *
 * Fail-open: an axios error leaves strategies=[] without throwing so
 * the consuming components fall back to their "characterisation not
 * yet computed" empty state. The agent context injection on the
 * backend uses the same data source from the DB directly; the
 * frontend store is purely a display cache.
 */
import { create } from 'zustand'
import axios from 'axios'

export interface PortfolioCharacteristics {
  avg_holdings: number | null
  avg_turnover_pct: number | null
  avg_concentration: number | null  // percent — the largest holding, avg over rebalances
  rebalance_frequency: string  // "buy and hold" | "quarterly" | "monthly" | "signal-driven"
}

export interface BehaviouralProfile {
  outperforms_when: string
  underperforms_when: string
  primary_risk_factor: string  // "Market (MKT-RF)" | "Size (SMB)" | "Value (HML)" | "Momentum (MOM)"
  diversification_role: string
}

export interface StrategyCharacterisation {
  strategy_id: string
  construction_summary: string
  portfolio_characteristics: PortfolioCharacteristics
  behavioural_profile: BehaviouralProfile
  regime_sensitivity: string
  behavioural_tag: string
  _computed_at?: string | null
  _data_hash?: string | null
  _stale?: boolean
}

interface CharacterisationsState {
  /** Keyed by strategy_id (the BENCHMARK / VOL_TARGETING / etc. id). */
  byId: Record<string, StrategyCharacterisation>
  loading: boolean
  loaded: boolean
  fetchedAt: Date | null
  available: boolean

  load: () => Promise<void>
  reload: () => Promise<void>
}

export const useCharacterisationsStore =
  create<CharacterisationsState>((set, get) => ({
    byId: {},
    loading: false,
    loaded: false,
    fetchedAt: null,
    available: false,

    load: async () => {
      // Already loaded — no network. Per-session cache.
      if (get().loaded) return
      // Already fetching — let the existing call complete.
      if (get().loading) return
      await get().reload()
    },

    reload: async () => {
      set({ loading: true })
      try {
        const res = await axios.get<{
          available: boolean
          data_hash?: string | null
          strategies: StrategyCharacterisation[]
          note?: string
        }>('/api/v1/strategies/characterisations')
        const byId: Record<string, StrategyCharacterisation> = {}
        for (const row of res.data.strategies ?? []) {
          if (row.strategy_id) byId[row.strategy_id] = row
        }
        set({
          byId,
          loading: false,
          loaded: true,
          fetchedAt: new Date(),
          available: !!res.data.available,
        })
      } catch {
        // Fail-open — characterisations are a contextual layer; their
        // absence does not block any other dashboard rendering.
        set({ loading: false, loaded: true, available: false })
      }
    },
  }))
