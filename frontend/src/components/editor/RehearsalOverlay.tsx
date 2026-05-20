/**
 * RehearsalOverlay — combined script + slide rehearsal view, mounted
 * over the script editor when the presenter clicks [Rehearse].
 *
 * Two panels side by side:
 *   Left (40% width)  — the current slide's script: slide number /
 *                       title (bold), speaker label, script text body
 *                       (scrollable), and the transition line at the
 *                       bottom prefixed →.
 *   Right (60% width) — a static canvas render of the same slide,
 *                       text elements positioned exactly as on canvas,
 *                       chart elements as REAL chart images from
 *                       /api/v1/charts/render/{key}. Speaker notes
 *                       strip at the bottom for presenter-only context.
 *
 * Navigation:
 *   ← / → arrow keys advance both panels together
 *   Esc exits the overlay
 *   On-screen ‹ Previous / Next › buttons mirror the keys
 *
 * Header carries the title "Rehearsal Mode", a live "min remaining"
 * counter (sum of remaining sections' word counts / 150) and an
 * Exit button (same affordance as Esc).
 *
 * Charts:  every unique chart_key across the deck is fetched ONCE on
 *          overlay mount and held in component state for the duration
 *          of the rehearsal session. Navigation never re-fetches.
 *          A failed fetch degrades to the labelled placeholder box
 *          (fail-open — a missing chart must never break rehearsal).
 *
 * Data:    GET /api/v1/documents/rehearsal (deck + parsed script
 *          sections, fetched once on mount).
 * Gate:    team_member (the endpoint refuses anything else).
 * Sizing:  inherits the canvas's 960×540 board, scaled to fit the
 *          available right-panel area — same scale logic as
 *          PresentationPreview.
 */
import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
} from 'react'
import axios from 'axios'
import { ChevronLeft, ChevronRight, Loader2, X } from 'lucide-react'

import type {
  CanvasChartElement, CanvasSlide, CanvasTextElement,
} from '../../types/editor'
import { CANVAS_WIDTH, CANVAS_HEIGHT } from './canvasSlide'

// Server-side chart render size — generous enough to stay sharp when the
// element is scaled up on a large monitor, small enough to keep the
// upfront-fetch fast. The render endpoint resizes; the browser scales.
const CHART_RENDER_WIDTH = 1200
const CHART_RENDER_HEIGHT = 750

type ChartCacheState =
  | { status: 'loading' }
  | { status: 'ready'; src: string }
  | { status: 'failed' }

type ChartCache = Record<string, ChartCacheState>

interface ScriptSection {
  slide_number: number | null
  title: string
  speaker: string | null
  script_text: string
  transition: string
  word_count: number
}

interface RehearsalPayload {
  deck: { draft_id: number | null; slides: CanvasSlide[] }
  script: {
    draft_id: number | null
    sections: ScriptSection[]
    total_words: number
    estimated_minutes: number
  }
}

interface MissingMessage {
  detail: string
}

interface Props {
  onClose: () => void
}


