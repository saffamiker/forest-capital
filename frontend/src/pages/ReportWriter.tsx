/**
 * frontend/src/pages/ReportWriter.tsx
 *
 * Eleven-step report writer page (item 12 commit 5 — pipeline UX fix).
 *
 * Steps 1-6 auto-cascade on mount:
 *   1. Stage Findings runs first.
 *   2-4. Source Citations / Team Activity / Validation Data fire in
 *        PARALLEL after Step 1 completes.
 *   5. Cross-Reference Check fires after Steps 2-4 all complete.
 *   6. Thesis Validation fires after Step 5 completes (any status
 *      except 'running' or 'idle' allows step 6 to attempt).
 *   7. Generate Draft is MANUAL — Bob clicks it after the gates pass.
 *
 * Each step records elapsed ms client-side. After Step 7 (or on
 * failure at any earlier step) the UI POSTs one audit row to
 * /api/v1/reports/pipeline-audit. The summary card below the editor
 * shows the per-step timing table.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import {
  FileText, Download, Search, AlertCircle, Clock,
} from 'lucide-react'

import BobBlockBadge from '../components/reportwriter/BobBlockBadge'
import IterationToolbar from '../components/reportwriter/IterationToolbar'
import AcademicReviewPanel from '../components/reportwriter/AcademicReviewPanel'
import type { AcademicReview } from '../components/reportwriter/AcademicReviewPanel'
import RubricPanel from '../components/reportwriter/RubricPanel'
import type { Rubric } from '../components/reportwriter/RubricPanel'
import PipelineGate, {
  useAutoFireStep5And6,
} from '../components/reportwriter/PipelineGate'
import type { StepResult, StepResults } from '../components/reportwriter/PipelineGate'
import {
  extractBobBlocks, tokenize,
  SECTION_BUDGETS, countWords, wordCountStatus,
} from '../lib/bobBlocks'
import { useReportWriterStore } from '../stores/reportWriterStore'

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
  thesis_validation?: { passed: boolean; conditions?: Array<unknown>; blocker_reasons?: string[] }
  academic_readiness?: string | null
}

interface AuditPayload {
  generation_id: number | null
  template_id: string
  total_pipeline_ms: number | null
  failure_step: number | null
  failure_reason: string | null
  steps: Record<string, unknown>
}

interface AuditRow {
  id: number
  generation_id: number | null
  template_id: string
  triggered_by: string | null
  run_at: string | null
  step_1_status?: string | null
  step_1_ms?: number | null
  step_2_status?: string | null
  step_2_ms?: number | null
  step_3_status?: string | null
  step_3_ms?: number | null
  step_4_status?: string | null
  step_4_ms?: number | null
  step_5_status?: string | null
  step_5_ms?: number | null
  step_6_status?: string | null
  step_6_ms?: number | null
  step_7_status?: string | null
  step_7_ms?: number | null
}


export default function ReportWriter() {
  const [templates, setTemplates] = useState<Template[]>([])
  const [templateId, setTemplateId] = useState<string>('midpoint_check_fna670')
  const [rubric, setRubric] = useState<Rubric | null>(null)
  const [generation, setGeneration] = useState<GenerationResponse | null>(null)
  const [paperMd, setPaperMd] = useState('')
  const [stepResults, setStepResults] = useState<StepResults>({})
  const [generating, setGenerating] = useState(false)
  const [savingPatch, setSavingPatch] = useState(false)
  const [runningCheck, setRunningCheck] = useState(false)
  const [runningReview, setRunningReview] = useState(false)
  const [review, setReview] = useState<AcademicReview | null>(null)
  const [selectedText, setSelectedText] = useState('')
  const [downloading, setDownloading] = useState<'paper' | 'appendix' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [auditPosted, setAuditPosted] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const stepResultsRef = useRef<StepResults>({})
  stepResultsRef.current = stepResults

  // Cross-screen pipeline state — drives the nav-bar badge and
  // round-trips the audit_id so incremental persistence updates the
  // same audit row on every step completion.
  const {
    badge: navBadge, setBadge, auditId, setAuditId,
    pipelineStartedAt, setPipelineStartedAt,
  } = useReportWriterStore()
  void navBadge // referenced via the store; silence the lint
  const auditIdRef = useRef<number | null>(null)
  auditIdRef.current = auditId
  const restoredOnceRef = useRef(false)

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

  // ── Step result helper ─────────────────────────────────────────────────────
  const setStep = useCallback((n: number, result: StepResult) => {
    setStepResults((prev) => ({ ...prev, [n]: result }))
  }, [])

  // ── Incremental persistence — every step write upserts the audit row ─────
  const persistStep = useCallback(async (
    n: number, result: StepResult,
  ): Promise<void> => {
    // The frontend round-trips audit_id so the backend updates the
    // same row across step completions. The first call (audit_id
    // null) inserts and returns the new id; we cache it in the
    // store so every cross-screen render sees the same value.
    try {
      const stepKey = `step_${n}_status`
      const msKey = `step_${n}_ms`
      const payload = result.payload as Record<string, unknown> | undefined
      const ms = payload && typeof payload['_ms'] === 'number'
        ? payload['_ms'] as number : null
      const steps: Record<string, unknown> = {
        [stepKey]: result.status,
        [msKey]: ms,
      }
      if (n === 5 && payload && payload['mismatch_count'] !== undefined) {
        steps['step_5_mismatch_count'] = payload['mismatch_count']
      }
      if (n === 6 && payload && payload['conditions'] !== undefined) {
        steps['step_6_conditions'] = payload['conditions']
      }
      const body: Record<string, unknown> = {
        template_id: templateId,
        steps,
        audit_id: auditIdRef.current,
      }
      if (result.status === 'failed') {
        body['failure_step'] = n
        body['failure_reason'] = result.message
      }
      const res = await axios.post<{ id: number | null; audit_id: number | null }>(
        '/api/v1/reports/pipeline-audit', body)
      const newId = res.data.audit_id ?? res.data.id
      if (typeof newId === 'number' && auditIdRef.current === null) {
        auditIdRef.current = newId
        setAuditId(newId)
      }
    } catch {
      // Audit failures are silent — the primary UX cannot be blocked
      // by an informational layer.
    }
  }, [templateId, setAuditId])

  // ── Generic step runner ───────────────────────────────────────────────────
  const runStep = useCallback(async (n: number): Promise<void> => {
    if (!pipelineStartedAt) {
      const t = Date.now()
      setPipelineStartedAt(t)
    }
    setBadge('running', `Step ${n} running`)
    setStep(n, { status: 'running', message: 'Running…' })
    const t0 = performance.now()
    try {
      const summary = await STEP_ACTIONS[n](templateId)
      const ms = Math.round(performance.now() - t0)
      const result: StepResult = {
        status: summary.status,
        message: summary.message,
        detail: `${(ms / 1000).toFixed(1)}s`,
        payload: { ...summary.payload, _ms: ms } as Record<string, unknown>,
      }
      setStep(n, result)
      void persistStep(n, result)
      if (summary.status === 'failed') {
        setBadge('failed', `Step ${n} failed`)
      } else {
        setBadge('running', `Step ${n} complete`)
      }
    } catch (err) {
      const ms = Math.round(performance.now() - t0)
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail || err.message) : (err as Error).message
      const result: StepResult = {
        status: 'failed',
        message: typeof msg === 'string' ? msg : 'Step failed.',
        detail: `${(ms / 1000).toFixed(1)}s`,
        payload: { _ms: ms } as Record<string, unknown>,
      }
      setStep(n, result)
      setBadge('failed', `Step ${n} failed`)
      void persistStep(n, result)
    }
  }, [templateId, setStep, pipelineStartedAt, setPipelineStartedAt,
       setBadge, persistStep])

  // ── Restore on mount + auto-cascade Step 1 → 2,3,4 → 5 → 6 ─────────────────
  // Before kicking off a fresh run we ask the backend for any
  // pipeline started by this user in the last 2 hours. When one
  // exists, we hydrate the step state from it so a user navigating
  // back to /reports/writer mid-run sees the same picture they left.
  // Otherwise we fall through to the fresh-run path.
  const autoStartedRef = useRef<string | null>(null)
  useEffect(() => {
    let cancelled = false
    if (autoStartedRef.current === templateId) return
    autoStartedRef.current = templateId

    ;(async () => {
      if (!restoredOnceRef.current) {
        restoredOnceRef.current = true
        try {
          const r = await axios.get<{
            available: boolean;
            audit?: AuditRow;
            paper_md?: string;
          }>('/api/v1/reports/pipeline-audit/active')
          if (!cancelled && r.data.available && r.data.audit) {
            hydrateFromAudit(r.data.audit, r.data.paper_md ?? '')
            return  // skip fresh-run kickoff
          }
        } catch {
          // Restore is best-effort — fall through to fresh run.
        }
      }
      if (cancelled) return
      setStepResults({})
      setAuditId(null)
      auditIdRef.current = null
      setPipelineStartedAt(Date.now())
      setBadge('running', 'Pipeline starting')
      void runStep(1)
    })()
    return () => { cancelled = true }
  }, [templateId, runStep, setAuditId, setPipelineStartedAt, setBadge])

  // Hydrate from a persisted audit row — populate step results from
  // the row's per-step columns, restore the paper_md, the audit_id,
  // and the pipeline-started-at timestamp.
  const hydrateFromAudit = useCallback((
    audit: AuditRow, paperFromServer: string,
  ) => {
    const restored: StepResults = {}
    for (const n of [1, 2, 3, 4, 5, 6, 7]) {
      const key = `step_${n}_status` as keyof AuditRow
      const msKey = `step_${n}_ms` as keyof AuditRow
      const status = audit[key]
      if (typeof status === 'string' && status) {
        const ms = audit[msKey]
        restored[n] = {
          status: status as StepResult['status'],
          message: status === 'complete' ? 'Restored from previous session'
                : status === 'warning' ? 'Restored with warning'
                : status === 'failed' ? 'Restored — previous run failed'
                : 'Restored — was in progress',
          detail: typeof ms === 'number' ? `${(ms / 1000).toFixed(1)}s` : undefined,
          payload: { _ms: ms } as Record<string, unknown>,
        }
      }
    }
    setStepResults(restored)
    setAuditId(audit.id)
    auditIdRef.current = audit.id
    if (audit.run_at) {
      const t = new Date(audit.run_at).getTime()
      if (Number.isFinite(t)) setPipelineStartedAt(t)
    }
    if (paperFromServer) {
      setPaperMd(paperFromServer)
      if (audit.generation_id) {
        // Pull the generation row so the editor knows flag_count etc.
        axios.get<GenerationResponse & { citations?: Record<string, unknown> }>(
          `/api/v1/reports/generations/${audit.generation_id}`)
          .then((r) => setGeneration(r.data))
          .catch(() => { /* best-effort */ })
      }
    }
    const allComplete = [1, 2, 3, 4, 5, 6, 7].every((n) => {
      const s = restored[n]?.status
      return s === 'complete' || s === 'warning'
    })
    setBadge(
      allComplete ? 'complete'
      : restored[7]?.status === 'failed' ? 'failed'
      : 'running',
      'Restored from previous session')
  }, [setAuditId, setBadge, setPipelineStartedAt])

  // After step 1 completes, fan out 2/3/4 in parallel — but only when
  // each is idle (so a manual re-run of just one doesn't re-trigger
  // the others).
  useEffect(() => {
    if (stepResults[1]?.status !== 'complete') return
    for (const n of [2, 3, 4]) {
      const r = stepResults[n]
      if (!r || r.status === 'idle') {
        void runStep(n)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepResults[1]?.status])

  // Steps 5 + 6 auto-fire when their prerequisites land. The shared
  // hook isolates the trigger logic from this component's render loop.
  useAutoFireStep5And6(stepResults, runStep)

  // ── Generation ────────────────────────────────────────────────────────────
  const handleGenerate = async (): Promise<void> => {
    if (generating) return
    setError(null)
    setGenerating(true)
    setReview(null)
    setStep(7, { status: 'running', message: 'Generating draft…' })
    const t0 = performance.now()
    try {
      const res = await axios.post<GenerationResponse>(
        `/api/v1/reports/templates/${templateId}/generate`)
      const ms = Math.round(performance.now() - t0)
      const data = res.data
      setGeneration(data)
      setPaperMd(data.paper_md || '')
      const bobCount = data.bob_block_count ?? 0
      const step7Result: StepResult = {
        status: bobCount > 0 ? 'warning' : 'complete',
        message: bobCount > 0
          ? `Draft generated · ${bobCount} callout point${bobCount === 1 ? '' : 's'} remaining`
          : 'Draft generated · no callouts',
        detail: `${(ms / 1000).toFixed(1)}s`,
        payload: { _ms: ms, generation_id: data.id } as Record<string, unknown>,
      }
      setStep(7, step7Result)
      setBadge('complete', 'Draft ready')
      // Final upsert — locks in the generation_id + total ms.
      void persistStep(7, step7Result)
      void postAudit({
        ...buildAuditPayload({
          templateId,
          results: { ...stepResultsRef.current, 7: step7Result },
          startedAt: pipelineStartedAt,
          generation_id: data.id,
          failure_step: null,
          failure_reason: null,
        }),
        audit_id: auditIdRef.current,
      }).then(() => setAuditPosted(true))
    } catch (err) {
      const ms = Math.round(performance.now() - t0)
      let msg = 'Generation failed.'
      let thesisDetail: string | null = null
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail
        if (typeof detail === 'string') {
          msg = detail
        } else if (typeof detail === 'object' && detail !== null) {
          const obj = detail as { error?: string; thesis_validation?: { blocker_reasons?: string[] } }
          if (obj.error === 'thesis_validation_blocked') {
            msg = 'Thesis validation blocked generation. See Step 6.'
            const reasons = obj.thesis_validation?.blocker_reasons ?? []
            thesisDetail = reasons.join(' · ')
          }
        }
      }
      setError(msg)
      const step7Failed: StepResult = {
        status: 'failed', message: msg, detail: `${(ms / 1000).toFixed(1)}s`,
        payload: { _ms: ms } as Record<string, unknown>,
      }
      setStep(7, step7Failed)
      setBadge('failed', `Step 7 failed`)
      void persistStep(7, step7Failed)
      if (thesisDetail) {
        setStep(6, {
          ...(stepResultsRef.current[6] || { status: 'failed', message: '' }),
          status: 'failed', message: thesisDetail,
        })
      }
      void postAudit({
        ...buildAuditPayload({
          templateId,
          results: stepResultsRef.current,
          startedAt: pipelineStartedAt,
          generation_id: null,
          failure_step: 7,
          failure_reason: msg,
        }),
        audit_id: auditIdRef.current,
      })
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
        setGeneration((g) => g ? { ...g, ...res.data } : g)
      } catch {
        // Best-effort save; next debounce retries.
      } finally {
        setSavingPatch(false)
      }
    }, 1500)
  }

  useEffect(() => () => {
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current)
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
  }, [generation, paperMd])

  // ── AI iteration ──────────────────────────────────────────────────────────
  const handleIterate = useCallback(async (
    action: 'rephrase' | 'tighten' | 'expand' | 'ask',
    instruction?: string,
  ) => {
    if (!generation) throw new Error('No generation in progress.')
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
    try {
      const res = await axios.post<GenerationResponse & { passed: boolean }>(
        `/api/v1/reports/generations/${generation.id}/final-check`)
      setGeneration((g) => g ? { ...g, ...res.data } : g)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail || err.message) : 'Final check failed.'
      setError(String(msg))
    } finally {
      setRunningCheck(false)
    }
  }

  // ── Academic review ───────────────────────────────────────────────────────
  const handleRunReview = async () => {
    if (!generation) return
    setRunningReview(true)
    try {
      const res = await axios.post<AcademicReview & { rubric_version?: number }>(
        `/api/v1/reports/generations/${generation.id}/academic-review`)
      setReview(res.data)
      setGeneration((g) => g ? { ...g, academic_readiness: res.data.readiness } : g)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail || err.message) : 'Review failed.'
      setError(String(msg))
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
        } catch { setError('Download failed even with override.') }
      } else {
        setError(msg)
      }
    } finally {
      setDownloading(null)
    }
  }

  const captureSelection = () => {
    const ta = textareaRef.current
    if (!ta) return
    const start = ta.selectionStart
    const end = ta.selectionEnd
    setSelectedText(start !== end ? paperMd.slice(start, end) : '')
  }

  // ── Derived display state ─────────────────────────────────────────────────
  const blocks = useMemo(() => extractBobBlocks(paperMd), [paperMd])
  const bobCount = blocks.length
  const flagCount = generation?.flag_count ?? 0

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

  const generateDisabledReason = useMemo(() => {
    if (!(stepResults[1]?.status === 'complete')) return 'Step 1 incomplete'
    for (const n of [2, 3, 4]) {
      const r = stepResults[n]
      if (!r || (r.status !== 'complete' && r.status !== 'warning')) {
        return `Step ${n} incomplete`
      }
    }
    const s5 = stepResults[5]?.status
    if (s5 !== 'complete' && s5 !== 'warning') return 'Step 5 incomplete'
    const s6 = stepResults[6]?.status
    if (s6 !== 'complete') return 'Step 6 not passing'
    return null
  }, [stepResults])

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
        <aside className="lg:col-span-1 space-y-4">
          <PipelineGate
            results={stepResults}
            generating={generating}
            generateDisabledReason={generateDisabledReason}
            onRunStep={runStep}
            onGenerate={handleGenerate}
          />
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
                  : 'Run the pipeline (Steps 1–6) then click Generate Draft.'
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

          {/* Pipeline summary card — shows after Step 7 completes */}
          {generation && stepResults[7]?.status &&
              ['complete', 'warning'].includes(stepResults[7].status) ? (
            <PipelineSummaryCard
              results={stepResults}
              auditPosted={auditPosted}
            />
          ) : null}
        </main>
      </div>

      {/* Preview pane — renders [BOB] blocks inline */}
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


