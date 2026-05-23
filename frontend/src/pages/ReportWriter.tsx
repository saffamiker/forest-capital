/**
 * frontend/src/pages/ReportWriter.tsx
 *
 * The eleven-step report writer page. Surfaces:
 *
 *   - Template dropdown (drives every endpoint slug)
 *   - 11-step pipeline status panel
 *   - Rubric panel (collapsible)
 *   - Inline editor (textarea) + preview pane with highlighted
 *     [BOB] blocks Bob can resolve individually
 *   - AI iteration toolbar (rephrase / tighten / expand / ask)
 *   - Word count per section with traffic-light status
 *   - Run Final Check button
 *   - Run Academic Review button + results panel
 *   - Two download buttons (paper + appendix), soft-gated by
 *     readiness + hard-gated by flag_count
 *
 * Editor model: paper_md is the source of truth and is round-tripped
 * with the server on every change via PATCH /paper-md (debounced
 * 1.5s). The Done button on a BOB block POSTs /resolve-bob and the
 * response carries the new paper_md + flag list. The AI toolbar
 * POSTs /iterate and renders the proposal in a review card before
 * Bob accepts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import {
  FileText, Download, Play, Search, AlertCircle,
} from 'lucide-react'

import BobBlockBadge from '../components/reportwriter/BobBlockBadge'
import IterationToolbar from '../components/reportwriter/IterationToolbar'
import AcademicReviewPanel from '../components/reportwriter/AcademicReviewPanel'
import type { AcademicReview } from '../components/reportwriter/AcademicReviewPanel'
import RubricPanel from '../components/reportwriter/RubricPanel'
import type { Rubric } from '../components/reportwriter/RubricPanel'
import PipelineSteps from '../components/reportwriter/PipelineSteps'
import type { PipelineStep } from '../components/reportwriter/PipelineSteps'
import {
  extractBobBlocks, tokenize,
  SECTION_BUDGETS, countWords, wordCountStatus,
} from '../lib/bobBlocks'

interface Template {
  template_id: string
  display_name: string
  course?: string | null
  format_spec?: Record<string, unknown> | null
}

interface GenerationResponse {
  id: number
  template_id: string
  paper_md: string
  appendix_md: string
  flag_count: number
  bob_block_count: number
  flags: Array<Record<string, unknown>>
  bob_blocks: Array<{
    marker: string; kind: string; description: string; position: number
  }>
  word_counts: {
    per_section?: Record<string, { words: number; budget: number; status: string }>
    total?: { words: number; budget: number; status: string }
  }
  verified_data?: Record<string, unknown>
  ranked_findings?: Array<Record<string, unknown>>
  citations?: Record<string, unknown>
  thesis_validation?: { passed: boolean; conditions?: Array<unknown> }
  academic_readiness?: string | null
}


export default function ReportWriter() {
  const [templates, setTemplates] = useState<Template[]>([])
  const [templateId, setTemplateId] = useState<string>('midpoint_check_fna670')
  const [rubric, setRubric] = useState<Rubric | null>(null)
  const [generation, setGeneration] = useState<GenerationResponse | null>(null)
  const [paperMd, setPaperMd] = useState('')
  const [stepStatus, setStepStatus] = useState<Record<number, PipelineStep['status']>>({})
  const [stepDetail, setStepDetail] = useState<Record<number, string>>({})
  const [error, setError] = useState<string | null>(null)

  const [generating, setGenerating] = useState(false)
  const [savingPatch, setSavingPatch] = useState(false)
  const [runningCheck, setRunningCheck] = useState(false)
  const [runningReview, setRunningReview] = useState(false)
  const [review, setReview] = useState<AcademicReview | null>(null)

  const [selectedText, setSelectedText] = useState('')
  const [downloading, setDownloading] = useState<'paper' | 'appendix' | null>(null)

  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  // ── Initial template + rubric load ────────────────────────────────────────
  useEffect(() => {
    axios.get<{ templates: Template[] }>('/api/v1/reports/templates')
      .then((r) => setTemplates(r.data.templates || []))
      .catch(() => setTemplates([]))
  }, [])

  useEffect(() => {
    if (!templateId) return
    axios.get<{ rubric: Rubric | null }>(
      `/api/v1/reports/templates/${templateId}/rubric`)
      .then((r) => setRubric(r.data.rubric ?? null))
      .catch(() => setRubric(null))
  }, [templateId])

  // ── Pipeline steps memo ───────────────────────────────────────────────────
  const steps: PipelineStep[] = useMemo(() => {
    const labels = [
      'Stage Findings', 'Source Citations', 'Pull Team Activity',
      'Pull Validation Data', 'Cross-Reference Check (auto)',
      'Thesis Validation (auto)', 'Generate Draft',
      'Review and Edit Draft', 'Run Final Check',
      'Run Academic Review', 'Download',
    ]
    return labels.map((label, idx) => {
      const number = idx + 1
      return {
        number,
        label,
        status: stepStatus[number] ?? 'idle',
        detail: stepDetail[number],
      }
    })
  }, [stepStatus, stepDetail])

  const setStep = useCallback(
    (n: number, status: PipelineStep['status'], detail?: string) => {
      setStepStatus((s) => ({ ...s, [n]: status }))
      if (detail !== undefined) {
        setStepDetail((s) => ({ ...s, [n]: detail }))
      }
    }, [])

  // ── Generation ────────────────────────────────────────────────────────────
  const handleGenerate = async () => {
    setError(null)
    setGenerating(true)
    setReview(null)
    // Mark steps 1-7 in_progress; the backend will resolve everything in
    // one shot, then we light up 1-7 complete from the response.
    for (let i = 1; i <= 7; i++) {
      setStep(i, 'in_progress')
    }
    try {
      const res = await axios.post<GenerationResponse>(
        `/api/v1/reports/templates/${templateId}/generate`)
      const data = res.data
      setGeneration(data)
      setPaperMd(data.paper_md || '')
      for (let i = 1; i <= 7; i++) {
        setStep(i, 'complete')
      }
      const bobCount = data.bob_block_count ?? 0
      setStep(8,
        bobCount > 0 ? 'warning' : 'complete',
        bobCount > 0 ? `${bobCount} block${bobCount === 1 ? '' : 's'} remaining` : 'ready')
      setStep(9, 'pending')
      setStep(10, 'pending')
      setStep(11, 'pending')
    } catch (err) {
      let msg = 'Generation failed.'
      let thesisDetail: string | null = null
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail
        if (typeof detail === 'string') {
          msg = detail
        } else if (typeof detail === 'object' && detail !== null) {
          const obj = detail as { error?: string; thesis_validation?: { blocker_reasons?: string[] } }
          if (obj.error === 'thesis_validation_blocked') {
            msg = 'Thesis validation blocked generation. See pipeline step 6.'
            const reasons = obj.thesis_validation?.blocker_reasons ?? []
            thesisDetail = reasons.join(' · ')
          }
        }
      }
      setError(msg)
      setStep(6, 'failed', thesisDetail || undefined)
      for (let i = 7; i <= 11; i++) setStep(i, 'pending')
    } finally {
      setGenerating(false)
    }
  }

  // ── Debounced paper_md save ───────────────────────────────────────────────
  const saveTimerRef = useRef<number | null>(null)
  const queuedMdRef = useRef<string | null>(null)
  const onPaperMdChange = (next: string) => {
    setPaperMd(next)
    if (!generation) return
    queuedMdRef.current = next
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(async () => {
      const queued = queuedMdRef.current
      if (queued === null) return
      setSavingPatch(true)
      try {
        const res = await axios.patch<GenerationResponse>(
          `/api/v1/reports/generations/${generation.id}/paper-md`,
          { paper_md: queued })
        // Merge response back so flag_count etc. is current.
        setGeneration((g) => g ? { ...g, ...res.data } : g)
      } catch {
        // Best-effort save; the next debounce will retry.
      } finally {
        setSavingPatch(false)
      }
    }, 1500)
  }

  useEffect(() => () => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
  }, [])

  // ── BOB block resolution ──────────────────────────────────────────────────
  const handleResolveBob = useCallback(async (
    marker: string, replacement: string,
  ): Promise<void> => {
    if (!generation) return
    const res = await axios.post<GenerationResponse>(
      `/api/v1/reports/generations/${generation.id}/resolve-bob`,
      { marker, replacement })
    setGeneration((g) => g ? { ...g, ...res.data } : g)
    setPaperMd(res.data.paper_md || paperMd)
    const remaining = res.data.bob_block_count ?? 0
    setStep(8,
      remaining > 0 ? 'warning' : 'complete',
      remaining > 0
        ? `${remaining} block${remaining === 1 ? '' : 's'} remaining`
        : 'all resolved')
  }, [generation, paperMd, setStep])

  // ── AI iteration ──────────────────────────────────────────────────────────
  const handleIterate = useCallback(async (
    action: 'rephrase' | 'tighten' | 'expand' | 'ask',
    instruction?: string,
  ) => {
    if (!generation) {
      throw new Error('No generation in progress.')
    }
    const res = await axios.post<{
      original: string; rewritten: string;
      word_delta: number;
      new_unverified_numbers: number[];
      new_unverified_citations: string[];
    }>(
      `/api/v1/reports/generations/${generation.id}/iterate`,
      { action, selection: selectedText, instruction })
    return res.data
  }, [generation, selectedText])

  const handleAcceptRewrite = useCallback((rewritten: string) => {
    if (!selectedText) return
    const next = paperMd.replace(selectedText, rewritten)
    onPaperMdChange(next)
    setSelectedText('')
  }, [paperMd, selectedText])

  // ── Final check ───────────────────────────────────────────────────────────
  const handleRunFinalCheck = async () => {
    if (!generation) return
    setRunningCheck(true)
    setStep(9, 'in_progress')
    try {
      const res = await axios.post<GenerationResponse & { passed: boolean }>(
        `/api/v1/reports/generations/${generation.id}/final-check`)
      setGeneration((g) => g ? { ...g, ...res.data } : g)
      setStep(9, res.data.passed ? 'complete' : 'warning',
        res.data.passed
          ? 'zero flags'
          : `${res.data.flag_count} flag${res.data.flag_count === 1 ? '' : 's'}`)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail || err.message) : 'Final check failed.'
      setStep(9, 'failed', String(msg))
    } finally {
      setRunningCheck(false)
    }
  }

  // ── Academic review ───────────────────────────────────────────────────────
  const handleRunReview = async () => {
    if (!generation) return
    setRunningReview(true)
    setStep(10, 'in_progress')
    try {
      const res = await axios.post<AcademicReview & { rubric_version?: number }>(
        `/api/v1/reports/generations/${generation.id}/academic-review`)
      setReview(res.data)
      let stat: PipelineStep['status'] = 'complete'
      if (res.data.readiness === 'needs_significant_revision') {
        stat = 'failed'
      } else if (res.data.readiness === 'needs_minor_revision') {
        stat = 'warning'
      }
      setStep(10, stat, (res.data.readiness || '').replace(/_/g, ' '))
      setGeneration((g) => g ? { ...g, academic_readiness: res.data.readiness } : g)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail || err.message) : 'Review failed.'
      setStep(10, 'failed', String(msg))
    } finally {
      setRunningReview(false)
    }
  }

  // ── Downloads ─────────────────────────────────────────────────────────────
  const handleDownload = async (which: 'paper' | 'appendix') => {
    if (!generation) return
    setDownloading(which)
    try {
      const url = which === 'paper'
        ? `/api/v1/reports/generations/${generation.id}/download-paper`
        : `/api/v1/reports/generations/${generation.id}/download-appendix`
      const res = await axios.get(url, { responseType: 'blob' })
      const dispo = String(res.headers['content-disposition'] ?? '')
      const m = /filename="?([^";]+)"?/i.exec(dispo)
      const filename = m?.[1] ?? `forest-capital-${which}.docx`
      triggerBlobDownload(res.data as Blob, filename)
      if (which === 'paper') setStep(11, 'complete', 'downloaded')
    } catch (err) {
      let msg = 'Download failed.'
      let allowAck = false
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail
        if (typeof detail === 'object' && detail !== null) {
          const obj = detail as { error?: string; message?: string }
          msg = obj.message || msg
          if (obj.error === 'academic_review_significant_revision') {
            allowAck = true
          }
        }
      }
      if (allowAck && window.confirm(`${msg}\n\nDownload anyway?`)) {
        try {
          const url = `/api/v1/reports/generations/${generation.id}/download-paper`
                     + '?acknowledge_warning=true'
          const res = await axios.get(url, { responseType: 'blob' })
          const dispo = String(res.headers['content-disposition'] ?? '')
          const m = /filename="?([^";]+)"?/i.exec(dispo)
          const filename = m?.[1] ?? 'forest-capital-paper.docx'
          triggerBlobDownload(res.data as Blob, filename)
          setStep(11, 'warning', 'downloaded with override')
        } catch {
          setError('Download failed even with override.')
        }
      } else {
        setError(msg)
      }
    } finally {
      setDownloading(null)
    }
  }

  // ── Selection capture (for AI toolbar) ────────────────────────────────────
  const captureSelection = () => {
    const ta = textareaRef.current
    if (!ta) return
    const start = ta.selectionStart
    const end = ta.selectionEnd
    if (start !== end) {
      setSelectedText(paperMd.slice(start, end))
    } else {
      setSelectedText('')
    }
  }

  // ── BOB block list (sidebar) ──────────────────────────────────────────────
  const blocks = useMemo(() => extractBobBlocks(paperMd), [paperMd])
  const bobCount = blocks.length
  const flagCount = generation?.flag_count ?? 0

  // ── Word count summary ────────────────────────────────────────────────────
  const sectionCounts = useMemo(() => {
    const lines = paperMd.split(/\n+/)
    const buckets: Record<number, string[]> = { 1: [], 2: [], 3: [], 4: [] }
    let current = 0
    for (const line of lines) {
      const m = /^#{1,3}\s*(?:SECTION\s+)?(\d)\.?/i.exec(line)
      if (m) {
        current = parseInt(m[1], 10)
      } else if (current >= 1 && current <= 4) {
        buckets[current].push(line)
      }
    }
    const out: Record<number, { words: number; budget: number; status: string }> = {}
    let total = 0
    for (const n of [1, 2, 3, 4]) {
      const words = countWords(buckets[n].join(' '))
      total += words
      const budget = SECTION_BUDGETS[n]
      out[n] = { words, budget, status: wordCountStatus(words, budget) }
    }
    return { sections: out, total, total_budget: 825 }
  }, [paperMd])

  const downloadGateLabel = useMemo(() => {
    if (!generation) return 'Generate the draft first'
    if (flagCount > 0) {
      return `${flagCount} unresolved flag${flagCount === 1 ? '' : 's'} — resolve before downloading`
    }
    if (generation.academic_readiness === 'needs_significant_revision') {
      return 'Academic review flagged significant gaps — override required'
    }
    return null
  }, [generation, flagCount])

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="p-4 md:p-6 max-w-screen-2xl mx-auto" data-testid="report-writer-page">
      <header className="mb-4">
        <h1 className="text-white font-semibold text-xl flex items-center gap-2">
          <FileText className="w-5 h-5 text-electric-blue" />
          Report Writer
        </h1>
        <p className="text-text-secondary text-sm">
          Pull verified data, generate a midpoint paper draft, resolve
          callout points, and download a submission-ready docx.
        </p>
      </header>

      {/* Template selector */}
      <div className="mb-4 flex items-center gap-3 flex-wrap">
        <label className="text-text-secondary text-xs">Template:</label>
        <select
          value={templateId}
          onChange={(e) => setTemplateId(e.target.value)}
          data-testid="template-selector"
          className={
            'px-2 py-1.5 bg-navy-900 border border-navy-700 ' +
            'rounded text-white text-sm focus:outline-none ' +
            'focus:border-electric-blue'
          }>
          <option value="midpoint_check_fna670">
            Midpoint Check Paper — FNA670
          </option>
          {templates
            .filter((t) => t.template_id !== 'midpoint_check_fna670')
            .map((t) => (
              <option key={t.template_id} value={t.template_id}>
                {t.display_name}
              </option>
            ))}
        </select>
        <button
          type="button"
          disabled={generating}
          onClick={handleGenerate}
          data-testid="generate-button"
          className={
            'inline-flex items-center gap-2 px-3 py-1.5 ' +
            'bg-electric-blue hover:bg-electric-blue/80 ' +
            'disabled:bg-navy-700 disabled:text-text-muted ' +
            'text-white text-sm font-medium rounded'
          }>
          <Play className="w-3.5 h-3.5" />
          {generating ? 'Generating…' : 'Generate Draft'}
        </button>
        {savingPatch ? (
          <span className="text-text-muted text-xs italic">Saving…</span>
        ) : null}
        {error ? (
          <span className="text-red-400 text-xs flex items-center gap-1">
            <AlertCircle className="w-3.5 h-3.5" />
            {error}
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Sidebar */}
        <aside className="lg:col-span-1 space-y-4">
          <PipelineSteps steps={steps} />
          <RubricPanel
            rubric={rubric}
            formatSpec={
              templates.find((t) => t.template_id === templateId)?.format_spec
              ?? null
            }
          />
          <CalloutSidebar
            count={bobCount}
            blocks={blocks}
            onResolve={handleResolveBob}
            disabled={!generation}
          />
          <WordCountSidebar counts={sectionCounts} />
        </aside>

        {/* Editor + AI toolbar + reviews */}
        <main className="lg:col-span-2 space-y-4">
          <div
            data-tour="editor-iteration"
            className="bg-navy-900 border border-navy-700 rounded p-3 space-y-3">
            <IterationToolbar
              selectedText={selectedText}
              onRun={handleIterate}
              onAccept={handleAcceptRewrite}
              disabled={!generation}
            />
            <textarea
              ref={textareaRef}
              value={paperMd}
              onChange={(e) => onPaperMdChange(e.target.value)}
              onSelect={captureSelection}
              onMouseUp={captureSelection}
              onKeyUp={captureSelection}
              placeholder={
                generation
                  ? 'Edit the draft inline. Highlight any text to enable the AI toolbar.'
                  : 'Click Generate Draft to produce the initial paper.'
              }
              rows={26}
              data-testid="paper-editor"
              className={
                'w-full p-3 bg-navy-950 border border-navy-700 ' +
                'rounded text-white text-sm font-mono leading-relaxed ' +
                'focus:outline-none focus:border-electric-blue'
              }
            />
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={!generation || runningCheck}
                  onClick={handleRunFinalCheck}
                  data-testid="final-check-button"
                  className={
                    'inline-flex items-center gap-1.5 px-3 py-1.5 ' +
                    'bg-navy-800 hover:bg-navy-700 ' +
                    'disabled:bg-navy-900 disabled:text-text-muted ' +
                    'border border-navy-700 text-white text-sm rounded'
                  }>
                  <Search className="w-3.5 h-3.5" />
                  {runningCheck ? 'Checking…' : 'Run Final Check'}
                </button>
                <button
                  type="button"
                  disabled={!generation || runningReview || flagCount > 0}
                  onClick={handleRunReview}
                  data-testid="academic-review-button"
                  className={
                    'inline-flex items-center gap-1.5 px-3 py-1.5 ' +
                    'bg-navy-800 hover:bg-navy-700 ' +
                    'disabled:bg-navy-900 disabled:text-text-muted ' +
                    'border border-navy-700 text-white text-sm rounded'
                  }>
                  <FileText className="w-3.5 h-3.5" />
                  {runningReview ? 'Reviewing…' : 'Run Academic Review'}
                </button>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={!generation || downloading === 'paper' || flagCount > 0}
                  onClick={() => handleDownload('paper')}
                  data-testid="download-paper-button"
                  title={downloadGateLabel || undefined}
                  className={
                    'inline-flex items-center gap-1.5 px-3 py-1.5 ' +
                    'bg-electric-blue hover:bg-electric-blue/80 ' +
                    'disabled:bg-navy-700 disabled:text-text-muted ' +
                    'text-white text-sm font-medium rounded'
                  }>
                  <Download className="w-3.5 h-3.5" />
                  {downloading === 'paper' ? 'Downloading…' : 'Download Paper'}
                </button>
                <button
                  type="button"
                  disabled={!generation || downloading === 'appendix'}
                  onClick={() => handleDownload('appendix')}
                  data-testid="download-appendix-button"
                  className={
                    'inline-flex items-center gap-1.5 px-3 py-1.5 ' +
                    'bg-navy-800 hover:bg-navy-700 ' +
                    'disabled:bg-navy-900 disabled:text-text-muted ' +
                    'border border-navy-700 text-white text-sm rounded'
                  }>
                  <Download className="w-3.5 h-3.5" />
                  {downloading === 'appendix' ? 'Downloading…' : 'Download Appendix'}
                </button>
              </div>
            </div>
            {downloadGateLabel ? (
              <p className="text-amber-300 text-xs flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {downloadGateLabel}
              </p>
            ) : null}
          </div>

          <AcademicReviewPanel review={review} loading={runningReview} />
        </main>
      </div>

      {/* Preview pane — bottom (renders [BOB] blocks inline) */}
      {paperMd ? (
        <section
          data-testid="preview-pane"
          className="mt-6 p-4 bg-navy-900 border border-navy-700 rounded">
          <h3 className="text-white font-medium text-sm mb-2">
            Preview · {bobCount} callout point{bobCount === 1 ? '' : 's'} remaining
          </h3>
          <PreviewWithBlocks
            paperMd={paperMd}
            onResolve={handleResolveBob}
            disabled={!generation}
          />
        </section>
      ) : null}
    </div>
  )
}


