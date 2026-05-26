/**
 * qa-badge-derivation.test.tsx — May 26 2026.
 *
 * Pins the qaStore badge-derivation contract:
 *
 *   FAIL — any check carrying a failure status.
 *   WARN — at least one unacknowledged actionable warning, AFTER
 *          excluding IN02 (and any other badge-excluded attestation
 *          check) and warnings the team has confirmed via the
 *          intentional-overrides endpoint.
 *   PASS — everything else.
 *
 * The badge should reflect ACTIONABLE state only. A fully disclosed
 * warning is not actionable, IN02 is an attestation check whose
 * raw WARN state is never actionable, and a server WARN verdict
 * that decomposes into only acknowledged or excluded warnings
 * should clear to PASS.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('axios', () => ({
  default: { post: vi.fn(), get: vi.fn() },
  isAxiosError: () => false,
}))

import axios from 'axios'
import { useQAStore } from '../stores/qaStore'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>
  get: ReturnType<typeof vi.fn>
}


function makeItem(check_id: string, status: string) {
  return {
    check_id,
    check: `Check ${check_id}`,
    category: 'DATA_INTEGRITY',
    description: '',
    status,
  }
}


beforeEach(() => {
  mockedAxios.post.mockReset()
  mockedAxios.get.mockReset()
  useQAStore.getState().clear()
})


describe('qaStore badge derivation — IN02 + acknowledgements', () => {
  it('clears to PASS when every warning is acknowledged', async () => {
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        verdict: 'WARN',
        checks_passed: 28, checks_warned: 2, checks_failed: 0,
        checks_total: 30,
        items: [
          makeItem('D01', 'pass'),
          makeItem('D03', 'warn'),
          makeItem('S03', 'warn'),
        ],
      },
    })
    // Both warnings have been confirmed via mark-intentional /
    // disclosure_required. The badge should read PASS.
    mockedAxios.get.mockResolvedValueOnce({
      data: { overrides: { D03: {}, S03: {} } },
    })

    await useQAStore.getState().reload(true)
    expect(useQAStore.getState().status).toBe('pass')
  })

  it('IN02 NEVER contributes to a WARN badge (attestation check)', async () => {
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        verdict: 'WARN',
        checks_passed: 29, checks_warned: 1, checks_failed: 0,
        checks_total: 30,
        items: [
          makeItem('D01', 'pass'),
          // IN02 is the ONLY warning. Acknowledged or not, it
          // must not flip the badge to WARN.
          makeItem('IN02', 'warn'),
        ],
      },
    })
    mockedAxios.get.mockResolvedValueOnce({
      data: { overrides: {} },
    })

    await useQAStore.getState().reload(true)
    expect(useQAStore.getState().status).toBe('pass')
  })

  it('keeps WARN when at least one warning is unacknowledged', async () => {
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        verdict: 'WARN',
        checks_passed: 27, checks_warned: 3, checks_failed: 0,
        checks_total: 30,
        items: [
          makeItem('D03', 'warn'),       // acknowledged
          makeItem('S03', 'warn'),       // NOT acknowledged
          makeItem('IN02', 'warn'),      // excluded
        ],
      },
    })
    mockedAxios.get.mockResolvedValueOnce({
      data: { overrides: { D03: {} } },
    })

    await useQAStore.getState().reload(true)
    expect(useQAStore.getState().status).toBe('warn')
  })

  it('FAIL trumps acknowledgements — a failure is always FAIL', async () => {
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        verdict: 'FAIL',
        checks_passed: 27, checks_warned: 2, checks_failed: 1,
        checks_total: 30,
        items: [
          makeItem('D01', 'fail'),
          makeItem('D03', 'warn'),
          makeItem('S03', 'warn'),
        ],
      },
    })
    // Even if every warning is acknowledged, the failure stands.
    mockedAxios.get.mockResolvedValueOnce({
      data: { overrides: { D03: {}, S03: {}, D01: {} } },
    })

    await useQAStore.getState().reload(true)
    expect(useQAStore.getState().status).toBe('fail')
  })

  it('pollOverrides re-derives status when an ack lands later', async () => {
    // 1. Initial audit returns one unacknowledged warning → WARN.
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        verdict: 'WARN',
        checks_passed: 29, checks_warned: 1, checks_failed: 0,
        checks_total: 30,
        items: [makeItem('S03', 'warn')],
      },
    })
    mockedAxios.get.mockResolvedValueOnce({
      data: { overrides: {} },
    })
    await useQAStore.getState().reload(true)
    expect(useQAStore.getState().status).toBe('warn')

    // 2. Team records the disclosure; pollOverrides picks it up.
    mockedAxios.get.mockResolvedValueOnce({
      data: { overrides: { S03: {} } },
    })
    await useQAStore.getState().pollOverrides()
    expect(useQAStore.getState().status).toBe('pass')
  })

  it('falls back to summary counts when items[] is absent', async () => {
    // Pre-items legacy payload shape — derive from the scalar
    // checks_failed / checks_warned summary.
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        verdict: 'WARN',
        checks_passed: 28, checks_warned: 2, checks_failed: 0,
        checks_total: 30,
        items: [],
      },
    })
    mockedAxios.get.mockResolvedValueOnce({
      data: { overrides: {} },
    })

    await useQAStore.getState().reload(true)
    // No items → can't apply per-check filters → trust the summary.
    expect(useQAStore.getState().status).toBe('warn')
  })
})