export default function RehearsalOverlay({ onClose }: Props) {
  const [payload, setPayload] = useState<RehearsalPayload | null>(null)
  const [missing, setMissing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [index, setIndex] = useState(0)
  // Chart cache keyed by chart_key — one entry per unique chart across
  // the whole deck. Pre-fetched on overlay open so slide navigation
  // never waits on the network.
  const [charts, setCharts] = useState<ChartCache>({})

  // Fetch once on mount. 404 with a clear detail message gets the
  // "Rehearsal requires both…" modal; any other error is a generic
  // failure state.
  useEffect(() => {
    let cancelled = false
    axios.get<RehearsalPayload>('/api/v1/documents/rehearsal')
      .then((res) => { if (!cancelled) setPayload(res.data) })
      .catch((err) => {
        if (cancelled) return
        if (axios.isAxiosError(err) && err.response?.status === 404) {
          const data = err.response.data as MissingMessage | undefined
          setMissing(data?.detail ?? 'Rehearsal data unavailable.')
        } else {
          setError(axios.isAxiosError(err)
            ? (err.response?.data?.detail ?? err.message)
            : 'Could not load rehearsal data.')
        }
      })
    return () => { cancelled = true }
  }, [])

  // Pair the deck and the script by index — both lists are in slide
  // order. A missing counterpart on either side falls back to undefined
  // so the panel still renders (the missing half shows a placeholder).
  const slides = payload?.deck.slides ?? []
  const sections = payload?.script.sections ?? []
  const total = Math.max(slides.length, sections.length)

  // Once the payload has arrived, fetch every unique chart in the deck
  // up front so slide navigation feels instant. Each chart is fetched
  // ONCE — the cache survives navigation. A failed fetch is recorded
  // as 'failed' so the placeholder renders without retrying.
  useEffect(() => {
    if (!payload) return
    const keys = Array.from(new Set(
      payload.deck.slides.flatMap((s) => s.elements
        .filter((e): e is CanvasChartElement => e.type === 'chart')
        .map((e) => e.chartKey)),
    ))
    if (keys.length === 0) return

    let cancelled = false
    const urls: string[] = []
    // Seed every pending chart as 'loading' so the slide panel can
    // show a spinner while the fetch is in flight.
    setCharts((prev) => {
      const next: ChartCache = { ...prev }
      for (const k of keys) if (!next[k]) next[k] = { status: 'loading' }
      return next
    })

    void Promise.all(keys.map(async (key) => {
      try {
        const res = await axios.get(`/api/v1/charts/render/${key}`, {
          params: { width: CHART_RENDER_WIDTH,
                    height: CHART_RENDER_HEIGHT, theme: 'light' },
          responseType: 'blob',
        })
        if (cancelled) return
        const src = URL.createObjectURL(res.data as Blob)
        urls.push(src)
        setCharts((prev) => ({ ...prev, [key]: { status: 'ready', src } }))
      } catch {
        if (cancelled) return
        // Fail-open: leave the slide rendering with the labelled
        // placeholder box instead of breaking rehearsal.
        setCharts((prev) => ({ ...prev, [key]: { status: 'failed' } }))
      }
    }))

    return () => {
      cancelled = true
      // Release the object URLs we created so a long-lived browser
      // session doesn't leak memory.
      for (const u of urls) URL.revokeObjectURL(u)
    }
  }, [payload])

  const go = useCallback((delta: number) => {
    setIndex((i) => Math.min(Math.max(i + delta, 0),
                              Math.max(total - 1, 0)))
  }, [total])

  // Keyboard nav — arrows advance, Esc exits.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight') go(1)
      else if (e.key === 'ArrowLeft') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, onClose])

  // "Min remaining" — sum of word counts from the CURRENT section
  // onwards, divided by 150 wpm. Updates as the presenter advances.
  const remainingMinutes = (() => {
    if (sections.length === 0) return 0
    const remainingWords = sections
      .slice(index)
      .reduce((sum, s) => sum + (s.word_count || 0), 0)
    return Math.max(1, Math.round(remainingWords / 150))
  })()

  // Loading / 404 / error early returns. Each renders a centred
  // dialog over a full-screen backdrop — the same surface area as the
  // populated rehearsal view, so a fade between states is smooth.
  if (missing !== null) {
    return (
      <div data-testid="rehearsal-overlay"
           className="fixed inset-0 z-[80] bg-black/70 flex items-center
                      justify-center p-4">
        <div className="card max-w-md w-full p-5 space-y-3">
          <h2 className="text-white font-semibold text-base">
            Rehearsal unavailable
          </h2>
          <p className="text-xs text-slate-300 leading-relaxed">
            Rehearsal requires both your presentation deck and script.
            {' '}{missing}
          </p>
          <button type="button" onClick={onClose}
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-electric text-white hover:bg-blue-500">
            Close
          </button>
        </div>
      </div>
    )
  }

  if (error !== null) {
    return (
      <div data-testid="rehearsal-overlay"
           className="fixed inset-0 z-[80] bg-black/70 flex items-center
                      justify-center p-4">
        <div className="card max-w-md w-full p-5 space-y-3">
          <h2 className="text-white font-semibold text-base">
            Could not load rehearsal
          </h2>
          <p className="text-xs text-danger">{error}</p>
          <button type="button" onClick={onClose}
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-electric text-white hover:bg-blue-500">
            Close
          </button>
        </div>
      </div>
    )
  }

  if (payload === null) {
    return (
      <div data-testid="rehearsal-overlay"
           className="fixed inset-0 z-[80] bg-black/70 flex items-center
                      justify-center">
        <div className="text-slate-300 text-sm flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading rehearsal…
        </div>
      </div>
    )
  }

  const slide = slides[index]
  const section = sections[index]

  return (
    <div data-testid="rehearsal-overlay"
         className="fixed inset-0 z-[80] bg-navy-950 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between gap-3 px-4 py-2
                      border-b border-border shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <h2 className="text-white font-semibold text-sm">Rehearsal Mode</h2>
          <span className="text-2xs text-muted">
            Slide {Math.min(index + 1, total)} of {total}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-2xs text-muted"
                data-testid="rehearsal-min-remaining">
            ~{remainingMinutes} min remaining
          </span>
          <button type="button" onClick={onClose}
            aria-label="Exit rehearsal"
            className="flex items-center gap-1 text-xs text-muted
                       hover:text-white">
            <X className="w-4 h-4" /> Exit (Esc)
          </button>
        </div>
      </div>

      {/* Two-panel body — left script, right slide */}
      <div className="flex-1 flex min-h-0">
        <ScriptPanel section={section} />
        <SlidePanel slide={slide} charts={charts} />
      </div>

      {/* Bottom nav */}
      <div className="flex items-center justify-between gap-3 px-4 py-2
                      border-t border-border shrink-0">
        <button type="button" onClick={() => go(-1)} disabled={index === 0}
          className="flex items-center gap-1 text-xs text-muted
                     hover:text-white disabled:opacity-30">
          <ChevronLeft className="w-4 h-4" /> Previous
        </button>
        <span className="text-2xs text-muted">
          Slide {Math.min(index + 1, total)} of {total}
        </span>
        <button type="button" onClick={() => go(1)}
          disabled={index >= total - 1}
          className="flex items-center gap-1 text-xs text-muted
                     hover:text-white disabled:opacity-30">
          Next <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}


