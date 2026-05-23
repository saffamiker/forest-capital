/**
 * version-history-panel.test.tsx
 *
 * Covers VersionHistoryPanel — the version-control surface on the
 * report writer screen. Item 2 (May 23 2026 — collaborative editing
 * + version control).
 *
 * Tests mock axios (the panel switched from raw fetch() to axios
 * on May 23 2026 so the X-API-Key session token is attached — raw
 * fetch was hitting 401 on every page load).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'

import VersionHistoryPanel from
  '../components/reportwriter/VersionHistoryPanel'


vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: (err: unknown) => boolean
}


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


let originalConfirm: typeof window.confirm

beforeEach(() => {
  originalConfirm = window.confirm
  window.confirm = vi.fn(() => true)
  mockedAxios.get = vi.fn()
  mockedAxios.post = vi.fn()
  mockedAxios.isAxiosError = (err) =>
    !!(err && (err as { isAxiosError?: boolean }).isAxiosError)
})

afterEach(() => {
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
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 0,
        versions: [], version_count: 0,
      },
    })

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByText(/No versions yet/i)).toBeTruthy()
    })
  })
})


describe('VersionHistoryPanel — fetch + render', () => {
  it('fetches versions on mount and renders newest first', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 7,
        versions: makeVersions(), version_count: 3,
      },
    })

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByTestId('version-row-3')).toBeTruthy()
      expect(screen.getByTestId('version-row-2')).toBeTruthy()
      expect(screen.getByTestId('version-row-1')).toBeTruthy()
    })
    expect(screen.getByText(/rev 7/i)).toBeTruthy()
  })

  it('GETs the versions endpoint via axios (auth header attached)', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: { generation_id: 42, paper_revision: 0,
              versions: [], version_count: 0 },
    })
    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledWith(
        '/api/v1/reports/generations/42/versions')
    })
  })

  it('shows the saved-by email + label + source decoration', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 3,
        versions: makeVersions(), version_count: 3,
      },
    })

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByText(/post AI iterate/i)).toBeTruthy()
      expect(screen.getByText(/AI iteration/)).toBeTruthy()
      expect(screen.getByText(/Saved manually/i)).toBeTruthy()
    })
  })

  it('surfaces a fetch error', async () => {
    mockedAxios.get.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 500, data: { detail: 'Server error' }},
      message: 'Request failed with status code 500',
    })

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.queryByText(/Server error|status code 500/)).toBeTruthy()
    })
  })
})


describe('VersionHistoryPanel — actions', () => {
  it('preview toggle expands and collapses the paper snapshot', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 3,
        versions: makeVersions(), version_count: 3,
      },
    })

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
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 3,
        versions: makeVersions(), version_count: 3,
      },
    })
    mockedAxios.post.mockResolvedValueOnce({
      data: { snapshot: { id: 4, version_number: 4 }},
    })
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 4,
        versions: [
          { ...makeVersions()[0], id: 4, version_number: 4,
            label: 'before review' },
          ...makeVersions(),
        ],
        version_count: 4,
      },
    })

    render(<VersionHistoryPanel generationId={42} />)
    await waitFor(() => screen.getByTestId('version-save-toggle'))

    fireEvent.click(screen.getByTestId('version-save-toggle'))
    fireEvent.change(screen.getByTestId('version-save-label'),
      { target: { value: 'before review' }})
    fireEvent.click(screen.getByTestId('version-save-submit'))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/reports/generations/42/versions',
        { label: 'before review', source: 'manual' })
    })
  })

  it('restore POSTs to the restore endpoint and fires onRestored', async () => {
    const onRestored = vi.fn()
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 3,
        versions: makeVersions(), version_count: 3,
      },
    })
    mockedAxios.post.mockResolvedValueOnce({
      data: { snapshot: { id: 4, version_number: 4 }},
    })
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 4,
        versions: makeVersions(), version_count: 4,
      },
    })

    render(<VersionHistoryPanel
      generationId={42}
      onRestored={onRestored} />)
    await waitFor(() => screen.getByTestId('version-restore-2'))

    fireEvent.click(screen.getByTestId('version-restore-2'))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/reports/generations/42/versions/2/restore')
    })
    await waitFor(() => {
      expect(onRestored).toHaveBeenCalledTimes(1)
    })
  })

  it('confirm cancellation aborts the restore', async () => {
    const onRestored = vi.fn()
    window.confirm = vi.fn(() => false)
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42, paper_revision: 3,
        versions: makeVersions(), version_count: 3,
      },
    })

    render(<VersionHistoryPanel
      generationId={42}
      onRestored={onRestored} />)
    await waitFor(() => screen.getByTestId('version-restore-2'))

    fireEvent.click(screen.getByTestId('version-restore-2'))

    // Only the initial GET was called — no restore POST.
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).not.toHaveBeenCalled()
    expect(onRestored).not.toHaveBeenCalled()
  })
})
