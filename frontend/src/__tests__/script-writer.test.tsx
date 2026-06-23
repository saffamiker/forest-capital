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
  isAxiosError: typeof axios.isAxiosError
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

  it('shows the script-specific review framing in the writing '
    + 'assistant', async () => {
      // June 23 2026 -- per-doc review surfaces. The script editor's
      // button now reads "Review Script" and the framing note below
      // describes the script-specific rubric (argument coherence,
      // audience clarity, slide coverage; formatting scores not
      // applied).
      mountEditor(scriptDraft())
      expect(await screen.findByText(
        /Presentation Script review evaluates argument coherence/))
        .toBeInTheDocument()
    })

  it('shows the DECK-specific review framing for a deck draft',
    async () => {
      // June 23 2026 -- the deck draft no longer has zero framing;
      // it shows its own deck-specific note (slide flow, so-what,
      // speaker-note coverage) and the button reads "Review Deck".
      // The script-specific framing must NOT appear on the deck.
      mountEditor(deckDraft(true))
      await screen.findByRole(
        'button', { name: /Review Deck/ })
      expect(await screen.findByText(
        /Presentation Deck review evaluates slide flow/))
        .toBeInTheDocument()
      expect(screen.queryByText(
        /Presentation Script review evaluates argument coherence/))
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


// ── Mobile pass — Document editor overlay treatment + canvas banner ───────────

describe('DocumentEditor — mobile overlay treatment', () => {
  // Force the matchMedia branch to "mobile" (lg query does NOT match) so
  // isDesktop is false and the editor renders the mobile overlay path.
  // The default jsdom env reports matchMedia undefined → falls back to
  // desktop. Patching it here is the simplest way to exercise the
  // mobile rendering branch from a unit test.
  beforeEach(() => {
    const _mq = vi.fn().mockImplementation(() => ({
      matches: false,
      media: '(min-width: 1024px)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }))
    Object.defineProperty(window, 'matchMedia', {
      writable: true, configurable: true, value: _mq,
    })
  })

  it('defaults both panels CLOSED on mobile', async () => {
    mountEditor(scriptDraft())
    // The script editor's delivery-time pill lives inside EditorNavigator
    // — present only when the left panel is open. On mobile both panels
    // default closed, so the delivery-time text must NOT appear.
    // June 23 2026 -- per-doc review buttons. A script draft's
    // button reads "Review Script" now.
    await screen.findByRole('button', { name: /Review Script/i })
      .catch(() => null)
    // Give the page one render cycle. The "Generating…" Generate-Script
    // button anchors the editor is mounted.
    await screen.findByText('Presentation Script')
    expect(screen.queryByText(/min delivery/)).toBeNull()
  })

  it('shows the canvas-editor mobile banner for a deck draft', async () => {
    mountEditor(deckDraft(true))
    expect(await screen.findByText(
      /presentation canvas editor works best on desktop/i,
    )).toBeInTheDocument()
  })

  it('does NOT show the canvas banner for a script draft', async () => {
    mountEditor(scriptDraft())
    await screen.findByText('Presentation Script')
    expect(screen.queryByText(
      /presentation canvas editor works best on desktop/i,
    )).toBeNull()
  })
})


// ── Rehearsal Mode — combined script + slide overlay ──────────────────────────

describe('Rehearsal Mode', () => {
  // Rehearsal mode is gated on the document_type — only a script draft
  // shows the [Rehearse] button in the header.

  it('shows the Rehearse button only in the script editor', async () => {
    mountEditor(scriptDraft())
    expect(await screen.findByRole('button', { name: /^Rehearse$/i }))
      .toBeInTheDocument()
  })

  it('does NOT show the Rehearse button in a deck editor', async () => {
    mountEditor(deckDraft(true))
    await screen.findByRole('button', { name: /Generate Script/i })
    expect(screen.queryByRole('button', { name: /^Rehearse$/i })).toBeNull()
  })

  it('opens the rehearsal overlay with both deck and script when present',
    async () => {
      // Wire the rehearsal endpoint to return a fully populated payload;
      // mountEditor's mock returns the script draft for the
      // /documents/drafts/:id load. Both must resolve for the overlay
      // to render its two panels.
      mockedAxios.get.mockImplementation((url: string) => {
        if (url.endsWith('/documents/rehearsal')) {
          return Promise.resolve({
            data: {
              deck: { draft_id: 1, slides: [{
                id: 1, title: 'Opening', background: '#FFFFFF',
                speaker_notes: 'Hold for applause.', speaker: 'Molly',
                elements: [{
                  id: 'el_001', type: 'text', x: 50, y: 60,
                  width: 800, height: 100, content: 'Opening',
                  fontSize: 48, fontWeight: 'bold', fontStyle: 'normal',
                  color: '#1A1A2E',
                }],
              }] },
              script: { draft_id: 2, total_words: 300,
                estimated_minutes: 2, sections: [{
                  slide_number: 1, title: 'Opening',
                  speaker: 'Molly',
                  script_text: 'Good evening. The question…',
                  transition: 'Move to the architecture.',
                  word_count: 300,
                }] },
            },
          })
        }
        if (url.endsWith('/versions')) {
          return Promise.resolve({ data: { versions: [] } })
        }
        if (url.includes('/documents/drafts/')) {
          return Promise.resolve({ data: scriptDraft() })
        }
        return Promise.resolve({ data: new Blob() })
      })
      render(
        <MemoryRouter initialEntries={['/editor/7']}>
          <Routes>
            <Route path="/editor/:draftId" element={<DocumentEditor />} />
          </Routes>
        </MemoryRouter>)
      // Click the header [Rehearse] button.
      const btn = await screen.findByRole('button', { name: /^Rehearse$/i })
      fireEvent.click(btn)
      // The overlay mounts; both the script panel and the slide panel
      // render via their stable data-testids.
      expect(await screen.findByTestId('rehearsal-overlay'))
        .toBeInTheDocument()
      await screen.findByTestId('rehearsal-script-panel')
      expect(screen.getByTestId('rehearsal-slide-panel')).toBeInTheDocument()
      // The min-remaining counter renders.
      expect(screen.getByTestId('rehearsal-min-remaining'))
        .toBeInTheDocument()
    })

  it('pre-fetches every unique chart in the deck on overlay open',
    async () => {
      // The slide carries a single chart element. Opening the overlay
      // must trigger one GET to /api/v1/charts/render/rolling_correlation
      // — pre-fetch, not on-demand. A chart used on multiple slides is
      // fetched ONCE; not exercised here, but the cache key is the
      // chart_key alone.
      mockedAxios.isAxiosError = ((() => true) as unknown) as typeof axios.isAxiosError
      mockedAxios.get.mockImplementation((url: string) => {
        if (url.endsWith('/documents/rehearsal')) {
          return Promise.resolve({
            data: {
              deck: { draft_id: 1, slides: [{
                id: 1, title: 'Correlation Break', background: '#FFFFFF',
                speaker_notes: '', speaker: 'Molly',
                elements: [{
                  id: 'el_001', type: 'chart', x: 100, y: 100,
                  width: 760, height: 340,
                  chartKey: 'rolling_correlation', verified: false,
                }],
              }] },
              script: { draft_id: 2, total_words: 150,
                estimated_minutes: 1, sections: [{
                  slide_number: 1, title: 'Correlation Break',
                  speaker: 'Molly', script_text: 'The 2022 break…',
                  transition: '', word_count: 150,
                }] },
            },
          })
        }
        if (url.startsWith('/api/v1/charts/render/')) {
          return Promise.resolve({ data: new Blob() })
        }
        if (url.endsWith('/versions')) {
          return Promise.resolve({ data: { versions: [] } })
        }
        if (url.includes('/documents/drafts/')) {
          return Promise.resolve({ data: scriptDraft() })
        }
        return Promise.resolve({ data: new Blob() })
      })
      render(
        <MemoryRouter initialEntries={['/editor/7']}>
          <Routes>
            <Route path="/editor/:draftId" element={<DocumentEditor />} />
          </Routes>
        </MemoryRouter>)
      fireEvent.click(await screen.findByRole(
        'button', { name: /^Rehearse$/i }))
      // Wait for the overlay AND the chart fetch.
      await screen.findByTestId('rehearsal-overlay')
      // The chart-render endpoint is called with the chart's key.
      await new Promise((r) => setTimeout(r, 0))
      const calls = mockedAxios.get.mock.calls.map(([u]) => u as string)
      expect(calls.some((u) =>
        u.startsWith('/api/v1/charts/render/rolling_correlation'),
      )).toBe(true)
      // Real chart image renders — <img> with alt naming the chart key.
      const img = await screen.findByAltText('rolling_correlation')
      expect(img).toBeInTheDocument()
    })

  it('falls back to the placeholder box when the chart fetch fails',
    async () => {
      // A failed /api/v1/charts/render call must not break rehearsal —
      // the labelled placeholder box renders in the chart's position.
      mockedAxios.isAxiosError = ((() => true) as unknown) as typeof axios.isAxiosError
      mockedAxios.get.mockImplementation((url: string) => {
        if (url.endsWith('/documents/rehearsal')) {
          return Promise.resolve({
            data: {
              deck: { draft_id: 1, slides: [{
                id: 1, title: 'Slide', background: '#FFFFFF',
                speaker_notes: '', speaker: 'Molly',
                elements: [{
                  id: 'el_001', type: 'chart', x: 100, y: 100,
                  width: 760, height: 340,
                  chartKey: 'cumulative_returns', verified: false,
                }],
              }] },
              script: { draft_id: 2, total_words: 100,
                estimated_minutes: 1, sections: [{
                  slide_number: 1, title: 'Slide', speaker: 'Molly',
                  script_text: '', transition: '', word_count: 100,
                }] },
            },
          })
        }
        if (url.startsWith('/api/v1/charts/render/')) {
          return Promise.reject(new Error('render failed'))
        }
        if (url.endsWith('/versions')) {
          return Promise.resolve({ data: { versions: [] } })
        }
        if (url.includes('/documents/drafts/')) {
          return Promise.resolve({ data: scriptDraft() })
        }
        return Promise.resolve({ data: new Blob() })
      })
      render(
        <MemoryRouter initialEntries={['/editor/7']}>
          <Routes>
            <Route path="/editor/:draftId" element={<DocumentEditor />} />
          </Routes>
        </MemoryRouter>)
      fireEvent.click(await screen.findByRole(
        'button', { name: /^Rehearse$/i }))
      await screen.findByTestId('rehearsal-overlay')
      // The placeholder still renders with the chart_key label —
      // underscores converted to spaces.
      expect(await screen.findByText('[cumulative returns]'))
        .toBeInTheDocument()
    })

  it('shows the missing-data modal when the rehearsal endpoint 404s',
    async () => {
      mockedAxios.isAxiosError = ((() => true) as unknown) as typeof axios.isAxiosError
      mockedAxios.get.mockImplementation((url: string) => {
        if (url.endsWith('/documents/rehearsal')) {
          return Promise.reject({
            response: {
              status: 404,
              data: { detail: 'No presentation deck found. '
                              + 'Generate your deck first.' },
            },
          })
        }
        if (url.endsWith('/versions')) {
          return Promise.resolve({ data: { versions: [] } })
        }
        if (url.includes('/documents/drafts/')) {
          return Promise.resolve({ data: scriptDraft() })
        }
        return Promise.resolve({ data: new Blob() })
      })
      render(
        <MemoryRouter initialEntries={['/editor/7']}>
          <Routes>
            <Route path="/editor/:draftId" element={<DocumentEditor />} />
          </Routes>
        </MemoryRouter>)
      const btn = await screen.findByRole('button', { name: /^Rehearse$/i })
      fireEvent.click(btn)
      // The "Rehearsal requires both…" modal renders inside the overlay.
      expect(await screen.findByText(
        /Rehearsal requires both/i)).toBeInTheDocument()
      expect(screen.getByText(
        /No presentation deck found/i)).toBeInTheDocument()
      // Close button works.
      const close = screen.getByRole('button', { name: /Close/i })
      fireEvent.click(close)
      // After closing, the overlay's content is gone (only the editor remains).
      expect(screen.queryByText(/Rehearsal requires both/i)).toBeNull()
    })
})