/** Left 40% — the current section's script text + transition. */
function ScriptPanel({ section }: { section?: ScriptSection }) {
  return (
    <aside data-testid="rehearsal-script-panel"
           className="w-2/5 shrink-0 border-r border-border bg-navy-900
                      flex flex-col">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {section ? (
          <>
            <div>
              <h3 className="text-white font-semibold text-lg">
                {section.slide_number != null
                  ? `Slide ${section.slide_number}: ${section.title}`
                  : (section.title || 'Untitled section')}
              </h3>
              {section.speaker && (
                <div className="text-2xs text-muted mt-0.5">
                  Speaker: {section.speaker}
                </div>
              )}
            </div>
            <div className="text-sm text-slate-200 leading-relaxed
                            whitespace-pre-wrap">
              {section.script_text || (
                <span className="italic text-muted">
                  No delivery prose for this section yet.
                </span>
              )}
            </div>
          </>
        ) : (
          <p className="text-muted text-xs italic">
            No script section for this slide.
          </p>
        )}
      </div>
      {section?.transition && (
        <div className="border-t border-border px-4 py-2 text-xs italic
                        text-muted">
          → {section.transition}
        </div>
      )}
    </aside>
  )
}


/** Right 60% — static canvas render of the current slide. */
function SlidePanel(
  { slide, charts }: { slide?: CanvasSlide; charts: ChartCache },
) {
  const areaRef = useRef<HTMLDivElement | null>(null)
  const [scale, setScale] = useState(1)
  useLayoutEffect(() => {
    const el = areaRef.current
    if (!el) return
    const measure = () => {
      const w = el.clientWidth - 48
      const h = el.clientHeight - 100  // leave room for speaker-notes strip
      setScale(Math.max(0.1, Math.min(w / CANVAS_WIDTH, h / CANVAS_HEIGHT)))
    }
    measure()
    const obs = new ResizeObserver(measure)
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  return (
    <section data-testid="rehearsal-slide-panel"
             className="flex-1 min-w-0 flex flex-col bg-navy-950">
      <div ref={areaRef}
        className="flex-1 flex items-center justify-center p-6 min-h-0
                   min-w-0">
        {slide ? (
          <div className="shadow-lg relative overflow-hidden"
            style={{
              width: CANVAS_WIDTH * scale,
              height: CANVAS_HEIGHT * scale,
              background: slide.background || '#FFFFFF',
            }}>
            {slide.elements.map((el) => (el.type === 'text'
              ? <RehearsalText key={el.id} el={el} scale={scale} />
              : <RehearsalChart key={el.id}
                  chartKey={el.chartKey}
                  state={charts[el.chartKey]}
                  x={el.x * scale} y={el.y * scale}
                  w={el.width * scale} h={el.height * scale} />))}
          </div>
        ) : (
          <p className="text-muted text-xs italic">
            No slide for this position.
          </p>
        )}
      </div>
      {/* Speaker notes — presenter-only, muted strip at the bottom. */}
      <div className="border-t border-border bg-navy-900 px-6 py-3
                      max-h-32 overflow-y-auto shrink-0">
        <div className="text-2xs uppercase tracking-wide text-muted mb-1">
          Your notes (not visible to audience)
        </div>
        <div className="text-xs text-slate-400 whitespace-pre-wrap">
          {slide?.speaker_notes?.trim()
            || 'No speaker notes for this slide.'}
        </div>
      </div>
    </section>
  )
}


/** Static text element — same positioning math PresentationPreview uses. */
function RehearsalText(
  { el, scale }: { el: CanvasTextElement; scale: number },
) {
  return (
    <div style={{
      position: 'absolute',
      left: el.x * scale, top: el.y * scale,
      width: el.width * scale, height: el.height * scale,
      fontSize: el.fontSize * scale,
      fontFamily: 'Inter, sans-serif',
      fontWeight: el.fontWeight,
      fontStyle: el.fontStyle === 'italic' ? 'italic' : 'normal',
      color: el.color, lineHeight: 1.2, overflow: 'hidden',
      whiteSpace: 'pre-wrap',
    }}>
      {el.content}
    </div>
  )
}


/**
 * Chart element — renders the real chart image from the cache when
 * ready, a loading spinner while the upfront fetch is in flight, and
 * the labelled placeholder box on failure (fail-open — a missing
 * chart must never break rehearsal).
 */
function RehearsalChart(
  { chartKey, state, x, y, w, h }: {
    chartKey: string
    state: ChartCacheState | undefined
    x: number; y: number; w: number; h: number
  },
) {
  const box: React.CSSProperties = {
    position: 'absolute', left: x, top: y, width: w, height: h,
  }
  if (state?.status === 'ready') {
    return (
      <div style={box} className="bg-white">
        <img src={state.src} alt={chartKey}
             className="w-full h-full object-contain" />
      </div>
    )
  }
  if (state?.status === 'failed' || state === undefined) {
    return (
      <div style={box}
           className="flex items-center justify-center
                      border-2 border-dashed border-slate-300 bg-gray-50">
        <span className="text-2xs font-mono text-slate-500 px-2 text-center">
          [{chartKey.replace(/_/g, ' ')}]
        </span>
      </div>
    )
  }
  // 'loading' — show a small spinner centred in the chart's area.
  return (
    <div style={box}
         className="flex items-center justify-center bg-gray-50
                    border border-slate-200">
      <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
    </div>
  )
}
