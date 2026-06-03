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
  Mic, X,
} from 'lucide-react'

import RichTextEditor from '../components/editor/RichTextEditor'
import CanvasSlideEditor from '../components/editor/CanvasSlideEditor'
import ChartPicker from '../components/editor/ChartPicker'
import EditorNavigator from '../components/editor/EditorNavigator'
import EditorTasksCallout from '../components/editor/EditorTasksCallout'
import AuditWarningsBanner from '../components/editor/AuditWarningsBanner'
import PresentationPreview from '../components/editor/PresentationPreview'
import RehearsalOverlay from '../components/editor/RehearsalOverlay'
import type { NavSection } from '../components/editor/EditorNavigator'
import WritingAssistant from '../components/editor/WritingAssistant'
import {
  canvasSlideStatus, deckToText, newChartElement,
} from '../components/editor/canvasSlide'
import { countMarkers, nodeToText } from '../lib/editorMarkers'
import { getSpeakerColour } from '../lib/speakerColours'
import type {
  CanvasDeck, EditorDraft, EditorDraftVersion, SaveState, TipTapDoc,
} from '../types/editor'

const AI_DRAFT_BANNER = 'AI DRAFT — REQUIRES HUMAN REVIEW'
const WORD_TARGETS: Record<string, number> = {
  midpoint_paper: 1500,
  executive_brief: 2000,
  presentation_deck: 0,
  presentation_script: 0,
  // The Appendix is dense (eight short intro paragraphs + tables) —
  // the editor surface only holds the prose, not the cached tables.
  analytical_appendix: 1200,
}

// A spoken presentation is delivered at ~150 words per minute.
const SCRIPT_WORDS_PER_MINUTE = 150

