/**
 * BobCalloutNode — the [[BOB]] callout as a TipTap block node.
 *
 * A generated draft embeds [[BOB: …]] working-aid callouts as plain
 * text. editorMarkers.transformBobMarkers converts each whole-paragraph
 * [[BOB: …]] into a `bobCallout` node on load; this node renders it as
 * a full-width amber panel inside the document flow (not an inline
 * span). "Mark as Complete" deletes the node — which removes the marker
 * and advances the section's progress bar.
 */
import { Node, mergeAttributes } from '@tiptap/core'
import {
  ReactNodeViewRenderer, NodeViewWrapper, type NodeViewProps,
} from '@tiptap/react'

/** The panel markup — extracted plain so it is unit-testable without a
 *  TipTap node-view context. */
export function BobCalloutPanel(
  { text, onComplete }: { text: string; onComplete: () => void },
) {
  return (
    <div contentEditable={false}
      className="my-3 rounded border border-warning/50 bg-warning/10 p-3">
      {/* Title — full-width bold heading; never inline with body text so
          a long callout cannot push it off-screen on mobile. */}
      <div className="text-warning font-semibold text-xs mb-1.5 w-full">
        ✏️ BOB — YOUR INPUT NEEDED
      </div>
      <p className="text-xs text-amber-100/90 whitespace-pre-wrap mb-2">
        {text || '(no callout text)'}
      </p>
      {/* Button — full width on mobile, natural inline width on sm: up. */}
      <button type="button" onClick={onComplete}
        className="w-full sm:w-auto text-2xs px-2 py-2 sm:py-1 rounded
                   bg-warning/20 text-warning border border-warning/40
                   hover:bg-warning/30 min-h-[44px] sm:min-h-0
                   flex items-center justify-center sm:inline-flex">
        Mark as Complete
      </button>
    </div>
  )
}

function BobCalloutView({ node, deleteNode }: NodeViewProps) {
  return (
    <NodeViewWrapper>
      <BobCalloutPanel
        text={String(node.attrs.text ?? '')}
        onComplete={() => deleteNode()}
      />
    </NodeViewWrapper>
  )
}

/**
 * The bobCallout block node. atom — it has no editable content; the
 * callout text lives in the `text` attribute. Resolved (deleted) via
 * the panel's Mark as Complete button.
 */
export const BobCallout = Node.create({
  name: 'bobCallout',
  group: 'block',
  atom: true,
  selectable: false,
  draggable: false,

  addAttributes() {
    return { text: { default: '' } }
  },

  parseHTML() {
    return [{ tag: 'div[data-bob-callout]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-bob-callout': '' })]
  },

  addNodeView() {
    return ReactNodeViewRenderer(BobCalloutView)
  },
})
