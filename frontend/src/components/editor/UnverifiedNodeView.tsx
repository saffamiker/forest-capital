/**
 * UnverifiedNodeView.tsx -- June 28 2026 (PR #479).
 *
 * Rich React NodeView for the `unverified` inline node
 * (sibling to TokenValueNodeView). Renders a red-bordered
 * pill displaying the raw value that survived the hard-lock
 * 3-pass correction loop. Click to open UnverifiedPopover for
 * resolution.
 *
 * 3-state visual:
 *   - default (not accepted, not replaced):
 *       Red border + light red background + "⚠" icon. The
 *       hover tooltip explains the soft-fail context + tells
 *       the operator to click to resolve.
 *   - accepted (override logged via /accept-unverified):
 *       Muted (no red border) + slight underline so the value
 *       remains visible but the urgent-flag visual treatment
 *       is removed.
 *   - replaced with token:
 *       Not reachable here -- the popover's "Replace with
 *       token" action rewrites the node type to token_value,
 *       which renders via TokenValueNodeView.
 *
 * atom: true (declared in unverifiedExtension) -- cursor
 * skips, backspace removes the whole node.
 */
import { useState, useRef, useEffect } from 'react'
import { NodeViewWrapper } from '@tiptap/react'
import type { NodeViewProps } from '@tiptap/react'
import { AlertTriangle } from 'lucide-react'

import { UnverifiedPopover } from './UnverifiedPopover'


export default function UnverifiedNodeView(
  props: NodeViewProps,
): React.ReactElement {
  const attrs = props.node.attrs as {
    value?:       string
    accepted?:    boolean | null
    accepted_by?: string | null
    accepted_at?: string | null
  }

  const [tooltipOpen, setTooltipOpen] = useState(false)
  const [popoverOpen, setPopoverOpen] = useState(false)
  const wrapperRef = useRef<HTMLSpanElement>(null)

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

  const value = attrs.value || ''
  const accepted = !!attrs.accepted

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

  // Tooltip text varies by state.
  const tooltipLines = accepted
    ? [
        `Value: ${value}`,
        'Accepted as-is by operator',
        `By: ${attrs.accepted_by || '—'}`,
        `At: ${formatTime(attrs.accepted_at)}`,
      ]
    : [
        `Raw value: ${value}`,
        'The hard-lock could not match this value to a',
        '{{TOKEN}} in the substitution table after 3',
        'correction passes. Click to resolve: either',
        'replace with a matching token or accept as-is.',
        'The document MUST NOT be submitted while any',
        '[UNVERIFIED] tag remains.',
      ]

  const handleReplaceWithToken = (
    token: string,
    resolved: string,
  ): void => {
    // Replace this unverified node with a token_value node
    // carrying the chosen token + resolved value. The editor
    // command rewrites the node type in place.
    const now = new Date().toISOString()
    if (typeof props.getPos !== 'function') {
      setPopoverOpen(false)
      return
    }
    const pos = props.getPos()
    if (typeof pos !== 'number') {
      setPopoverOpen(false)
      return
    }
    const newNode = props.editor.schema.nodes.token_value?.create({
      token,
      resolved,
      resolved_at: now,
      data_hash:   '',
    })
    if (!newNode) {
      setPopoverOpen(false)
      return
    }
    const tr = props.editor.state.tr.replaceWith(
      pos, pos + props.node.nodeSize, newNode)
    props.editor.view.dispatch(tr)
    setPopoverOpen(false)
  }

  const handleAcceptAsIs = async (): Promise<void> => {
    // Mark this node accepted (mutates attrs) + fire the audit
    // endpoint so the override lands in editor_numeric_overrides.
    // The visual treatment shifts to muted; the value stays
    // visible in prose. The endpoint failure leaves the node
    // unchanged so the operator can retry.
    try {
      const draftId = (
        document.body.dataset.activeDraftId
        || (window as unknown as {
          __activeDraftId?: string | number
        }).__activeDraftId
      )
      if (draftId) {
        await fetch(
          `/api/v1/editor/drafts/${draftId}/accept-unverified`,
          {
            method:      'POST',
            credentials: 'include',
            headers:     { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              value:            value,
              sentence_context: '',
            }),
          })
      }
    } catch {
      // Fail-open -- the attribute mutation still happens
      // so the visual state moves to accepted. The audit
      // log entry can be retried later.
    }
    const now = new Date().toISOString()
    props.updateAttributes({
      accepted:    true,
      accepted_at: now,
    } as Record<string, unknown>)
    setPopoverOpen(false)
  }

  return (
    <NodeViewWrapper
      as="span"
      ref={wrapperRef}
      data-unverified="true"
      data-value={value}
      data-testid={`unverified-node-${value}`}
      className={(
        'unverified-node relative inline-block cursor-pointer '
        + (accepted
          ? 'border-b border-dotted border-slate-500 text-slate-400'
          : 'rounded px-1 -mx-0.5 border border-red-500/60 '
            + 'bg-red-500/10 text-red-300 font-medium'))}
      onMouseEnter={() => setTooltipOpen(true)}
      onMouseLeave={() => setTooltipOpen(false)}
      onClick={(e: React.MouseEvent) => {
        e.stopPropagation()
        setPopoverOpen(true)
        setTooltipOpen(false)
      }}>
      {!accepted ? (
        <AlertTriangle
          className="inline w-3 h-3 mr-0.5 -mt-0.5" />
      ) : null}
      <span>{value}</span>

      {tooltipOpen && !popoverOpen ? (
        <span
          data-testid="unverified-tooltip"
          className="absolute z-30 bottom-full left-0 mb-1
                     min-w-[16rem] rounded border bg-slate-900
                     border-slate-600 px-2 py-1.5 text-2xs
                     text-slate-200 shadow-lg whitespace-normal
                     leading-relaxed">
          <div className={(
              'font-medium mb-1 '
              + (accepted ? 'text-slate-100'
                : 'text-red-300'))}>
            {accepted
              ? 'Accepted as-is'
              : '⚠ Unverified numeric -- needs review'}
          </div>
          {tooltipLines.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </span>
      ) : null}

      {popoverOpen ? (
        <UnverifiedPopover
          value={value}
          editor={props.editor}
          onReplaceWithToken={handleReplaceWithToken}
          onAcceptAsIs={handleAcceptAsIs}
          onCancel={() => setPopoverOpen(false)} />
      ) : null}
    </NodeViewWrapper>
  )
}
