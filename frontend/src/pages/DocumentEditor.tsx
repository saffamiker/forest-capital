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
  PanelRightClose, PanelRightOpen, MonitorPlay, Download, FileSignature,
} from 'lucide-react'

import RichTextEditor from '../components/editor/RichTextEditor'
import CanvasSlideEditor from '../components/editor/CanvasSlideEditor'
import ChartPicker from '../components/editor/ChartPicker'
import EditorNavigator from '../components/editor/EditorNavigator'
import EditorTasksCallout from '../components/editor/EditorTasksCallout'
import PresentationPreview from '../components/editor/PresentationPreview'
import type { NavSection } from '../components/editor/EditorNavigator'
import WritingAssistant from '../components/editor/WritingAssistant'
import {
  canvasSlideStatus, deckToText, newChartElement,
} from '../components/editor/canvasSlide'
import { countMarkers, nodeToText } from '../lib/editorMarkers'
import type {
  CanvasDeck, EditorDraft, EditorDraftVersion, SaveState, TipTapDoc,
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
  const [contentJson, setContentJson] = useState<TipTapDoc | CanvasDeck | null>(null)
  const [contentText, setContentText] = useState('')
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [lastSaved, setLastSaved] = useState<string>('not yet')
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [exporting, setExporting] = useState(false)
  // The deck slide shown on the canvas — owned here so the left
  // navigator and the canvas always agree on the active slide.
  const [activeSlideId, setActiveSlideId] = useState<number | null>(null)
  // The editor's right panel: the Writing Assistant, or the chart
  // picker drawer while a chart is being added to a slide.
  const [rightPanelMode, setRightPanelMode] =
    useState<'assistant' | 'chartpicker'>('assistant')
  const [generatingScript, setGeneratingScript] = useState(false)
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
      if (d.data.document_type === 'presentation_deck') {
        const first = (d.data.content_json as CanvasDeck | null)?.slides?.[0]
        setActiveSlideId(first?.id ?? null)
      } else {
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

  const onDeckChange = (deck: CanvasDeck) => {
    setContentJson(deck)
    setContentText(deckToText(deck.slides))
    dirtyRef.current = true
    setSaveState('idle')
  }

  // Adds a chart element (from the chart picker) to the active slide,
  // then returns the right panel to the Writing Assistant.
  const handleAddChart = (chartKey: string) => {
    const deck = contentJson as CanvasDeck | null
    if (!deck) return
    const targetId = activeSlideId ?? deck.slides[0]?.id
    onDeckChange({
      slides: deck.slides.map((s) => (s.id === targetId
        ? { ...s, elements: [...s.elements, newChartElement(chartKey)] }
        : s)),
    })
    setRightPanelMode('assistant')
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
      if (d.data.document_type === 'presentation_deck') {
        const first = (d.data.content_json as CanvasDeck | null)?.slides?.[0]
        setActiveSlideId(first?.id ?? null)
      }
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

  const isDeck = draft?.document_type === 'presentation_deck'

  // Deck speaker assignment — drives the navigator badges and the
  // Generate Script button.
  const deckSlides = isDeck
    ? ((contentJson as CanvasDeck | null)?.slides ?? []) : []
  const deckHasSpeaker = deckSlides.some((s) => Boolean(s.speaker))
  const speakerSuggestions = Array.from(new Set(
    deckSlides.map((s) => s.speaker).filter((x): x is string => Boolean(x))))

  const jumpToSection = (heading: string) => {
    // A deck navigator entry selects the slide shown on the canvas.
    if (isDeck) {
      const m = /^Slide (\d+):/.exec(heading)
      const slides = (contentJson as CanvasDeck | null)?.slides ?? []
      const target = m ? slides[Number(m[1]) - 1] : undefined
      if (target) setActiveSlideId(target.id)
      return
    }
    // Paper/brief headings render as <h1>..<h3>; scroll to the match.
    const nodes = document.querySelectorAll('.editor-prose h1, '
      + '.editor-prose h2, .editor-prose h3')
    for (const n of Array.from(nodes)) {
      if ((n.textContent ?? '').includes(heading)) {
        n.scrollIntoView({ behavior: 'smooth', block: 'start' })
        return
      }
    }
  }

  // A deck auto-saves on a 2-second debounce after any canvas change —
  // the 30s interval above still covers a paper/brief.
  useEffect(() => {
    if (!isDeck || !dirtyRef.current) return
    const timer = setTimeout(() => { void save() }, 2000)
    return () => clearTimeout(timer)
  }, [isDeck, contentJson, save])

  // Assigns (or clears) a slide's presenter from the deck navigator.
  const handleAssignSpeaker = (heading: string, speaker: string | null) => {
    const deck = contentJson as CanvasDeck | null
    if (!deck) return
    const m = /^Slide (\d+):/.exec(heading)
    if (!m) return
    const idx = Number(m[1]) - 1
    onDeckChange({
      slides: deck.slides.map((s, i) =>
        (i === idx ? { ...s, speaker } : s)),
    })
  }

  // Generates a presentation script from this deck, then opens it.
  const generateScript = useCallback(async () => {
    setGeneratingScript(true)
    setError(null)
    try {
      await save()  // flush speaker assignments before generation reads them
      const res = await axios.post<{ draft_id: number }>(
        '/api/v1/documents/script/generate', { draft_id: id })
      const newId = res.data?.draft_id
      if (newId) navigate(`/editor/${newId}`)
      else setError('Script generation returned no draft.')
    } catch {
      setError('Could not generate the script — please retry.')
    } finally {
      setGeneratingScript(false)
    }
  }, [id, save, navigate])

  // Navigator sections + the unresolved-marker total.
  const { sections, unresolved } = useMemo(() => {
    if (!draft) return { sections: [] as NavSection[], unresolved: 0 }
    if (isDeck) {
      const slides = (contentJson as CanvasDeck | null)?.slides ?? []
      const secs: NavSection[] = slides.map((s, i) => ({
        heading: `Slide ${i + 1}: ${s.title}`,
        totalMarkers: 1,
        markersRemaining: canvasSlideStatus(s) === 'complete' ? 0 : 1,
        speaker: s.speaker ?? null,
      }))
      return {
        sections: secs,
        unresolved: slides.filter(
          (s) => canvasSlideStatus(s) !== 'complete').length,
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
          {isDeck && (
            <button type="button" onClick={() => void generateScript()}
              disabled={!deckHasSpeaker || generatingScript}
              title={deckHasSpeaker
                ? 'Generate a presentation script from this deck'
                : 'Assign speakers to slides before generating the script.'}
              className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                         border border-electric/40 text-electric
                         hover:bg-electric/10 disabled:opacity-50">
              {generatingScript
                ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Generating…</>
                : <><FileSignature className="w-3.5 h-3.5" />
                    Generate Script</>}
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
              onAssignSpeaker={isDeck ? handleAssignSpeaker : undefined}
              speakerSuggestions={isDeck ? speakerSuggestions : undefined}
            />
          </aside>
        )}

        <main className="flex-1 min-w-0 bg-navy-900">
          {isDeck ? (
            <CanvasSlideEditor draftId={id}
              deck={(contentJson as CanvasDeck | null) ?? { slides: [] }}
              activeSlideId={activeSlideId}
              onChange={onDeckChange}
              onRequestChartPicker={() => {
                setRightOpen(true)
                setRightPanelMode('chartpicker')
              }} />
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
            {isDeck && rightPanelMode === 'chartpicker' ? (
              <ChartPicker onSelect={handleAddChart}
                onClose={() => setRightPanelMode('assistant')} />
            ) : (
              <WritingAssistant draftId={id} unresolvedMarkers={unresolved}
                prefill={assistantPrefill} />
            )}
          </aside>
        )}
      </div>

      {/* Presentation Preview — a full-screen rehearsal view for a deck. */}
      {previewOpen && isDeck && (
        <PresentationPreview
          slides={(contentJson as CanvasDeck | null)?.slides ?? []}
          onClose={() => setPreviewOpen(false)} />
      )}
    </div>
  )
}

function countWords(text: string): number {
  const t = (text || '').trim()
  return t ? t.split(/\s+/).length : 0
}
