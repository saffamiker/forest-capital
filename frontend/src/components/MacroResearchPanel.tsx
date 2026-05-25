/**
 * MacroResearchPanel — surfaces the latest macro market research digest
 * the council and academic_review prompts inject as a CURRENT MACRO
 * CONDITIONS block. FEATURE 2 (May 21 2026), Commit 4/5.
 *
 * May 24 2026 — P1 + P2 hotfix:
 *   P1 — digest state moved into macroDigestStore (Zustand) with a
 *        5-minute stale-while-revalidate window. The previous panel
 *        held the digest in useState, so every dashboard mount fired
 *        a fresh GET. Now the cached digest renders instantly on
 *        re-mount and a background refresh only runs past the
 *        freshness window.
 *   P2 — panel is CONDENSED BY DEFAULT. The collapsed view shows
 *        the regime read line + the top 2 key signals as a single
 *        compact card so the dashboard header reads cleanly. An
 *        expand toggle reveals the full key-signals list +
 *        implications + source links.
 *
 * The "Run now" button is sysadmin-only (useIsSysadmin) because it
 * bypasses the 24h freshness gate and burns the Sonnet + web_search
 * budget on demand. Viewers see the latest digest but not the trigger.
 *
 * May 28 2026 — UAT report: the button was reaching non-sysadmin
 * users on the dashboard. The previous gate (TeamGate with
 * permission="manage_users") was functionally correct but the
 * useIsSysadmin hook is the pattern other admin controls use
 * (Settings → Users, Settings → Admin in pages/Settings.tsx). The
 * swap aligns the gating pattern across the codebase: every
 * admin-only control reads `useIsSysadmin()` and conditionally
 * renders, not a TeamGate wrap.
 */
import { useCallback, useEffect, useState } from 'react'
import { Newspaper, RefreshCw, ExternalLink, ChevronDown } from 'lucide-react'
import { useIsSysadmin } from '../hooks/usePermissions'
import { useMacroDigestStore, type MacroSignal } from '../stores/macroDigestStore'


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
  // Collapsed by default per the P2 spec — Dr. Panttser sees a
  // compact summary card, not the full digest.
  const [expanded, setExpanded] = useState(false)
  // Sysadmin gate for the Run Now button — same hook the Settings
  // page's Users + Admin sections use. Non-sysadmin users see the
  // digest with current data but no refresh trigger.
  const isSysadmin = useIsSysadmin()

  const latest = useMacroDigestStore((s) => s.latest)
  const loading = useMacroDigestStore((s) => s.loading)
  const error = useMacroDigestStore((s) => s.error)
  const triggering = useMacroDigestStore((s) => s.triggeringRunNow)
  const load = useMacroDigestStore((s) => s.load)
  const runNow = useMacroDigestStore((s) => s.runNow)

  // Stale-while-revalidate on every mount. load() short-circuits
  // when the cached entry is under the freshness window so this
  // hits the network at most once per 5 minutes regardless of how
  // many times the dashboard mounts.
  useEffect(() => {
    void load()
  }, [load])

  const onRunNow = useCallback(() => { void runNow() }, [runNow])

  if (loading && !latest) {
    return (
      <div className="rounded-lg border border-border bg-navy-800 p-4"
           data-testid="macro-research-panel">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Newspaper className="w-4 h-4 animate-pulse" />
          Loading current macro conditions…
        </div>
      </div>
    )
  }

  const digest = latest?.digest ?? null
  const ageH = ageHours(digest?.generated_at)
  const stale = ageH !== null && ageH > 24
  // Top 2 signals are surfaced inline in the collapsed view so
  // Dr. Panttser sees the most salient signals without expanding.
  // The signals are already ranked by the research engine
  // (high-priority first).
  const topSignals: MacroSignal[] = (digest?.key_signals ?? []).slice(0, 2)

  return (
    <div className="rounded-lg border border-border bg-navy-800 overflow-hidden"
         data-tour="macro-research-panel"
         data-testid="macro-research-panel">
      <div className="flex items-start justify-between gap-3 px-4 py-3
                      border-b border-border">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          data-testid="macro-expand-toggle"
          aria-expanded={expanded}
          className="flex items-center gap-2 text-left flex-1 min-h-[36px]">
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
        {/* Sysadmin-only: bypasses the 24h freshness gate and burns
            the Sonnet + web_search budget on demand. Non-sysadmin
            users see the digest with current data but no trigger. */}
        {isSysadmin && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onRunNow() }}
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
        )}
      </div>

      {/* CONDENSED VIEW — always rendered when there's a digest.
          Shows regime read + top 2 signals inline so the dashboard
          header reads cleanly. The full key-signals list + every
          implication + source link is behind the expand toggle. */}
      {!expanded && digest && (
        <div data-testid="macro-condensed" className="px-4 py-3 space-y-2">
          {digest.regime_implication && (
            <p className="text-xs text-slate-200 leading-relaxed">
              <span className="text-2xs uppercase tracking-wider text-muted
                               mr-1.5">
                Regime
              </span>
              {digest.regime_implication}
            </p>
          )}
          {topSignals.length > 0 && (
            <ul className="space-y-1">
              {topSignals.map((sig, i) => (
                <li key={i}
                    className="text-xs text-slate-300 leading-snug
                               flex items-baseline gap-1.5">
                  <span className="text-[10px] uppercase tracking-wider
                                   text-muted px-1.5 py-0.5 rounded
                                   bg-navy-900 border border-border
                                   shrink-0">
                    {CATEGORY_LABELS[sig.category] ?? sig.category}
                  </span>
                  <span className="line-clamp-1">{sig.signal}</span>
                </li>
              ))}
            </ul>
          )}
          {digest.key_signals.length > topSignals.length && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              data-testid="macro-show-all"
              className="text-2xs text-electric hover:underline">
              + {digest.key_signals.length - topSignals.length} more signal
              {digest.key_signals.length - topSignals.length === 1 ? '' : 's'}
            </button>
          )}
        </div>
      )}

      {/* EXPANDED VIEW — the original full-digest render. Reached via
          the expand toggle. Includes the long summary paragraph,
          every key signal with its implication and source URL, and
          the regime-read paragraph. */}
      {expanded && (
        <div className="px-4 py-3 space-y-3" data-testid="macro-expanded">
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

          <button
            type="button"
            onClick={() => setExpanded(false)}
            data-testid="macro-show-less"
            className="text-2xs text-electric hover:underline">
            − Show less
          </button>
        </div>
      )}
    </div>
  )
}
