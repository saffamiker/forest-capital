/**
 * MacroResearchPanel — surfaces the latest macro market research digest
 * the council and academic_review prompts inject as a CURRENT MACRO
 * CONDITIONS block. FEATURE 2 (May 21 2026), Commit 4/5.
 *
 * Two reasons the panel sits on the dashboard:
 *  1. Transparency — the user can see exactly what current-conditions
 *     context the agents are reasoning against.
 *  2. Verification — every key signal carries the source URL the
 *     web_search tool returned. The user (and the FNA 670 panel) can
 *     click through and verify the citation.
 *
 * STATES:
 *  - loading                — initial fetch in flight
 *  - empty (no digest yet)  — cold deploy state; the startup hook will
 *                             produce a digest in minutes
 *  - failed (latest=null
 *           but had a run)  — the last research run errored; "Try
 *                             again" surfaced to sysadmins only
 *  - normal                 — digest summary + signals + regime
 *                             implication + generated_at timestamp
 *
 * The "Run now" button is sysadmin-only (TeamGate with permission
 * "manage_users") because it bypasses the 24h freshness gate and
 * burns the Sonnet + web_search budget on demand. Viewers see the
 * latest digest but not the trigger.
 */
import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { Newspaper, RefreshCw, ExternalLink, ChevronDown } from 'lucide-react'
import TeamGate from './TeamGate'

interface MacroSignal {
  category:    string
  signal:      string
  implication: string
  source_url:  string
}

interface MacroDigest {
  id:                 number
  generated_at:       string | null
  triggered_by:       string
  summary_text:       string
  regime_implication: string
  key_signals:        MacroSignal[]
  citation_urls:      string[]
  model:              string | null
  metadata:           Record<string, unknown>
}

interface LatestResponse {
  digest:            MacroDigest | null
  last_completed_at: string | null
}

const CATEGORY_LABELS: Record<string, string> = {
  monetary_policy: 'Monetary Policy',
  inflation:       'Inflation',
  growth:          'Growth',
  rates:           'Rates',
  credit:          'Credit',
  volatility:      'Volatility',
  geopolitical:    'Geopolitical',
  other:           'Other',
}

function formatGeneratedAt(iso: string | null | undefined): string {
  if (!iso) return 'unknown'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function ageHours(iso: string | null | undefined): number | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return (Date.now() - d.getTime()) / 3600000
}


export default function MacroResearchPanel() {
  const [data, setData] = useState<LatestResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [expanded, setExpanded] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setError(null)
      const r = await axios.get<LatestResponse>('/api/v1/research/latest')
      setData(r.data)
    } catch (exc) {
      setError('Could not load the macro digest.')
      // Leave whatever `data` was — a transient fetch failure should
      // not blank the dashboard.
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const runNow = useCallback(async () => {
    setTriggering(true)
    try {
      await axios.post('/api/v1/research/run')
      // Poll once after a short delay so the user sees the new digest
      // without a full page refresh. The model call takes 30-90s so
      // we poll three times at 30s intervals before giving up the
      // "fresh result will appear" promise.
      let attempts = 0
      const poll = async () => {
        attempts += 1
        await refresh()
        if (attempts < 3 && data?.last_completed_at ===
            (await axios.get<LatestResponse>('/api/v1/research/latest')
                .then((r) => r.data.last_completed_at))) {
          setTimeout(() => void poll(), 30000)
        }
      }
      setTimeout(() => void poll(), 30000)
    } catch (exc) {
      setError('Could not start a research run.')
    } finally {
      setTriggering(false)
    }
  }, [refresh, data?.last_completed_at])

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-navy-800 p-4">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Newspaper className="w-4 h-4 animate-pulse" />
          Loading current macro conditions…
        </div>
      </div>
    )
  }

  const digest = data?.digest ?? null
  const ageH = ageHours(digest?.generated_at)
  const stale = ageH !== null && ageH > 24

  return (
    <div className="rounded-lg border border-border bg-navy-800 overflow-hidden"
         data-tour="macro-research-panel">
      <div className="flex items-start justify-between gap-3 px-4 py-3
                      border-b border-border">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-2 text-left flex-1 min-h-[36px]"
          aria-expanded={expanded}
        >
          <Newspaper className="w-4 h-4 text-electric shrink-0" />
          <span className="text-sm font-semibold text-white">
            Current Macro Conditions
          </span>
          {digest && (
            <span className={`text-xs ${stale ? 'text-warning' : 'text-muted'}`}>
              · as of {formatGeneratedAt(digest.generated_at)}
              {stale && ' (stale)'}
            </span>
          )}
          <ChevronDown
            className={`w-3 h-3 text-muted ml-auto transition-transform ${
              expanded ? 'rotate-180' : ''
            }`}
          />
        </button>
        <TeamGate permission="manage_users" block={false}>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); void runNow() }}
            disabled={triggering}
            className="text-xs px-2.5 py-1.5 rounded border border-electric/30
                       bg-electric/10 text-electric hover:bg-electric/20
                       disabled:opacity-50 disabled:cursor-not-allowed
                       flex items-center gap-1.5 min-h-[28px]"
            title="Forces a fresh research run (bypasses 24h cache)"
          >
            <RefreshCw className={`w-3 h-3 ${triggering ? 'animate-spin' : ''}`} />
            {triggering ? 'Running…' : 'Run now'}
          </button>
        </TeamGate>
      </div>

      {expanded && (
        <div className="px-4 py-3 space-y-3">
          {error && (
            <p className="text-xs text-warning">{error}</p>
          )}

          {!digest && (
            <p className="text-xs text-muted italic">
              No completed digest yet. The first research run produces
              one within a few minutes of platform startup.
            </p>
          )}

          {digest && digest.summary_text && (
            <p className="text-sm text-slate-200 leading-relaxed">
              {digest.summary_text}
            </p>
          )}

          {digest && digest.key_signals.length > 0 && (
            <div>
              <h4 className="text-2xs uppercase tracking-wider text-muted mb-1.5">
                Key signals
              </h4>
              <ul className="space-y-2">
                {digest.key_signals.map((sig, i) => (
                  <li key={i}
                      className="text-xs text-slate-300 leading-relaxed">
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-[10px] uppercase tracking-wider
                                       text-muted px-1.5 py-0.5 rounded
                                       bg-navy-900 border border-border
                                       shrink-0">
                        {CATEGORY_LABELS[sig.category] ?? sig.category}
                      </span>
                      <span>{sig.signal}</span>
                    </div>
                    {sig.implication && (
                      <div className="ml-1 mt-0.5 text-muted">
                        Implication: {sig.implication}
                      </div>
                    )}
                    {sig.source_url && (
                      <a href={sig.source_url} target="_blank"
                         rel="noopener noreferrer"
                         className="ml-1 inline-flex items-center gap-1
                                    text-electric hover:underline">
                        <ExternalLink className="w-2.5 h-2.5" />
                        source
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {digest && digest.regime_implication && (
            <div>
              <h4 className="text-2xs uppercase tracking-wider text-muted mb-1">
                Regime read
              </h4>
              <p className="text-xs text-slate-300 leading-relaxed">
                {digest.regime_implication}
              </p>
            </div>
          )}

          {digest && (
            <p className="text-2xs text-muted pt-1 border-t border-border">
              Agents inject this digest as a CURRENT MACRO CONDITIONS
              block. Citations verified via web_search.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