// ── Step actions ─────────────────────────────────────────────────────────────
// Each entry receives the template_id and returns a {status, message,
// payload} summary the renderer surfaces in the step row. Throws on
// HTTP failure so runStep can record it as a failed step.

interface StepSummary {
  status: 'complete' | 'warning' | 'failed'
  message: string
  payload?: Record<string, unknown>
}

const STEP_ACTIONS: Record<number, (templateId: string) => Promise<StepSummary>> = {
  // Step 1 — Stage Findings (no body needed — backend uses the latest cache).
  1: async () => {
    const res = await axios.post<{
      strategy_count: number; n_high_strength?: number;
      high_strength_count?: number; surprise_count: number;
    }>('/api/v1/reports/stage-findings')
    const high = res.data.high_strength_count ?? res.data.n_high_strength ?? 0
    return {
      status: 'complete',
      message: (
        `${res.data.strategy_count} strategies staged · ${high} HIGH-strength findings`),
      payload: res.data as Record<string, unknown>,
    }
  },
  // Step 2 — Source Citations (per template).
  2: async (tid) => {
    const res = await axios.post<{
      verified_count: number; concept_count: number; quality: string;
    }>('/api/v1/reports/source-citations', { template_id: tid })
    const status: StepSummary['status'] =
      res.data.quality === 'red' ? 'warning' : 'complete'
    return {
      status,
      message: (
        `${res.data.verified_count} / ${res.data.concept_count} citations ` +
        `verified · quality ${res.data.quality}`),
      payload: res.data as Record<string, unknown>,
    }
  },
  // Step 3 — Pull Team Activity.
  // Send an explicit empty-object body — axios.post() with no second
  // arg sends `undefined` which some FastAPI/Starlette middleware
  // chains reject before the route handler sees the request (user
  // report: "clicking does nothing"). An empty {} is safe — the
  // endpoint signature takes no body, FastAPI ignores extra fields.
  3: async () => {
    const res = await axios.post<{
      activity: Record<string, number>;
      cross_check_flags: string[];
    }>('/api/v1/reports/team-activity', {})
    const activity = res.data.activity || {}
    const total = activity.team_total_uat_steps ?? 0
    const flags = res.data.cross_check_flags || []
    // Distinguish "endpoint returned no activity" (DB empty, fresh
    // deploy) from "endpoint returned with cross-check warnings".
    if (Object.keys(activity).length === 0 || total === 0) {
      return {
        status: 'complete',
        message: 'No activity recorded yet — pipeline proceeds',
        payload: res.data as Record<string, unknown>,
      }
    }
    return {
      status: flags.length > 0 ? 'warning' : 'complete',
      message: flags.length > 0
        ? `${total} UAT steps · ${flags.length} cross-check flag(s)`
        : `${total} UAT steps · activity reconciled`,
      payload: res.data as Record<string, unknown>,
    }
  },
  // Step 4 — Pull Validation Data (latest audit run).
  // FIX (May 23 2026 — user report): when no audit has been run yet
  // the endpoint returns 200 with statistical_status: null. The
  // previous handler rendered this as "Statistical audit: unknown"
  // which read as a failure. The pipeline is informational — a
  // missing audit is a "run the QA audit first" prompt, NOT a
  // generation blocker. Now: returns 'complete' with a clear
  // "No audit on record" message and an actionable detail in the
  // payload so the expansion panel can suggest the QA audit run.
  4: async () => {
    try {
      const res = await axios.get<{
        statistical_status?: string | null;
        qa_status?: string | null;
        total_checks?: number; failed_checks?: number;
        run_at?: string; passed?: number; warning?: number; failed?: number;
        layer1_status?: string; layer2_status?: string; layer3_status?: string;
      }>('/api/v1/audit/runs/latest')
      const status = res.data.statistical_status
      if (!status) {
        return {
          status: 'complete',
          message: 'No audit on record — pipeline proceeds (run QA audit before submission)',
          payload: { ...res.data, _no_audit: true } as Record<string, unknown>,
        }
      }
      return {
        status: status === 'pass' ? 'complete' : 'warning',
        message: `Statistical audit: ${status}`,
        payload: res.data as Record<string, unknown>,
      }
    } catch (err) {
      // A 404 is also "no audit on record yet" — treat as
      // informational, not as a failed pipeline step.
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        return {
          status: 'complete',
          message: 'No audit on record — pipeline proceeds (run QA audit before submission)',
          payload: { _no_audit: true } as Record<string, unknown>,
        }
      }
      throw err
    }
  },
  // Step 5 — Cross-Reference Check (recompute live ↔ staged).
  5: async () => {
    const res = await axios.post<{
      passed: boolean; conditions: unknown[]; blocker_reasons: string[];
    }>('/api/v1/reports/validate-thesis')
    // Step 5 in the user's UX is "Cross-Reference"; the backend
    // /validate-thesis returns the joined live+staged check result
    // (it cross-checks during build). The blocker_reasons array
    // surfaces any mismatch.
    const flags = res.data.blocker_reasons || []
    return {
      status: flags.length === 0 ? 'complete'
            : flags.length <= 2 ? 'warning' : 'failed',
      message: flags.length === 0
        ? 'No cross-reference mismatches'
        : `${flags.length} mismatch flag(s)`,
      payload: { mismatch_count: flags.length, flags } as Record<string, unknown>,
    }
  },
  // Step 6 — Thesis Validation.
  6: async () => {
    const res = await axios.post<{
      passed: boolean; conditions: Array<{ id: string; passed: boolean; description?: string; value?: unknown; threshold?: unknown }>;
      blocker_reasons: string[];
    }>('/api/v1/reports/validate-thesis')
    return {
      status: res.data.passed ? 'complete' : 'failed',
      message: res.data.passed
        ? 'All three thesis conditions pass'
        : `Thesis blocked: ${(res.data.blocker_reasons || []).join('; ')}`,
      payload: res.data as Record<string, unknown>,
    }
  },
}


