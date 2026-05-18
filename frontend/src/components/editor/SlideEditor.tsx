/**
 * SlideEditor — the centre panel for a presentation_deck draft. The deck
 * renders as a vertical stack of slide cards (not TipTap): an editable
 * title and content, amber data-point markers the presenter verifies,
 * and a speaker-notes field with a Generate Talking Points helper.
 *
 * A slide is complete when every data point is verified AND the speaker
 * notes are non-empty.
 */
import { useState } from 'react'
import axios from 'axios'
import { CheckCircle2, Circle, Loader2, Sparkles, Plus } from 'lucide-react'

import type { DeckContent, DeckSlide } from '../../types/editor'

interface Props {
  draftId: number
  deck: DeckContent
  onChange: (deck: DeckContent) => void
}

export function slideComplete(s: DeckSlide): boolean {
  return s.verified && s.notes_written
}

export default function SlideEditor({ draftId, deck, onChange }: Props) {
  const slides = deck.slides ?? []

  const patchSlide = (id: number, patch: Partial<DeckSlide>) => {
    onChange({
      slides: slides.map((s) => {
        if (s.id !== id) return s
        const next = { ...s, ...patch }
        next.notes_written = next.speaker_notes.trim().length > 0
        return next
      }),
    })
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-5 space-y-5">
      {slides.length === 0 && (
        <p className="text-sm text-muted italic">
          This deck draft has no slides.
        </p>
      )}
      {slides.map((s, i) => (
        <SlideCard key={s.id} draftId={draftId} slide={s} index={i + 1}
          total={slides.length}
          onPatch={(patch) => patchSlide(s.id, patch)} />
      ))}
    </div>
  )
}

function SlideCard({
  draftId, slide, index, total, onPatch,
}: {
  draftId: number
  slide: DeckSlide
  index: number
  total: number
  onPatch: (patch: Partial<DeckSlide>) => void
}) {
  const [points, setPoints] = useState<string[]>([])
  const [genLoading, setGenLoading] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const complete = slide.verified && slide.notes_written

  const generateTalkingPoints = async () => {
    setGenLoading(true)
    setGenError(null)
    try {
      const res = await axios.post(`/api/documents/${draftId}/assistant`, {
        message: 'Generate 4-6 concise speaker-note talking points that '
          + 'explain this slide to a non-technical audience. Do not read '
          + 'the slide verbatim — expand on it. One point per line.',
        context_content: `${slide.title}\n\n${slide.content}`,
        context_type: 'slide',
      })
      const text: string = res.data?.suggestion || res.data?.explanation || ''
      const lines = text.split('\n').map((l) => l.replace(/^[-*\d.\s]+/, '').trim())
        .filter(Boolean)
      setPoints(lines.slice(0, 6))
      if (lines.length === 0) setGenError('No talking points returned.')
    } catch {
      setGenError('Could not generate talking points.')
    } finally {
      setGenLoading(false)
    }
  }

  const insertPoint = (p: string) => {
    const notes = slide.speaker_notes.trim()
    onPatch({ speaker_notes: notes ? `${notes}\n• ${p}` : `• ${p}` })
    setPoints((prev) => prev.filter((x) => x !== p))
  }

  return (
    <div data-tour="slide-card"
      className={`card p-4 border-l-2 ${complete
        ? 'border-l-success' : 'border-l-border'}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-2xs text-muted uppercase tracking-wide">
          Slide {index} of {total}
        </span>
        <span className={`text-2xs flex items-center gap-1 ${complete
          ? 'text-success' : 'text-muted'}`}>
          {complete
            ? <><CheckCircle2 className="w-3.5 h-3.5" /> Complete</>
            : <><Circle className="w-3.5 h-3.5" /> {slide.notes_written
                ? 'Verify data points' : 'Notes missing'}</>}
        </span>
      </div>

      {/* Title */}
      <label className="text-2xs text-muted uppercase tracking-wide">Title</label>
      <input
        value={slide.title}
        onChange={(e) => onPatch({ title: e.target.value })}
        className="w-full bg-navy-800 border border-border rounded text-sm
                   text-white px-2 py-1.5 mb-3 mt-0.5"
      />

      {/* Content */}
      <label className="text-2xs text-muted uppercase tracking-wide">Content</label>
      <textarea
        value={slide.content}
        onChange={(e) => onPatch({ content: e.target.value })}
        rows={3}
        className="w-full bg-navy-800 border border-border rounded text-sm
                   text-white px-2 py-1.5 mb-3 mt-0.5 resize-y"
      />

      {/* Data points */}
      {slide.data_points && slide.data_points.length > 0 && (
        <div className="mb-3">
          <label className="text-2xs text-muted uppercase tracking-wide">
            Data points
          </label>
          <div className="mt-1 space-y-1">
            {slide.data_points.map((dp, i) => (
              <div key={i}
                className="text-xs px-2 py-1 rounded bg-warning/10
                           border border-warning/30 text-warning">
                {dp}
              </div>
            ))}
          </div>
          <button type="button"
            onClick={() => onPatch({ verified: !slide.verified })}
            className={`mt-1.5 text-2xs flex items-center gap-1 ${slide.verified
              ? 'text-success' : 'text-muted hover:text-white'}`}>
            {slide.verified
              ? <><CheckCircle2 className="w-3.5 h-3.5" /> Data points verified</>
              : <><Circle className="w-3.5 h-3.5" /> Mark data points verified</>}
          </button>
        </div>
      )}

      {/* Speaker notes */}
      <label className="text-2xs text-muted uppercase tracking-wide">
        Speaker notes
      </label>
      <textarea
        value={slide.speaker_notes}
        onChange={(e) => onPatch({ speaker_notes: e.target.value })}
        rows={4}
        placeholder="Write your speaker notes here…"
        className="w-full bg-navy-800 border border-border rounded text-sm
                   text-white px-2 py-1.5 mt-0.5 resize-y"
      />
      <button type="button" onClick={generateTalkingPoints} disabled={genLoading}
        className="mt-1.5 text-2xs flex items-center gap-1 text-electric
                   hover:underline disabled:opacity-50">
        {genLoading
          ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Generating…</>
          : <><Sparkles className="w-3.5 h-3.5" /> Generate Talking Points</>}
      </button>
      {genError && <p className="text-2xs text-danger mt-1">{genError}</p>}
      {points.length > 0 && (
        <div className="mt-2 space-y-1">
          {points.map((p, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <button type="button" onClick={() => insertPoint(p)}
                aria-label="Insert talking point"
                className="text-electric hover:text-white shrink-0 mt-0.5">
                <Plus className="w-3.5 h-3.5" />
              </button>
              <span className="text-slate-300">{p}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
