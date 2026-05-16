/**
 * AcademicReviewButton — a secondary action on the Council screen that
 * runs POST /api/council/academic-review.
 *
 * The endpoint streams text/event-stream:
 *   1. {"type":"peer_responses","data":{agentId: text}}
 *   2. {"type":"arbiter_chunk","text": chunk}   (streamed)
 *   3. data: [DONE]
 *
 * While the peer agents run the button shows "Consulting the council…".
 * Once the peer_responses frame arrives the arbiter verdict streams in
 * and is rendered section by section (### headings + **Rating:** labels).
 * Peer responses sit in a collapsible accordion below the verdict.
 * The run can be cancelled mid-stream.
 */
import { useState, useRef } from 'react'
import { GraduationCap, Loader2, X, ChevronDown, ChevronRight } from 'lucide-react'

type Phase = 'idle' | 'consulting' | 'streaming' | 'done' | 'error'

interface VerdictSection {
  heading: string
  rating: string | null
  body: string
}

// Peer agent id → display name for the accordion.
const PEER_NAMES: Record<string, string> = {
  equity_analyst: 'Equity Analyst',
  fixed_income_analyst: 'Fixed Income Analyst',
  risk_manager: 'Risk Manager',
  quant_backtester: 'Quant Backtester',
  cio: 'Chief Investment Officer',
  independent_analyst: 'Independent Analyst (Gemini)',
  contrarian_analyst: 'Contrarian Analyst (Grok)',
}

// Qualitative rating → badge styling.
const RATING_STYLE: Record<string, string> = {
  Strong: 'bg-success/15 text-success border-success/30',
  Developing: 'bg-warning/15 text-warning border-warning/30',
  'Needs Work': 'bg-danger/15 text-danger border-danger/30',
}

/** Split the arbiter markdown into sections by "### " headings,
 *  pulling out the **Rating:** line from each. */
function parseVerdict(text: string): VerdictSection[] {
  return text
    .split(/^### /m)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((part) => {
      const lines = part.split('\n')
      const heading = (lines[0] ?? '').trim()
      let rating: string | null = null
      const body: string[] = []
      for (const ln of lines.slice(1)) {
        const m = ln.match(/^\*\*Rating:\*\*\s*(.+?)\s*$/)
        if (m && rating === null) {
          rating = m[1].trim()
          continue
        }
        body.push(ln)
      }
      return { heading, rating, body: body.join('\n').trim() }
    })
}

function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) return null
  const cls = RATING_STYLE[rating] ?? 'bg-navy-700 text-muted border-border'
  return (
    <span className={`text-2xs px-2 py-0.5 rounded-full border ${cls}`}>
      {rating}
    </span>
  )
}

export default function AcademicReviewButton() {
  const [phase, setPhase] = useState<Phase>('idle')
  const [peerResponses, setPeerResponses] = useState<Record<string, string>>({})
  const [arbiterText, setArbiterText] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [peersOpen, setPeersOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const running = phase === 'consulting' || phase === 'streaming'

  const cancel = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setPhase('idle')
  }

  const runReview = async () => {
    setPhase('consulting')
    setPeerResponses({})
    setArbiterText('')
    setErrorMsg('')
    setPeersOpen(false)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const token = localStorage.getItem('fc_session_token') ?? ''
      const res = await fetch('/api/council/academic-review', {
        method: 'POST',
        headers: { 'X-API-Key': token },
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        throw new Error(`Request failed (${res.status})`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE frames are separated by a blank line.
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep).trim()
          buffer = buffer.slice(sep + 2)
          if (!frame.startsWith('data:')) continue
          const payload = frame.slice(5).trim()
          if (payload === '[DONE]') {
            setPhase('done')
            continue
          }
          let evt: { type?: string; data?: Record<string, string>; text?: string; message?: string }
          try {
            evt = JSON.parse(payload)
          } catch {
            continue
          }
          if (evt.type === 'peer_responses') {
            setPeerResponses(evt.data ?? {})
            setPhase('streaming')
          } else if (evt.type === 'arbiter_chunk') {
            setArbiterText((prev) => prev + (evt.text ?? ''))
          } else if (evt.type === 'error') {
            setErrorMsg(evt.message ?? 'Academic review failed.')
            setPhase('error')
          }
        }
      }
      // The stream ended without an explicit [DONE]; settle the phase.
      setPhase((p) => (p === 'error' ? p : 'done'))
    } catch (err) {
      if (controller.signal.aborted) return // cancelled — phase already 'idle'
      setErrorMsg(err instanceof Error ? err.message : 'Academic review failed.')
      setPhase('error')
    } finally {
      abortRef.current = null
    }
  }

  const sections = parseVerdict(arbiterText)
  const peerEntries = Object.entries(peerResponses)

  return (
    <div className="space-y-3">
      {/* Trigger row */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void runReview()}
          disabled={running}
          title="Have the council evaluate your analytics, findings, and deliverables against project requirements"
          className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium
                     border border-electric/30 bg-electric/10 text-electric
                     hover:bg-electric/20 disabled:opacity-50 disabled:cursor-not-allowed
                     transition-colors"
        >
          {running
            ? <Loader2 className="w-4 h-4 animate-spin" />
            : <GraduationCap className="w-4 h-4" />}
          Academic Review
        </button>
        {running && (
          <>
            <span className="text-xs text-muted">
              {phase === 'consulting'
                ? 'Consulting the council…'
                : 'Synthesising the verdict…'}
            </span>
            <button
              type="button"
              onClick={cancel}
              className="flex items-center gap-1 text-xs text-muted hover:text-danger transition-colors"
            >
              <X className="w-3.5 h-3.5" /> Cancel
            </button>
          </>
        )}
      </div>

      {phase === 'error' && (
        <div className="card border border-danger/30 bg-danger/5 p-3 text-danger text-xs">
          {errorMsg}
        </div>
      )}

      {/* Arbiter verdict — renders section by section as it streams */}
      {(phase === 'streaming' || phase === 'done') && sections.length > 0 && (
        <div className="card p-4" style={{ borderLeft: '3px solid #f59e0b' }}>
          <div className="flex items-center gap-2 mb-3">
            <GraduationCap className="w-4 h-4 text-warning" />
            <h3 className="text-white font-semibold text-sm">
              Academic Review — Council Verdict
            </h3>
          </div>
          <div className="space-y-4">
            {sections.map((s, i) => (
              <div key={i}>
                <div className="flex items-center gap-2 flex-wrap">
                  <h4 className="text-white font-medium text-sm">{s.heading}</h4>
                  <RatingBadge rating={s.rating} />
                </div>
                {s.body && (
                  <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap mt-1">
                    {s.body}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Peer responses — supporting detail, collapsed by default */}
      {peerEntries.length > 0 && (
        <div className="card overflow-hidden">
          <button
            type="button"
            onClick={() => setPeersOpen((o) => !o)}
            className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-white
                       hover:bg-navy-700 transition-colors"
          >
            {peersOpen
              ? <ChevronDown className="w-4 h-4 text-muted" />
              : <ChevronRight className="w-4 h-4 text-muted" />}
            <span>Peer reviews ({peerEntries.length})</span>
            <span className="text-2xs text-muted ml-1">supporting detail</span>
          </button>
          {peersOpen && (
            <div className="border-t border-border divide-y divide-border">
              {peerEntries.map(([agentId, text]) => (
                <div key={agentId} className="px-4 py-3">
                  <div className="text-xs font-semibold text-electric mb-1">
                    {PEER_NAMES[agentId] ?? agentId}
                  </div>
                  <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
                    {text}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
