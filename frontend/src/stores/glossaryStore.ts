/**
 * frontend/src/stores/glossaryStore.ts
 *
 * Single source of truth for AI-generated Commentary-mode content.
 * Holds five namespaces matching the backend ExplainerAgent's five
 * output types (CLAUDE.md Section 15):
 *
 *   terms       — keyed by metric name ("sharpe_ratio", "fdr", …)
 *   parameters  — keyed by config parameter ("OPTIMIZATION_WINDOW", …)
 *   personas    — keyed by agent name ("Equity Analyst", …)
 *   qa          — keyed by check_id ("D01", …)
 *   charts      — keyed by chart_id ("cpcv_sharpe_distribution", …)
 *
 * Every loader is idempotent: it checks the cache first and returns
 * immediately if the key is present. This is the invariant that makes
 * Commentary mode performant — hovering 50 metrics on the dashboard
 * triggers exactly one /api/explain/terms call per session, not 50.
 *
 * No persistence: glossary content is tied to the current strategy
 * results, so we re-fetch on each session. Persisting it would risk
 * showing stale explanations after a data refresh.
 */
import { create } from 'zustand'
import axios from 'axios'
import { useCouncilStore } from './councilStore'
import type {
  GlossaryTerm,
  ParameterExplanation,
  PersonaExplanation,
  QAItemExplanation,
  ChartExplanation,
} from '../types/glossary'

interface LoadFlags {
  termsLoaded: boolean
  termsLoading: boolean
  // Per-key flags for parameters/personas/charts — these are loaded on
  // demand and we don't want to fire duplicate requests while one is
  // already in flight.
  inflight: Set<string>
}

interface GlossaryState extends LoadFlags {
  terms: Record<string, GlossaryTerm>
  parameters: Record<string, ParameterExplanation>
  personas: Record<string, PersonaExplanation>
  qa: Record<string, QAItemExplanation>
  charts: Record<string, ChartExplanation>

  loadTerms: (councilOutput?: Record<string, unknown>) => Promise<void>
  loadParameter: (
    parameter: string,
    value: unknown,
    currentResults: Record<string, unknown>,
  ) => Promise<void>
  loadPersona: (
    agentName: string,
    systemPrompt: string,
    findings: Record<string, unknown>,
  ) => Promise<void>
  loadChart: (
    chartId: string,
    chartType: string,
    chartData: unknown,
    currentResults: Record<string, unknown>,
  ) => Promise<void>
  loadQA: (auditResults: Array<Record<string, unknown>>) => Promise<void>

  clear: () => void
}

export const useGlossaryStore = create<GlossaryState>((set, get) => ({
  terms:      {},
  parameters: {},
  personas:   {},
  qa:         {},
  charts:     {},

  termsLoaded:  false,
  termsLoading: false,
  inflight:     new Set<string>(),

  // ── Terms: loaded once per session. The backend accepts any council_output
  //    shape — we pass current strategy results when no council has run yet
  //    so the explanations are still anchored to real numbers.
  loadTerms: async (councilOutput) => {
    if (get().termsLoaded || get().termsLoading) return
    set({ termsLoading: true })
    try {
      // Anchor the glossary in the current session: when the caller does
      // not pass council output explicitly, fall back to the last
      // council result so the Explainer can fill each term's
      // `this_session` field. An empty object when no council has run.
      const council = councilOutput
        ?? (useCouncilStore.getState().result as
            Record<string, unknown> | null)
        ?? {}
      const res = await axios.post<Record<string, GlossaryTerm>>(
        '/api/explain/terms',
        { council_output: council },
      )
      set({
        terms: res.data ?? {},
        termsLoaded: true,
        termsLoading: false,
      })
    } catch {
      // Fail silently — Commentary mode should degrade to no-tooltip-content
      // rather than break the dashboard. Tooltips fall back to a generic
      // "Hover for details" hint in the consumer.
      set({ termsLoading: false, termsLoaded: true })
    }
  },

  loadParameter: async (parameter, value, currentResults) => {
    if (get().parameters[parameter] || get().inflight.has(`param:${parameter}`)) return
    const inflight = new Set(get().inflight)
    inflight.add(`param:${parameter}`)
    set({ inflight })
    try {
      const res = await axios.post<ParameterExplanation>(
        '/api/explain/parameter',
        { parameter, value, current_results: currentResults },
      )
      set((s) => ({
        parameters: { ...s.parameters, [parameter]: res.data },
        inflight: new Set([...s.inflight].filter((k) => k !== `param:${parameter}`)),
      }))
    } catch {
      set((s) => ({
        inflight: new Set([...s.inflight].filter((k) => k !== `param:${parameter}`)),
      }))
    }
  },

  loadPersona: async (agentName, systemPrompt, findings) => {
    if (get().personas[agentName] || get().inflight.has(`persona:${agentName}`)) return
    const inflight = new Set(get().inflight)
    inflight.add(`persona:${agentName}`)
    set({ inflight })
    try {
      const res = await axios.post<PersonaExplanation>(
        '/api/explain/persona',
        { agent_name: agentName, system_prompt: systemPrompt, findings },
      )
      set((s) => ({
        personas: { ...s.personas, [agentName]: res.data },
        inflight: new Set([...s.inflight].filter((k) => k !== `persona:${agentName}`)),
      }))
    } catch {
      set((s) => ({
        inflight: new Set([...s.inflight].filter((k) => k !== `persona:${agentName}`)),
      }))
    }
  },

  loadChart: async (chartId, chartType, chartData, currentResults) => {
    if (get().charts[chartId] || get().inflight.has(`chart:${chartId}`)) return
    const inflight = new Set(get().inflight)
    inflight.add(`chart:${chartId}`)
    set({ inflight })
    try {
      const res = await axios.post<ChartExplanation>(
        '/api/explain/chart',
        {
          chart_id: chartId,
          chart_type: chartType,
          chart_data: chartData,
          current_results: currentResults,
        },
      )
      set((s) => ({
        charts: { ...s.charts, [chartId]: res.data },
        inflight: new Set([...s.inflight].filter((k) => k !== `chart:${chartId}`)),
      }))
    } catch {
      set((s) => ({
        inflight: new Set([...s.inflight].filter((k) => k !== `chart:${chartId}`)),
      }))
    }
  },

  loadQA: async (auditResults) => {
    if (Object.keys(get().qa).length > 0 || get().inflight.has('qa')) return
    const inflight = new Set(get().inflight)
    inflight.add('qa')
    set({ inflight })
    try {
      const res = await axios.post<Record<string, QAItemExplanation>>(
        '/api/explain/qa',
        { audit_results: auditResults },
      )
      set((s) => ({
        qa: res.data ?? {},
        inflight: new Set([...s.inflight].filter((k) => k !== 'qa')),
      }))
    } catch {
      set((s) => ({
        inflight: new Set([...s.inflight].filter((k) => k !== 'qa')),
      }))
    }
  },

  clear: () => set({
    terms: {}, parameters: {}, personas: {}, qa: {}, charts: {},
    termsLoaded: false, termsLoading: false, inflight: new Set(),
  }),
}))