// ── Audit payload helper ────────────────────────────────────────────────────


function buildAuditPayload(args: {
  templateId: string
  results: StepResults
  startedAt: number | null
  generation_id: number | null
  failure_step: number | null
  failure_reason: string | null
}): AuditPayload {
  const steps: Record<string, unknown> = {}
  for (const n of [1, 2, 3, 4, 5, 6, 7]) {
    const r = args.results[n]
    if (r) {
      steps[`step_${n}_status`] = r.status
      const payload = r.payload as Record<string, unknown> | undefined
      const ms = payload && typeof payload['_ms'] === 'number'
        ? (payload['_ms'] as number) : null
      steps[`step_${n}_ms`] = ms
      if (n === 5 && payload && payload['mismatch_count'] !== undefined) {
        steps['step_5_mismatch_count'] = payload['mismatch_count']
      }
      if (n === 6 && payload && payload['conditions'] !== undefined) {
        steps['step_6_conditions'] = payload['conditions']
      }
    }
  }
  const total_pipeline_ms = args.startedAt !== null
    ? Date.now() - args.startedAt : null
  return {
    generation_id:     args.generation_id,
    template_id:       args.templateId,
    total_pipeline_ms,
    failure_step:      args.failure_step,
    failure_reason:    args.failure_reason,
    steps,
  }
}


