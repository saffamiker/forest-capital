/**
 * tokenValueExtension.ts -- June 28 2026.
 *
 * Minimal pass-through TipTap extension that registers the
 * `token_value` inline node type so the editor preserves its
 * `attrs` on round-trip (load -> render -> getJSON -> save).
 *
 * Without this extension, TipTap drops the unknown node type
 * on first `editor.getJSON()` save -- silently corrupting any
 * draft upgraded via `tools/draft_token_upgrade`.
 *
 * Phase 1 (PR-DM-Lite) renders the node as a plain
 * `<span class="token-value">{resolved}</span>` -- no lock
 * icon, no hover tooltip, no override popover. Those visual
 * affordances ship in Phase 2 (PR-DM-Rich) as a richer
 * NodeView, additive on top of this same extension.
 *
 * `atom: true` is critical: it makes the node read-only by
 * default (cursor jumps over it, backspace removes the whole
 * node not its characters). The author cannot accidentally
 * type-over a token_value mid-prose. Override-to-plain-text
 * goes through an explicit unlock command in Phase 2.
 *
 * Inline + group=inline means the node renders mid-paragraph
 * like a span, not as a block.
 */
import { Node, mergeAttributes } from '@tiptap/core'
import { ReactNodeViewRenderer } from '@tiptap/react'

import TokenValueNodeView from './TokenValueNodeView'


export interface TokenValueAttrs {
  /** The substitution token literal, e.g. "{{OOS_SHARPE_BLEND}}". */
  token: string
  /** The resolved value string at generation time, e.g. "0.86". */
  resolved: string
  /** ISO 8601 timestamp when the resolved value was written. */
  resolved_at: string
  /** The data_hash the resolved value was computed against. */
  data_hash: string
  /** Optional manual override -- when present, the renderer
   *  displays this value instead of `resolved`, AND the
   *  light-refresh rewriter skips this node. */
  override?: string | null
  /** Email of the team member who applied the override. */
  override_by?: string | null
  /** ISO 8601 timestamp of the override. */
  override_at?: string | null
  /** Free-text reason supplied by the override author. */
  override_reason?: string | null
}


export const TokenValueExtension = Node.create({
  name: 'token_value',
  group: 'inline',
  inline: true,
  // atom: true -- the node is treated as a single character
  // for selection purposes. The author can't position the
  // cursor inside it, can only select / delete the whole node.
  // Prevents accidental mid-token typing.
  atom: true,
  // selectable: true so backspace / arrow keys behave
  // predictably around the node. The editor visibly selects
  // the node when focused.
  selectable: true,
  // Use HTML attributes for the DOM rendering. The serializer
  // round-trips these to `attrs` on every save, so the token
  // reference survives editor.getJSON() -> persist -> reload.
  addAttributes() {
    return {
      token: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-token') || '',
        renderHTML: (attrs) => ({
          'data-token': (attrs as TokenValueAttrs).token,
        }),
      },
      resolved: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-resolved') || '',
        renderHTML: (attrs) => ({
          'data-resolved': (attrs as TokenValueAttrs).resolved,
        }),
      },
      resolved_at: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-resolved-at') || '',
        renderHTML: (attrs) => ({
          'data-resolved-at': (attrs as TokenValueAttrs).resolved_at,
        }),
      },
      data_hash: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-hash') || '',
        renderHTML: (attrs) => ({
          'data-hash': (attrs as TokenValueAttrs).data_hash,
        }),
      },
      override: {
        default: null,
        parseHTML: (el) => el.getAttribute('data-override') || null,
        renderHTML: (attrs) => {
          const v = (attrs as TokenValueAttrs).override
          return v ? { 'data-override': v } : {}
        },
      },
      override_by: {
        default: null,
        parseHTML: (el) => el.getAttribute('data-override-by') || null,
        renderHTML: (attrs) => {
          const v = (attrs as TokenValueAttrs).override_by
          return v ? { 'data-override-by': v } : {}
        },
      },
      override_at: {
        default: null,
        parseHTML: (el) => el.getAttribute('data-override-at') || null,
        renderHTML: (attrs) => {
          const v = (attrs as TokenValueAttrs).override_at
          return v ? { 'data-override-at': v } : {}
        },
      },
      override_reason: {
        default: null,
        parseHTML: (el) =>
          el.getAttribute('data-override-reason') || null,
        renderHTML: (attrs) => {
          const v = (attrs as TokenValueAttrs).override_reason
          return v ? { 'data-override-reason': v } : {}
        },
      },
    }
  },
  // Parse the HTML representation back into the node on load.
  // The span carries data-token so the parser knows this is a
  // token_value, not a generic span.
  parseHTML() {
    return [
      {
        tag: 'span[data-token]',
      },
    ]
  },
  // Render the node to HTML. The displayed text is the
  // override (if present) or the resolved value. The class
  // 'token-value' lets the editor stylesheet add the subtle
  // distinction (underline, lock icon) in Phase 2 without
  // changing this extension.
  renderHTML({ node, HTMLAttributes }) {
    const attrs = node.attrs as TokenValueAttrs
    const displayText = attrs.override || attrs.resolved || ''
    const className = attrs.override
      ? 'token-value token-value-overridden'
      : 'token-value'
    return [
      'span',
      mergeAttributes(HTMLAttributes, { class: className }),
      displayText,
    ]
  },
  // No text-content -- the inner text is computed from attrs
  // in renderHTML. This prevents TipTap from trying to round-
  // trip a "text" field through the node.
  renderText({ node }) {
    const attrs = node.attrs as TokenValueAttrs
    return attrs.override || attrs.resolved || ''
  },
  // June 28 2026 (PR-DM-Rich) -- rich React NodeView replaces
  // the plain renderHTML output in the EDITOR display. The
  // renderHTML above is still consumed by getJSON / DOCX-source
  // round-trips + non-React TipTap consumers (storage,
  // copy-paste). The NodeView only mounts in the editor.
  addNodeView() {
    return ReactNodeViewRenderer(TokenValueNodeView)
  },
})

export default TokenValueExtension
