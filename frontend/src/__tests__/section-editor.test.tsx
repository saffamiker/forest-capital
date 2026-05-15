/**
 * section-editor.test.tsx
 *
 * Tests for Bob's section editor (Sprint 6 Phase 10):
 *   1. documentsStore — createDraft / loadDocument / updateSection /
 *      regenerateSection / revertSection / saveNamedVersion / restoreVersion
 *   2. SectionEditor renders the AI DRAFT banner, the section list,
 *      and the editor surface for the selected section.
 *   3. Per-section affordances: View AI Draft panel, Regenerate AI,
 *      Revert with confirmation, word count live.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, renderHook, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import axios from 'axios'

import { useDocumentsStore } from '../stores/documentsStore'
import SectionEditor from '../pages/SectionEditor'
import type {
  SectionDocument, SectionDocDraftResponse, DocumentDraftResponse,
} from '../types/documents'


vi.mock('axios')
const mockedAxios = axios as unknown as {
  get:   ReturnType<typeof vi.fn>
  post:  ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
}


function buildDocFixture(): SectionDocument {
  return {
    doc_type: 'executive_brief',
    title:    'Forest Capital — Executive Brief',
    subtitle: 'FNA 670 Practicum',
    sections: [
      {
        id:          'executive_summary',
        title:       '1. Executive Summary',
        ai_draft:    'AI-drafted executive summary anchored to live strategy results.',
        content:     'AI-drafted executive summary anchored to live strategy results.',
        last_edited: '2026-05-14T10:00:00Z',
      },
      {
        id:          'methodology',
        title:       '2. Methodology',
        ai_draft:    'The study employed a tiered statistical framework.',
        content:     'The study employed a tiered statistical framework.',
        last_edited: '2026-05-14T10:00:00Z',
      },
    ],
  }
}


function renderEditorAt(documentId: string): void {
  // SectionEditor uses useParams — wrap it in a MemoryRouter at the
  // matching route so the loader fires the GET we mocked below.
  render(
    <MemoryRouter initialEntries={[`/reports/document/${documentId}`]}>
      <Routes>
        <Route path="/reports/document/:documentId" element={<SectionEditor />} />
        <Route path="/reports" element={<div>Reports</div>} />
      </Routes>
    </MemoryRouter>,
  )
}


beforeEach(() => {
  useDocumentsStore.setState({
    documentId: null, document: null, versions: [],
    loading: false, saving: false, lastSavedAt: null, error: null,
    selectedSectionId: null,
  })
  mockedAxios.get   = vi.fn()
  mockedAxios.post  = vi.fn()
  mockedAxios.patch = vi.fn()
  mockedAxios.isAxiosError = (() => false) as never
})

afterEach(() => {
  vi.clearAllMocks()
})


// ── Store invariants ────────────────────────────────────────────────────────

describe('documentsStore', () => {
  it('createDraft posts the doc_type and stores the returned content', async () => {
    const fixture = buildDocFixture()
    const response: SectionDocDraftResponse = {
      document_id: 'doc-123',
      content:     fixture,
      persistence: 'saved',
    }
    mockedAxios.post = vi.fn().mockResolvedValue({ data: response })

    const { result } = renderHook(() => useDocumentsStore())
    let docId: string | null = null
    await act(async () => {
      docId = await result.current.createDraft('executive_brief')
    })

    expect(docId).toBe('doc-123')
    expect(result.current.documentId).toBe('doc-123')
    expect(result.current.document).toEqual(fixture)
    expect(result.current.selectedSectionId).toBe('executive_summary')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/documents/section-doc/draft',
      { doc_type: 'executive_brief' },
    )
  })

  it('updateSection mutates the active section and timestamps last_edited', async () => {
    const fixture = buildDocFixture()
    useDocumentsStore.setState({
      documentId: 'doc-1', document: fixture, selectedSectionId: 'executive_summary',
    })

    const { result } = renderHook(() => useDocumentsStore())
    act(() => {
      result.current.updateSection('executive_summary', { content: 'Bob rewrote this.' })
    })

    const updated = result.current.document?.sections.find((s) => s.id === 'executive_summary')
    expect(updated?.content).toBe('Bob rewrote this.')
    // ai_draft must NOT change — that's the immutability contract.
    expect(updated?.ai_draft).toBe(fixture.sections[0]!.ai_draft)
  })

  it('revertSection copies ai_draft back into content', () => {
    const fixture = buildDocFixture()
    // Diverge content from ai_draft first.
    fixture.sections[0]!.content = 'Bob diverged this.'
    useDocumentsStore.setState({
      documentId: 'doc-1', document: fixture, selectedSectionId: 'executive_summary',
    })

    const { result } = renderHook(() => useDocumentsStore())
    act(() => {
      result.current.revertSection('executive_summary')
    })

    const reverted = result.current.document?.sections.find((s) => s.id === 'executive_summary')
    expect(reverted?.content).toBe(reverted?.ai_draft)
  })

  it('regenerateSection updates ai_draft but leaves content untouched', async () => {
    const fixture = buildDocFixture()
    fixture.sections[1]!.content = 'Bob already edited methodology.'
    useDocumentsStore.setState({
      documentId: 'doc-1', document: fixture, selectedSectionId: 'methodology',
    })
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: { ai_draft: 'Fresh AI draft of methodology.', section_id: 'methodology' },
    })

    const { result } = renderHook(() => useDocumentsStore())
    await act(async () => {
      await result.current.regenerateSection('methodology')
    })

    const section = result.current.document?.sections.find((s) => s.id === 'methodology')
    // ai_draft swapped — content preserved (Bob's edits intact).
    expect(section?.ai_draft).toBe('Fresh AI draft of methodology.')
    expect(section?.content).toBe('Bob already edited methodology.')
  })

  it('loadVersions populates versions array', async () => {
    useDocumentsStore.setState({ documentId: 'doc-1' })
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: { versions: [{ id: 'v1', version_number: 1, version_name: 'First',
                           change_summary: null, created_at: null,
                           created_by: 'bob', is_auto_save: false, restored_from: null }] },
    })

    const { result } = renderHook(() => useDocumentsStore())
    await act(async () => { await result.current.loadVersions() })
    expect(result.current.versions.length).toBe(1)
    expect(result.current.versions[0]?.version_name).toBe('First')
  })

  it('clear resets all state to initial', () => {
    useDocumentsStore.setState({
      documentId: 'doc-1', document: buildDocFixture(),
      versions: [], loading: false, saving: false, lastSavedAt: null,
      error: null, selectedSectionId: 'methodology',
    })
    const { result } = renderHook(() => useDocumentsStore())
    act(() => result.current.clear())
    expect(result.current.documentId).toBeNull()
    expect(result.current.document).toBeNull()
    expect(result.current.selectedSectionId).toBeNull()
  })
})


// ── UI rendering ────────────────────────────────────────────────────────────

describe('SectionEditor — rendering', () => {
  function mockDocLoad(doc: SectionDocument) {
    const draftResponse: DocumentDraftResponse = {
      document_id:      'doc-123',
      content:          doc,
      last_saved_at:    null,
      based_on_version: null,
    }
    mockedAxios.get = vi.fn((url: string) => {
      if (url.endsWith('/versions')) return Promise.resolve({ data: { versions: [] } })
      return Promise.resolve({ data: draftResponse })
    }) as never
  }

  it('renders the AI DRAFT banner', async () => {
    mockDocLoad(buildDocFixture())
    renderEditorAt('doc-123')
    expect(await screen.findByTestId('section-editor-ai-draft-banner')).toBeInTheDocument()
    expect(screen.getByText(/AI DRAFT — REQUIRES HUMAN REVIEW/)).toBeInTheDocument()
  })

  it('renders one tab per section', async () => {
    mockDocLoad(buildDocFixture())
    renderEditorAt('doc-123')
    expect(await screen.findByTestId('section-tab-executive_summary')).toBeInTheDocument()
    expect(screen.getByTestId('section-tab-methodology')).toBeInTheDocument()
  })

  it('shows the first section in the editor surface by default', async () => {
    mockDocLoad(buildDocFixture())
    renderEditorAt('doc-123')
    expect(await screen.findByTestId('section-editor-executive_summary')).toBeInTheDocument()
  })

  it('switches sections when a tab is clicked', async () => {
    mockDocLoad(buildDocFixture())
    const user = userEvent.setup()
    renderEditorAt('doc-123')
    await screen.findByTestId('section-tab-methodology')
    await user.click(screen.getByTestId('section-tab-methodology'))
    expect(await screen.findByTestId('section-editor-methodology')).toBeInTheDocument()
  })

  it('shows live word count when content is edited', async () => {
    mockDocLoad(buildDocFixture())
    const user = userEvent.setup()
    renderEditorAt('doc-123')
    const textarea = await screen.findByTestId('section-textarea-executive_summary') as HTMLTextAreaElement
    await user.clear(textarea)
    await user.type(textarea, 'One two three four five.')
    expect(textarea.value).toBe('One two three four five.')
    // Sidebar word-count for the section reflects the new text — 5 words.
    expect(screen.getAllByText(/5 words/).length).toBeGreaterThan(0)
  })

  it('opens the View AI Draft panel on click', async () => {
    mockDocLoad(buildDocFixture())
    const user = userEvent.setup()
    renderEditorAt('doc-123')
    await user.click(await screen.findByTestId('view-ai-draft-button'))
    expect(screen.getByTestId('ai-draft-panel')).toBeInTheDocument()
    // Panel shows the immutable ai_draft text.
    expect(screen.getAllByText(/AI-drafted executive summary/i).length).toBeGreaterThan(0)
  })

  it('Revert button opens a confirmation dialog', async () => {
    mockDocLoad(buildDocFixture())
    const user = userEvent.setup()
    renderEditorAt('doc-123')
    await user.click(await screen.findByTestId('revert-button'))
    expect(screen.getByText(/Revert section to AI draft\?/i)).toBeInTheDocument()
    expect(screen.getByTestId('confirm-revert-button')).toBeInTheDocument()
  })

  it('Save Version button opens the save dialog', async () => {
    mockDocLoad(buildDocFixture())
    const user = userEvent.setup()
    renderEditorAt('doc-123')
    await user.click(await screen.findByTestId('save-version-button'))
    expect(screen.getByTestId('save-version-name-input')).toBeInTheDocument()
  })
})
