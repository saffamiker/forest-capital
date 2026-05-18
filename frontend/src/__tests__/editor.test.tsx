import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import {
  findMarkers, countMarkers, nodeToText, docToText, transformBobMarkers,
} from '../lib/editorMarkers'
import { BobCalloutPanel } from '../lib/BobCalloutNode'
import RichTextEditor, { VerifyPopup } from '../components/editor/RichTextEditor'
import SlideEditor, { slideComplete } from '../components/editor/SlideEditor'
import PresentationPreview from '../components/editor/PresentationPreview'
import type { DeckContent, DeckSlide, TipTapDoc } from '../types/editor'

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

// ── transformBobMarkers / docToText — [[BOB]] block-node projection ───────────

describe('transformBobMarkers / docToText', () => {
  it('promotes a whole-paragraph [[BOB]] marker to a bobCallout block node', () => {
    const doc: TipTapDoc = {
      type: 'doc',
      content: [
        { type: 'paragraph', content: [{ type: 'text', text: 'Intro line.' }] },
        { type: 'paragraph', content: [{ type: 'text',
          text: '[[BOB: write the roles section]]' }] },
      ],
    }
    const out = transformBobMarkers(doc)
    expect(out.content?.[0]?.type).toBe('paragraph')
    expect(out.content?.[1]?.type).toBe('bobCallout')
    expect((out.content?.[1]?.attrs as { text: string }).text)
      .toBe('write the roles section')
  })

  it('leaves an inline [[VERIFY]] marker in its paragraph — never a node', () => {
    const doc: TipTapDoc = {
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text',
        text: 'Sharpe was [[VERIFY: 0.63]] last year.' }] }],
    }
    const out = transformBobMarkers(doc)
    expect(out.content?.[0]?.type).toBe('paragraph')
    expect(JSON.stringify(out).includes('bobCallout')).toBe(false)
  })

  it('is idempotent — a doc already carrying bobCallout nodes is unchanged', () => {
    const doc: TipTapDoc = {
      type: 'doc',
      content: [{ type: 'bobCallout', attrs: { text: 'already a node' } }],
    }
    expect(transformBobMarkers(doc)).toEqual(doc)
  })

  it('docToText projects a bobCallout node back to its [[BOB: …]] marker', () => {
    const doc: TipTapDoc = {
      type: 'doc',
      content: [
        { type: 'paragraph', content: [{ type: 'text', text: 'Body.' }] },
        { type: 'bobCallout', attrs: { text: 'your input needed' } },
      ],
    }
    const text = docToText(doc)
    expect(text).toContain('[[BOB: your input needed]]')
    // so the projected text still registers as an unresolved marker —
    // section progress keeps counting a [[BOB]] node.
    expect(countMarkers(text, 'bob')).toBe(1)
  })

  it('nodeToText returns the marker text for a lone bobCallout node', () => {
    expect(nodeToText({ type: 'bobCallout', attrs: { text: 'x' } }))
      .toBe('[[BOB: x]]')
  })
})

// ── BobCalloutPanel — the [[BOB]] block panel ─────────────────────────────────

describe('BobCalloutPanel', () => {
  it('renders [[BOB]] as a full-width block panel, not an inline span', () => {
    render(<BobCalloutPanel text="write the roles section"
      onComplete={() => {}} />)
    expect(screen.getByText('✏️ BOB — YOUR INPUT NEEDED')).toBeInTheDocument()
    expect(screen.getByText('write the roles section')).toBeInTheDocument()
    expect(screen.getByText('Mark as Complete')).toBeInTheDocument()
  })

  it('Mark as Complete fires onComplete — the callout is removed', () => {
    const onComplete = vi.fn()
    render(<BobCalloutPanel text="x" onComplete={onComplete} />)
    fireEvent.click(screen.getByText('Mark as Complete'))
    expect(onComplete).toHaveBeenCalledTimes(1)
  })
})

// ── VerifyPopup — the floating [[VERIFY]] confirm popup ───────────────────────