function PreviewWithBlocks({
  paperMd, onResolve, disabled,
}: {
  paperMd: string
  onResolve: (marker: string, replacement: string) => Promise<void>
  disabled?: boolean
}) {
  const tokens = tokenize(paperMd)
  return (
    <div
      data-testid="preview-with-blocks"
      className={
        'text-text-secondary text-sm whitespace-pre-wrap ' +
        'leading-relaxed max-h-[60vh] overflow-y-auto'
      }>
      {tokens.map((tok, i) => (
        tok.kind === 'text' ? (
          <span key={i}>{tok.value}</span>
        ) : (
          <BobBlockBadge
            key={i}
            block={tok.block}
            onResolve={onResolve}
            disabled={disabled}
          />
        )
      ))}
    </div>
  )
}


function CalloutSidebar({
  count, blocks, onResolve, disabled,
}: {
  count: number
  blocks: ReturnType<typeof extractBobBlocks>
  onResolve: (marker: string, replacement: string) => Promise<void>
  disabled?: boolean
}) {
  const [open, setOpen] = useState(true)
  return (
    <section
      data-testid="callout-sidebar"
      className="bg-navy-900 border border-navy-700 rounded">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between p-3 hover:bg-navy-800">
        <span className="text-white font-medium text-sm flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-amber-400" />
          Callout points
          <span
            data-testid="callout-count"
            className={
              'px-2 py-0.5 text-2xs rounded ' +
              (count === 0
                ? 'bg-green-500/15 text-green-300'
                : 'bg-amber-500/15 text-amber-300')
            }>
            {count} remaining
          </span>
        </span>
      </button>
      {open && count > 0 ? (
        <div className="p-3 pt-0 space-y-2">
          {blocks.map((b, i) => (
            <BobBlockBadge
              key={`${b.marker}-${i}`}
              block={b}
              onResolve={onResolve}
              disabled={disabled}
            />
          ))}
        </div>
      ) : null}
      {open && count === 0 ? (
        <div className="p-3 pt-0">
          <p className="text-text-muted text-xs italic">
            No callout points remaining.
          </p>
        </div>
      ) : null}
    </section>
  )
}


function WordCountSidebar({
  counts,
}: {
  counts: {
    sections: Record<number, { words: number; budget: number; status: string }>
    total: number
    total_budget: number
  }
}) {
  return (
    <section
      data-testid="word-count-sidebar"
      className="bg-navy-900 border border-navy-700 rounded p-3">
      <h3 className="text-white font-medium text-sm mb-2">Word counts</h3>
      <ul className="space-y-1">
        {[1, 2, 3, 4].map((n) => {
          const c = counts.sections[n]
          const cls =
            c.status === 'red'   ? 'text-red-400' :
            c.status === 'amber' ? 'text-amber-300' : 'text-green-300'
          return (
            <li
              key={n}
              data-testid={`word-section-${n}`}
              className="flex items-center justify-between text-xs">
              <span className="text-text-secondary">
                Section {n}
              </span>
              <span className={cls}>
                {c.words} / {c.budget}
              </span>
            </li>
          )
        })}
        <li className="flex items-center justify-between text-xs pt-1 border-t border-navy-700 mt-1">
          <span className="text-white font-medium">Total</span>
          <span className="text-white font-medium">
            {counts.total} / {counts.total_budget}
          </span>
        </li>
      </ul>
    </section>
  )
}


function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
