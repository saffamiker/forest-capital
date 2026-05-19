/**
 * canvasSlide.ts — pure helpers for the Konva presentation-deck editor.
 *
 * Kept free of React and Konva so the slide-status and element-factory
 * logic is unit-testable without a canvas environment.
 */
import type {
  CanvasChartElement, CanvasSlide, CanvasTextElement,
} from '../../types/editor'

// The fixed slide canvas — 16:9, the same dimensions migration 022 and
// the PPTX EMU mapping assume.
export const CANVAS_WIDTH = 960
export const CANVAS_HEIGHT = 540

// The six brand colours offered in the text colour picker.
export const COLOR_PRESETS = [
  '#1B2A4A', '#FFFFFF', '#333333', '#B45309', '#059669', '#DC2626',
] as const

// The font sizes offered in the text format dropdown.
export const FONT_SIZES = [12, 14, 18, 24, 32, 36, 48] as const

export type CanvasSlideStatus = 'complete' | 'in_progress' | 'not_started'

/**
 * A slide's completion status, driving the navigator progress indicator:
 *   complete    — speaker notes written AND every chart element verified
 *   not_started — no speaker notes and no chart elements (untouched)
 *   in_progress — anything between (notes missing, or a chart unverified)
 */
export function canvasSlideStatus(slide: CanvasSlide): CanvasSlideStatus {
  const charts = (slide.elements ?? [])
    .filter((e): e is CanvasChartElement => e.type === 'chart')
  const hasNotes = (slide.speaker_notes ?? '').trim().length > 0
  const allVerified = charts.every((c) => c.verified)
  if (hasNotes && allVerified) return 'complete'
  if (!hasNotes && charts.length === 0) return 'not_started'
  return 'in_progress'
}

// A short, collision-resistant element id. Migration 022 emits el_001…;
// editor-created elements use a timestamp+random suffix so a new element
// never clashes with a migrated one.
export function genElementId(): string {
  return `el_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

/** A fresh text element, placed mid-canvas for the user to drag/edit. */
export function newTextElement(): CanvasTextElement {
  return {
    id: genElementId(), type: 'text',
    x: 120, y: 230, width: 480, height: 80,
    content: 'New text', fontSize: 24, fontWeight: 'normal',
    fontStyle: 'normal', color: '#1B2A4A', locked: false,
  }
}

/** A fresh chart element — placed in the right half, vertically centred. */
export function newChartElement(chartKey: string): CanvasChartElement {
  const width = 360
  const height = 220
  return {
    id: genElementId(), type: 'chart',
    x: CANVAS_WIDTH - width - 60, y: (CANVAS_HEIGHT - height) / 2,
    width, height, chartKey, verified: false, locked: false,
  }
}

/** The Konva `fontStyle` string for a text element (Konva combines both). */
export function konvaFontStyle(el: CanvasTextElement): string {
  const parts: string[] = []
  if (el.fontWeight === 'bold') parts.push('bold')
  if (el.fontStyle === 'italic') parts.push('italic')
  return parts.length ? parts.join(' ') : 'normal'
}

/** The plain-text projection of a deck — feeds content_text / word count. */
export function deckToText(slides: CanvasSlide[]): string {
  return slides.map((s) => {
    const text = s.elements
      .filter((e): e is CanvasTextElement => e.type === 'text')
      .map((e) => e.content)
      .join('\n')
    return `${s.title}\n${text}\n${s.speaker_notes}`.trim()
  }).join('\n\n')
}

