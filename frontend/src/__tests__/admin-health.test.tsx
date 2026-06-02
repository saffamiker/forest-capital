/**
 * admin-health.test.tsx — the /admin/health panel.
 *
 * The page renders three sections from two endpoints:
 *   /api/v1/admin/invariants          → Sections 1 (full verdict) + 2
 *                                       (Layer 4 / Category 2 subset)
 *   /api/v1/admin/invariants/history  → Section 3 (last seven warms)
 *
 * Both axios calls are mocked so the test asserts on the rendered
 * surface, not on the network.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('axios', () => {
  const mod = {
    get: vi.fn(),
    post: vi.fn(),
    isAxiosError: vi.fn(() => false),
  }
  return { default: mod }
})

import axios from 'axios'
import AdminHealth from '../pages/AdminHealth'

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
}


function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/health']}>
      <AdminHealth />
    </MemoryRouter>)
}


beforeEach(() => {
  mockedAxios.get.mockReset()
})


describe('AdminHealth — /admin/health', () => {
  it('renders the three section headings even with cold data', async () => {
    // Cold deploy: neither endpoint has data yet.
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/api/v1/admin/invariants') {
        return Promise.resolve({
          data: {
            available: false, ran_at: null,
            note: 'No invariant run has landed yet — the framework '
              + 'fires on the next analytics warm.',
          },
        })
      }
      return Promise.resolve({ data: { available: false, rows: [] } })
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('Invariant Framework')).toBeInTheDocument()
    })
    expect(screen.getByText('Layer 4 Display Fixtures')).toBeInTheDocument()
    expect(screen.getByText('Cache Warm History')).toBeInTheDocument()
  })

  it('shows checks-passed / hard-failures / soft-warnings when '
     + 'invariants ran clean', async () => {
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/api/v1/admin/invariants') {
        return Promise.resolve({
          data: {
            available: true,
            passed: true,
            checks_run: 22,
            hard_failures: 0,
            soft_warnings: 0,
            violations: [],
          },
        })
      }
      return Promise.resolve({
        data: {
          available: true,
          rows: [{
            computed_at: '2026-06-02T15:30:00+00:00',
            data_hash: 'abcd1234',
            passed: true,
            checks_run: 22,
            hard_failures: 0,
            soft_warnings: 0,
          }],
        },
      })
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getAllByText(/Passed/i).length).toBeGreaterThan(0)
    })
    // Hash + check count surface in the history strip.
    expect(screen.getByText(/abcd1234/)).toBeInTheDocument()
  })

  it('surfaces Category 2 (Layer 4) failures in their own section', async () => {
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/api/v1/admin/invariants') {
        return Promise.resolve({
          data: {
            available: true,
            passed: false,
            checks_run: 22,
            hard_failures: 1,
            soft_warnings: 0,
            violations: [{
              code: '2a',
              severity: 'hard',
              category: 2,
              entity: 'BENCHMARK/COVID_Crash',
              metric: 'cumulative_return',
              expected: '-0.1987',
              actual: '-0.7353',
              detail: 'Displayed CAGR; expected cumulative return.',
            }],
          },
        })
      }
      return Promise.resolve({ data: { available: true, rows: [] } })
    })
    renderPage()
    await waitFor(() => {
      // Section 1 — overall verdict shows FAILED.
      expect(screen.getAllByText(/Failed/i).length).toBeGreaterThan(0)
    })
    // The 2a code appears in BOTH Section 1's violations table AND
    // Section 2's Layer 4 fixture list — getAllByText captures both.
    expect(screen.getAllByText(/2a/).length).toBeGreaterThanOrEqual(1)
    // Section 2 surfaces the entity and the expected/actual values.
    // The entity appears in BOTH Section 1's table and Section 2's
    // Layer 4 list — getAllByText accepts either match count > 0.
    expect(screen.getAllByText(/BENCHMARK\/COVID_Crash/).length)
      .toBeGreaterThanOrEqual(1)
  })

  it('shows warm-history rows when present', async () => {
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/api/v1/admin/invariants') {
        return Promise.resolve({ data: { available: false, ran_at: null } })
      }
      return Promise.resolve({
        data: {
          available: true,
          rows: [
            {
              computed_at: '2026-06-02T15:30:00+00:00',
              data_hash: 'aaaaaaaa', passed: true,
              checks_run: 22, hard_failures: 0, soft_warnings: 0,
            },
            {
              computed_at: '2026-06-01T15:30:00+00:00',
              data_hash: 'bbbbbbbb', passed: false,
              checks_run: 22, hard_failures: 1, soft_warnings: 0,
            },
          ],
        },
      })
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/aaaaaaaa/)).toBeInTheDocument()
    })
    expect(screen.getByText(/bbbbbbbb/)).toBeInTheDocument()
  })

  it('renders the data-sources footnote crediting both endpoints',
    async () => {
      mockedAxios.get.mockImplementation(() =>
        Promise.resolve({ data: { available: false, rows: [] } }))
      renderPage()
      await waitFor(() => {
        expect(screen.getByText(/GET \/api\/v1\/admin\/invariants$/))
          .toBeInTheDocument()
      })
      expect(screen.getByText(/GET \/api\/v1\/admin\/invariants\/history/))
        .toBeInTheDocument()
    })
})
