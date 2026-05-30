/**
 * PeerReview — item 7, May 23 2026.
 *
 * Two tabs:
 *   A — Peer Review Assistant: upload another team's midpoint
 *       submission (PDF / DOCX / MD), stream a 3-4 minute review
 *       script back. Tone: critical but professional.
 *   B — Thesis Defense Prep: auto-loads the team's most recent
 *       midpoint draft, generates a mock-panel Q&A prep sheet
 *       across technical / academic / governance categories.
 *
 * Both flows talk to harness-gated Opus endpoints that stream the
 * verdict via Server-Sent Events. The same SSE wire pattern as
 * /api/council/academic-review (arbiter_chunk frames + a [DONE]
 * sentinel) so a future refactor can share a consumer helper.
 *
 * Results live in peerReviewStore so a navigate-away-and-back
 * round-trip is instant — the user keeps the last verdict until
 * they fire a fresh run.
 */
import { useCallback, useRef, useState } from 'react'
import {
  AlertCircle, ClipboardCheck, FileText, GraduationCap,
  Loader2, Upload, X,
} from 'lucide-react'
import { usePeerReviewStore } from '../stores/peerReviewStore'
import Markdown from '../components/Markdown'
import FloatingSectionNav from '../components/FloatingSectionNav'


type Tab = 'peer-review' | 'defense-prep'


export default function PeerReview() {
  const [tab, setTab] = useState<Tab>('peer-review')

  // May 24 2026 — defensive log to surface a blank-page failure
  // (Dr. Panttser's first-load report) in the browser console.
  // The page itself renders unconditional content below, so any
  // "blank" symptom now points at a CSS / hydration race rather
  // than a missing state branch.
  return (
    <div
      className="p-4 md:p-6 max-w-screen-2xl mx-auto"
      data-testid="peer-review-page">
      <FloatingSectionNav pageKey="peer-review" minSections={2} />
      <header
        className="mb-4"
        data-section-id="peer-review-overview"
        data-section-label="Overview">
        <h1 className="text-white font-semibold text-xl flex items-center gap-2">
          <ClipboardCheck className="w-5 h-5 text-electric-blue" />
          Peer Review
        </h1>
        <p className="text-text-secondary text-sm">
          Two flows for the cohort meetup: critically review another
          team's submission, and stress-test your own draft against
          an anticipated panel Q&amp;A.
        </p>
        {/* May 24 2026 — first-load welcome card. The page used to
            land on the active tab's form immediately, which a few
            testers reported as "blank" on first visit (the form
            renders but with no visible header beyond the page
            title, looking sparse). This banner gives the user an
            unambiguous "you are on the Peer Review page" cue
            even before they click a tab. */}
        <div
          data-testid="peer-review-welcome"
          className="mt-3 px-3 py-2 rounded
                     border border-navy-700 bg-navy-900
                     text-2xs text-text-muted leading-relaxed">
          <p>
            <span className="text-text-secondary font-medium">
              Pick a tab below.
            </span>
            {' '}
            <span className="text-text-primary">
              Peer Review Assistant
            </span>
            {' '}uploads another team's submission and streams a
            rubric-aligned critique; {' '}
            <span className="text-text-primary">
              Thesis Defense Prep
            </span>
            {' '}auto-loads your most-recent midpoint draft and
            generates a mock-panel Q&amp;A sheet.
          </p>
        </div>
      </header>

      <div className="mb-4 flex border-b border-navy-700">
        <TabButton
          active={tab === 'peer-review'}
          onClick={() => setTab('peer-review')}
          testId="peer-review-tab">
          <ClipboardCheck className="w-4 h-4" />
          Peer Review Assistant
        </TabButton>
        <TabButton
          active={tab === 'defense-prep'}
          onClick={() => setTab('defense-prep')}
          testId="defense-prep-tab">
          <GraduationCap className="w-4 h-4" />
          Thesis Defense Prep
        </TabButton>
      </div>

      {tab === 'peer-review' ? (
        <div
          data-section-id="peer-review-assistant"
          data-section-label="Peer Review Assistant">
          <PeerReviewAssistant />
        </div>
      ) : null}
      {tab === 'defense-prep' ? (
        <div
          data-section-id="defense-prep"
          data-section-label="Thesis Defense Prep">
          <ThesisDefensePrep />
        </div>
      ) : null}
    </div>
  )
}


interface TabButtonProps {
  active: boolean
  onClick: () => void
  testId: string
  children: React.ReactNode
}

function TabButton({ active, onClick, testId, children }: TabButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={
        'px-4 py-2 text-sm font-medium border-b-2 transition-colors '
        + 'flex items-center gap-2 -mb-px '
        + (active
            ? 'border-electric-blue text-electric-blue'
            : 'border-transparent text-text-secondary '
              + 'hover:text-text-primary hover:border-navy-600')
      }>
      {children}
    </button>
  )
}


