/**
 * step4-audit-lookup.test.tsx — Step 4 audit-lookup contract
 * (UAT 2026-05-25).
 *
 * Bug: Step 4 always read "No audit on record" even when a
 * completed audit existed. The endpoint /api/v1/audit/runs/latest
 * returns {run, is_current, statistical_current, qa_current, ...}
 * but the runner read flat fields off res.data (statistical_status,
 * passed, failed, ...) that the endpoint never returns, so the
 * !status branch ALWAYS tripped.
 *
 * Fix: the runner now reads res.data.run.* and reshapes the row
 * into the legacy flat field names the detail panel expects. The
 * detail-panel contract (statistical_status / passed / failed /
 * warning / run_at pills) is unchanged.
 *
 * These tests pin the lookup contract directly — they invoke
 * STEP_ACTIONS[4] with mocked axios and assert the reshaped
 * payload, separately from the panel-render tests in
 * pipeline-step-detail.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import axios from 'axios'
import { STEP_ACTIONS } from '../pages/ReportWriter'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

beforeEach(() => {
  vi.clearAllMocks()
  mockedAxios.isAxiosError = vi.fn().mockReturnValue(false) as never
})

describe('Step 4 audit-lookup — reshapes the run row', () => {
  it('returns complete + reshaped payload when run.status is pass', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: {
        run: {
          status: 'pass',
          layer_1_status: 'pass',
          layer_2_status: 'pass',
          layer_3_status: 'pass',
          passed: 39, failed: 0, warnings: 2,
          completed_at: '2026-05-22T10:00:00Z',
        },
        is_current: true,
        statistical_current: true,
        qa_current: true,
      },
    })
    const result = await STEP_ACTIONS[4]!('test-template-id')
    expect(result.status).toBe('complete')
    expect(result.message).toBe('Statistical audit: pass')
    // The detail panel reads payload['statistical_status']; the
    // reshape must put run.status under that key.
    expect(result.payload?.statistical_status).toBe('pass')
    // The plural-to-singular rename (warnings → warning) is intentional;
    // the detail panel renders `${payload['warning']} warnings`.
    expect(result.payload?.warning).toBe(2)
    expect(result.payload?.passed).toBe(39)
    expect(result.payload?.failed).toBe(0)
    expect(result.payload?.run_at).toBe('2026-05-22T10:00:00Z')
    expect(result.payload?._no_audit).toBeUndefined()
  })

  it('returns warning when run.status is warn (not pass)', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: {
        run: {
          status: 'warn',
          layer_1_status: 'pass',
          layer_2_status: 'warn',
          layer_3_status: 'pass',
          passed: 35, failed: 0, warnings: 4,
          completed_at: '2026-05-25T08:00:00Z',
        },
        is_current: true,
      },
    })
    const result = await STEP_ACTIONS[4]!('t')
    expect(result.status).toBe('warning')
    expect(result.message).toBe('Statistical audit: warn')
    expect(result.payload?.statistical_status).toBe('warn')
    // The no-audit flag MUST be absent — a warn-status run is still
    // a run on record; the pipeline should unblock past this gate.
    expect(result.payload?._no_audit).toBeUndefined()
  })

  it('falls back to no-audit warning when run is null', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: { run: null, is_current: false },
    })
    const result = await STEP_ACTIONS[4]!('t')
    expect(result.status).toBe('warning')
    expect(result.message).toContain('No audit on record')
    expect(result.payload?._no_audit).toBe(true)
  })

  it('falls back to no-audit warning when run lacks a status', async () => {
    // A 'running' row exists but hasn't reached a verdict yet — the
    // pipeline should still surface the gate, not falsely advance.
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: { run: { status: null }, is_current: false },
    })
    const result = await STEP_ACTIONS[4]!('t')
    expect(result.status).toBe('warning')
    expect(result.payload?._no_audit).toBe(true)
  })

  it('falls back to no-audit warning on 404', async () => {
    const err = Object.assign(new Error('not found'), {
      response: { status: 404 },
    })
    mockedAxios.get = vi.fn().mockRejectedValue(err)
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(true) as never
    const result = await STEP_ACTIONS[4]!('t')
    expect(result.status).toBe('warning')
    expect(result.message).toContain('No audit on record')
    expect(result.payload?._no_audit).toBe(true)
  })

  it('rethrows on non-404 errors', async () => {
    const err = Object.assign(new Error('server error'), {
      response: { status: 500 },
    })
    mockedAxios.get = vi.fn().mockRejectedValue(err)
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(true) as never
    await expect(STEP_ACTIONS[4]!('t')).rejects.toThrow()
  })

  it('reshapes layer statuses so the detail panel can show them', async () => {
    // The detail panel currently only renders statistical_status, but
    // exposing the per-layer fields on the payload keeps the door
    // open for a future per-layer pill row without re-plumbing the
    // runner. Pin the contract now so a later refactor doesn't drop
    // the fields silently.
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: {
        run: {
          status: 'pass',
          layer_1_status: 'pass',
          layer_2_status: 'pass',
          layer_3_status: 'warn',
          passed: 38, failed: 0, warnings: 1,
          completed_at: '2026-05-25T09:00:00Z',
        },
        is_current: true,
      },
    })
    const result = await STEP_ACTIONS[4]!('t')
    expect(result.payload?.layer1_status).toBe('pass')
    expect(result.payload?.layer2_status).toBe('pass')
    expect(result.payload?.layer3_status).toBe('warn')
  })
})
