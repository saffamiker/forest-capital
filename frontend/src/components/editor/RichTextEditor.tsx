/**
 * RichTextEditor — the centre panel for a midpoint_paper / executive_brief
 * draft. TipTap rich text with a Bold / Italic / Heading / list / quote
 * toolbar, and [[VERIFY]] / [[BOB]] markers rendered as amber spans.
 *
 * Clicking a marker offers to resolve it: "Mark as Verified" for a
 * [[VERIFY]] marker, "Mark as Complete" for a [[BOB]] callout — both
 * delete the marker text from the document.
 */
import { useEffect, useState } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import {
  Bold, Italic, Heading1, Heading2, Heading3, List, ListOrdered, Quote,
  Sparkles,
} from 'lucide-react'

import { markerExtension, MARKER_RE } from '../../lib/editorMarkers'
import type { TipTapDoc } from '../../types/editor'

interface Props {
  content: TipTapDoc | null
  onChange: (json: TipTapDoc, text: string) => void
  /** Called with the selected text when the user clicks the floating
   *  "Ask AI" button over an editor selection. */
  onAskAI?: (text: string) => void
}

const EMPTY_DOC: TipTapDoc = { type: 'doc', content: [{ type: 'paragraph' }] }

export default function RichTextEditor({ content, onChange, onAskAI }: Props) {
  // The floating "Ask AI" button over a non-empty selection.
  const [ask, setAsk] = useState<
    { text: string; top: number; left: number } | null>(null)

  const editor = useEditor({
    extensions: [
      StarterKit,
      markerExtension.configure({
        onMarkerClick: (marker) => {
          const verb = marker.kind === 'bob'
            ? 'Mark this BOB callout as complete and remove it?'
            : 'Verify this value against the Analytics page before '
              + 'removing this marker. Mark as verified and remove it?'
          if (!window.confirm(verb)) return
          removeMarkerText(marker.text)
        },
      }),
    ],
    content: content ?? EMPTY_DOC,
    editorProps: {
      attributes: {
        class: 'editor-prose focus:outline-none',
      },
    },
    onUpdate: ({ editor: ed }) => {
      onChange(ed.getJSON() as TipTapDoc, ed.getText())
    },
    onSelectionUpdate: ({ editor: ed }) => {
      if (!onAskAI) return
      const { from, to } = ed.state.selection
      const text = from === to
        ? '' : ed.state.doc.textBetween(from, to, ' ').trim()
      if (!text) { setAsk(null); return }
      try {
        const c = ed.view.coordsAtPos(from)
        setAsk({ text, top: c.top, left: c.left })
      } catch {
        setAsk(null)
      }
    },
  })

  // Deletes the first occurrence of a marker's exact text.
  function removeMarkerText(markerText: string): void {
    if (!editor) return
    const { doc } = editor.state
    let range: { from: number; to: number } | null = null
    doc.descendants((node, pos) => {
      if (range || !node.isText || !node.text) return
      const idx = node.text.indexOf(markerText)
      if (idx >= 0) {
        range = { from: pos + idx, to: pos + idx + markerText.length }
      }
    })
    if (range) {
      editor.chain().focus().deleteRange(range).run()
    }
  }

  // Load a new draft's content when the editor is remounted with it.
  useEffect(() => {
    if (editor && content && editor.isEmpty) {
      editor.commands.setContent(content)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor])

  if (!editor) {
    return <div className="text-muted text-sm p-6">Loading editor…</div>
  }

  const btn = (active: boolean) =>
    `p-1.5 rounded min-h-[32px] min-w-[32px] flex items-center justify-center `
    + `transition-colors ${active
      ? 'bg-electric/20 text-electric'
      : 'text-muted hover:text-white hover:bg-navy-700'}`

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-1 flex-wrap border-b border-border
                      px-3 py-2 sticky top-0 bg-navy-900 z-10">
        <button type="button" aria-label="Bold" className={btn(editor.isActive('bold'))}
          onClick={() => editor.chain().focus().toggleBold().run()}>
          <Bold className="w-4 h-4" />
        </button>
        <button type="button" aria-label="Italic" className={btn(editor.isActive('italic'))}
          onClick={() => editor.chain().focus().toggleItalic().run()}>
          <Italic className="w-4 h-4" />
        </button>
        <span className="w-px h-5 bg-border mx-1" />
        <button type="button" aria-label="Heading 1"
          className={btn(editor.isActive('heading', { level: 1 }))}
          onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}>
          <Heading1 className="w-4 h-4" />
        </button>
        <button type="button" aria-label="Heading 2"
          className={btn(editor.isActive('heading', { level: 2 }))}
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
          <Heading2 className="w-4 h-4" />
        </button>
        <button type="button" aria-label="Heading 3"
          className={btn(editor.isActive('heading', { level: 3 }))}
          onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>
          <Heading3 className="w-4 h-4" />
        </button>
        <span className="w-px h-5 bg-border mx-1" />
        <button type="button" aria-label="Bullet list"
          className={btn(editor.isActive('bulletList'))}
          onClick={() => editor.chain().focus().toggleBulletList().run()}>
          <List className="w-4 h-4" />
        </button>
        <button type="button" aria-label="Numbered list"
          className={btn(editor.isActive('orderedList'))}
          onClick={() => editor.chain().focus().toggleOrderedList().run()}>
          <ListOrdered className="w-4 h-4" />
        </button>
        <button type="button" aria-label="Blockquote"
          className={btn(editor.isActive('blockquote'))}
          onClick={() => editor.chain().focus().toggleBlockquote().run()}>
          <Quote className="w-4 h-4" />
        </button>
      </div>

      {/* Editing surface */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <EditorContent editor={editor} />
      </div>

      {/* Floating "Ask AI" button over a selection. */}
      {ask && onAskAI && (
        <button
          type="button"
          // preventDefault on mousedown keeps the editor selection alive
          // through the click.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => { onAskAI(ask.text); setAsk(null) }}
          style={{ position: 'fixed', top: ask.top - 40, left: ask.left,
                   zIndex: 60 }}
          className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                     bg-electric text-white shadow-lg hover:bg-blue-500"
        >
          <Sparkles className="w-3 h-3" /> Ask AI
        </button>
      )}
    </div>
  )
}

/** Re-export so the page can count markers without importing the lib. */
export { MARKER_RE }