// ── Feature A — Peer Review Assistant ───────────────────────────────────────


function PeerReviewAssistant() {
  const slot = usePeerReviewStore((s) => s.peerReview)
  const start = usePeerReviewStore((s) => s.startPeerReview)
  const setMeta = usePeerReviewStore((s) => s.setPeerReviewMeta)
  const appendChunk = usePeerReviewStore((s) => s.appendPeerReviewChunk)
  const finish = usePeerReviewStore((s) => s.finishPeerReview)
  const fail = usePeerReviewStore((s) => s.failPeerReview)
  const reset = usePeerReviewStore((s) => s.resetPeerReview)

  const [file, setFile] = useState<File | null>(null)
  const [submissionName, setSubmissionName] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null
    setFile(f)
    if (f && !submissionName) {
      // Default the display name to the filename stem.
      const stem = f.name.replace(/\.(pdf|docx|md|markdown)$/i, '')
      setSubmissionName(stem)
    }
  }

  const onRun = useCallback(async () => {
    if (!file) return
    start()
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const token = localStorage.getItem('fc_session_token') ?? ''
      const form = new FormData()
      form.append('file', file)
      form.append('submission_name', submissionName.trim())
      const res = await fetch('/api/council/peer-review', {
        method: 'POST',
        headers: { 'X-API-Key': token },
        body: form,
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        // Try to parse the structured detail FastAPI returns on 4xx.
        let detail = `Request failed (${res.status})`
        try {
          const body = await res.json()
          if (typeof body?.detail === 'string') detail = body.detail
        } catch { /* not JSON */ }
        throw new Error(detail)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep).trim()
          buffer = buffer.slice(sep + 2)
          if (!frame.startsWith('data:')) continue
          const payload = frame.slice(5).trim()
          if (payload === '[DONE]') {
            finish()
            continue
          }
          let evt: {
            type?: string
            name?: string
            char_count?: number
            text?: string
            message?: string
          }
          try {
            evt = JSON.parse(payload)
          } catch {
            continue
          }
          if (evt.type === 'submission_meta') {
            setMeta({
              name: evt.name ?? 'submission',
              char_count: evt.char_count ?? 0,
            })
          } else if (evt.type === 'arbiter_chunk') {
            appendChunk(evt.text ?? '')
          } else if (evt.type === 'error') {
            fail(evt.message ?? 'Peer review failed.')
          }
        }
      }
      finish()
    } catch (err) {
      if (controller.signal.aborted) return
      fail(err instanceof Error ? err.message : 'Peer review failed.')
    } finally {
      abortRef.current = null
    }
  }, [file, submissionName, start, setMeta, appendChunk, finish, fail])

  const onCancel = () => {
    abortRef.current?.abort()
    abortRef.current = null
  }

  return (
    <section className="space-y-4">
      <div className="bg-navy-900 border border-navy-700 rounded p-4 space-y-3">
        <h2 className="text-white font-semibold text-sm flex items-center gap-1.5">
          <Upload className="w-4 h-4 text-electric-blue" />
          Upload the peer team's submission
        </h2>
        <p className="text-text-secondary text-xs">
          PDF, DOCX, or Markdown. Max 2 MB. The file is processed
          in-memory and never stored.
        </p>

        <div className="flex flex-col sm:flex-row gap-3 items-start">
          <input
            type="file"
            accept=".pdf,.docx,.md,.markdown"
            onChange={onFileChange}
            data-testid="peer-review-file-input"
            className={
              'text-text-secondary text-xs '
              + 'file:mr-3 file:px-3 file:py-1.5 file:rounded '
              + 'file:border file:border-navy-600 file:bg-navy-800 '
              + 'file:text-text-primary file:text-xs '
              + 'file:hover:bg-navy-700 file:cursor-pointer'
            } />
          <input
            type="text"
            value={submissionName}
            onChange={(e) => setSubmissionName(e.target.value)}
            placeholder="Display name (optional)"
            data-testid="peer-review-name-input"
            className={
              'flex-1 text-xs px-2 py-1.5 rounded '
              + 'bg-navy-800 border border-navy-700 text-white '
              + 'placeholder:text-text-muted'
            } />
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={onRun}
            disabled={!file || slot.loading}
            data-testid="peer-review-run"
            className={
              'px-3 py-1.5 rounded text-xs font-medium '
              + 'bg-electric-blue text-navy-950 '
              + 'hover:bg-electric-blue/90 '
              + 'disabled:opacity-50 disabled:cursor-not-allowed'
            }>
            {slot.loading ? 'Running review…' : 'Run peer review'}
          </button>
          {slot.loading ? (
            <button
              type="button"
              onClick={onCancel}
              data-testid="peer-review-cancel"
              className={
                'px-2 py-1.5 rounded text-xs '
                + 'border border-navy-600 text-text-secondary '
                + 'hover:bg-navy-800'
              }>
              <X className="w-3 h-3 inline" /> Cancel
            </button>
          ) : null}
          {slot.verdict || slot.error ? (
            <button
              type="button"
              onClick={reset}
              className={
                'px-2 py-1.5 rounded text-xs '
                + 'border border-navy-600 text-text-secondary '
                + 'hover:bg-navy-800'
              }>
              Clear
            </button>
          ) : null}
        </div>
      </div>

      {slot.error ? (
        <ErrorCard message={slot.error} />
      ) : null}

      {slot.loading && !slot.verdict ? (
        <LoadingCard label="Reviewing the submission against the FNA 670 rubric. This usually takes 30-60 seconds." />
      ) : null}

      {slot.verdict ? (
        <VerdictCard
          title={
            slot.submissionMeta
              ? `Review of ${slot.submissionMeta.name}`
              : 'Peer review'
          }
          meta={
            slot.submissionMeta
              ? `${slot.submissionMeta.char_count.toLocaleString()} characters extracted`
              : null
          }
          verdict={slot.verdict}
          streaming={slot.loading}
        />
      ) : null}
    </section>
  )
}


