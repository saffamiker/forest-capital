/**
 * document-generation-panel.test.tsx — the Generate Documents cards
 * and the async generation flow.
 *
 * Generation is asynchronous: a POST returns a job_id, polling lives in
 * the module-level store (lib/generationJobs), and the card derives its
 * state from the tracked job. The GenerationToast announces a job that
 * completed while the user was away.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  render, screen, fireEvent, waitFor, within,
} from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'

vi.mock('axios', () => {
  const mod = {
    post: vi.fn(), get: vi.fn(), delete: vi.fn(),
    isAxiosError: vi.fn(() => false),
  }
  return { default: mod }
})

import axios from 'axios'
import { AuthContext } from '../App'
import DocumentGenerationPanel from '../components/DocumentGenerationPanel'
import GenerationToast from '../components/GenerationToast'
import { trackJob, __resetGenerationJobs } from '../lib/generationJobs'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>
  get: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  isAxiosError: ReturnType<typeof vi.fn>
}

const TEAM_PERMS = [
  'view_analytics', 'ask_council', 'team_member',
  'generate_documents', 'export_package',
]

function renderPanel(ui: ReactNode, route = '/reports') {
  const value = {
    session: {
      token: 't', email: 'thaob@queens.edu', permissions: TEAM_PERMS,
    },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </AuthContext.Provider>)
}

function briefCard(): HTMLElement {
  return screen.getByText('Executive Brief').closest('.card') as HTMLElement
}

beforeEach(() => {
  __resetGenerationJobs()
  mockNavigate.mockClear()
  mockedAxios.post.mockReset()
  mockedAxios.get.mockReset()
  mockedAxios.delete.mockReset()
  mockedAxios.isAxiosError.mockReturnValue(false)
  // loadExistingJobs() — default to no prior jobs.
  mockedAxios.get.mockResolvedValue({ data: { jobs: [] } })
  localStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
  __resetGenerationJobs()
})

describe('DocumentGenerationPanel — async generation', () => {
  it('shows the in-progress state with the navigate-away message', async () => {
    mockedAxios.post.mockResolvedValue({
      data: { job_id: 'j-brief', status: 'pending' } })
    renderPanel(<DocumentGenerationPanel />)
    fireEvent.click(within(briefCard()).getByRole('button',
      { name: /Generate/ }))
    await waitFor(() => expect(
      within(briefCard()).getByText(/Generating your/)).toBeInTheDocument())
    expect(within(briefCard()).getByText(/You can navigate away/))
      .toBeInTheDocument()
    expect(within(briefCard()).getByRole('button', { name: /Cancel/ }))
      .toBeInTheDocument()
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/v1/export/executive-brief')
  })

  it('polls to completion and shows Open in Editor and Download', async () => {
    vi.useFakeTimers()
    renderPanel(<DocumentGenerationPanel />)
    // A running job is already tracked (e.g. started moments ago).
    trackJob({
      job_id: 'j-brief', document_type: 'executive_brief',
      status: 'running', draft_id: null, download_url: null, error: null,
    })
    mockedAxios.get.mockResolvedValue({
      data: {
        job_id: 'j-brief', document_type: 'executive_brief',
        status: 'complete', draft_id: 4242,
        download_url: '/api/v1/jobs/j-brief/download', error: null,
      } })
    await vi.advanceTimersByTimeAsync(3100)   // the 3s poll fires
    expect(within(briefCard()).getByText('Open in Editor'))
      .toBeInTheDocument()
    expect(within(briefCard()).getByText('Download')).toBeInTheDocument()
  })

  it('Open in Editor navigates to /editor/{draft_id}', async () => {
    renderPanel(<DocumentGenerationPanel />)
    trackJob({
      job_id: 'j-brief', document_type: 'executive_brief',
      status: 'complete', draft_id: 99,
      download_url: '/api/v1/jobs/j-brief/download', error: null,
    })
    fireEvent.click(await within(briefCard()).findByText('Open in Editor'))
    expect(mockNavigate).toHaveBeenCalledWith('/editor/99')
  })

  it('resumes an in-progress job found on page load', async () => {
    // PR #338 retired the midpoint card; the brief card is the
    // resume target now.
    mockedAxios.get.mockResolvedValue({
      data: { jobs: [{
        job_id: 'j-brief-resume', document_type: 'executive_brief',
        status: 'running', draft_id: null, download_url: null, error: null,
      }] } })
    renderPanel(<DocumentGenerationPanel />)
    const briefCardEl = await screen.findByText('Executive Brief')
    await waitFor(() => expect(
      within(briefCardEl.closest('.card') as HTMLElement)
        .getByText(/Generating your/)).toBeInTheDocument())
  })

  it('renders the analytical appendix card with its POST endpoint', async () => {
    mockedAxios.post.mockResolvedValue({
      data: { job_id: 'j-appx', status: 'pending' } })
    renderPanel(<DocumentGenerationPanel />)
    const appxCard = (
      screen.getByText('Analytical Appendix').closest('.card') as HTMLElement)
    expect(appxCard).toBeInTheDocument()
    fireEvent.click(within(appxCard).getByRole('button',
      { name: /Generate/ }))
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/v1/export/analytical-appendix')
    await waitFor(() => expect(
      within(appxCard).getByText(/Generating your/)).toBeInTheDocument())
  })

  it('renders the deck card with the post-rebuild eleven-slide description (bridges #98 / #100)', async () => {
    // The card description has been rewritten twice: 16-slide
    // (pre-#86) -> six-slide (#86) -> eleven-slide (#98 / #100).
    // The current copy must describe the eleven-slide narrative and
    // must NOT carry either older wording.
    renderPanel(<DocumentGenerationPanel />)
    const deckCard = (
      screen.getByText('Final Presentation Deck').closest('.card') as HTMLElement)
    expect(deckCard).toBeInTheDocument()
    expect(deckCard.textContent).toMatch(/Eleven-slide/i)
    expect(deckCard.textContent).not.toMatch(/Six-slide/i)
    expect(deckCard.textContent).not.toMatch(/16-slide/i)
  })

  it('shows a Regenerate button on a complete-state card (bridge #90)', async () => {
    // Bridge #90: a completed generation should still let the team
    // trigger a fresh run without manual draft deletion. The button
    // sits alongside Open in Editor + Download on the complete-state
    // card.
    renderPanel(<DocumentGenerationPanel />)
    trackJob({
      job_id: 'j-deck', document_type: 'presentation_deck',
      status: 'complete', draft_id: 33,
      download_url: '/api/v1/jobs/j-deck/download', error: null,
    })
    const deckCard = (
      await screen.findByText('Final Presentation Deck')).closest(
        '.card') as HTMLElement
    const button = await within(deckCard).findByTestId(
      'regenerate-deck')
    expect(button).toBeInTheDocument()
    expect(button.textContent).toMatch(/Regenerate/i)
  })

  it('Regenerate POSTs the generation endpoint after the user confirms (bridge #90)', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
      .mockImplementation(() => true)
    mockedAxios.post.mockResolvedValue({
      data: { job_id: 'j-deck-2', status: 'pending' } })
    renderPanel(<DocumentGenerationPanel />)
    trackJob({
      job_id: 'j-deck', document_type: 'presentation_deck',
      status: 'complete', draft_id: 33,
      download_url: '/api/v1/jobs/j-deck/download', error: null,
    })
    const deckCard = (
      await screen.findByText('Final Presentation Deck')).closest(
        '.card') as HTMLElement
    const button = await within(deckCard).findByTestId(
      'regenerate-deck')
    fireEvent.click(button)
    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => expect(mockedAxios.post)
      .toHaveBeenCalledWith('/api/v1/export/presentation-deck'))
    confirmSpy.mockRestore()
  })

  it('Regenerate is a no-op when the user dismisses the confirm prompt (bridge #90)', async () => {
    // Defensive: clicking Regenerate and then cancelling the confirm
    // must NOT fire the POST. Pinning this guards against the
    // confirm prompt being silently removed in a future refactor.
    const confirmSpy = vi.spyOn(window, 'confirm')
      .mockImplementation(() => false)
    renderPanel(<DocumentGenerationPanel />)
    trackJob({
      job_id: 'j-brief-c', document_type: 'executive_brief',
      status: 'complete', draft_id: 41,
      download_url: '/api/v1/jobs/j-brief-c/download', error: null,
    })
    const card = briefCard()
    const button = await within(card).findByTestId('regenerate-brief')
    fireEvent.click(button)
    expect(confirmSpy).toHaveBeenCalled()
    expect(mockedAxios.post).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('shows the error state with Try Again on a failed job', async () => {
    renderPanel(<DocumentGenerationPanel />)
    trackJob({
      job_id: 'j-brief', document_type: 'executive_brief',
      status: 'failed', draft_id: null, download_url: null,
      error: 'Generation failed (ref: abcd1234)',
    })
    // trackJob → store emit → useSyncExternalStore re-render. Flush via
    // findByText (async) — synchronous getByText would race the schedule.
    expect(await within(briefCard()).findByText(/Generation failed/))
      .toBeInTheDocument()
    expect(within(briefCard()).getByRole('button', { name: 'Try Again' }))
      .toBeInTheDocument()
  })
})

describe('DocumentGenerationPanel — Brief Workflow guide', () => {
  it('renders the Info button on the Executive Brief card and not on '
    + 'the deck card', () => {
      // PR #338 retired the midpoint card; only brief + deck +
      // appendix remain. The info button stays brief-only.
      renderPanel(<DocumentGenerationPanel />)
      const briefInfo = within(briefCard()).getByTestId(
        'brief-workflow-info-button')
      expect(briefInfo).toBeInTheDocument()
      const deckCard = screen.getByText('Final Presentation Deck')
        .closest('.card') as HTMLElement
      expect(within(deckCard).queryByTestId(
        'brief-workflow-info-button')).not.toBeInTheDocument()
    })

  it('renders the persistent helper text below the brief description',
    () => {
      renderPanel(<DocumentGenerationPanel />)
      expect(
        within(briefCard()).getByTestId('brief-workflow-helper-link'),
      ).toBeInTheDocument()
      expect(within(briefCard()).getByText(
        /Review the step-by-step guide/i)).toBeInTheDocument()
    })

  it('clicking the Info button opens the workflow modal', () => {
    renderPanel(<DocumentGenerationPanel />)
    expect(screen.queryByTestId('brief-workflow-modal'))
      .not.toBeInTheDocument()
    fireEvent.click(within(briefCard()).getByTestId(
      'brief-workflow-info-button'))
    expect(screen.getByTestId('brief-workflow-modal'))
      .toBeInTheDocument()
    expect(screen.getByText(/How to Build the Executive Brief/i))
      .toBeInTheDocument()
  })

  it('Generate button still works when the modal is closed', async () => {
    mockedAxios.post.mockResolvedValue({
      data: { job_id: 'j-brief-guide', status: 'pending' } })
    renderPanel(<DocumentGenerationPanel />)
    // Modal is closed; click Generate.
    fireEvent.click(within(briefCard()).getByRole('button',
      { name: /^Generate$/ }))
    await waitFor(() =>
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/export/executive-brief'))
  })
})


describe('GenerationToast — background completion', () => {
  it('announces a completed job away from the Reports page', () => {
    trackJob({
      job_id: 'j-deck', document_type: 'presentation_deck',
      status: 'complete', draft_id: 7,
      download_url: '/api/v1/jobs/j-deck/download', error: null,
    })
    renderPanel(<GenerationToast />, '/dashboard')
    expect(screen.getByText(/presentation deck is ready/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Open in Editor'))
    expect(mockNavigate).toHaveBeenCalledWith('/editor/7')
  })

  it('is suppressed on the Reports page', () => {
    trackJob({
      job_id: 'j-deck', document_type: 'presentation_deck',
      status: 'complete', draft_id: 7,
      download_url: '/api/v1/jobs/j-deck/download', error: null,
    })
    renderPanel(<GenerationToast />, '/reports')
    expect(screen.queryByText(/is ready/)).toBeNull()
  })
})
