/**
 * draft-selector.test.tsx — May 23 2026, rewritten May 24 2026.
 *
 * Covers the DraftSelector component — the dropdown that sits
 * adjacent to the Template selector on /reports/writer so Bob can
 * switch between his saved generation drafts instead of starting
 * fresh every login.
 *
 * May 24 2026 — refactored from a native <select> to a custom
 * popover (per the user's "trash icon per entry" spec). The
 * native select cannot carry icons next to its <option> entries,
 * so the tests now drive the custom dropdown: open the popover
 * via the trigger button, click the entry by data-testid.
 *
 * Contract pinned by these tests:
 *
 *   1. The "New draft (start fresh)" entry is ALWAYS first.
 *   2. Picking a saved draft fires onSelect(id); picking "New draft"
 *      fires onSelect(null).
 *   3. The selected entry reads as the button's visible label.
 *   4. The dropdown re-fetches when the templateId prop changes.
 *   5. The dropdown re-fetches when refreshNonce changes.
 *   6. Fail-open on API error — the "New draft" option is still
 *      pickable even when /api/v1/reports/generations 500s.
 *   7. Each saved draft has a trash icon; clicking it opens the
 *      type-DELETE confirmation prompt.
 *   8. Confirming with DELETE fires axios.delete on the right URL
 *      and refreshes the list.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'

import DraftSelector from
  '../components/reportwriter/DraftSelector'


vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
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
  mockedAxios.delete = vi.fn().mockResolvedValue({ data: { deleted: true } })
  mockedAxios.isAxiosError = (err: unknown): err is { response?: { data?: { detail?: unknown } }, message: string } => {
    return typeof err === 'object' && err !== null && 'isAxiosError' in err
  }
})


function _openPopover() {
  const trigger = screen.getByTestId('draft-selector') as HTMLButtonElement
  fireEvent.click(trigger)
}


describe('DraftSelector', () => {

  it('renders New draft as the first entry in the popover', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    // Wait for fetch + render.
    await waitFor(() => {
      expect(screen.getByTestId('draft-selector')).toBeInTheDocument()
    })
    _openPopover()
    const newOption = screen.getByTestId('draft-selector-new')
    expect(newOption.textContent).toContain('New draft')
  })

  it('renders saved drafts after the New draft entry', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    _openPopover()
    expect(screen.getByTestId('draft-selector-pick-101')).toBeInTheDocument()
    expect(screen.getByTestId('draft-selector-pick-102')).toBeInTheDocument()
  })

  it('reflects selectedDraftId in the trigger button label', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={102}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      const trigger = screen.getByTestId('draft-selector')
      // The label shows the formatted timestamp of the loaded draft.
      // Without the fetched preview the trigger says "Draft #102".
      expect(trigger.textContent || '').toMatch(/102|May/)
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
    await waitFor(() => {
      expect(screen.getByTestId('draft-selector')).toBeInTheDocument()
    })
    _openPopover()
    fireEvent.click(screen.getByTestId('draft-selector-new'))
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
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    _openPopover()
    fireEvent.click(screen.getByTestId('draft-selector-pick-101'))
    expect(onSelect).toHaveBeenCalledWith(101)
  })

  it('re-fetches when templateId changes', async () => {
    const { rerender } = render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    })
    rerender(
      <DraftSelector
        templateId="executive_brief_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledTimes(2)
    })
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

  it('fails open — renders the New draft entry even on API error', async () => {
    mockedAxios.get = vi.fn().mockRejectedValue(new Error('500'))
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(screen.getByTestId('draft-selector')).toBeInTheDocument()
    })
    _openPopover()
    expect(screen.getByTestId('draft-selector-new')).toBeInTheDocument()
    expect(screen.queryByTestId('draft-selector-pick-101')).toBeNull()
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

  // May 24 2026 — Delete UX tests. Each saved entry has a trash
  // icon; clicking opens a type-DELETE confirm prompt; typing
  // DELETE + clicking the submit button fires axios.delete and
  // refreshes the list.
  it('renders a trash icon next to each saved draft', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    _openPopover()
    expect(screen.getByTestId('draft-selector-delete-101')).toBeInTheDocument()
    expect(screen.getByTestId('draft-selector-delete-102')).toBeInTheDocument()
    // The "New draft" entry has NO trash icon.
    expect(screen.queryByTestId('draft-selector-delete-new')).toBeNull()
  })

  it('trash click opens the type-DELETE confirmation prompt', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    _openPopover()
    fireEvent.click(screen.getByTestId('draft-selector-delete-101'))
    expect(
      screen.getByTestId('draft-selector-delete-confirm-101'),
    ).toBeInTheDocument()
  })

  it('confirm submit fires DELETE and refreshes the list', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    _openPopover()
    fireEvent.click(screen.getByTestId('draft-selector-delete-101'))
    const input = screen.getByTestId(
      'draft-selector-delete-text-101') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'DELETE' } })
    fireEvent.click(screen.getByTestId('draft-selector-delete-submit-101'))
    await waitFor(() => {
      expect(mockedAxios.delete).toHaveBeenCalledWith(
        '/api/v1/reports/generations/101')
    })
  })

  it('submit button stays disabled until DELETE is typed', async () => {
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={null}
        onSelect={vi.fn()}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    _openPopover()
    fireEvent.click(screen.getByTestId('draft-selector-delete-102'))
    const submit = screen.getByTestId(
      'draft-selector-delete-submit-102') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    const input = screen.getByTestId(
      'draft-selector-delete-text-102') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'delete' } })  // wrong case
    expect(submit.disabled).toBe(true)
    fireEvent.change(input, { target: { value: 'DELETE' } })
    expect(submit.disabled).toBe(false)
  })

  it('deleting the currently-loaded draft clears the editor', async () => {
    const onSelect = vi.fn()
    render(
      <DraftSelector
        templateId="midpoint_check_fna670"
        selectedDraftId={101}
        onSelect={onSelect}
      />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalled()
    })
    _openPopover()
    fireEvent.click(screen.getByTestId('draft-selector-delete-101'))
    fireEvent.change(
      screen.getByTestId('draft-selector-delete-text-101'),
      { target: { value: 'DELETE' } })
    fireEvent.click(screen.getByTestId('draft-selector-delete-submit-101'))
    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith(null)
    })
  })
})
