/**
 * unverifiedExtension.ts -- June 28 2026 (PR #479).
 *
 * TipTap inline node registering the `unverified` node type.
 * Mirrors tokenValueExtension's shape so the schema treats
 * unverified placeholders the same way as token_value
 * placeholders -- atom + inline + group=inline so the cursor
 * skips over them and they round-trip through editor.getJSON()
 * without TipTap dropping the unknown node type.
 *
 * Origin
 *   The backend hard-lock soft-fail (PR #478) wraps each
 *   surviving raw numeric in a literal
 *   "<unverified>VALUE</unverified>" substring. The auto-upgrade
 *   walker (draft_token_upgrade.upgrade_content_json_for_
 *   unverified_tags) splits text nodes containing those
 *   substrings into structured unverified nodes carrying the
 *   raw value as an attribute.
 *
 * Visual
 *   UnverifiedNodeView renders a red-bordered pill displaying
 *   the raw value. Clicking opens UnverifiedPopover for token
 *   resolution OR accept-as-is logging.
 *
 * Persist contract
 *   On save (PATCH /api/v1/documents/drafts/{id}), the node
 *   serialises back via renderHTML as
 *   <span data-unverified data-value="VALUE">[UNVERIFIED: VALUE]</span>
 *   so non-React parsers (DOCX exporter, plain-text fallback)
 *   still see the value. The DOCX renderer at
 *   academic_docx._tiptap_runs handles the 3-state render
 *   (accepted -> raw value, default -> [UNVERIFIED: VALUE]).
 */
import { Node, mergeAttributes } from '@tiptap/core'
import { ReactNodeViewRenderer } from '@tiptap/react'

import UnverifiedNodeView from './UnverifiedNodeView'


export interface UnverifiedAttrs {
  /** The raw numeric value the hard-lock flagged, e.g. "+0.5". */
  value: string
  /** True once an operator has clicked "Accept as-is" -- the
   *  pill renders as a muted version (no red border) + the
   *  DOCX exporter drops the [UNVERIFIED: ...] marker. */
  accepted?: boolean | null
  /** Email of the team member who clicked Accept (audit
   *  log; also persisted to editor_numeric_overrides). */
  accepted_by?: string | null
  /** ISO 8601 timestamp of the accept-as-is click. */
  accepted_at?: string | null
}


export const UnverifiedExtension = Node.create({
  name: 'unverified',
  group: 'inline',
  inline: true,
  // atom: true -- treat as one character. Cursor skips, the
  // whole node deletes on backspace, the author can't type
  // inside it. Same protection as token_value.
  atom: true,
  selectable: true,
  addAttributes() {
    return {
      value: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-value') || '',
        renderHTML: (attrs) => ({
          'data-value': (attrs as UnverifiedAttrs).value,
        }),
      },
      accepted: {
        default: null,
        parseHTML: (el) =>
          el.getAttribute('data-accepted') === 'true',
        renderHTML: (attrs) => {
          const a = (attrs as UnverifiedAttrs).accepted
          return a ? { 'data-accepted': 'true' } : {}
        },
      },
      accepted_by: {
        default: null,
        parseHTML: (el) =>
          el.getAttribute('data-accepted-by') || null,
        renderHTML: (attrs) => {
          const v = (attrs as UnverifiedAttrs).accepted_by
          return v ? { 'data-accepted-by': v } : {}
        },
      },
      accepted_at: {
        default: null,
        parseHTML: (el) =>
          el.getAttribute('data-accepted-at') || null,
        renderHTML: (attrs) => {
          const v = (attrs as UnverifiedAttrs).accepted_at
          return v ? { 'data-accepted-at': v } : {}
        },
      },
    }
  },
  parseHTML() {
    return [{ tag: 'span[data-unverified]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    const attrs = node.attrs as UnverifiedAttrs
    const value = attrs.value || ''
    const accepted = !!attrs.accepted
    const className = accepted
      ? 'unverified-tag unverified-tag-accepted'
      : 'unverified-tag'
    const displayText = accepted
      ? value
      : `[UNVERIFIED: ${value}]`
    return [
      'span',
      mergeAttributes(HTMLAttributes, {
        class:               className,
        'data-unverified':   'true',
      }),
      displayText,
    ]
  },
  renderText({ node }) {
    const attrs = node.attrs as UnverifiedAttrs
    const value = attrs.value || ''
    if (attrs.accepted) return value
    return `[UNVERIFIED: ${value}]`
  },
  addNodeView() {
    return ReactNodeViewRenderer(UnverifiedNodeView)
  },
})

export default UnverifiedExtension
