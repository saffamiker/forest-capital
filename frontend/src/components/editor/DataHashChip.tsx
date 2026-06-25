/**
 * DataHashChip -- June 25 2026.
 *
 * Compact status chip rendered next to the editor's Export button.
 * Reads the draft's data_hash against the current live strategy
 * hash from /api/v1/audit/runs/latest (the same source verify-all
 * uses) and surfaces:
 *
 *   green  "Data current"
 *   amber  "Data stale — Light Refresh recommended"
 *
 * Clicking the amber chip toggles a tooltip explaining what Light
 * Refresh does + linking to the Reports page where the button
 * lives. Bob and Molly get immediate visibility into draft
 * freshness without going to Reports.
 *
 * Renders nothing when either hash is unavailable so the chip
 * never flashes a misleading state on first mount.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { CheckCircle, AlertTriangle, X } from 'lucide-react'


export interface DataHashChipProps {
  /** The current draft's persisted data_hash. */
  draftDataHash: string | null | undefined
}


export default function DataHashChip(
  { draftDataHash }: DataHashChipProps,
): React.ReactElement | null {
  const navigate = useNavigate()
  const [liveHash, setLiveHash] = useState<string | null>(null)
  const [tooltipOpen, setTooltipOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    axios.get<{ current_data_hash?: string | null }>(
      '/api/v1/audit/runs/latest')
      .then((res) => {
        if (cancelled) return
        setLiveHash(res.data?.current_data_hash ?? null)
      })
      .catch(() => { if (!cancelled) setLiveHash(null) })
    return () => { cancelled = true }
  }, [draftDataHash])

  if (!draftDataHash || !liveHash) return null

  const match = draftDataHash === liveHash
  if (match) {
    return (
      <span
        data-testid="data-hash-chip-current"
        title={`Data hash ${draftDataHash.slice(0, 12)}… matches the analytics cache. No refresh needed.`}
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-success/15
                   border border-success/40 text-success">
        <CheckCircle className="w-3 h-3" />
        Data current
      </span>
    )
  }

  return (
    <span className="relative inline-block">
      <button
        type="button"
        onClick={() => setTooltipOpen(!tooltipOpen)}
        data-testid="data-hash-chip-stale"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                    rounded text-2xs font-medium bg-warning/15
                    border border-warning/40 text-warning
                    hover:bg-warning/25">
        <AlertTriangle className="w-3 h-3" />
        Data stale — Light Refresh recommended
      </button>
      {tooltipOpen && (
        <div
          data-testid="data-hash-chip-tooltip"
          className="absolute right-0 top-full mt-1 z-30 w-80
                     card p-3 text-2xs text-slate-300
                     leading-relaxed">
          <button
            type="button"
            onClick={() => setTooltipOpen(false)}
            aria-label="Close"
            className="absolute top-1.5 right-1.5 text-muted
                       hover:text-white">
            <X className="w-3 h-3" />
          </button>
          <p className="font-semibold text-white">
            This draft was generated against an older dataset.
          </p>
          <p className="mt-1">
            <span className="font-mono text-muted">draft </span>
            <span className="font-mono">
              {draftDataHash.slice(0, 14)}…
            </span>
          </p>
          <p>
            <span className="font-mono text-muted">live  </span>
            <span className="font-mono">
              {liveHash.slice(0, 14)}…
            </span>
          </p>
          <p className="mt-2">
            Run <strong>Light Refresh</strong> on the Reports page
            to refresh the analytics cache. The refresh re-runs the
            backtester, academic analytics, and cost sensitivity
            chain for the current data hash. If any figures
            changed, regenerate the document afterward.
          </p>
          <button
            type="button"
            onClick={() => {
              setTooltipOpen(false)
              navigate('/reports')
            }}
            className="mt-2 text-electric hover:underline">
            Open Reports page →
          </button>
        </div>
      )}
    </span>
  )
}
