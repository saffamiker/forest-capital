/**
 * LiveDataHashBanner -- June 25 2026.
 *
 * Persistent strip at the top of Reports & Deliverables surfacing
 * the live platform data hash and any draft mismatches.
 *
 *   green  "All drafts current" -- every current draft's data_hash
 *          equals the live strategy_hash. Hash chip is click-to-copy.
 *
 *   amber  "Hash mismatch detected" -- one or more drafts were
 *          generated against an older dataset. Lists the stale docs
 *          ("Executive Brief — generated against d0b1339e (current:
 *          f2e87dec)") and links to Light Refresh on the same page.
 *
 * Doc types with NO current draft are excluded from the mismatch
 * list -- nothing to compare against.
 *
 * Renders nothing while the live hash is unavailable so the page
 * doesn't flash an indeterminate state on first mount.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  AlertTriangle, CheckCircle, Copy, Check,
} from 'lucide-react'


export type LiveDataHashBannerDocType =
  | 'executive_brief'
  | 'analytical_appendix'
  | 'presentation_deck'
  | 'presentation_script'

const DOC_LABELS: Record<LiveDataHashBannerDocType, string> = {
  executive_brief:     'Executive Brief',
  analytical_appendix: 'Analytical Appendix',
  presentation_deck:   'Final Presentation Deck',
  presentation_script: 'Presentation Script',
}


interface DraftRow {
  document_type: string
  is_current?:   boolean
  data_hash?:    string | null
}


export default function LiveDataHashBanner(): React.ReactElement | null {
  const [liveHash, setLiveHash] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<DraftRow[]>([])
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false
    axios.get<{ current_data_hash?: string | null }>(
      '/api/v1/audit/runs/latest')
      .then((res) => {
        if (cancelled) return
        setLiveHash(res.data?.current_data_hash ?? null)
      })
      .catch(() => { if (!cancelled) setLiveHash(null) })
    axios.get<{ drafts: DraftRow[] }>(
      '/api/v1/documents/drafts')
      .then((res) => {
        if (cancelled) return
        setDrafts(res.data?.drafts ?? [])
      })
      .catch(() => { if (!cancelled) setDrafts([]) })
    return () => { cancelled = true }
  }, [])

  if (!liveHash) return null

  const currentDrafts = drafts.filter(
    (d) => d.is_current !== false
      && d.data_hash
      && d.document_type in DOC_LABELS)

  const staleDocs = currentDrafts
    .filter((d) => d.data_hash !== liveHash)
    .map((d) => ({
      label: DOC_LABELS[
        d.document_type as LiveDataHashBannerDocType],
      draftHash: (d.data_hash || '').slice(0, 8),
    }))

  const allCurrent = (
    currentDrafts.length > 0 && staleDocs.length === 0)

  const handleCopy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(liveHash)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable -- silently no-op */
    }
  }

  const handleLightRefreshScroll = (): void => {
    const target = document.querySelector(
      '[data-section-id="light-refresh"]')
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  return (
    <section
      data-testid="live-data-hash-banner"
      data-section-id="live-data-hash-banner"
      data-section-label="Live Data Hash"
      className={
        staleDocs.length > 0
          ? 'rounded border border-warning/40 bg-warning/5 p-3'
          : 'rounded border border-success/30 bg-success/5 p-3'}>
      <div className="flex flex-wrap items-center gap-2 text-xs
                       text-slate-200">
        {staleDocs.length > 0
          ? <AlertTriangle
              className="w-4 h-4 text-warning shrink-0" />
          : <CheckCircle
              className="w-4 h-4 text-success shrink-0" />}
        <span className="font-semibold">
          {staleDocs.length > 0
            ? 'Hash mismatch detected'
            : (allCurrent
              ? 'All drafts current'
              : 'Live platform data hash')}
        </span>
        <span className="text-muted">
          Live platform data hash:
        </span>
        <button
          type="button"
          onClick={() => { void handleCopy() }}
          data-testid="live-data-hash-copy"
          title="Click to copy the full hash"
          className="font-mono text-electric hover:text-blue-400
                      inline-flex items-center gap-1">
          {liveHash.slice(0, 8)}
          {copied
            ? <Check className="w-3 h-3 text-success" />
            : <Copy className="w-3 h-3 opacity-60" />}
        </button>
      </div>

      {staleDocs.length > 0 && (
        <div className="mt-2 text-2xs text-slate-300 space-y-0.5"
          data-testid="live-data-hash-stale-list">
          {staleDocs.map((d) => (
            <div key={d.label} className="flex items-start gap-1.5">
              <span className="text-warning">•</span>
              <span>
                <strong>{d.label}</strong> — generated against{' '}
                <span className="font-mono">{d.draftHash}</span>{' '}
                <span className="text-muted">
                  (current:{' '}
                  <span className="font-mono">
                    {liveHash.slice(0, 8)}
                  </span>)
                </span>
              </span>
            </div>
          ))}
          <button
            type="button"
            onClick={handleLightRefreshScroll}
            data-testid="live-data-hash-refresh-link"
            className="text-electric hover:underline mt-1">
            Run Light Refresh →
          </button>
        </div>
      )}
    </section>
  )
}
