import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { findMarkers, countMarkers } from '../lib/editorMarkers'
import SlideEditor, { slideComplete } from '../components/editor/SlideEditor'
import type { DeckContent, DeckSlide } from '../types/editor'

// ── [[VERIFY]] / [[BOB]] marker detection ─────────────────────────────────────

describe('editorMarkers', () => {
  it('finds [[VERIFY]] and [[VERIFY CITATION]] markers', () => {
    const text = 'A value [[VERIFY: Sharpe = 0.63]] and a cite '
      + '[[VERIFY CITATION: check Carhart 1997]].'
    const hits = findMarkers(text)
    expect(hits).toHaveLength(2)
    expect(hits.every((h) => h.kind === 'verify')).toBe(true)
  })

  it('finds [[BOB]] callout markers', () => {
    const hits = findMarkers('Intro. [[BOB: write your own analysis here]]')
    expect(hits).toHaveLength(1)
    expect(hits[0].kind).toBe('bob')
  })

  it('counts markers by kind', () => {
    const text = '[[VERIFY: a]] [[VERIFY: b]] [[BOB: c]]'
    expect(countMarkers(text)).toBe(3)
    expect(countMarkers(text, 'verify')).toBe(2)
    expect(countMarkers(text, 'bob')).toBe(1)
  })

  it('ignores plain text with no markers', () => {
    expect(countMarkers('no markers here at all')).toBe(0)
    expect(findMarkers('')).toEqual([])
  })
})

// ── Slide completion logic ────────────────────────────────────────────────────

describe('slideComplete', () => {
  const base: DeckSlide = {
    id: 1, title: 'T', content: 'C', data_points: [],
    speaker_notes: '', verified: false, notes_written: false,
  }

  it('is complete only when verified AND notes written', () => {
    expect(slideComplete(base)).toBe(false)
    expect(slideComplete({ ...base, verified: true })).toBe(false)
    expect(slideComplete({ ...base, notes_written: true })).toBe(false)
    expect(slideComplete({ ...base, verified: true, notes_written: true }))
      .toBe(true)
  })
})

// ── SlideEditor — the presentation-deck centre panel ──────────────────────────

function deck(): DeckContent {
  return {
    slides: [
      { id: 1, title: 'Title slide', content: 'Intro', data_points: ['Sharpe 0.63'],
        speaker_notes: '', verified: false, notes_written: false },
      { id: 2, title: 'Results', content: 'Results body', data_points: [],
        speaker_notes: 'already written', verified: false, notes_written: true },
    ],
  }
}

describe('SlideEditor', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('renders a slide card per slide (not a TipTap surface)', () => {
    render(<SlideEditor draftId={1} deck={deck()} onChange={() => {}} />)
    expect(screen.getByDisplayValue('Title slide')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Results')).toBeInTheDocument()
    expect(screen.getByText('Slide 1 of 2')).toBeInTheDocument()
  })

  it('has an editable speaker-notes field that fires onChange', () => {
    const onChange = vi.fn()
    render(<SlideEditor draftId={1} deck={deck()} onChange={onChange} />)
    const notes = screen.getAllByPlaceholderText(
      'Write your speaker notes here…')[0]
    fireEvent.change(notes, { target: { value: 'my talking points' } })
    expect(onChange).toHaveBeenCalled()
    const next = onChange.mock.calls[0][0] as DeckContent
    expect(next.slides[0].speaker_notes).toBe('my talking points')
    // Writing notes flips notes_written.
    expect(next.slides[0].notes_written).toBe(true)
  })

  it('marks data points verified on click', () => {
    const onChange = vi.fn()
    render(<SlideEditor draftId={1} deck={deck()} onChange={onChange} />)
    fireEvent.click(screen.getByText('Mark data points verified'))
    const next = onChange.mock.calls[0][0] as DeckContent
    expect(next.slides[0].verified).toBe(true)
  })
})