// ── Feature B — Thesis Defense Prep ─────────────────────────────────────────


function ThesisDefensePrep() {
  const slot = usePeerReviewStore((s) => s.defensePrep)
  const start = usePeerReviewStore((s) => s.startDefensePrep)
  const setMeta = usePeerReviewStore((s) => s.setDefensePrepMeta)
  const appendChunk = usePeerReviewStore((s) => s.appendDefensePrepChunk)
  const finish = usePeerReviewStore((s) => s.finishDefensePrep)
  const fail = usePeerReviewStore((s) => s.failDefensePrep)
  const reset = usePeerReviewStore((s) => s.resetDefensePrep)

  const abortRef = useRef<AbortController | null>(null)
  // Session-only — the uploaded document is held in component state and
  // sent via FormData; it is never persisted on the server.
  const [file, setFile] = useState<File | null>(null)

  const onPickFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setFile(e.target.files?.[0] ?? null)
      reset()
    }, [reset])

  const onRemove = useCallback(() => {
    setFile(null)
    reset()
  }, [reset])

  const onRun = useCallback(async () => {
    if (!file) return
    start()
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const token = localStorage.getItem('fc_session_token') ?? ''
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/api/council/defense-prep', {
        method: 'POST',
        // No Content-Type — the browser sets multipart boundaries.
        headers: { 'X-API-Key': token },
        body: form,
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        let detail = `Request failed (${res.status})`
        try {
          const body = await res.json()
          if (typeof body?.detail === 'string') detail = body.detail
        } catch { /* not JSON */ }
        throw new Error(detail)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep).trim()
          buffer = buffer.slice(sep + 2)
          if (!frame.startsWith('data:')) continue
          const payload = frame.slice(5).trim()
          if (payload === '[DONE]') {
            finish()
            continue
          }
          let evt: {
            type?: string
            title?: string
            word_count?: number
            updated_at?: string | null
            text?: string
            message?: string
          }
          try {
            evt = JSON.parse(payload)
          } catch {
            continue
          }
          if (evt.type === 'draft_meta') {
            setMeta({
              title: evt.title ?? 'Draft',
              word_count: evt.word_count ?? 0,
              updated_at: evt.updated_at ?? null,
            })
          } else if (evt.type === 'arbiter_chunk') {
            appendChunk(evt.text ?? '')
          } else if (evt.type === 'error') {
            fail(evt.message ?? 'Defense prep failed.')
          }
        }
      }
      finish()
    } catch (err) {
      if (controller.signal.aborted) return
      fail(err instanceof Error ? err.message : 'Defense prep failed.')
    } finally {
      abortRef.current = null
    }
  }, [file, start, setMeta, appendChunk, finish, fail])

  // No auto-run on mount — Defense Prep burns Opus tokens, so it
  // fires on an explicit click. The uploaded document is the only
  // source; the click is the user's "yes, run this now" gesture.

  return (
    <section className="space-y-4">
      <div className="bg-navy-900 border border-navy-700 rounded p-4 space-y-3">
        <h2 className="text-white font-semibold text-sm flex items-center gap-1.5">
          <GraduationCap className="w-4 h-4 text-warning" />
          Mock panel Q&amp;A — against your uploaded document
        </h2>
        <p className="text-text-secondary text-xs">
          Upload the document you want to defend (.pdf or .docx). The
          mock panel answers from this document only — nothing is saved
          on the server. Generates anticipated questions across three
          categories — technical, academic, governance — with
          rehearsable responses for each.
        </p>

        {/* Upload area / file status */}
        {!file ? (
          <label
            data-testid="defense-prep-file-input"
            className={
              'flex items-center gap-2 px-3 py-2 border border-dashed '
              + 'border-navy-600 rounded text-text-secondary text-xs '
              + 'hover:bg-navy-800/40 cursor-pointer w-fit'
            }>
            <Upload className="w-3.5 h-3.5 text-electric-blue" />
            <span>Choose a .pdf or .docx</span>
            <input
              type="file"
              accept=".pdf,.docx"
              className="hidden"
              onChange={onPickFile}
            />
          </label>
        ) : (
          <div className="flex items-center gap-2 text-xs">
            <FileText className="w-3.5 h-3.5 text-electric-blue shrink-0" />
            <span
              data-testid="defense-prep-filename"
              className="text-white truncate max-w-[28rem]">
              Answering from: {file.name}
            </span>
            <button
              type="button"
              onClick={onRemove}
              data-testid="defense-prep-remove"
              className={
                'ml-2 px-2 py-0.5 rounded border border-navy-600 '
                + 'text-text-secondary hover:bg-navy-800'
              }>
              Remove
            </button>
          </div>
        )}
        {!file ? (
          <p className="text-text-secondary text-xs italic">
            Upload a document before asking questions.
          </p>
        ) : null}

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={onRun}
            disabled={!file || slot.loading}
            data-testid="defense-prep-run"
            title={!file ? 'Upload a .pdf or .docx first' : undefined}
            className={
              'px-3 py-1.5 rounded text-xs font-medium '
              + 'bg-warning text-navy-950 hover:bg-warning/90 '
              + 'disabled:opacity-50 disabled:cursor-not-allowed'
            }>
            {slot.loading ? 'Generating Q&A…' : 'Run defense prep'}
          </button>
          {slot.loading ? (
            <button
              type="button"
              onClick={() => abortRef.current?.abort()}
              data-testid="defense-prep-cancel"
              className={
                'px-2 py-1.5 rounded text-xs '
                + 'border border-navy-600 text-text-secondary '
                + 'hover:bg-navy-800'
              }>
              <X className="w-3 h-3 inline" /> Cancel
            </button>
          ) : null}
          {slot.verdict || slot.error ? (
            <button
              type="button"
              onClick={reset}
              className={
                'px-2 py-1.5 rounded text-xs '
                + 'border border-navy-600 text-text-secondary '
                + 'hover:bg-navy-800'
              }>
              Clear
            </button>
          ) : null}
        </div>
      </div>

      {slot.error ? (
        <ErrorCard message={slot.error} />
      ) : null}

      {slot.loading && !slot.verdict ? (
        <LoadingCard label="Stress-testing your draft against the mock panel. This usually takes 30-60 seconds." />
      ) : null}

      {slot.verdict ? (
        <VerdictCard
          title={
            slot.draftMeta
              ? `Q&A prep — ${slot.draftMeta.title}`
              : 'Q&A prep'
          }
          meta={
            slot.draftMeta
              ? `${slot.draftMeta.word_count.toLocaleString()} words · uploaded`
              : null
          }
          verdict={slot.verdict}
          streaming={slot.loading}
        />
      ) : null}
    </section>
  )
}


