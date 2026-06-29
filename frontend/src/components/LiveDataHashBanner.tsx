/**
 * LiveDataHashBanner -- June 25 2026.
 *
 * Persistent strip at the top of Reports & Deliverables surfacing
 * the live platform data hash and any draft mismatches.
 *
 *   green  "All drafts current" -- every current draft's data_hash
 *          equals the live strategy_hash. Hash chip is click-to-copy.
 *
 *   green  "All drafts locked to submission freeze hash" -- freeze
 *          active and every current draft matches the freeze hash.
 *          NO "Run Light Refresh" prompt (June 28 2026 hotfix --
 *          mirrors the LightRefreshButton freeze-aware fix in
 *          PR #459 so the Reports page doesn't show a misleading
 *          "Data stale" warning when drafts are correctly on
 *          the freeze hash).
 *
 *   amber  "Hash mismatch detected" -- one or more drafts were
 *          generated against an older dataset (or under freeze,
 *          against a hash other than the freeze hash). Lists the
 *          stale docs ("Executive Brief — generated against
 *          d0b1339e (current: f2e87dec)") and links to Light
 *          Refresh on the same page.
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


// June 29 2026 -- prefix-tolerant hash equality. The platform
// stores draft.data_hash with the SAME canonical 16-char form
// the cache writes (tools.cache._compute_data_hash takes
// SHA256[:16]), but some legacy drafts persisted only the
// 8-char display form, and the freeze config row stores the
// full 16-char value. A strict === comparison miscounted
// legacy 8-char drafts as 'drifted' when they were on the
// same cache state. Returns True when either hash is a
// prefix of the other (case-insensitive) OR they're equal.
// Empty / null returns False.
function _hashesMatch(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  if (!a || !b) return false
  const al = a.toLowerCase()
  const bl = b.toLowerCase()
  if (al === bl) return true
  const longer = al.length >= bl.length ? al : bl
  const shorter = al.length >= bl.length ? bl : al
  if (shorter.length < 4) return false
  return longer.startsWith(shorter)
}


// June 28 2026 hotfix -- submission-freeze status shape returned
// by GET /api/v1/admin/submission-status. Mirrors the FreezeStatus
// interface in LightRefreshButton.tsx (PR #459). Available to any
// authenticated user; fail-open to freeze_active=false on fetch
// failure -- the banner shows the legacy live-hash comparison in
// that case.
interface FreezeStatus {
  freeze_active:     boolean
  freeze_hash:       string | null
  current_live_hash: string
}


export default function LiveDataHashBanner(): React.ReactElement | null {
  const [liveHash, setLiveHash] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<DraftRow[]>([])
  const [copied, setCopied] = useState(false)
  // June 28 2026 hotfix -- freeze-aware hash comparison. Without
  // this, the banner compared draft_hash to live_hash + flagged
  // every draft 'stale' under freeze even when drafts correctly
  // carried the freeze hash. Mirrors the LightRefreshButton fix
  // from PR #459.
  const [freezeStatus, setFreezeStatus]
    = useState<FreezeStatus | null>(null)

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
    axios.get<FreezeStatus>('/api/v1/admin/submission-status')
      .then((res) => {
        if (cancelled) return
        setFreezeStatus(res.data ?? null)
      })
      .catch(() => { if (!cancelled) setFreezeStatus(null) })
    return () => { cancelled = true }
  }, [])

  if (!liveHash) return null

  // June 28 2026 hotfix -- comparison hash = freeze hash when
  // freeze active, live hash otherwise. The stale-docs list
  // checks against this comparison hash, not the raw live hash,
  // so a draft correctly carrying the freeze hash does NOT get
  // flagged stale + the "Run Light Refresh" prompt does NOT
  // fire.
  const freezeActive = Boolean(
    freezeStatus?.freeze_active && freezeStatus.freeze_hash)
  const comparisonHash: string = (
    freezeActive && freezeStatus?.freeze_hash
      ? freezeStatus.freeze_hash
      : liveHash)

  const currentDrafts = drafts.filter(
    (d) => d.is_current !== false
      && d.data_hash
      && d.document_type in DOC_LABELS)

  const staleDocs = currentDrafts
    .filter((d) => !_hashesMatch(d.data_hash, comparisonHash))
    .map((d) => ({
      label: DOC_LABELS[
        d.document_type as LiveDataHashBannerDocType],
      draftHash: (d.data_hash || '').slice(0, 8),
    }))

  const allCurrent = (
    currentDrafts.length > 0 && staleDocs.length === 0)

  const handleCopy = async (): Promise<void> => {
    try {
      // June 28 2026 hotfix -- copy the FREEZE hash when freeze
      // is active, so the operator's clipboard carries the value
      // the comparison + the banner header actually reference.
      await navigator.clipboard.writeText(
        freezeActive && freezeStatus?.freeze_hash
          ? freezeStatus.freeze_hash
          : liveHash)
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
            ? (freezeActive
              ? 'Drafts drifted from freeze hash'
              : 'Hash mismatch detected')
            : (allCurrent
              ? (freezeActive
                ? 'All drafts locked to submission freeze hash'
                : 'All drafts current')
              : (freezeActive
                ? 'Submission freeze active'
                : 'Live platform data hash'))}
        </span>
        <span className="text-muted">
          {freezeActive ? 'Freeze hash:' : 'Live platform data hash:'}
        </span>
        <button
          type="button"
          onClick={() => { void handleCopy() }}
          data-testid="live-data-hash-copy"
          title="Click to copy the full hash"
          className="font-mono text-electric hover:text-blue-400
                      inline-flex items-center gap-1">
          {(freezeActive && freezeStatus?.freeze_hash
            ? freezeStatus.freeze_hash
            : liveHash).slice(0, 8)}
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
                  ({freezeActive ? 'freeze' : 'current'}:{' '}
                  <span className="font-mono">
                    {comparisonHash.slice(0, 8)}
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
            {freezeActive
              ? 'Regenerate against freeze hash →'
              : 'Run Light Refresh →'}
          </button>
        </div>
      )}
    </section>
  )
}
