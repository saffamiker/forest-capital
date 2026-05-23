/**
 * draft-selector.test.tsx — May 23 2026.
 *
 * Covers the DraftSelector component — the dropdown that sits
 * adjacent to the Template selector on /reports/writer so Bob can
 * switch between his saved generation drafts instead of starting
 * fresh every login.
 *
 * Contract pinned by these tests:
 *
 *   1. The "New draft (start fresh)" option is ALWAYS first so
 *      picking the dropdown is never accidentally destructive.
 *   2. Picking a saved draft fires onSelect(id); picking "New draft"
 *      fires onSelect(null).
 *   3. Selected option reflects the prop selectedDraftId (the
 *      currently-loaded generation).
 *   4. The dropdown re-fetches when the templateId prop changes.
 *   5. The dropdown re-fetches when refreshNonce changes (parent
 *      bumps it after Generate / Restore so the new draft shows up
 *      without a page reload).
 *   6. Fail-open on API error — the dropdown renders the "New
 *      draft" option even when /api/v1/reports/generations 500s.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'

import DraftSelector from
  '../components/reportwriter/DraftSelector'


vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  isAxiosError: (err: unknown) => boolean
}


function makeDrafts() {
  return [
    {
      id: 101, template_id: 'midpoint_check_fna670',
      flag_count: 12, word_count_total: 850,
      generated_at: '2026-05-23T14:23:00Z',
      preview: '## 1. Data and Methodology...',
    },
    {
      id: 102, template_id: 'midpoint_check_fna670',
      flag_count: 0, word_count_total: 920,
      generated_at: '2026-05-23T16:48:00Z',
      preview: '## 1. Data and Methodology v2...',
    },
  ]
}


beforeEach(() => {
  vi.clearAllMocks()
  mockedAxios.get = vi.fn().mockResolvedValue({
    data: { drafts: makeDrafts() }
  })
  mockedAxios.isAxiosError = (err: unknown): err is { response?: { data?: { detail?: unknown } }, message: string } => {
    return typeof err === 'object' && err !== null && 'isAxiosError' in err
  }
})


describe('DraftSelector', () => {

  it('renders New draft as the first option', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    const select = await screen.findByTestId('draft-selector')
    expect(select).toBeInTheDocument()
    const options = select.querySelectorAll('option')
    // First option must be New draft regardless of fetched list
    expect(options[0]?.textContent).toContain('New draft')
    expect(options[0]?.getAttribute('value')).toBe('new')
  })

  it('renders saved drafts after the New draft option', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      const select = screen.getByTestId('draft-selector')
      const options = select.querySelectorAll('option')
      expect(options.length).toBe(3) // New draft + 2 saved
    })
    const select = screen.getByTestId('draft-selector')
    const options = select.querySelectorAll('option')
    // Saved drafts carry the generation id as value
    const draftValues = Array.from(options)
      .map((o) => o.getAttribute('value'))
    expect(draftValues).toContain('101')
    expect(draftValues).toContain('102')
  })

  it('reflects selectedDraftId in the value attribute', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={102}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      const select = screen.getByTestId('draft-selector') as HTMLSelectElement
      expect(select.value).toBe('102')
    })
  })

  it('fires onSelect(null) when New draft is picked', async () => {
    const onSelect = vi.fn()
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={101}
        onSelect={onSelect}
      />)
    const select = await screen.findByTestId('draft-selector')
    fireEvent.change(select, { target: { value: 'new' } })
    expect(onSelect).toHaveBeenCalledWith(null)
  })

  it('fires onSelect(id) when a saved draft is picked', async () => {
    const onSelect = vi.fn()
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={onSelect}
      />)
    // Wait for the saved drafts to land
    await waitFor(() => {
      const select = screen.getByTestId('draft-selector')
      expect(select.querySelectorAll('option').length).toBeGreaterThan(1)
    })
    const select = screen.getByTestId('draft-selector')
    fireEvent.change(select, { target: { value: '101' } })
    expect(onSelect).toHaveBeenCalledWith(101)
  })

  it('re-fetches when templateId changes', async () => {
    const { rerender } = render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    // First mount triggers one fetch
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    })
    // Change template — must trigger another fetch
    rerender(
      <DraftSelector
        templateId="executive_brief_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledTimes(2)
    })
    // Second call uses the new template_id
    const lastCall = mockedAxios.get.mock.calls[1]
    expect(lastCall[1]?.params?.template_id).toBe('executive_brief_fna670')
  })

  it('re-fetches when refreshNonce changes', async () => {
    const { rerender } = render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
        refreshNonce={0}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    })
    // Bumping the nonce — used by the parent after a Generate
    // lands — must trigger a re-fetch.
    rerender(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
        refreshNonce={1}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledTimes(2)
    })
  })

  it('fails open — renders New draft option even on API error', async () => {
    mockedAxios.get = vi.fn().mockRejectedValue(new Error('500'))
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      const select = screen.getByTestId('draft-selector')
      const options = select.querySelectorAll('option')
      // Only the New draft option survives — saved drafts couldn't
      // load, but the selector is still usable.
      expect(options.length).toBe(1)
      expect(options[0].getAttribute('value')).toBe('new')
    })
  })

  it('sends limit=20 by default', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    const firstCall = mockedAxios.get.mock.calls[0]
    expect(firstCall[0]).toBe('/api/v1/reports/generations')
    expect(firstCall[1]?.params?.limit).toBe(20)
  })
})
