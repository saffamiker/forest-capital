/**
 * presentation-script-card.test.tsx -- June 21 2026.
 *
 * Pins the fourth card on the DocumentGenerationPanel:
 *   - Renders "Download Script" when deck_story_plan_available is true
 *   - Renders disabled "Generate Deck First" when the flag is false /
 *     null and shows the helper line
 *   - Clicking Download Script POSTs to /api/v1/export/presentation-
 *     script and triggers a blob download
 *   - 404 from the endpoint surfaces a "Generate the Presentation
 *     Deck first" error (defence in depth: the readiness flag should
 *     gate the button, but a stale flag could slip through)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import DocumentGenerationPanel from '../components/DocumentGenerationPanel'
import { AuthContext } from '../App'
import { SessionProvider } from '../context/SessionContext'
import { useReportReadinessStore } from '../stores/reportReadinessStore'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

vi.mock('../components/BriefWorkflowModal', () => ({
  BriefWorkflowModal: () => null,
}))

const TEAM_AUTH = {
  session: {
    token: 't', email: 'ruurdsm@queens.edu',
    permissions: [
      'view_analytics', 'ask_council', 'team_member',
      'generate_documents', 'export_package',
    ],
  },
  isVerifying: false, login: vi.fn(), logout: vi.fn(),
}

function renderPanel() {
  return render(
    <AuthContext.Provider value={TEAM_AUTH}>
      <SessionProvider>
        <MemoryRouter>
          <DocumentGenerationPanel />
        </MemoryRouter>
      </SessionProvider>
    </AuthContext.Provider>,
  )
}

function seedReadiness(deckPlanAvailable: boolean | null) {
  // Direct store-seed: the store's load() debounces by TTL, so a
  // pre-seeded `readiness` object suppresses the network call and
  // the component reads the flag we want it to see.
  useReportReadinessStore.setState({
    readiness: {
      is_ready: true,
      blocking_count: 0,
      statistical: {
        unreviewed_warnings: [], unreviewed_failures: [],
      },
      methodology: {
        unresolved_warnings: [], unresolved_failures: [],
      },
      caches_warm: true,
      cold_caches: [],
      warm_status: 'warm',
      ...(deckPlanAvailable !== null
        ? { deck_story_plan_available: deckPlanAvailable }
        : {}),
      checked_at: '2026-06-21T10:00:00Z',
    },
    loading: false,
    fetchedAt: new Date(),
  })
}

describe('PresentationScriptCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(false) as never
    mockedAxios.get = vi.fn().mockResolvedValue({ data: { jobs: [] } })
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: new Blob(['stub'], { type: 'application/octet-stream' }),
      headers: {
        'content-type': 'application/vnd.openxmlformats-officedocument.'
          + 'wordprocessingml.document',
        'content-disposition':
          'attachment; filename="forest-capital-presentation-script.docx"',
      },
    })
  })

  it('renders the Download Script button when the deck plan is cached',
    async () => {
      seedReadiness(true)
      renderPanel()
      await waitFor(() => {
        const btn = screen.getByTestId('download-presentation-script')
        expect(btn).toHaveTextContent(/Download Script/i)
        expect(btn).not.toBeDisabled()
      })
    })

  it('renders disabled "Generate Deck First" when the deck plan is missing',
    async () => {
      seedReadiness(false)
      renderPanel()
      await waitFor(() => {
        const btn = screen.getByTestId('download-presentation-script')
        expect(btn).toHaveTextContent(/Generate Deck First/i)
        expect(btn).toBeDisabled()
      })
      expect(
        screen.getByText(/Generate the Presentation Deck to produce the script/i),
      ).toBeInTheDocument()
    })

  it('renders disabled state when the readiness flag is unknown (null)',
    async () => {
      // null = endpoint failed or not loaded yet. The card must NOT
      // assume availability -- it falls back to the disabled state so
      // a user can't trigger a 404 from the script endpoint.
      seedReadiness(null)
      renderPanel()
      await waitFor(() => {
        const btn = screen.getByTestId('download-presentation-script')
        expect(btn).toBeDisabled()
      })
    })

  it('POSTs to the script endpoint when Download Script is clicked',
    async () => {
      seedReadiness(true)
      renderPanel()
      const btn = await screen.findByTestId('download-presentation-script')
      fireEvent.click(btn)
      await waitFor(() => {
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/api/v1/export/presentation-script',
          {},
          expect.objectContaining({ responseType: 'blob' }),
        )
      })
    })

  it('surfaces the spec\'d error copy on a 404 response', async () => {
    seedReadiness(true)
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(true) as never
    mockedAxios.post = vi.fn().mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: 'not cached' } },
      message: '404',
    })
    renderPanel()
    const btn = await screen.findByTestId('download-presentation-script')
    fireEvent.click(btn)
    await waitFor(() => {
      expect(
        screen.getByText(/Generate the Presentation Deck first/i),
      ).toBeInTheDocument()
    })
  })
})
