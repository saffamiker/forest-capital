/**
 * LightRefreshButton -- June 25 2026.
 *
 * Self-service analytics refresh trigger for team members.
 * Mounts on the Reports page next to Key Metrics.
 *
 * What it does: POSTs /api/v1/data/light-refresh and renders
 * the per-step status report inline. Does NOT touch story
 * plans, drafts, or document content -- regeneration of any
 * document remains the user's explicit action on the
 * Generate / Regenerate tile.
 *
 * Hash-change warning: when the strategy_hash returned by the
 * refresh differs from the hash the page loaded with, surface
 * a non-blocking banner telling the user new data was
 * detected and recommending a document regen.
 *
 * Gated behind TeamGate (generate_documents permission --
 * same permission the doc generators use, so anyone with
 * regen rights can self-serve a refresh without sysadmin
 * escalation).
 */
import { useState } from 'react'
import axios from 'axios'
import {
  CheckCircle, AlertTriangle, RefreshCw, Loader2,
} from 'lucide-react'

import TeamGate from './TeamGate'


interface RefreshStep {
  step:           string
  ok:             boolean
  error?:         string
  data_hash?:     string
  strategy_hash?: string
  n_strategies?:  number
}

interface RefreshResponse {
  ok:             boolean
  strategy_hash:  string | null
  steps:          RefreshStep[]
  note?:          string
}


export interface LightRefreshButtonProps {
  /** The data hash currently shown on the page (e.g. from Key
   *  Metrics). When the refresh returns a different hash, the
   *  panel surfaces a "new data detected -- consider regenerating"
   *  warning so the user knows the docs may be stale. */
  currentDataHash?: string | null | undefined
}


export default function LightRefreshButton(
  { currentDataHash }: LightRefreshButtonProps = {},
): React.ReactElement {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<RefreshResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleRefresh = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const res = await axios.post<RefreshResponse>(
        '/api/v1/data/light-refresh')
      setResult(res.data)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Light refresh failed.'
      // The detail can be a structured object (steps + blocked_at)
      // -- stringify for display rather than rendering [object Object].
      setError(
        typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setBusy(false)
    }
  }

  const hashChanged = (
    result !== null
    && result.strategy_hash !== null
    && currentDataHash !== null
    && currentDataHash !== undefined
    && result.strategy_hash !== currentDataHash)

  return (
    <section
      data-testid="light-refresh-button"
      data-section-id="light-refresh"
      data-section-label="Light Refresh"
      className="card p-4 space-y-3">
      <div>
        <h2 className="text-white font-semibold text-sm flex
                       items-center gap-1.5">
          <RefreshCw className="w-4 h-4 text-electric" />
          Light Refresh
        </h2>
        <p className="text-xs text-muted mt-1 leading-relaxed">
          Re-runs the analytics cache (backtester, academic
          analytics, OOS cost sensitivity) for the current data
          hash. Does NOT touch story plans, drafts, or document
          content. Use when you want the latest market data
          reflected in Key Metrics before regenerating any
          deliverable.
        </p>
      </div>

      <TeamGate
        block
        permission="generate_documents"
        tooltip="Light refresh is available to team members with document generation rights">
        <button
          type="button"
          onClick={() => { void handleRefresh() }}
          disabled={busy}
          data-testid="light-refresh-run"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded
                     text-xs font-semibold bg-electric text-white
                     hover:bg-blue-500 disabled:opacity-50
                     disabled:cursor-not-allowed">
          {busy
            ? <><Loader2 className="w-3 h-3 animate-spin" />
                Refreshing analytics…</>
            : <><RefreshCw className="w-3 h-3" />
                Run Light Refresh</>}
        </button>
      </TeamGate>

      {error && (
        <div
          data-testid="light-refresh-error"
          className="text-2xs text-danger flex items-start gap-1.5
                     rounded border border-danger/30 bg-danger/5
                     p-2.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div data-testid="light-refresh-result"
          className="space-y-2">
          {hashChanged && (
            <div
              data-testid="light-refresh-hash-changed-warning"
              className="rounded border border-warning/40
                         bg-warning/5 p-2.5 text-2xs text-warning
                         leading-relaxed flex items-start gap-1.5">
              <AlertTriangle
                className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                New data detected -- hash updated. Consider
                regenerating documents to reflect the latest
                figures.
              </span>
            </div>
          )}
          {!hashChanged && result.ok && (
            <div className="text-2xs text-success flex items-start
                            gap-1.5">
              <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                Refresh complete. Data hash unchanged
                {result.strategy_hash
                  && (
                    <>
                      {' ('}
                      <span className="font-mono">
                        {result.strategy_hash.slice(0, 12)}…
                      </span>
                      {')'}
                    </>)}.
              </span>
            </div>
          )}
          <ul className="text-2xs text-slate-300 space-y-0.5">
            {result.steps.map((s, i) => (
              <li key={i}
                className="flex items-start gap-1.5">
                {s.ok
                  ? <CheckCircle
                      className="w-3 h-3 text-success shrink-0
                                 mt-0.5" />
                  : <AlertTriangle
                      className="w-3 h-3 text-warning shrink-0
                                 mt-0.5" />}
                <span>
                  <span className="font-semibold">{s.step}</span>
                  {s.ok ? ' -- ok' : ' -- '}
                  {!s.ok && (
                    <span className="text-danger">{s.error}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
