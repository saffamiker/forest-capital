/**
 * TokenValueNodeView.tsx -- June 28 2026.
 *
 * Rich React NodeView for the token_value inline node
 * (PR-DM-Rich, follow-up to PR-DM-Lite's pass-through extension).
 *
 * Visual contract:
 *   - Platform-managed value: subtle green underline + tiny lock
 *     icon. Hover surfaces a tooltip with the source token,
 *     data_hash (8-char prefix), and last-updated timestamp.
 *   - Manually-overridden value: amber underline + 'Manually
 *     overridden' badge in the tooltip. Light refresh + apply
 *     skip this node.
 *
 * Interaction:
 *   - Click the node to open the override popover (see
 *     TokenValueOverridePopover). The popover lets the author
 *     enter an explicit override value + reason. Cancel keeps
 *     the resolved value untouched.
 *   - atom: true (declared in tokenValueExtension) means
 *     keyboard cursor jumps over the node; backspace removes
 *     the whole node. The author cannot accidentally type mid-
 *     token.
 *
 * The NodeView replaces the renderHTML output of the
 * pass-through extension when registered via addNodeView in
 * the extension config. It does NOT change the underlying
 * node attrs schema -- this is a purely presentational layer
 * on top of the same attribute set.
 */
import { useState, useRef, useEffect } from 'react'
import { NodeViewWrapper } from '@tiptap/react'
import type { NodeViewProps } from '@tiptap/react'
import { Lock } from 'lucide-react'

import { TokenValueOverridePopover } from './TokenValueOverridePopover'


export default function TokenValueNodeView(
  props: NodeViewProps,
): React.ReactElement {
  const attrs = props.node.attrs as {
    token?:           string
    resolved?:        string
    resolved_at?:     string
    data_hash?:       string
    override?:        string | null
    override_by?:     string | null
    override_at?:     string | null
    override_reason?: string | null
  }

  const [tooltipOpen, setTooltipOpen] = useState(false)
  const [popoverOpen, setPopoverOpen] = useState(false)
  const wrapperRef = useRef<HTMLSpanElement>(null)

  // Close popover on outside click.
  useEffect(() => {
    if (!popoverOpen) return
    const handler = (e: MouseEvent): void => {
      if (wrapperRef.current
          && !wrapperRef.current.contains(e.target as Node)) {
        setPopoverOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [popoverOpen])

  const overridden = attrs.override != null
  const displayText = overridden
    ? (attrs.override as string)
    : (attrs.resolved || '')

  // Tooltip text: source token + cache hash prefix +
  // last-updated time. For overridden nodes, include the
  // override metadata.
  const formatTime = (iso?: string | null): string => {
    if (!iso) return '—'
    try {
      const d = new Date(iso)
      return d.toLocaleString(undefined, {
        hour: '2-digit', minute: '2-digit',
        month: 'short', day: 'numeric',
      })
    } catch {
      return iso.slice(0, 16)
    }
  }

  const tooltipLines = overridden
    ? [
        `Override: ${attrs.override}`,
        `Source: ${attrs.token || '—'}`,
        `Original resolved: ${attrs.resolved || '—'}`,
        `By: ${attrs.override_by || '—'}`,
        `At: ${formatTime(attrs.override_at)}`,
        attrs.override_reason
          ? `Reason: ${attrs.override_reason}` : '',
      ].filter(Boolean)
    : [
        `Source: ${attrs.token || '—'}`,
        `Cache: ${(attrs.data_hash || '').slice(0, 8) || '—'}`,
        `Last updated: ${formatTime(attrs.resolved_at)}`,
      ]

  const handleApplyOverride = (
    overrideValue: string,
    overrideReason: string,
  ): void => {
    // Update node attrs via the editor's setNodeAttribute
    // command. The pass-through extension's addAttributes
    // accepts arbitrary attrs; we set override + metadata.
    const now = new Date().toISOString()
    // Best-effort: the email isn't available client-side
    // without a fetch -- defer to the backend to populate
    // override_by from the session on the next save round-trip.
    props.updateAttributes({
      override:        overrideValue,
      override_at:     now,
      override_reason: overrideReason || null,
    } as Record<string, unknown>)
    setPopoverOpen(false)
  }

  const handleClearOverride = (): void => {
    props.updateAttributes({
      override:        null,
      override_by:     null,
      override_at:     null,
      override_reason: null,
    } as Record<string, unknown>)
    setPopoverOpen(false)
  }

  return (
    <NodeViewWrapper
      as="span"
      ref={wrapperRef}
      data-token={attrs.token}
      data-testid={`token-value-node-${
        (attrs.token || '').replace(/[{}]/g, '')}`}
      className={(
        'token-value-node relative inline-block cursor-pointer '
        + 'border-b border-dotted '
        + (overridden
          ? 'border-warning text-warning'
          : 'border-success text-slate-100'))}
      onMouseEnter={() => setTooltipOpen(true)}
      onMouseLeave={() => setTooltipOpen(false)}
      onClick={(e: React.MouseEvent) => {
        e.stopPropagation()
        setPopoverOpen(true)
        setTooltipOpen(false)
      }}>
      <span>{displayText}</span>
      {overridden ? (
        <Lock
          className="inline w-2.5 h-2.5 ml-0.5 -mt-0.5
                     text-warning" />
      ) : null}

      {tooltipOpen && !popoverOpen ? (
        <span
          data-testid="token-value-tooltip"
          className="absolute z-30 bottom-full left-0 mb-1
                     min-w-[14rem] rounded border bg-slate-900
                     border-slate-600 px-2 py-1.5 text-2xs
                     text-slate-200 shadow-lg whitespace-normal
                     leading-relaxed">
          <div className="font-medium text-slate-100 mb-1">
            {overridden
              ? 'Manually overridden'
              : 'Platform-managed value'}
          </div>
          {tooltipLines.map((line, i) => (
            <div key={i} className="font-mono">{line}</div>
          ))}
        </span>
      ) : null}

      {popoverOpen ? (
        <TokenValueOverridePopover
          token={attrs.token || ''}
          resolved={attrs.resolved || ''}
          override={attrs.override ?? null}
          overrideReason={attrs.override_reason ?? null}
          onApply={handleApplyOverride}
          onClear={overridden ? handleClearOverride : null}
          onCancel={() => setPopoverOpen(false)} />
      ) : null}
    </NodeViewWrapper>
  )
}
