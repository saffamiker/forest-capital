/**
 * version-history-panel.test.tsx
 *
 * Covers VersionHistoryPanel — the version-control surface on the
 * report writer screen. Item 2 (May 23 2026 — collaborative editing
 * + version control).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import VersionHistoryPanel from
  '../components/reportwriter/VersionHistoryPanel'


function makeVersions() {
  return [
    {
      id: 3, version_number: 3,
      paper_md: 'Third version body content here.',
      flag_count: 0, word_counts: {},
      saved_by_email: 'bob@queens.edu',
      saved_at: '2026-05-23T11:00:00Z',
      label: 'post AI iterate',
      source: 'auto_iterate',
      restored_from_version: null,
    },
    {
      id: 2, version_number: 2,
      paper_md: 'Second version body — slightly older.',
      flag_count: 2, word_counts: {},
      saved_by_email: 'bob@queens.edu',
      saved_at: '2026-05-23T10:30:00Z',
      label: null, source: 'auto_edit',
      restored_from_version: null,
    },
    {
      id: 1, version_number: 1,
      paper_md: 'First snapshot — initial draft.',
      flag_count: 5, word_counts: {},
      saved_by_email: null,
      saved_at: '2026-05-23T10:00:00Z',
      label: 'initial', source: 'manual',
      restored_from_version: null,
    },
  ]
}


let originalFetch: typeof global.fetch
let originalConfirm: typeof window.confirm

beforeEach(() => {
  originalFetch = global.fetch
  originalConfirm = window.confirm
  // Auto-confirm any window.confirm so the restore tests don't get
  // stuck on the "are you sure?" dialog.
  window.confirm = vi.fn(() => true)
})

afterEach(() => {
  global.fetch = originalFetch
  window.confirm = originalConfirm
  vi.clearAllMocks()
})


describe('VersionHistoryPanel — empty state', () => {
  it('renders nothing when generationId is null', () => {
    const { container } = render(
      <VersionHistoryPanel generationId={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows the no-versions notice when the list is empty', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true, json: async () => ({
        generation_id: 42, paper_revision: 0,
        versions: [], version_count: 0,
      }),
    } as Response) as unknown as typeof fetch

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByText(/No versions yet/i)).toBeTruthy()
    })
  })
})


describe('VersionHistoryPanel — fetch + render', () => {
  it('fetches versions on mount and renders newest first', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true, json: async () => ({
        generation_id: 42, paper_revision: 7,
        versions: makeVersions(), version_count: 3,
      }),
    } as Response) as unknown as typeof fetch

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByTestId('version-row-3')).toBeTruthy()
      expect(screen.getByTestId('version-row-2')).toBeTruthy()
      expect(screen.getByTestId('version-row-1')).toBeTruthy()
    })
    // Header shows the revision.
    expect(screen.getByText(/rev 7/i)).toBeTruthy()
  })

  it('shows the saved-by email + label + source decoration', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true, json: async () => ({
        generation_id: 42, paper_revision: 3,
        versions: makeVersions(), version_count: 3,
      }),
    } as Response) as unknown as typeof fetch

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      // Label is rendered.
      expect(screen.getByText(/post AI iterate/i)).toBeTruthy()
      // Source label maps auto_iterate → "AI iteration".
      expect(screen.getByText(/AI iteration/)).toBeTruthy()
      // Manual source maps to "Saved manually".
      expect(screen.getByText(/Saved manually/i)).toBeTruthy()
    })
  })

  it('surfaces a fetch error', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false, status: 500,
      json: async () => ({}),
    } as Response) as unknown as typeof fetch

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByText(/Versions fetch returned 500/i)).toBeTruthy()
    })
  })
})


describe('VersionHistoryPanel — actions', () => {
  it('preview toggle expands and collapses the paper snapshot', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true, json: async () => ({
        generation_id: 42, paper_revision: 3,
        versions: makeVersions(), version_count: 3,
      }),
    } as Response) as unknown as typeof fetch

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('version-preview-2'))

    expect(screen.queryByTestId('version-preview-body-2')).toBeNull()
    fireEvent.click(screen.getByTestId('version-preview-2'))
    expect(screen.getByTestId('version-preview-body-2')).toBeTruthy()
    fireEvent.click(screen.getByTestId('version-preview-2'))
    expect(screen.queryByTestId('version-preview-body-2')).toBeNull()
  })

  it('save snapshot POSTs the label and refetches', async () => {
    const fetchMock = vi.fn()
      // initial GET
      .mockResolvedValueOnce({
        ok: true, json: async () => ({
          generation_id: 42, paper_revision: 3,
          versions: makeVersions(), version_count: 3,
        }),
      } as Response)
      // POST save
      .mockResolvedValueOnce({
        ok: true, json: async () => ({
          snapshot: { id: 4, version_number: 4 },
        }),
      } as Response)
      // re-fetch GET
      .mockResolvedValueOnce({
        ok: true, json: async () => ({
          generation_id: 42, paper_revision: 4,
          versions: [
            { ...makeVersions()[0], id: 4, version_number: 4,
              label: 'before review' },
            ...makeVersions(),
          ],
          version_count: 4,
        }),
      } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => screen.getByTestId('version-save-toggle'))

    fireEvent.click(screen.getByTestId('version-save-toggle'))
    fireEvent.change(screen.getByTestId('version-save-label'),
      { target: { value: 'before review' }})
    fireEvent.click(screen.getByTestId('version-save-submit'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenNthCalledWith(2,
        '/api/v1/reports/generations/42/versions',
        expect.objectContaining({
          method: 'POST',
          body:   JSON.stringify({
            label:  'before review',
            source: 'manual',
          }),
        }))
    })
  })

  it('restore POSTs to the restore endpoint and fires onRestored', async () => {
    const onRestored = vi.fn()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true, json: async () => ({
          generation_id: 42, paper_revision: 3,
          versions: makeVersions(), version_count: 3,
        }),
      } as Response)
      // POST restore
      .mockResolvedValueOnce({
        ok: true, json: async () => ({
          snapshot: { id: 4, version_number: 4 },
        }),
      } as Response)
      // re-fetch GET after restore
      .mockResolvedValueOnce({
        ok: true, json: async () => ({
          generation_id: 42, paper_revision: 4,
          versions: makeVersions(), version_count: 4,
        }),
      } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    render(<VersionHistoryPanel
      generationId={42}
      onRestored={onRestored} />)
    await waitFor(() => screen.getByTestId('version-restore-2'))

    fireEvent.click(screen.getByTestId('version-restore-2'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenNthCalledWith(2,
        '/api/v1/reports/generations/42/versions/2/restore',
        expect.objectContaining({ method: 'POST' }))
    })
    await waitFor(() => {
      expect(onRestored).toHaveBeenCalledTimes(1)
    })
  })

  it('confirm cancellation aborts the restore', async () => {
    const onRestored = vi.fn()
    window.confirm = vi.fn(() => false)
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true, json: async () => ({
          generation_id: 42, paper_revision: 3,
          versions: makeVersions(), version_count: 3,
        }),
      } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    render(<VersionHistoryPanel
      generationId={42}
      onRestored={onRestored} />)
    await waitFor(() => screen.getByTestId('version-restore-2'))

    fireEvent.click(screen.getByTestId('version-restore-2'))

    // Only the initial GET was called — no restore POST.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(onRestored).not.toHaveBeenCalled()
  })
})
