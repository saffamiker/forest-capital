/**
 * script-writer.test.tsx — the presentation script writer frontend.
 *
 * Covers the speaker badge in the deck navigator, the canvas presenter
 * label, the script editor's delivery-time indicator and export
 * buttons, the Generate Script button gating, and the MOLLY callout.
 * react-konva is mocked globally (setup.ts); axios is mocked here.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

vi.mock('axios', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

import axios from 'axios'
import EditorNavigator from '../components/editor/EditorNavigator'
import type { NavSection } from '../components/editor/EditorNavigator'
import EditorTasksCallout from '../components/editor/EditorTasksCallout'
import CanvasSlideEditor from '../components/editor/CanvasSlideEditor'
import DocumentEditor from '../pages/DocumentEditor'
import type { CanvasDeck, EditorDraft, TipTapDoc } from '../types/editor'

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  mockedAxios.get.mockReset()
  mockedAxios.post.mockReset()
  mockedAxios.patch.mockReset()
  mockedAxios.patch.mockResolvedValue({ data: {} })
  mockedAxios.get.mockResolvedValue({ data: new Blob() })
})

// ── EditorNavigator — speaker badge + delivery-time line ──────────────────────

describe('EditorNavigator — speaker assignment', () => {
  const sections: NavSection[] = [
    { heading: 'Slide 1: Opening', totalMarkers: 1, markersRemaining: 1,
      speaker: 'Molly' },
    { heading: 'Slide 2: Agenda', totalMarkers: 1, markersRemaining: 1,
      speaker: null },
  ]
  const base = {
    title: 'Deck', wordCount: 0, wordTarget: 0, lastSavedLabel: 'never',
    saveState: 'idle' as const, versions: [],
    onJumpToSection: () => {}, onSaveVersion: () => {},
    onRestoreVersion: () => {},
  }

  it('renders a speaker badge per slide — assigned and unassigned', () => {
    render(<EditorNavigator {...base} sections={sections}
      onAssignSpeaker={() => {}} speakerSuggestions={['Molly', 'Bob']} />)
    // Slide 1 shows the assigned name; slide 2 offers [+ Speaker].
    expect(screen.getByText('Molly ▾')).toBeInTheDocument()
    expect(screen.getByText('+ Speaker')).toBeInTheDocument()
  })

  it('offers previously-used names as dropdown suggestions', () => {
    render(<EditorNavigator {...base} sections={sections}
      onAssignSpeaker={() => {}} speakerSuggestions={['Molly', 'Bob']} />)
    fireEvent.click(screen.getByText('+ Speaker'))
    // Both known names are suggested for the unassigned slide.
    expect(screen.getByRole('button', { name: 'Molly' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bob' })).toBeInTheDocument()
  })

  it('shows a read-only speaker line when there is no edit handler', () => {
    render(<EditorNavigator {...base} sections={sections} />)
    // The script navigator shows the speaker but no [+ Speaker] control.
    expect(screen.getByText('Molly')).toBeInTheDocument()
    expect(screen.queryByText('+ Speaker')).toBeNull()
  })

  it('renders the delivery-time metric line with its tone', () => {
    const { rerender } = render(<EditorNavigator {...base} sections={[]}
      metricLine="~22 min delivery · 3300 words" metricTone="ok" />)
    expect(screen.getByText('~22 min delivery · 3300 words'))
      .toBeInTheDocument()
    rerender(<EditorNavigator {...base} sections={[]}
      metricLine="~4 min delivery · 600 words" metricTone="warn" />)
    expect(screen.getByText('~4 min delivery · 600 words').className)
      .toContain('text-warning')
  })
})

// ── EditorTasksCallout — the MOLLY script callout ─────────────────────────────

describe('EditorTasksCallout — presentation_script', () => {
  beforeEach(() => sessionStorage.clear())

  it('shows the MOLLY script callout and dismisses it', () => {
    render(<EditorTasksCallout documentType="presentation_script"
      draftId={1} />)
    expect(screen.getByText('MOLLY — YOUR TASKS')).toBeInTheDocument()
    expect(screen.getByText(/rewrite every section in your own voice/i))
      .toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Dismiss'))
    expect(screen.queryByText('MOLLY — YOUR TASKS')).toBeNull()
  })
})

// ── CanvasSlideEditor — presenter label ───────────────────────────────────────

describe('CanvasSlideEditor — presenter label', () => {
  const deck = (speaker: string | null): CanvasDeck => ({
    slides: [{
      id: 1, title: 'Opening', background: '#FFFFFF', speaker_notes: '',
      speaker, elements: [],
    }],
  })

  it('shows the presenter label above the canvas when assigned', () => {
    render(<CanvasSlideEditor draftId={1} deck={deck('Molly')}
      activeSlideId={1} onChange={() => {}} onRequestChartPicker={() => {}} />)
    expect(screen.getByText(/Presenter:/)).toBeInTheDocument()
    expect(screen.getByText('Molly')).toBeInTheDocument()
  })

  it('shows no presenter label when the slide is unassigned', () => {
    render(<CanvasSlideEditor draftId={1} deck={deck(null)}
      activeSlideId={1} onChange={() => {}} onRequestChartPicker={() => {}} />)
    expect(screen.queryByText(/Presenter:/)).toBeNull()
  })
})

// ── DocumentEditor — Generate Script + script export ──────────────────────────

function deckDraft(withSpeaker: boolean): EditorDraft {
  return {
    id: 1, document_type: 'presentation_deck', owner_email: 't@q.edu',
    title: 'Deck', word_count: 0, version: 1, is_current: true,
    is_deleted: false, created_from: 'generated',
    created_at: null, updated_at: null, content_text: '',
    content_json: {
      slides: [{
        id: 1, title: 'Opening', background: '#FFFFFF', speaker_notes: '',
        speaker: withSpeaker ? 'Molly' : null, elements: [],
      }],
    },
  }
}

function scriptDraft(): EditorDraft {
  const content: TipTapDoc = {
    type: 'doc',
    content: [
      { type: 'heading', attrs: { level: 2 },
        content: [{ type: 'text', text: 'Slide 1: Opening' }] },
      { type: 'heading', attrs: { level: 3 },
        content: [{ type: 'text', text: 'Speaker: Molly' }] },
      { type: 'paragraph',
        content: [{ type: 'text', text: 'Delivery text for the opening.' }] },
      { type: 'heading', attrs: { level: 2 },
        content: [{ type: 'text', text: 'Slide 2: Findings' }] },
      { type: 'heading', attrs: { level: 3 },
        content: [{ type: 'text', text: 'Speaker: Bob' }] },
      { type: 'paragraph',
        content: [{ type: 'text', text: 'Delivery text for the findings.' }] },
    ],
  }
  return {
    id: 7, document_type: 'presentation_script', owner_email: 't@q.edu',
    title: 'Presentation Script', word_count: 10, version: 1,
    is_current: true, is_deleted: false, created_from: 'generated',
    created_at: null, updated_at: null,
    content_text: 'Slide 1: Opening\nDelivery text for the opening.',
    content_json: content,
  }
}

function mountEditor(draft: EditorDraft) {
  mockedAxios.get.mockImplementation((url: string) => {
    if (url.endsWith('/versions')) {
      return Promise.resolve({ data: { versions: [] } })
    }
    if (url.includes('/documents/drafts/')) {
      return Promise.resolve({ data: draft })
    }
    return Promise.resolve({ data: new Blob() })
  })
  return render(
    <MemoryRouter initialEntries={[`/editor/${draft.id}`]}>
      <Routes>
        <Route path="/editor/:draftId" element={<DocumentEditor />} />
      </Routes>
    </MemoryRouter>)
}

describe('DocumentEditor — Generate Script gating', () => {
  it('disables Generate Script when no slide has a speaker', async () => {
    mountEditor(deckDraft(false))
    const btn = await screen.findByRole('button', { name: /Generate Script/ })
    expect(btn).toBeDisabled()
  })

  it('enables Generate Script when a slide has a speaker', async () => {
    mountEditor(deckDraft(true))
    const btn = await screen.findByRole('button', { name: /Generate Script/ })
    expect(btn).toBeEnabled()
  })
})

describe('DocumentEditor — script export', () => {
  it('renders a master export and a button per unique speaker', async () => {
    mountEditor(scriptDraft())
    expect(await screen.findByRole('button',
      { name: /Export Master Script/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export: Molly' }))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export: Bob' }))
      .toBeInTheDocument()
  })

  it('shows the delivery-time estimate for a script draft', async () => {
    mountEditor(scriptDraft())
    expect(await screen.findByText(/min delivery/)).toBeInTheDocument()
  })

  it('shows the Academic Review script note in the writing assistant',
    async () => {
      // FIX 3 — the note appears below the Run Academic Review button
      // only when the draft is a presentation_script. Reminds the
      // presenter that the arbiter rubric is tuned for written work.
      mountEditor(scriptDraft())
      expect(await screen.findByText(
        /Academic Review is optimised for written submissions/))
        .toBeInTheDocument()
    })

  it('does NOT show the Academic Review script note for a deck draft',
    async () => {
      mountEditor(deckDraft(true))
      // The button is anchored by data-tour; the note must be absent.
      await screen.findByRole('button', { name: /Run Academic Review/ })
      expect(screen.queryByText(
        /Academic Review is optimised for written submissions/))
        .toBeNull()
    })

  it('shows the rehearsal note in the script navigator', async () => {
    // FIX 4 — the rehearsal note sits below the delivery-time line
    // in the EditorNavigator only for a presentation_script draft.
    mountEditor(scriptDraft())
    expect(await screen.findByText(/To rehearse with slides/))
      .toBeInTheDocument()
  })

  it('does NOT show the rehearsal note for a deck draft', async () => {
    mountEditor(deckDraft(true))
    // The deck has its own speaker/canvas affordances — the script-
    // specific rehearsal note must not appear there.
    await screen.findByRole('button', { name: /Generate Script/ })
    expect(screen.queryByText(/To rehearse with slides/)).toBeNull()
  })
})

// ── FIX 4 — EditorNavigator footnote rendering (no router needed) ─────────────

describe('EditorNavigator — footnote prop', () => {
  const base = {
    title: 'Script', wordCount: 0, wordTarget: 0, lastSavedLabel: 'never',
    saveState: 'idle' as const, sections: [], versions: [],
    onJumpToSection: () => {}, onSaveVersion: () => {},
    onRestoreVersion: () => {},
  }

  it('renders the rehearsal footnote when set', () => {
    render(<EditorNavigator {...base}
      footnote="To rehearse with slides: open your presentation deck in a second tab." />)
    expect(screen.getByText(/To rehearse with slides/))
      .toBeInTheDocument()
  })

  it('omits the footnote when not set', () => {
    render(<EditorNavigator {...base} />)
    expect(screen.queryByText(/To rehearse/)).toBeNull()
  })
})
