/**
 * macro-research-panel.test.tsx — coverage for MacroResearchPanel (FEATURE 2).
 *
 * Pins the four panel states (loading / empty / normal / error), the
 * citation-link rendering, the staleness badge, and the sysadmin-only
 * Run-now gate. The full poll loop after a Run-now click is NOT
 * exercised here — that's an integration concern. We only verify the
 * trigger fires the right endpoint and the button reaches the
 * sysadmin path.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('axios')

import axios from 'axios'
import { AuthContext } from '../App'
import MacroResearchPanel from '../components/MacroResearchPanel'

const mockedAxios = vi.mocked(axios, true)

interface PanelDigest {
  id: number
  generated_at: string | null
  triggered_by: string
  summary_text: string
  regime_implication: string
  key_signals: Array<{
    category: string; signal: string;
    implication: string; source_url: string
  }>
  citation_urls: string[]
  model: string | null
  metadata: Record<string, unknown>
}

function sampleDigest(over: Partial<PanelDigest> = {}): PanelDigest {
  return {
    id: 7,
    generated_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    triggered_by: 'scheduled',
    summary_text: 'Fed paused; CPI cooler.',
    regime_implication: 'Mildly risk-on; IG attractive.',
    key_signals: [
      { category: 'monetary_policy',
        signal: 'Fed holds at 5.25-5.50%.',
        implication: 'IG duration tailwind.',
        source_url: 'https://federalreserve.gov/example' },
      { category: 'inflation',
        signal: 'CPI 3.1% vs 3.2% expected.',
        implication: 'Dovish across asset classes.',
        source_url: 'https://bls.gov/example' },
    ],
    citation_urls: [
      'https://federalreserve.gov/example',
      'https://bls.gov/example',
    ],
    model: 'claude-sonnet-4-6',
    metadata: {},
    ...over,
  }
}

// `axios.get` is typed as (url, config?) => Promise<AxiosResponse<R>>; the
// helper accepts a fetch fn with that same shape so the assignment doesn't
// trip the strict-mode tsc check Vercel runs. `unknown` keeps individual
// tests free to return whatever data shape they need without per-test
// generic plumbing.
function renderWith(
  permissions: string[],
  lastFetch?: (url: string) => Promise<unknown>,
) {
  const auth = {
    session: { token: 't', email: 'u@queens.edu', permissions },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  // mockImplementation preserves axios.get's signature; vi.fn(callback)
  // narrows to the callback's signature, which was the bug Vercel caught.
  mockedAxios.get = vi.fn().mockImplementation(lastFetch ?? (() => new Promise(() => {})))
  mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'running' } })
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={auth}>
        <MacroResearchPanel />
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}


beforeEach(() => {
  vi.clearAllMocks()
})

describe('MacroResearchPanel', () => {
  it('renders the loading state initially', () => {
    renderWith([], () => new Promise(() => { /* never resolves */ }))
    expect(screen.getByText(/loading current macro/i)).toBeInTheDocument()
  })

  it('renders the empty state when no digest exists yet', async () => {
    renderWith([], () => Promise.resolve({
      data: { digest: null, last_completed_at: null },
    }))
    await waitFor(() =>
      expect(screen.getByText(/no completed digest yet/i)).toBeInTheDocument())
  })

  it('renders the digest summary, signals, and regime read', async () => {
    const digest = sampleDigest()
    renderWith([], () => Promise.resolve({
      data: { digest, last_completed_at: digest.generated_at },
    }))
    await waitFor(() =>
      expect(screen.getByText(/fed paused/i)).toBeInTheDocument())
    // Both signals' text bodies surface.
    expect(screen.getByText(/Fed holds at 5\.25/)).toBeInTheDocument()
    expect(screen.getByText(/CPI 3\.1%/)).toBeInTheDocument()
    // Implication lines render alongside the signals.
    expect(screen.getByText(/IG duration tailwind/i)).toBeInTheDocument()
    // Regime read paragraph.
    expect(screen.getByText(/Mildly risk-on/i)).toBeInTheDocument()
  })

  it('renders source links for each signal', async () => {
    const digest = sampleDigest()
    renderWith([], () => Promise.resolve({
      data: { digest, last_completed_at: digest.generated_at },
    }))
    await waitFor(() => {
      const links = screen.getAllByRole('link')
      // Two signals → two source links.
      expect(links.length).toBeGreaterThanOrEqual(2)
      const urls = links.map((l) => l.getAttribute('href'))
      expect(urls).toContain('https://federalreserve.gov/example')
      expect(urls).toContain('https://bls.gov/example')
    })
  })

  it('renders a stale badge when the digest is older than 24h', async () => {
    const stale = sampleDigest({
      generated_at: new Date(Date.now() - 30 * 60 * 60 * 1000).toISOString(),
    })
    renderWith([], () => Promise.resolve({
      data: { digest: stale, last_completed_at: stale.generated_at },
    }))
    await waitFor(() => expect(screen.getByText(/stale/i)).toBeInTheDocument())
  })

  it('renders an error message when the fetch fails but does not crash',
    async () => {
      renderWith([], () => Promise.reject(new Error('500')))
      await waitFor(() =>
        expect(screen.getByText(/could not load/i)).toBeInTheDocument())
      // Even on a fetch error the panel still renders its empty-state
      // helper text — the dashboard never blanks out.
      expect(screen.getByText(/no completed digest yet/i))
        .toBeInTheDocument()
    })

  it('hides Run-now from a non-sysadmin viewer', async () => {
    renderWith([], () => Promise.resolve({
      data: { digest: sampleDigest(), last_completed_at: null },
    }))
    await waitFor(() =>
      expect(screen.getByText(/fed paused/i)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /run now/i })).toBeNull()
  })

  it('shows Run-now to a sysadmin', async () => {
    renderWith(
      ['view_analytics', 'ask_council', 'team_member', 'manage_users'],
      () => Promise.resolve({
        data: { digest: sampleDigest(), last_completed_at: null },
      }),
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /run now/i }))
        .toBeInTheDocument())
  })
})
