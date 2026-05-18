/**
 * editorMarkers.ts — working-aid markers for the TipTap editor.
 *
 * The generated draft carries two kinds of plain-text working aid:
 *   [[VERIFY: …]] / [[VERIFY CITATION: …]] — an unverified value/citation
 *   [[BOB: …]]                            — a section needing the author
 *
 * [[VERIFY]] stays inline: markerExtension decorates it as an amber span
 * and reports a click (with coordinates) so the editor can anchor a
 * confirm popup. [[BOB]] is promoted to a block node — transformBobMarkers
 * converts each whole-paragraph [[BOB: …]] into a `bobCallout` node on
 * load (see lib/BobCalloutNode).
 *
 * docToText / nodeToText project the document — including bobCallout
 * nodes back to [[BOB: …]] text — so marker counts and the Academic
 * Review overlay still see every unresolved callout.
 */
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import type { TipTapDoc } from '../types/editor'

// A VERIFY or BOB marker. Non-greedy, never crosses a closing ]].
export const MARKER_RE = /\[\[(VERIFY[^\]]*?|BOB:[^\]]*?)\]\]/g

export type MarkerKind = 'verify' | 'bob'

export interface MarkerHit {
  kind: MarkerKind
  text: string   // the full marker, e.g. "[[VERIFY: …]]"
}

/** Every marker in a plain-text string, in document order. */
export function findMarkers(text: string): MarkerHit[] {
  const out: MarkerHit[] = []
  for (const m of (text || '').matchAll(MARKER_RE)) {
    out.push({
      kind: m[1].startsWith('BOB') ? 'bob' : 'verify',
      text: m[0],
    })
  }
  return out
}

/** Count of unresolved markers of a kind in a plain-text string. */
export function countMarkers(text: string, kind?: MarkerKind): number {
  const hits = findMarkers(text)
  return kind ? hits.filter((h) => h.kind === kind).length : hits.length
}

// A whole-paragraph [[BOB: …]] marker — the form transformBobMarkers
// promotes to a bobCallout node.
const WHOLE_BOB_RE = /^\s*\[\[BOB:\s*([\s\S]*?)\]\]\s*$/

interface JNode {
  type?: string
  text?: string
  attrs?: Record<string, unknown>
  content?: JNode[]
}

/**
 * The plain text of a single TipTap JSON node. A bobCallout node is
 * projected back to its [[BOB: …]] marker so marker counts and the
 * Academic Review overlay still see an unresolved callout.
 */
export function nodeToText(node: JNode | null | undefined): string {
  if (!node || typeof node !== 'object') return ''
  if (node.type === 'bobCallout') {
    return `[[BOB: ${String(node.attrs?.text ?? '')}]]`
  }
  if (node.text) return String(node.text)
  if (Array.isArray(node.content)) {
    return node.content.map(nodeToText).join('')
  }
  return ''
}

/** The plain-text projection of a whole TipTap document. */
export function docToText(doc: TipTapDoc | null | undefined): string {
  const content = (doc as JNode | null)?.content
  if (!Array.isArray(content)) return ''
  return content.map(nodeToText).join('\n\n').trim()
}

/**
 * Converts each whole-paragraph [[BOB: …]] marker in a TipTap document
 * into a `bobCallout` block node. Idempotent — a document already
 * carrying bobCallout nodes is returned unchanged. Top-level only;
 * generated drafts always emit a [[BOB]] marker as its own paragraph.
 */
export function transformBobMarkers(doc: TipTapDoc): TipTapDoc {
  const d = doc as JNode
  if (!d || !Array.isArray(d.content)) return doc
  const content = d.content.map((node) => {
    if (node?.type === 'paragraph') {
      const m = WHOLE_BOB_RE.exec(nodeToText(node).trim())
      if (m) {
        return { type: 'bobCallout', attrs: { text: m[1].trim() } }
      }
    }
    return node
  })
  return { ...d, content } as TipTapDoc
}

const markerPluginKey = new PluginKey('editor-markers')

function buildDecorations(doc: import('@tiptap/pm/model').Node): DecorationSet {
  const decorations: Decoration[] = []
  doc.descendants((node, pos) => {
    if (!node.isText || !node.text) return
    for (const m of node.text.matchAll(MARKER_RE)) {
      const start = pos + (m.index ?? 0)
      const end = start + m[0].length
      const kind = m[1].startsWith('BOB') ? 'bob' : 'verify'
      decorations.push(Decoration.inline(start, end, {
        class: `editor-marker editor-marker-${kind}`,
      }))
    }
  })
  return DecorationSet.create(doc, decorations)
}

export interface MarkerOptions {
  /** Called with the clicked marker and the click's viewport
   *  coordinates — used to anchor the [[VERIFY]] confirm popup. */
  onMarkerClick: (marker: MarkerHit, coords: { x: number; y: number }) => void
}

/**
 * TipTap extension: decorates inline [[VERIFY]] markers as amber spans
 * and routes a click on one to options.onMarkerClick. [[BOB]] markers
 * are block nodes (see BobCalloutNode) and never reach this plugin.
 */
export const markerExtension = Extension.create<MarkerOptions>({
  name: 'editorMarkers',

  addOptions() {
    return { onMarkerClick: () => {} }
  },

  addProseMirrorPlugins() {
    const options = this.options
    return [
      new Plugin({
        key: markerPluginKey,
        state: {
          init: (_config, state) => buildDecorations(state.doc),
          apply: (tr, old) =>
            tr.docChanged ? buildDecorations(tr.doc) : old,
        },
        props: {
          decorations(state) {
            return markerPluginKey.getState(state)
          },
          handleClick(view, pos, event) {
            // Resolve the text around the click and test for a marker
            // that spans pos.
            const { doc } = view.state
            let hit: MarkerHit | null = null
            doc.descendants((node, nodePos) => {
              if (hit || !node.isText || !node.text) return
              for (const m of node.text.matchAll(MARKER_RE)) {
                const start = nodePos + (m.index ?? 0)
                const end = start + m[0].length
                if (pos >= start && pos <= end) {
                  hit = {
                    kind: m[1].startsWith('BOB') ? 'bob' : 'verify',
                    text: m[0],
                  }
                  return
                }
              }
            })
            if (hit) {
              options.onMarkerClick(hit, {
                x: event.clientX, y: event.clientY,
              })
              return true
            }
            return false
          },
        },
      }),
    ]
  },
})
