/**
 * editorMarkers.ts — inline working-aid markers for the TipTap editor.
 *
 * The generated draft carries two kinds of plain-text working aid:
 *   [[VERIFY: …]] / [[VERIFY CITATION: …]] — an unverified value/citation
 *   [[BOB: …]]                            — a section needing the author
 *
 * A TipTap extension (markerExtension) decorates both so they render as
 * amber spans, and reports a click on one so the editor can offer
 * "Mark as Verified" / "Mark as Complete" (which deletes the marker
 * text). Plain-text helpers count and locate markers for section
 * progress.
 */
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

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
  /** Called with the clicked marker's full text and kind. */
  onMarkerClick: (marker: MarkerHit) => void
}

/**
 * TipTap extension: decorates [[VERIFY]] / [[BOB]] markers and routes a
 * click on one to options.onMarkerClick.
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
          handleClick(view, pos) {
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
              options.onMarkerClick(hit)
              return true
            }
            return false
          },
        },
      }),
    ]
  },
})
