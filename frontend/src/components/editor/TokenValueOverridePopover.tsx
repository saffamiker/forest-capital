/**
 * TokenValueOverridePopover.tsx -- June 28 2026.
 *
 * Anchored popover that lets the author explicitly override a
 * platform-managed token_value. Opens on click of the parent
 * NodeView; submits via onApply (writes override + reason to
 * the node attrs) or onCancel (no-op).
 *
 * Clear-override action is shown only when the node already
 * carries an override -- lets the author revert to the platform-
 * managed value with one click.
 *
 * Override semantics (defined by PR-DM-Lite):
 *   - attrs.override holds the manually-entered value string
 *   - light refresh + apply-updates SKIP nodes with override
 *     set (per draft_token_upgrade.apply_token_updates)
 *   - export renders attrs.override in place of attrs.resolved
 *   - review panel labels these "Manually overridden" + skips
 *     auto-update
 */
import { useState } from 'react'
import { X } from 'lucide-react'


export interface TokenValueOverridePopoverProps {
  token:          string
  resolved:       string
  override:       string | null
  overrideReason: string | null
  onApply:        (value: string, reason: string) => void
  onClear:        (() => void) | null
  onCancel:       () => void
}


export function TokenValueOverridePopover(
  props: TokenValueOverridePopoverProps,
): React.ReactElement {
  const [value, setValue] = useState(
    props.override ?? props.resolved)
  const [reason, setReason] = useState(
    props.overrideReason ?? '')

  const handleApply = (e: React.FormEvent): void => {
    e.preventDefault()
    if (!value.trim()) return
    props.onApply(value.trim(), reason.trim())
  }

  return (
    <div
      data-testid="token-value-override-popover"
      className="absolute z-40 top-full left-0 mt-1
                 w-72 rounded border bg-slate-900
                 border-slate-600 p-3 shadow-lg
                 text-2xs text-slate-200">
      <button
        type="button"
        onClick={props.onCancel}
        aria-label="Close"
        className="absolute top-1.5 right-1.5 text-muted
                   hover:text-white">
        <X className="w-3 h-3" />
      </button>
      <div className="font-medium text-slate-100 mb-1.5">
        Override platform value
      </div>
      <div className="text-muted mb-2 font-mono text-2xs">
        {props.token}
      </div>
      <form onSubmit={handleApply} className="space-y-2">
        <label className="block">
          <span className="text-muted">Value</span>
          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            data-testid="override-value-input"
            className="block w-full mt-0.5 px-2 py-1
                       rounded bg-slate-800 border
                       border-slate-600 text-slate-100
                       font-mono text-xs
                       focus:border-electric outline-none" />
        </label>
        <label className="block">
          <span className="text-muted">
            Reason (optional)
          </span>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid="override-reason-input"
            placeholder="e.g. 4dp precision for appendix table"
            className="block w-full mt-0.5 px-2 py-1
                       rounded bg-slate-800 border
                       border-slate-600 text-slate-100
                       text-2xs
                       focus:border-electric outline-none" />
        </label>
        <div className="flex items-center justify-between
                        gap-2 pt-1">
          {props.onClear ? (
            <button
              type="button"
              onClick={props.onClear}
              data-testid="override-clear-button"
              className="text-2xs text-muted
                         hover:text-warning underline">
              Clear override
            </button>
          ) : <span />}
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={props.onCancel}
              data-testid="override-cancel-button"
              className="px-2 py-1 text-2xs
                         border border-slate-600 rounded
                         hover:bg-slate-800">
              Cancel
            </button>
            <button
              type="submit"
              data-testid="override-apply-button"
              className="px-2 py-1 text-2xs font-medium
                         bg-warning/15 border border-warning/40
                         text-warning rounded
                         hover:bg-warning/25">
              Apply override
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}

export default TokenValueOverridePopover