describe('VerifyPopup', () => {
  it('shows the verify-against-Analytics message and the two actions', () => {
    render(<VerifyPopup x={100} y={100} onVerify={() => {}}
      onCancel={() => {}} />)
    expect(screen.getByRole('dialog', { name: 'Verify marker' }))
      .toBeInTheDocument()
    expect(screen.getByText(/Verify this value against the Analytics page/))
      .toBeInTheDocument()
    expect(screen.getByText('Mark as Verified')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()
  })

  it('Mark as Verified fires onVerify — the marker is removed', () => {
    const onVerify = vi.fn()
    render(<VerifyPopup x={0} y={0} onVerify={onVerify} onCancel={() => {}} />)
    fireEvent.click(screen.getByText('Mark as Verified'))
    expect(onVerify).toHaveBeenCalledTimes(1)
  })

  it('Cancel and a backdrop click fire onCancel — the marker stays intact', () => {
    const onCancel = vi.fn()
    render(<VerifyPopup x={0} y={0} onVerify={() => {}} onCancel={onCancel} />)
    fireEvent.click(screen.getByText('Cancel'))
    fireEvent.click(screen.getByTestId('verify-backdrop'))
    expect(onCancel).toHaveBeenCalledTimes(2)
  })
})

// ── RichTextEditor — [[BOB]] block node + [[VERIFY]] inline marker ────────────

describe('RichTextEditor markers', () => {
  afterEach(() => { vi.restoreAllMocks() })

  const bobDoc: TipTapDoc = {
    type: 'doc',
    content: [
      { type: 'paragraph', content: [{ type: 'text', text: 'Section body.' }] },
      { type: 'paragraph', content: [{ type: 'text',
        text: '[[BOB: write the roles section]]' }] },
    ],
  }

  it('renders a [[BOB]] marker as a block panel, not an inline span', async () => {
    render(<RichTextEditor content={bobDoc} onChange={() => {}} />)
    expect(await screen.findByText('✏️ BOB — YOUR INPUT NEEDED'))
      .toBeInTheDocument()
    expect(screen.getByText('write the roles section')).toBeInTheDocument()
    // promoted to a node — no inline [[BOB]] marker decoration remains.
    expect(document.querySelector('.editor-marker-bob')).toBeNull()
  })

  it('Mark as Complete removes the callout and drops the marker count', async () => {
    const onChange = vi.fn()
    render(<RichTextEditor content={bobDoc} onChange={onChange} />)
    const btn = await screen.findByText('Mark as Complete')
    fireEvent.click(btn)
    const calls = onChange.mock.calls
    expect(calls.length).toBeGreaterThan(0)
    // the projected text no longer carries a [[BOB]] marker — section
    // progress for that heading advances.
    const lastText = calls[calls.length - 1][1] as string
    expect(countMarkers(lastText, 'bob')).toBe(0)
  })

  it('renders a [[VERIFY]] marker as an inline amber marker span', async () => {
    const verifyDoc: TipTapDoc = {
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text',
        text: 'The Sharpe ratio was [[VERIFY: 0.63]] over the period.' }] }],
    }
    render(<RichTextEditor content={verifyDoc} onChange={() => {}} />)
    await screen.findByText(/The Sharpe ratio was/)
    const span = document.querySelector('.editor-marker-verify')
    expect(span).not.toBeNull()
    expect(span?.textContent).toContain('[[VERIFY: 0.63]]')
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

// ── PresentationPreview — the full-screen rehearsal overlay ───────────────────

describe('PresentationPreview', () => {
  const slides: DeckSlide[] = [
    { id: 1, title: 'Opening', content: 'Intro body', data_points: [],
      speaker_notes: 'Say hello and set up the question', verified: false,
      notes_written: true },
    { id: 2, title: 'Findings', content: 'Findings body', data_points: [],
      speaker_notes: 'Walk through the 2022 break', verified: false,
      notes_written: true },
    { id: 3, title: 'Close', content: 'Closing body', data_points: [],
      speaker_notes: '', verified: false, notes_written: false },
  ]

  it('opens a full-screen overlay showing the first slide', () => {
    render(<PresentationPreview slides={slides} onClose={() => {}} />)
    expect(screen.getByTestId('presentation-preview')).toBeInTheDocument()
    expect(screen.getByText('Opening')).toBeInTheDocument()
    expect(screen.getByText('1 / 3')).toBeInTheDocument()
  })

  it('navigates slides with the arrow keys', () => {
    render(<PresentationPreview slides={slides} onClose={() => {}} />)
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
    expect(screen.getByText('Findings')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(screen.getByText('1 / 3')).toBeInTheDocument()
  })

  it('shows the presenter speaker notes in a strip', () => {
    render(<PresentationPreview slides={slides} onClose={() => {}} />)
    expect(screen.getByText('Your notes (not visible to audience)'))
      .toBeInTheDocument()
    expect(screen.getByText('Say hello and set up the question'))
      .toBeInTheDocument()
  })

  it('closes on Escape', () => {
    const onClose = vi.fn()
    render(<PresentationPreview slides={slides} onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('closes when the Exit button is clicked', () => {
    const onClose = vi.fn()
    render(<PresentationPreview slides={slides} onClose={onClose} />)
    fireEvent.click(screen.getByLabelText('Exit preview'))
    expect(onClose).toHaveBeenCalled()
  })
})
