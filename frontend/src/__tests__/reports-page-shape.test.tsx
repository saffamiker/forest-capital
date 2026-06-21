/**
 * reports-page-shape.test.tsx -- June 21 2026.
 *
 * Pins the Reports page surface after the "Bob's Deliverables" /
 * "Molly's Deliverables" shadow-panel was removed. The page must:
 *
 *   - render DocumentGenerationPanel (the ONE canonical generation
 *     surface, which runs the two-pass story plan architecture)
 *   - NOT render the legacy card grid (Bob's / Molly's headings)
 *   - NOT issue a /api/reports/manifest GET on mount (the page no
 *     longer reads the manifest; the only consumer is programmatic)
 *
 * The previous reports-spinner-safety.test.tsx file was removed
 * alongside the manifest fetch -- there is no spinner to safeguard
 * because there is no in-flight request.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import Reports from '../pages/Reports'
import { AuthContext } from '../App'
import { SessionProvider } from '../context/SessionContext'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

// Mock the heavy child panels -- we're testing wrapper markup +
// the absence of the legacy headings, not what each panel renders.
vi.mock('../components/DocumentGenerationPanel', () => ({
  default: () => <div data-testid="document-generation-panel" />,
}))
vi.mock('../components/TeamActivityPanel', () => ({
  default: () => <div data-testid="team-activity-panel" />,
}))
vi.mock('../components/ReportReadinessIndicator', () => ({
  ReportReadinessBanner: () => <div data-testid="report-readiness-banner" />,
}))
vi.mock('../components/FloatingSectionNav', () => ({
  default: () => null,
}))
vi.mock('../components/AcademicExportModal', () => ({
  default: () => null,
}))
vi.mock('../components/SubmissionGuides', () => ({
  default: () => null,
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

function renderReports() {
  return render(
    <AuthContext.Provider value={TEAM_AUTH}>
      <SessionProvider>
        <MemoryRouter>
          <Reports />
        </MemoryRouter>
      </SessionProvider>
    </AuthContext.Provider>,
  )
}

describe('Reports page after shadow-panel retirement', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(false) as never
    mockedAxios.get = vi.fn().mockResolvedValue({ data: {} })
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
  })

  it('renders DocumentGenerationPanel as the only generation surface', () => {
    renderReports()
    expect(screen.getByTestId('document-generation-panel'))
      .toBeInTheDocument()
  })

  it('does not render the retired Bob\'s Deliverables heading', () => {
    renderReports()
    expect(screen.queryByText("Bob's Deliverables")).not.toBeInTheDocument()
  })

  it('does not render the retired Molly\'s Deliverables heading', () => {
    renderReports()
    expect(screen.queryByText("Molly's Deliverables")).not.toBeInTheDocument()
  })

  it('does not fetch /api/reports/manifest on mount', () => {
    renderReports()
    const manifestCalls = (mockedAxios.get as ReturnType<typeof vi.fn>)
      .mock.calls.filter((call: unknown[]) =>
        call[0] === '/api/reports/manifest')
    expect(manifestCalls).toHaveLength(0)
  })

  it('keeps the data-section-id markers FloatingSectionNav depends on', () => {
    const { container } = renderReports()
    const ids = Array.from(
      container.querySelectorAll('[data-section-id]'),
    ).map((el) => el.getAttribute('data-section-id'))
    expect(ids).toContain('report-readiness')
    expect(ids).toContain('document-generation')
    expect(ids).toContain('team-activity')
  })
})