// ── Shared sub-components ───────────────────────────────────────────────────


interface VerdictCardProps {
  title: string
  meta: string | null
  verdict: string
  streaming: boolean
}


function VerdictCard({ title, meta, verdict, streaming }: VerdictCardProps) {
  return (
    <article
      data-testid="peer-review-verdict"
      className="bg-navy-900 border border-navy-700 rounded p-4 space-y-2">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-white font-semibold text-sm flex items-center gap-1.5">
            <FileText className="w-4 h-4 text-electric-blue" />
            {title}
          </h3>
          {meta ? (
            <p className="text-text-muted text-xs mt-0.5">{meta}</p>
          ) : null}
        </div>
        {streaming ? (
          <Loader2
            className="w-3.5 h-3.5 animate-spin text-text-muted shrink-0 mt-1"
            aria-label="Streaming" />
        ) : null}
      </header>
      <div className="text-text-secondary text-sm leading-relaxed">
        <Markdown content={verdict} />
      </div>
    </article>
  )
}


function LoadingCard({ label }: { label: string }) {
  return (
    <div
      data-testid="peer-review-loading"
      className="bg-navy-900 border border-navy-700 rounded p-4 flex items-start gap-3">
      <Loader2 className="w-4 h-4 animate-spin text-electric-blue mt-0.5" />
      <p className="text-text-secondary text-sm">{label}</p>
    </div>
  )
}


function ErrorCard({ message }: { message: string }) {
  return (
    <div
      data-testid="peer-review-error"
      className="bg-red-500/10 border border-red-500/30 rounded p-3 flex items-start gap-2">
      <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
      <p className="text-red-300 text-sm">{message}</p>
    </div>
  )
}