// The export endpoint for each document type — the in-editor Export
// button POSTs {editor_draft_id} to it and downloads the result.
const EXPORT_ENDPOINT: Record<string, string> = {
  midpoint_paper: '/api/v1/export/midpoint-paper',
  executive_brief: '/api/v1/export/executive-brief',
  presentation_deck: '/api/v1/export/presentation-deck',
  analytical_appendix: '/api/v1/export/analytical-appendix',
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

// Walks a presentation_script doc into per-slide sections — one per H2
// heading, carrying the speaker from its H3 and whether it has any
// delivery prose yet (the progress signal — a script has no [[BOB]]
// markers).
function scriptSections(doc: TipTapDoc | null): {
  heading: string; speaker: string | null; hasContent: boolean
}[] {
  const out: { heading: string; speaker: string | null;
               hasContent: boolean }[] = []
  let current: { heading: string; speaker: string | null;
                 hasContent: boolean } | null = null
  for (const raw of (doc?.content ?? [])) {
    const node = raw as { type?: string; attrs?: { level?: number } }
    const level = node.attrs?.level ?? 1
    if (node.type === 'heading' && level <= 2) {
      if (current) out.push(current)
      current = { heading: nodeToText(raw) || 'Section', speaker: null,
                  hasContent: false }
    } else if (current && node.type === 'heading' && level === 3) {
      const m = /Speaker:\s*(.+)/i.exec(nodeToText(raw))
      if (m) current.speaker = m[1].trim()
    } else if (current && (node.type === 'paragraph'
        || node.type === 'blockquote')) {
      if (nodeToText(raw).trim()) current.hasContent = true
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
  // Viewport gating — desktop renders the side-aside panels; mobile
  // (below the lg breakpoint) renders the same panels as full-screen
  // overlay drawers. We track isDesktop as JS state so the two
  // rendering paths are mutually exclusive (the matchMedia change
  // listener updates this on viewport resize). The matchMedia call is
  // guarded for jsdom envs that have no media-query implementation —
  // fall back to "lg" so the test contract (panels render by default)
  // is preserved.
  const [isDesktop, setIsDesktop] = useState(() => (
    typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      ? window.matchMedia('(min-width: 1024px)').matches
      : true
  ))
  useEffect(() => {
    if (typeof window === 'undefined'
        || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(min-width: 1024px)')
    const handler = () => setIsDesktop(mq.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])
  // Panel state — default open on desktop, default closed on mobile.
  const [leftOpen, setLeftOpen] = useState(isDesktop)
  const [rightOpen, setRightOpen] = useState(isDesktop)
  const [previewOpen, setPreviewOpen] = useState(false)
  // Rehearsal mode — script editor's combined script + slide view.
  // The overlay fetches GET /api/v1/documents/rehearsal on mount, so
  // no precondition gate is needed here beyond document_type.
  const [rehearsalOpen, setRehearsalOpen] = useState(false)
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

  // Academic Review status — populated by the auto-fired review the
  // generation endpoint schedules on its way out. Shape mirrors
  // GET /api/v1/documents/drafts/{id}/academic-review-status. The
  // editor polls while status reads "missing" so a fresh draft picks
  // up its score the moment the background task lands. See the
  // Auto-fire Academic Review block in main.py for the producer side.
  const [reviewStatus, setReviewStatus] = useState<{
    status: 'complete' | 'running' | 'missing'
    score: number | null
    rating: string | null
    advisory: boolean
    document_type: string
    section_ratings: Record<string, string>
    run_at: string | null
    threshold: number
  } | null>(null)

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

  // Academic Review status — fetched once on draft load, then polled
  // every 20 seconds while the auto-fired review is still in flight.
  // We give up after a few polls (the review backend always completes
  // OR silently fails; either way the editor stops asking). Poll only
  // for the two document types that schedule an auto-review.
  useEffect(() => {
    if (!draft) return
    if (draft.document_type !== 'midpoint_paper'
        && draft.document_type !== 'executive_brief') {
      setReviewStatus(null)
      return
    }
    let cancelled = false
    let attempts = 0
    const MAX_POLLS = 15  // ~5 minutes at the 20s cadence
    const fetchStatus = async () => {
      try {
        const r = await axios.get(
          `/api/v1/documents/drafts/${id}/academic-review-status`)
        if (cancelled) return
        setReviewStatus(r.data)
        if (r.data?.status === 'complete') return  // stop polling
        attempts += 1
        if (attempts >= MAX_POLLS) return
        window.setTimeout(() => { void fetchStatus() }, 20000)
      } catch {
        // Endpoint failures are quiet — the editor falls back to "no
        // score" rather than showing an error chrome.
      }
    }
    void fetchStatus()
    return () => { cancelled = true }
  }, [id, draft])

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

  // Script export — the master script, or one speaker's slides only.
  const exportScript = useCallback(async (speaker?: string) => {
    setExporting(true)
    setError(null)
    try {
      await save()
      const res = await axios.post(
        `/api/v1/documents/drafts/${id}/export`,
        speaker ? { speaker } : {}, { responseType: 'blob' })
      const dispo = String(res.headers['content-disposition'] ?? '')
      const match = /filename="?([^";]+)"?/i.exec(dispo)
      const filename = match?.[1]
        ?? `forest-capital-script-${speaker ?? 'master'}.docx`
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
  }, [id, save])

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
  const isScript = draft?.document_type === 'presentation_script'

  // The script's left panel shows estimated delivery time (150 wpm)
  // rather than a word-count target.
  const scriptWordCount = isScript ? countWords(contentText) : 0
  const deliveryMinutes = scriptWordCount / SCRIPT_WORDS_PER_MINUTE
  const scriptMetricLine = isScript
    ? `~${Math.round(deliveryMinutes)} min delivery · ${scriptWordCount} words`
    : undefined
  const scriptMetricTone: 'ok' | 'warn' | undefined = isScript
    ? (deliveryMinutes >= 18 && deliveryMinutes <= 27 ? 'ok' : 'warn')
    : undefined
  // Unique speakers in the script — one export button per speaker.
  const scriptSpeakers = isScript
    ? Array.from(new Set(
        scriptSections(contentJson as TipTapDoc | null)
          .map((s) => s.speaker)
          .filter((x): x is string => Boolean(x))))
    : []

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
    if (isScript) {
      // One section per slide (H2); progress is "has delivery prose yet".
      const secs: NavSection[] = scriptSections(contentJson as TipTapDoc | null)
        .map((s) => ({
          heading: s.heading,
          totalMarkers: 1,
          markersRemaining: s.hasContent ? 0 : 1,
          speaker: s.speaker,
        }))
      return { sections: secs, unresolved: countMarkers(contentText) }
    }
    const secs: NavSection[] = tiptapSections(contentJson as TipTapDoc | null)
      .map((s) => {
        const remaining = countMarkers(s.text)
        const total = Math.max(markerBaseline.current[s.heading] ?? 0, remaining)
        return { heading: s.heading, markersRemaining: remaining,
                 totalMarkers: total }
      })
    return { sections: secs, unresolved: countMarkers(contentText) }
  }, [draft, isDeck, isScript, contentJson, contentText])

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
    // 100dvh — the dynamic viewport unit accounts for iOS Safari's URL
    // bar, which is included in 100vh and would otherwise push the
    // editor shell ~50px below the visible viewport on first paint.
    // 100dvh is supported by every browser the project targets; it
    // resolves identically to 100vh on desktop.
    <div className="flex flex-col h-[calc(100dvh-3rem)]">
      {/* AI DRAFT banner — permanent, non-dismissable. */}
      <div className="bg-warning text-navy-900 text-2xs font-bold uppercase
                      tracking-wide text-center py-1">
        {AI_DRAFT_BANNER}
      </div>

      {/* Auto-fired Academic Review advisory banner — midpoint only,
          score below 6.0. Renders above the header so it's seen the
          moment the draft opens. The score and threshold come from
          the same payload the header pill reads. */}
      {reviewStatus?.status === 'complete'
        && reviewStatus.document_type === 'midpoint_paper'
        && reviewStatus.advisory
        && typeof reviewStatus.score === 'number' && (
        <div data-testid="review-advisory-banner"
          className="bg-warning/15 text-warning border-b border-warning/40
                     px-3 py-2 text-xs">
          Academic Review flagged concerns with this draft
          (score: {reviewStatus.score.toFixed(1)}/10).
          Review the findings in the Council before submitting.
        </div>
      )}

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
          {/* Academic Review score pill — small green when ≥ 6.0,
              amber when below. Shown only when a review has landed;
              while the auto-fire is still running we show a quiet
              "Reviewing…" placeholder so the user knows it's coming. */}
          {reviewStatus && (reviewStatus.document_type === 'midpoint_paper'
              || reviewStatus.document_type === 'executive_brief') && (
            reviewStatus.status === 'complete'
              && typeof reviewStatus.score === 'number' ? (
              <span
                data-testid="review-score-pill"
                data-advisory={reviewStatus.advisory ? 'true' : 'false'}
                title={
                  reviewStatus.rating
                    ? `Academic Review: ${reviewStatus.rating}`
                    : 'Academic Review score'
                }
                className={
                  'text-2xs px-2 py-0.5 rounded-full border shrink-0 ' +
                  (reviewStatus.advisory
                    ? 'bg-warning/10 text-warning border-warning/40'
                    : 'bg-success/10 text-success border-success/40')
                }
              >
                Review {reviewStatus.score.toFixed(1)}/10
              </span>
            ) : (
              <span
                data-testid="review-score-pending"
                className="text-2xs px-2 py-0.5 rounded-full
                           bg-muted/10 text-muted border border-muted/30
                           shrink-0">
                Reviewing…
              </span>
            )
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-2xs text-muted">
            {saveState === 'saving' ? 'Saving…'
              : saveState === 'error' ? 'Save failed'
              : saveState === 'saved' ? `Saved ${lastSaved}` : 'Unsaved changes'}
          </span>
          {isScript ? (
            <>
              {/* Rehearse — opens the combined script + slide overlay.
                  Fetches /api/v1/documents/rehearsal on mount; a missing
                  deck or script surfaces a "Rehearsal requires both…"
                  modal inside the overlay itself. */}
              <button type="button" onClick={() => setRehearsalOpen(true)}
                data-tour="editor-rehearse"
                className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                           border border-electric/40 text-electric
                           hover:bg-electric/10">
                <Mic className="w-3.5 h-3.5" /> Rehearse
              </button>
              <button type="button" onClick={() => void exportScript()}
                disabled={exporting}
                className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                           border border-electric/40 text-electric
                           hover:bg-electric/10 disabled:opacity-50">
                {exporting
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Exporting…</>
                  : <><Download className="w-3.5 h-3.5" />
                      Export Master Script</>}
              </button>
              {scriptSpeakers.map((name) => {
                // Per-speaker colour from the shared palette — the same
                // colour the navigator label uses and the DOCX export
                // applies to the SPEAKER: heading. A presenter scanning
                // the editor recognises their own colour everywhere.
                const colour = getSpeakerColour(name, scriptSpeakers)
                return (
                  <button key={name} type="button"
                    onClick={() => void exportScript(name)} disabled={exporting}
                    style={{ color: colour, borderColor: `${colour}66` }}
                    className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                               border hover:bg-electric/10 disabled:opacity-50">
                    <Download className="w-3.5 h-3.5" /> Export: {name}
                  </button>
                )
              })}
            </>
          ) : (
            <button type="button" onClick={() => void exportDocument()}
              disabled={exporting}
              className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                         border border-electric/40 text-electric
                         hover:bg-electric/10 disabled:opacity-50">
              {exporting
                ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Exporting…</>
                : <><Download className="w-3.5 h-3.5" />
                    {isDeck ? 'Export PPTX' : 'Export DOCX'}</>}
            </button>
          )}
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

      {/* Post-generation audit banner — surfaces flagged numeric
          cross-reference / label-direction / cross-section
          consistency / citation completeness issues from migration
          051's audit. Informational; never blocks editing. */}
      {draft.audit_warnings && draft.audit_warnings.flag_counts
        && draft.audit_warnings.flag_counts.total > 0 && (
        <AuditWarningsBanner
          draftId={id}
          audit={draft.audit_warnings} />
      )}

      {/* Mobile canvas-editor banner — the Konva Stage scales but
          pixel-precise editing is not feasible on touch. The banner
          surfaces the constraint clearly; the navigator drawer still
          works and the speaker notes field is fully editable. */}
      {isDeck && (
        <div className="lg:hidden px-3 py-2 bg-warning/10 border-b
                        border-warning/30 text-2xs text-warning">
          The presentation canvas editor works best on desktop. Open
          on a larger screen for full editing capability.
        </div>
      )}

      {/* Three panels.
          Desktop (lg+): two side asides with the centre between them.
          Mobile/tablet: centre fills the viewport; the two panels
          render as full-screen overlay drawers when opened. The
          mutual exclusion is JS-driven on isDesktop (not pure CSS)
          so jsdom tests see exactly one rendering at a time. */}
      <div className="flex flex-1 min-h-0">
        {/* Left navigator — desktop aside. */}
        {isDesktop && leftOpen && (
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
              scriptSpeakers={isScript ? scriptSpeakers : undefined}
              metricLine={scriptMetricLine}
              metricTone={scriptMetricTone}
              footnote={isScript
                ? 'To rehearse with slides: open your presentation '
                  + 'deck in a second tab and use Presentation Preview '
                  + 'alongside this script.'
                : undefined}
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

        {/* Right Writing Assistant / chart picker — desktop aside. */}
        {isDesktop && rightOpen && (
          <aside className="w-[300px] shrink-0
                            border-l border-border bg-navy-900">
            {isDeck && rightPanelMode === 'chartpicker' ? (
              <ChartPicker onSelect={handleAddChart}
                onClose={() => setRightPanelMode('assistant')} />
            ) : (
              <WritingAssistant draftId={id} unresolvedMarkers={unresolved}
                prefill={assistantPrefill}
                documentType={draft.document_type} />
            )}
          </aside>
        )}
      </div>

      {/* Mobile/tablet panel overlays — full-screen drawers when open.
          The trigger buttons in the header bar toggle the same state
          as the desktop side asides, but below lg the panel renders
          as a slide-in overlay so the editor body keeps the viewport.
          !isDesktop gates the entire block so jsdom (which defaults
          to desktop) never renders both this overlay AND the side
          aside at the same time. */}
      {!isDesktop && leftOpen && (
        <div className="fixed inset-0 z-[70] flex"
             role="dialog" aria-label="Editor navigator" aria-modal="true">
          <div className="fixed inset-0 bg-black/40"
               onClick={() => setLeftOpen(false)} />
          <aside className="relative w-full max-w-[320px] h-full
                            bg-navy-900 border-r border-border
                            flex flex-col">
            <div className="flex items-center justify-between px-3 py-2
                            border-b border-border">
              <span className="text-2xs text-muted uppercase tracking-wide">
                Sections
              </span>
              <button type="button" onClick={() => setLeftOpen(false)}
                aria-label="Close navigator"
                className="text-muted hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <EditorNavigator
                title={draft.title}
                wordCount={countWords(contentText)}
                wordTarget={WORD_TARGETS[draft.document_type] ?? 1500}
                lastSavedLabel={lastSaved}
                saveState={saveState}
                sections={sections}
                versions={versions}
                onJumpToSection={(h) => { jumpToSection(h); setLeftOpen(false) }}
                onSaveVersion={saveVersion}
                onRestoreVersion={restoreVersion}
                onAssignSpeaker={isDeck ? handleAssignSpeaker : undefined}
                speakerSuggestions={isDeck ? speakerSuggestions : undefined}
                scriptSpeakers={isScript ? scriptSpeakers : undefined}
                metricLine={scriptMetricLine}
                metricTone={scriptMetricTone}
                footnote={isScript
                  ? 'To rehearse with slides: open your presentation '
                    + 'deck in a second tab and use Presentation Preview '
                    + 'alongside this script.'
                  : undefined}
              />
            </div>
          </aside>
        </div>
      )}

      {!isDesktop && rightOpen && (
        <div className="fixed inset-0 z-[70] flex justify-end"
             role="dialog" aria-label="Writing assistant" aria-modal="true">
          <div className="fixed inset-0 bg-black/40"
               onClick={() => setRightOpen(false)} />
          <aside className="relative w-full max-w-[360px] h-full
                            bg-navy-900 border-l border-border
                            flex flex-col">
            <div className="flex items-center justify-between px-3 py-2
                            border-b border-border">
              <span className="text-2xs text-muted uppercase tracking-wide">
                Assistant
              </span>
              <button type="button" onClick={() => setRightOpen(false)}
                aria-label="Close assistant"
                className="text-muted hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {isDeck && rightPanelMode === 'chartpicker' ? (
                <ChartPicker onSelect={(k) => { handleAddChart(k); setRightOpen(false) }}
                  onClose={() => setRightPanelMode('assistant')} />
              ) : (
                <WritingAssistant draftId={id} unresolvedMarkers={unresolved}
                  prefill={assistantPrefill}
                  documentType={draft.document_type} />
              )}
            </div>
          </aside>
        </div>
      )}

      {/* Presentation Preview — a full-screen rehearsal view for a deck. */}
      {previewOpen && isDeck && (
        <PresentationPreview
          slides={(contentJson as CanvasDeck | null)?.slides ?? []}
          onClose={() => setPreviewOpen(false)} />
      )}

      {/* Rehearsal Mode — combined script + slide overlay for a script.
          Fetches the deck and script in one call, renders side-by-side
          with keyboard navigation. The overlay handles its own
          loading / 404 / error states. */}
      {rehearsalOpen && isScript && (
        <RehearsalOverlay onClose={() => setRehearsalOpen(false)} />
      )}
    </div>
  )
}

function countWords(text: string): number {
  const t = (text || '').trim()
  return t ? t.split(/\s+/).length : 0
}
