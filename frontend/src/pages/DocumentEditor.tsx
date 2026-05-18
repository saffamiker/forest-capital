/**
 * DocumentEditor — the in-platform editor at /editor/:draftId.
 *
 * Three panels: a left Document Navigator (sections + version history),
 * a centre editor (TipTap rich text for a paper/brief, slide cards for a
 * presentation deck), and a right Writing Assistant (Academic Review +
 * AI chat). The draft auto-saves every 30 seconds.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  ArrowLeft, Loader2, PanelLeftClose, PanelLeftOpen,
  PanelRightClose, PanelRightOpen, MonitorPlay, Download,
} from 'lucide-react'

import RichTextEditor from '../components/editor/RichTextEditor'
import SlideEditor, { slideComplete } from '../components/editor/SlideEditor'
import EditorNavigator from '../components/editor/EditorNavigator'
import EditorTasksCallout from '../components/editor/EditorTasksCallout'
import PresentationPreview from '../components/editor/PresentationPreview'
import type { NavSection } from '../components/editor/EditorNavigator'
import WritingAssistant from '../components/editor/WritingAssistant'
import { countMarkers, nodeToText } from '../lib/editorMarkers'
import type {
  DeckContent, EditorDraft, EditorDraftVersion, SaveState, TipTapDoc,
} from '../types/editor'

const AI_DRAFT_BANNER = 'AI DRAFT — REQUIRES HUMAN REVIEW'
const WORD_TARGETS: Record<string, number> = {
  midpoint_paper: 1500,
  executive_brief: 2000,
  presentation_deck: 0,
}

// The export endpoint for each document type — the in-editor Export
// button POSTs {editor_draft_id} to it and downloads the result.
const EXPORT_ENDPOINT: Record<string, string> = {
  midpoint_paper: '/api/v1/export/midpoint-paper',
  executive_brief: '/api/v1/export/executive-brief',
  presentation_deck: '/api/v1/export/presentation-deck',
}

// Walks a TipTap doc into (heading, body-text) sections for the navigator.
// nodeToText projects a bobCallout node back to its [[BOB: …]] marker, so a
// resolved/unresolved callout still counts toward the section's progress.
function tiptapSections(doc: TipTapDoc | null): { heading: string; text: string }[] {
  const out: { heading: string; text: string }[] = []
  let current: { heading: string; text: string } | null = null
  for (const raw of (doc?.content ?? [])) {
    const node = raw as { type?: string }
    if (node.type === 'heading') {
      if (current) out.push(current)
      current = { heading: nodeToText(raw) || 'Section', text: '' }
    } else if (current) {
      current.text += '\n' + nodeToText(raw)
    }
  }
  if (current) out.push(current)
  return out
}

export default function DocumentEditor() {
  const { draftId } = useParams<{ draftId: string }>()
  const navigate = useNavigate()
  const id = Number(draftId)

  const [draft, setDraft] = useState<EditorDraft | null>(null)
  const [versions, setVersions] = useState<EditorDraftVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Working content — kept in state so auto-save and the navigator see it.
  const [contentJson, setContentJson] = useState<TipTapDoc | DeckContent | null>(null)
  const [contentText, setContentText] = useState('')
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [lastSaved, setLastSaved] = useState<string>('not yet')
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [exporting, setExporting] = useState(false)
  // A quoted passage pushed into the Writing Assistant by the "Ask AI"
  // selection action; the nonce re-triggers the panel's prefill effect.
  const [assistantPrefill, setAssistantPrefill] =
    useState<{ text: string; nonce: number } | null>(null)

  const dirtyRef = useRef(false)
  // Initial marker total per section heading — the progress denominator.
  const markerBaseline = useRef<Record<string, number>>({})

  const loadDraft = useCallback(async () => {
    setLoading(true)
    try {
      const [d, v] = await Promise.all([
        axios.get<EditorDraft>(`/api/v1/documents/drafts/${id}`),
        axios.get<{ versions: EditorDraftVersion[] }>(
          `/api/v1/documents/drafts/${id}/versions`),
      ])
      setDraft(d.data)
      setVersions(v.data.versions ?? [])
      setContentJson(d.data.content_json)
      setContentText(d.data.content_text ?? '')
      // Capture the per-section marker baseline once, on load.
      if (d.data.document_type !== 'presentation_deck') {
        const base: Record<string, number> = {}
        for (const s of tiptapSections(d.data.content_json as TipTapDoc)) {
          base[s.heading] = countMarkers(s.text)
        }
        markerBaseline.current = base
      }
      setError(null)
    } catch {
      setError('Could not load this draft.')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { void loadDraft() }, [loadDraft])

  // Auto-save — every 30 seconds while dirty.
  const save = useCallback(async () => {
    if (!dirtyRef.current || !draft) return
    setSaveState('saving')
    try {
      await axios.patch(`/api/v1/documents/drafts/${id}`, {
        content_json: contentJson,
        content_text: contentText,
        word_count: countWords(contentText),
      })
      dirtyRef.current = false
      setSaveState('saved')
      setLastSaved(new Date().toLocaleTimeString(undefined,
        { hour: '2-digit', minute: '2-digit' }))
    } catch {
      setSaveState('error')
    }
  }, [id, draft, contentJson, contentText])

  useEffect(() => {
    const timer = setInterval(() => { void save() }, 30000)
    return () => clearInterval(timer)
  }, [save])

  const onRichChange = (json: TipTapDoc, text: string) => {
    setContentJson(json)
    setContentText(text)
    dirtyRef.current = true
    setSaveState('idle')
  }

  const onDeckChange = (deck: DeckContent) => {
    setContentJson(deck)
    setContentText(deck.slides.map(
      (s) => `${s.title}\n${s.content}\n${s.speaker_notes}`).join('\n\n'))
    dirtyRef.current = true
    setSaveState('idle')
  }

  const saveVersion = async (label: string) => {
    await save()  // flush the working content first
    try {
      await axios.post(`/api/v1/documents/drafts/${id}/versions`,
        { version_label: label || undefined })
      const v = await axios.get<{ versions: EditorDraftVersion[] }>(
        `/api/v1/documents/drafts/${id}/versions`)
      setVersions(v.data.versions ?? [])
    } catch { /* surfaced via the save indicator on the next cycle */ }
  }

  const restoreVersion = async (versionId: number) => {
    if (!window.confirm('Restore this version as the current content?')) return
    try {
      const d = await axios.post<EditorDraft>(
        `/api/v1/documents/drafts/${id}/restore/${versionId}`)
      setDraft(d.data)
      setContentJson(d.data.content_json)
      setContentText(d.data.content_text ?? '')
      dirtyRef.current = false
      setSaveState('saved')
    } catch { setError('Restore failed.') }
  }

  // In-editor export — flushes pending edits, then POSTs the draft id to
  // the matching export endpoint and downloads the rendered file.
  const exportDocument = useCallback(async () => {
    if (!draft) return
    setExporting(true)
    try {
      await save()
      const endpoint = EXPORT_ENDPOINT[draft.document_type]
      const res = await axios.post(endpoint, { editor_draft_id: id },
        { responseType: 'blob' })
      const dispo = String(res.headers['content-disposition'] ?? '')
      const match = /filename="?([^";]+)"?/i.exec(dispo)
      const filename = match?.[1] ?? `forest-capital-${draft.document_type}`
      const url = URL.createObjectURL(res.data as Blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch {
      setError('Export failed — please try again.')
    } finally {
      setExporting(false)
    }
  }, [draft, id, save])

  // "Ask AI" on an editor selection — opens the assistant panel and
  // pre-fills the chat input with the quoted passage.
  const handleAskAI = (text: string) => {
    setRightOpen(true)
    setAssistantPrefill({
      text: `> ${text}\n\nHow can I improve this?`,
      nonce: Date.now(),
    })
  }

  const jumpToSection = (heading: string) => {
    // Headings render as <h1>..<h3>; find the one whose text matches.
    const nodes = document.querySelectorAll('.editor-prose h1, '
      + '.editor-prose h2, .editor-prose h3, [data-tour="slide-card"]')
    for (const n of Array.from(nodes)) {
      if ((n.textContent ?? '').includes(heading)) {
        n.scrollIntoView({ behavior: 'smooth', block: 'start' })
        return
      }
    }
  }

  const isDeck = draft?.document_type === 'presentation_deck'

  // Navigator sections + the unresolved-marker total.
  const { sections, unresolved } = useMemo(() => {
    if (!draft) return { sections: [] as NavSection[], unresolved: 0 }
    if (isDeck) {
      const slides = (contentJson as DeckContent | null)?.slides ?? []
      const secs: NavSection[] = slides.map((s, i) => ({
        heading: `Slide ${i + 1}: ${s.title}`,
        totalMarkers: 1,
        markersRemaining: slideComplete(s) ? 0 : 1,
      }))
      return {
        sections: secs,
        unresolved: slides.filter((s) => !slideComplete(s)).length,
      }
    }
    const secs: NavSection[] = tiptapSections(contentJson as TipTapDoc | null)
      .map((s) => {
        const remaining = countMarkers(s.text)
        const total = Math.max(markerBaseline.current[s.heading] ?? 0, remaining)
        return { heading: s.heading, markersRemaining: remaining,
                 totalMarkers: total }
      })
    return { sections: secs, unresolved: countMarkers(contentText) }
  }, [draft, isDeck, contentJson, contentText])

  if (loading) {
    return (
      <div className="p-10 text-center text-muted text-sm">
        <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
        Loading editor…
      </div>
    )
  }
  if (error || !draft) {
    return (
      <div className="p-10 text-center">
        <p className="text-danger text-sm mb-3">{error ?? 'Draft not found.'}</p>
        <button type="button" onClick={() => navigate('/reports')}
          className="text-xs text-electric hover:underline">
          ← Back to Reports
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)]">
      {/* AI DRAFT banner — permanent, non-dismissable. */}
      <div className="bg-warning text-navy-900 text-2xs font-bold uppercase
                      tracking-wide text-center py-1">
        {AI_DRAFT_BANNER}
      </div>

      {/* Header bar */}
      <div className="flex items-center justify-between gap-3 px-3 py-2
                      border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <button type="button" onClick={() => navigate('/reports')}
            aria-label="Back to Reports"
            className="text-muted hover:text-white">
            <ArrowLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-white truncate">{draft.title}</span>
          <span className="text-2xs text-muted uppercase tracking-wide
                           shrink-0 hidden sm:inline">
            {draft.document_type.replace('_', ' ')}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-2xs text-muted">
            {saveState === 'saving' ? 'Saving…'
              : saveState === 'error' ? 'Save failed'
              : saveState === 'saved' ? `Saved ${lastSaved}` : 'Unsaved changes'}
          </span>
          <button type="button" onClick={() => void exportDocument()}
            disabled={exporting}
            className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                       border border-electric/40 text-electric
                       hover:bg-electric/10 disabled:opacity-50">
            {exporting
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Exporting…</>
              : <><Download className="w-3.5 h-3.5" />
                  {isDeck ? 'Export PPTX' : 'Export DOCX'}</>}
          </button>
          {isDeck && (
            <button type="button" onClick={() => setPreviewOpen(true)}
              className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                         border border-electric/40 text-electric
                         hover:bg-electric/10">
              <MonitorPlay className="w-3.5 h-3.5" /> Presentation Preview
            </button>
          )}
          <button type="button" onClick={() => setLeftOpen((v) => !v)}
            aria-label="Toggle navigator"
            className="text-muted hover:text-white">
            {leftOpen ? <PanelLeftClose className="w-4 h-4" />
              : <PanelLeftOpen className="w-4 h-4" />}
          </button>
          <button type="button" onClick={() => setRightOpen((v) => !v)}
            aria-label="Toggle assistant"
            className="text-muted hover:text-white">
            {rightOpen ? <PanelRightClose className="w-4 h-4" />
              : <PanelRightOpen className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Tasks callout — the tracking note + the checklist for this
          document type, dismissible per draft. */}
      <EditorTasksCallout documentType={draft.document_type} draftId={id} />

      {/* Three panels */}
      <div className="flex flex-1 min-h-0">
        {leftOpen && (
          <aside className="w-[220px] shrink-0 border-r border-border
                            bg-navy-900">
            <EditorNavigator
              title={draft.title}
              wordCount={countWords(contentText)}
              wordTarget={WORD_TARGETS[draft.document_type] ?? 1500}
              lastSavedLabel={lastSaved}
              saveState={saveState}
              sections={sections}
              versions={versions}
              onJumpToSection={jumpToSection}
              onSaveVersion={saveVersion}
              onRestoreVersion={restoreVersion}
            />
          </aside>
        )}

        <main className="flex-1 min-w-0 bg-navy-900">
          {isDeck ? (
            <SlideEditor draftId={id}
              deck={(contentJson as DeckContent | null) ?? { slides: [] }}
              onChange={onDeckChange} />
          ) : (
            <RichTextEditor
              content={(contentJson as TipTapDoc | null)}
              onChange={onRichChange}
              onAskAI={handleAskAI} />
          )}
        </main>

        {rightOpen && (
          <aside className="w-[300px] shrink-0 border-l border-border
                            bg-navy-900">
            <WritingAssistant draftId={id} unresolvedMarkers={unresolved}
              prefill={assistantPrefill} />
          </aside>
        )}
      </div>

      {/* Presentation Preview — a full-screen rehearsal view for a deck. */}
      {previewOpen && isDeck && (
        <PresentationPreview
          slides={(contentJson as DeckContent | null)?.slides ?? []}
          onClose={() => setPreviewOpen(false)} />
      )}
    </div>
  )
}

function countWords(text: string): number {
  const t = (text || '').trim()
  return t ? t.split(/\s+/).length : 0
}
