/**
 * page-section-markers.test.tsx — May 25 2026.
 *
 * The FloatingSectionNav auto-discovers sections via
 * data-section-id + data-section-label DOM attributes. This file
 * verifies those markers are emitted on the Settings and Reports
 * pages (the two newly-wired surfaces). The nav itself is exercised
 * by mount on QAHub / RegimeAnalysis — here we confirm the markup
 * contract those pages depend on is in place.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { BrowserRouter } from 'react-router-dom'
import axios from 'axios'
import { AuthContext } from '../App'
import { BrandProvider } from '../context/BrandContext'
import { SessionProvider } from '../context/SessionContext'

// Mock the heavy child panels — we're testing wrapper markup, not
// what each panel renders internally. Each panel's own test file
// covers its content.
vi.mock('axios')
vi.mock('../components/AcademicDocumentsPanel', () => ({
  default: () => <div>academic-documents-panel</div>,
}))
vi.mock('../components/UserManagementPanel', () => ({
  default: () => <div>user-management-panel</div>,
}))
vi.mock('../components/admin/WarmAnalyticsCacheButton', () => ({
  default: () => <div>warm-analytics-cache</div>,
}))
vi.mock('../components/TestRunnerSettings', () => ({
  TestResultsSection: () => <div>test-results-section</div>,
  TestAdminSections: () => <div>test-admin-sections</div>,
}))
vi.mock('../components/TeamActivityPanel', () => ({
  default: () => <div>team-activity-panel</div>,
}))
vi.mock('../components/DocumentGenerationPanel', () => ({
  default: () => <div>document-generation-panel</div>,
}))
vi.mock('../components/AcademicExportModal', () => ({
  default: () => <div>export-modal</div>,
}))
vi.mock('../components/AdvisorPanel', () => ({
  default: () => <div>advisor-panel</div>,
}))
vi.mock('../components/ReportReadinessIndicator', () => ({
  ReportReadinessBanner: () => <div>report-readiness-banner</div>,
}))
vi.mock('../components/SubmissionGuides', () => ({
  default: () => <div>submission-guides</div>,
}))


const TEAM_PERMS = [
  'view_analytics', 'ask_council', 'team_member',
  'generate_documents', 'export_package',
]

beforeEach(() => {
  // The pages call axios.get on mount (e.g. /api/reports/manifest,
  // /api/auth/me). Return empty bodies so the effects resolve quickly
  // and don't crash with 'Cannot read properties of undefined'.
  vi.mocked(axios.get).mockResolvedValue({ data: {} })
  vi.mocked(axios.post).mockResolvedValue({ data: {} })
})

function renderWithAuth(ui: ReactNode) {
  const value = {
    session: {
      token: 't', email: 'u@queens.edu',
      permissions: TEAM_PERMS,
    },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>
      <BrandProvider>
        <SessionProvider>
          <BrowserRouter>{ui}</BrowserRouter>
        </SessionProvider>
      </BrandProvider>
    </AuthContext.Provider>,
  )
}


// ── Settings — section markers ───────────────────────────────────────────────

describe('Settings page — section markers', () => {
  it('emits data-section-id + data-section-label on every SettingsSection', async () => {
    const Settings = (await import('../pages/Settings')).default
    const { container } = renderWithAuth(<Settings />)
    const sections = Array.from(
      container.querySelectorAll('[data-section-id]'))
    const ids = sections.map((el) =>
      el.getAttribute('data-section-id'))
    // The five SettingsSection ids rendered for every team-member
    // user. The nav reads these exact attrs.
    expect(ids).toContain('organisation')
    expect(ids).toContain('data-study-period')
    expect(ids).toContain('analytics-configuration')
    expect(ids).toContain('academic-documents')
    expect(ids).toContain('account')
    // Every section also carries a human-readable label.
    for (const el of sections) {
      const label = el.getAttribute('data-section-label')
      expect(label).toBeTruthy()
      expect(label!.length).toBeGreaterThan(2)
    }
  })

  it('mounts a FloatingSectionNav with pageKey="settings"', async () => {
    const Settings = (await import('../pages/Settings')).default
    const { container } = renderWithAuth(<Settings />)
    const nav = container.querySelector(
      '[data-testid="floating-section-nav"][data-page-key="settings"]')
    expect(nav).not.toBeNull()
  })
})


// ── Reports — section markers ────────────────────────────────────────────────

describe('Reports page — section markers', () => {
  it('emits data-section-id on the major content panels', async () => {
    const Reports = (await import('../pages/Reports')).default
    const { container } = renderWithAuth(<Reports />)
    const ids = Array.from(
      container.querySelectorAll('[data-section-id]')
    ).map((el) => el.getAttribute('data-section-id'))
    // The panels above the deliverables grid render unconditionally,
    // so these are asserted without waiting on the manifest fetch.
    expect(ids).toContain('report-readiness')
    expect(ids).toContain('document-generation')
    expect(ids).toContain('team-activity')
  })

  it('mounts a FloatingSectionNav with pageKey="reports"', async () => {
    const Reports = (await import('../pages/Reports')).default
    const { container } = renderWithAuth(<Reports />)
    const nav = container.querySelector(
      '[data-testid="floating-section-nav"][data-page-key="reports"]')
    expect(nav).not.toBeNull()
  })
})
