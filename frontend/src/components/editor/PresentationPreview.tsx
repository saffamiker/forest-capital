/**
 * PresentationPreview — a full-screen rehearsal view for a
 * presentation_deck draft.
 *
 * Renders one slide at a time on a clean white background — large
 * title, content below, and the presenter's speaker notes in a greyed
 * bottom strip. Arrow keys or the on-screen ‹ › buttons navigate; Esc
 * exits. No API call — it renders from the slides already in state.
 *
 * This is a rehearsal tool, not the .pptx renderer; it deliberately
 * does not match the export theme.
 */
import { useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'

import type { DeckSlide } from '../../types/editor'

interface Props {
  slides: DeckSlide[]
  onClose: () => void
}

export default function PresentationPreview({ slides, onClose }: Props) {
  const [index, setIndex] = useState(0)
  const total = slides.length
  const slide: DeckSlide | undefined = slides[index]

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

  return (
    <div data-testid="presentation-preview"
      className="fixed inset-0 z-[80] bg-white flex flex-col">
      {/* Top bar — counter + exit */}
      <div className="flex items-center justify-between px-4 py-2
                      border-b border-gray-200 shrink-0">
        <span className="text-sm font-mono text-gray-500">
          {total === 0 ? '0 / 0' : `${index + 1} / ${total}`}
        </span>
        <button type="button" onClick={onClose} aria-label="Exit preview"
          className="flex items-center gap-1 text-sm text-gray-500
                     hover:text-gray-900">
          <X className="w-4 h-4" /> Exit (Esc)
        </button>
      </div>

      {/* Slide */}
      <div className="flex-1 flex items-center min-h-0">
        <button type="button" onClick={() => go(-1)} disabled={index === 0}
          aria-label="Previous slide"
          className="px-4 h-full text-gray-300 hover:text-gray-700
                     disabled:opacity-30 disabled:hover:text-gray-300">
          <ChevronLeft className="w-8 h-8" />
        </button>

        <div className="flex-1 h-full overflow-y-auto px-8 py-10">
          {slide ? (
            <div className="max-w-4xl mx-auto">
              <h1 className="text-4xl font-bold text-gray-900 mb-6">
                {slide.title}
              </h1>
              <div className="text-xl text-gray-700 whitespace-pre-wrap
                              leading-relaxed">
                {slide.content || '(No slide content yet.)'}
              </div>
              {slide.data_points && slide.data_points.length > 0 && (
                <ul className="mt-6 space-y-1 text-lg text-gray-600 list-disc pl-6">
                  {slide.data_points.map((dp, i) => <li key={i}>{dp}</li>)}
                </ul>
              )}
            </div>
          ) : (
            <p className="text-gray-400 text-center mt-20">
              This deck has no slides.
            </p>
          )}
        </div>

        <button type="button" onClick={() => go(1)}
          disabled={index >= total - 1}
          aria-label="Next slide"
          className="px-4 h-full text-gray-300 hover:text-gray-700
                     disabled:opacity-30 disabled:hover:text-gray-300">
          <ChevronRight className="w-8 h-8" />
        </button>
      </div>

      {/* Speaker notes strip — presenter-only. */}
      <div className="border-t border-gray-200 bg-gray-50 px-8 py-3 shrink-0
                      max-h-40 overflow-y-auto">
        <div className="text-2xs uppercase tracking-wide text-gray-400 mb-1">
          Your notes (not visible to audience)
        </div>
        <div className="text-sm text-gray-500 whitespace-pre-wrap">
          {slide?.speaker_notes?.trim()
            || 'No speaker notes for this slide yet.'}
        </div>
      </div>
    </div>
  )
}
