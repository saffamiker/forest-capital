/**
 * PresentationPreview — a full-screen rehearsal view for a
 * presentation_deck draft.
 *
 * Renders one canvas slide at a time scaled to fit the viewport — text
 * and chart elements laid out exactly as positioned in the editor —
 * with the presenter's speaker notes in a darker strip below. Arrow
 * keys or the on-screen ‹ › buttons navigate; Esc exits. No API call
 * beyond the chart PNGs.
 *
 * Theme matches the PPTX export (academic_deck.py): navy stage / white
 * title text / darker-navy speaker notes strip with muted text, so the
 * preview looks like a rendered version of what the .pptx will show
 * rather than a plain document view. The slide BODY still honours its
 * own background (default white, settable per slide) — the deck builder
 * does the same on export.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import axios from 'axios'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'

import type { CanvasSlide, CanvasTextElement } from '../../types/editor'
import { CANVAS_WIDTH, CANVAS_HEIGHT } from './canvasSlide'

interface Props {
  slides: CanvasSlide[]
  onClose: () => void
}

export default function PresentationPreview({ slides, onClose }: Props) {
  const [index, setIndex] = useState(0)
  const total = slides.length
  const slide: CanvasSlide | undefined = slides[index]

  const go = useCallback((delta: number) => {
    setIndex((i) => Math.min(Math.max(i + delta, 0), Math.max(total - 1, 0)))
  }, [total])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight') go(1)
      else if (e.key === 'ArrowLeft') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, onClose])

  // Scale the slide board to fit the available area.
  const areaRef = useRef<HTMLDivElement | null>(null)
  const [scale, setScale] = useState(1)
  useLayoutEffect(() => {
    const el = areaRef.current
    if (!el) return
    const measure = () => {
      const w = el.clientWidth - 48
      const h = el.clientHeight - 48
      setScale(Math.max(0.1, Math.min(w / CANVAS_WIDTH, h / CANVAS_HEIGHT)))
    }
    measure()
    const obs = new ResizeObserver(measure)
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // Theme matches the PPTX export — see academic_deck.py.
  //   Stage / chrome:  #1B2A4A navy
  //   Title text:      #FFFFFF white
  //   Body / muted:    #E2E8F0 slate-200
  //   Accent / data:   #F59E0B amber
  //   Notes strip:     #0F172A darker navy
  //   Notes text:      #94A3B8 slate-400
  return (
    <div data-testid="presentation-preview"
      className="fixed inset-0 z-[80] flex flex-col"
      style={{ background: '#1B2A4A' }}>
      {/* Top bar — counter + exit. Sits on a faint divider, white text. */}
      <div className="flex items-center justify-between px-4 py-2 shrink-0"
        style={{ borderBottom: '1px solid rgba(255,255,255,0.10)' }}>
        <span className="text-sm font-mono" style={{ color: '#FFFFFF' }}>
          {total === 0 ? '0 / 0' : `${index + 1} / ${total}`}
        </span>
        <button type="button" onClick={onClose} aria-label="Exit preview"
          className="flex items-center gap-1 text-sm"
          style={{ color: '#E2E8F0' }}>
          <X className="w-4 h-4" /> Exit (Esc)
        </button>
      </div>

      {/* Slide */}
      <div className="flex-1 flex items-center min-h-0">
        <button type="button" onClick={() => go(-1)} disabled={index === 0}
          aria-label="Previous slide"
          className="px-4 h-full disabled:opacity-30"
          style={{ color: '#E2E8F0' }}>
          <ChevronLeft className="w-8 h-8" />
        </button>

        <div ref={areaRef}
          className="flex-1 h-full flex items-center justify-center p-6 min-w-0"
          style={{ background: '#1B2A4A' }}>
          {slide ? (
            <div className="shadow-lg relative overflow-hidden"
              style={{
                width: CANVAS_WIDTH * scale,
                height: CANVAS_HEIGHT * scale,
                background: slide.background || '#FFFFFF',
              }}>
              {slide.elements.map((el) => (el.type === 'text'
                ? <PreviewText key={el.id} el={el} scale={scale} />
                : <PreviewChart key={el.id} chartKey={el.chartKey}
                    x={el.x * scale} y={el.y * scale}
                    w={el.width * scale} h={el.height * scale} />))}
            </div>
          ) : (
            <p className="text-center" style={{ color: '#E2E8F0' }}>
              This deck has no slides.
            </p>
          )}
        </div>

        <button type="button" onClick={() => go(1)}
          disabled={index >= total - 1}
          aria-label="Next slide"
          className="px-4 h-full disabled:opacity-30"
          style={{ color: '#E2E8F0' }}>
          <ChevronRight className="w-8 h-8" />
        </button>
      </div>

      {/* Speaker notes strip — presenter-only, darker navy beneath the
          stage so the audience-facing area reads as the slide itself. */}
      <div className="px-8 py-3 shrink-0 max-h-40 overflow-y-auto"
        style={{ background: '#0F172A',
                 borderTop: '1px solid rgba(255,255,255,0.08)' }}>
        <div className="text-2xs uppercase tracking-wide mb-1"
          style={{ color: '#94A3B8' }}>
          Your notes (not visible to audience)
        </div>
        <div className="text-sm whitespace-pre-wrap"
          style={{ color: '#94A3B8' }}>
          {slide?.speaker_notes?.trim()
            || 'No speaker notes for this slide yet.'}
        </div>
      </div>
    </div>
  )
}

function PreviewText({ el, scale }: { el: CanvasTextElement; scale: number }) {
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

function PreviewChart({
  chartKey, x, y, w, h,
}: {
  chartKey: string; x: number; y: number; w: number; h: number
}) {
  const [src, setSrc] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let url: string | null = null
    void (async () => {
      try {
        const res = await axios.get(`/api/v1/charts/render/${chartKey}`, {
          params: { width: Math.round(w), height: Math.round(h),
            theme: 'light' },
          responseType: 'blob',
        })
        if (cancelled) return
        url = URL.createObjectURL(res.data as Blob)
        setSrc(url)
      } catch { /* leave blank — a missing chart must not break rehearsal */ }
    })()
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url) }
  }, [chartKey, w, h])

  return (
    <div style={{ position: 'absolute', left: x, top: y, width: w, height: h }}
      className="bg-white">
      {src && <img src={src} alt="" className="w-full h-full object-fill" />}
    </div>
  )
}