async function postAudit(
  payload: AuditPayload & { audit_id?: number | null },
): Promise<void> {
  try {
    await axios.post('/api/v1/reports/pipeline-audit', payload)
  } catch {
    // Audit failures are silent — informational layer only.
  }
}


// ── Subcomponents ────────────────────────────────────────────────────────────


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


function WordCountSidebar({ counts }: {
  counts: {
    sections: Record<number, { words: number; budget: number; status: string }>
    total: number; total_budget: number
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
              <span className="text-text-secondary">Section {n}</span>
              <span className={cls}>{c.words} / {c.budget}</span>
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


function PipelineSummaryCard({
  results, auditPosted,
}: {
  results: StepResults
  auditPosted: boolean
}) {
  const STEP_LABELS: Record<number, string> = {
    1: 'Stage Findings',
    2: 'Source Citations',
    3: 'Pull Team Activity',
    4: 'Pull Validation Data',
    5: 'Cross-Reference',
    6: 'Thesis Validation',
    7: 'Generate Draft',
  }
  const rows = [1, 2, 3, 4, 5, 6, 7].map((n) => {
    const r = results[n]
    const ms = r?.payload && typeof (r.payload as { _ms?: number })._ms === 'number'
      ? (r.payload as { _ms?: number })._ms ?? 0 : 0
    return { n, label: STEP_LABELS[n], status: r?.status ?? 'idle', ms }
  })
  const totalMs = rows.reduce((acc, r) => acc + (r.ms || 0), 0)
  return (
    <section
      data-testid="pipeline-summary-card"
      className="bg-navy-900 border border-navy-700 rounded p-3">
      <header className="flex items-center justify-between mb-2">
        <h3 className="text-white font-semibold text-sm flex items-center gap-2">
          <Clock className="w-4 h-4 text-electric-blue" />
          Pipeline completed in {(totalMs / 1000).toFixed(1)}s
        </h3>
        {auditPosted ? (
          <span className="text-text-muted text-2xs italic">
            audit recorded
          </span>
        ) : null}
      </header>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-muted text-left">
            <th className="py-1 pr-2">Step</th>
            <th className="py-1 pr-2">Status</th>
            <th className="py-1 pr-2 text-right">Time</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.n}
              data-testid={`summary-row-${r.n}`}
              className="border-t border-navy-800">
              <td className="py-1 pr-2 text-text-secondary">
                {r.n}. {r.label}
              </td>
              <td className="py-1 pr-2">
                <StatusPill status={r.status} />
              </td>
              <td className="py-1 pr-2 text-right text-text-muted">
                {r.ms ? `${(r.ms / 1000).toFixed(1)}s` : '—'}
              </td>
            </tr>
          ))}
          <tr className="border-t-2 border-navy-700">
            <td className="py-1 pr-2 text-white font-semibold">Total to draft</td>
            <td className="py-1 pr-2" />
            <td className="py-1 pr-2 text-right text-white font-semibold">
              {(totalMs / 1000).toFixed(1)}s
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  )
}


function StatusPill({ status }: { status: string }) {
  const styles: Record<string, string> = {
    complete: 'bg-green-500/15 text-green-300',
    warning:  'bg-amber-500/15 text-amber-300',
    failed:   'bg-red-500/15 text-red-300',
    running:  'bg-electric-blue/15 text-electric-blue',
    idle:     'bg-navy-800 text-text-muted',
  }
  const labels: Record<string, string> = {
    complete: 'Complete', warning: 'Warning', failed: 'Failed',
    running: 'Running', idle: 'Idle',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-2xs font-medium ${styles[status] || styles.idle}`}>
      {labels[status] || status}
    </span>
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
