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
import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
  type ReactElement,
} from 'react'
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

// Bridge (June 8 2026) -- markdown helpers so slide text elements that
// carry table_data render as proper visual tables instead of raw pipe
// strings. We split the element's content into "blocks" -- a sequence
// of either plain-text lines or a markdown table -- and render each
// block in order.
//
// Recognised markdown table shape (the one editor_content._markdown_table
// emits):
//   | Header A | Header B |
//   |---|---|
//   | Cell    | Cell    |
//
// Detection rule: a line of `|...|` followed by a line that's pure
// `|---|---|` separators (dashes / colons / pipes / whitespace only).
// Bold (**text**) is also unwrapped per-text-run.

interface TableBlock {
  kind: 'table'
  headers: string[]
  rows: string[][]
}
interface TextBlock {
  kind: 'text'
  lines: string[]
}
type ContentBlock = TableBlock | TextBlock

function _isSeparatorRow(line: string): boolean {
  const trimmed = line.trim()
  if (!/^\|/.test(trimmed) || !/\|$/.test(trimmed)) return false
  // Cells between pipes must be dashes / colons / whitespace only.
  const cells = trimmed.slice(1, -1).split('|')
  if (cells.length === 0) return false
  return cells.every((c) => /^[\s:-]+$/.test(c) && /-/.test(c))
}

function _splitPipeRow(line: string): string[] {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|')) s = s.slice(0, -1)
  return s.split('|').map((c) => c.trim())
}

export function _splitIntoBlocks(content: string): ContentBlock[] {
  const lines = content.split('\n')
  const blocks: ContentBlock[] = []
  let buffer: string[] = []
  const flush = () => {
    if (buffer.length > 0) {
      blocks.push({ kind: 'text', lines: buffer })
      buffer = []
    }
  }
  let i = 0
  while (i < lines.length) {
    const headerLike = lines[i]
    const separatorLike = i + 1 < lines.length ? lines[i + 1] : ''
    if (
      headerLike != null
      && /\|/.test(headerLike) && /\|/.test(headerLike.trim().slice(1, -1) || ' ')
      && _isSeparatorRow(separatorLike)
    ) {
      flush()
      const headers = _splitPipeRow(headerLike)
      const rows: string[][] = []
      let j = i + 2
      while (j < lines.length && /\|/.test(lines[j])) {
        rows.push(_splitPipeRow(lines[j]))
        j += 1
      }
      blocks.push({ kind: 'table', headers, rows })
      i = j
      continue
    }
    buffer.push(headerLike ?? '')
    i += 1
  }
  flush()
  return blocks
}

function _renderInline(text: string): ReactElement[] {
  // Minimal markdown -- bold (**text**) only. Italic (*text*) is
  // currently rare in slide bullets; keep this minimal so the preview
  // is predictable.
  const parts: ReactElement[] = []
  let cursor = 0
  const re = /\*\*(.+?)\*\*/g
  let m: RegExpExecArray | null
  let key = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > cursor) {
      parts.push(
        <span key={`t${key++}`}>{text.slice(cursor, m.index)}</span>,
      )
    }
    parts.push(<strong key={`b${key++}`}>{m[1]}</strong>)
    cursor = m.index + m[0].length
  }
  if (cursor < text.length) {
    parts.push(<span key={`t${key++}`}>{text.slice(cursor)}</span>)
  }
  return parts
}

function PreviewText({ el, scale }: { el: CanvasTextElement; scale: number }) {
  const blocks = _splitIntoBlocks(el.content || '')
  const hasTable = blocks.some((b) => b.kind === 'table')
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
      whiteSpace: hasTable ? 'normal' : 'pre-wrap',
    }}>
      {blocks.map((block, idx) => block.kind === 'table' ? (
        <table
          key={`tbl-${idx}`}
          data-testid="preview-text-table"
          style={{
            width: '100%', borderCollapse: 'collapse',
            margin: '0.4em 0',
            fontSize: 'inherit', fontFamily: 'inherit',
            color: '#1B2A4A',
          }}>
          <thead>
            <tr>
              {block.headers.map((h, hi) => (
                <th key={hi} style={{
                  background: '#1B2A4A', color: '#FFFFFF',
                  padding: '0.35em 0.55em',
                  textAlign: 'left',
                  fontWeight: 'bold',
                  border: 'none',
                }}>{_renderInline(h)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, ri) => (
              <tr key={ri} style={{
                background: ri % 2 === 0
                  ? 'rgba(27,42,74,0.04)'
                  : 'rgba(27,42,74,0.10)',
              }}>
                {row.map((cell, ci) => (
                  <td key={ci} style={{
                    padding: '0.35em 0.55em',
                    border: 'none',
                  }}>{_renderInline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div key={`txt-${idx}`} style={{ whiteSpace: 'pre-wrap' }}>
          {block.lines.map((line, li) => (
            <div key={li}>{_renderInline(line)}</div>
          ))}
        </div>
      ))}
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
