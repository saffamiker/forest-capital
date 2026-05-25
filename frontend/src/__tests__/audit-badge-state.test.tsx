/**
 * audit-badge-state.test.tsx — pins the four-state badge derivation
 * for the QA / Statistical Audit panel (May 24 2026).
 *
 * UAT feedback: the badge stayed amber WARN forever even after every
 * warning had been acknowledged. The new derivation reads the
 * findings list to detect a "fully acknowledged" state and renders
 * a green READY badge instead.
 */
import { describe, it, expect } from 'vitest'
import { overallStatus } from '../components/AuditPanel'

interface RowFinding {
  id: number
  layer: number
  check_name: string
  metric: string
  strategy: string | null
  severity: string
  status: string
  platform_value: string | null
  auditor_value: string | null
  discrepancy: string | null
  auditor_reasoning: string | null
  resolved?: boolean
  resolution_note?: string | null
}

function warn(id: number, resolved: boolean): RowFinding {
  return {
    id, layer: 2, check_name: 't', metric: 'm', strategy: null,
    severity: 'warning', status: 'warning',
    platform_value: null, auditor_value: null, discrepancy: null,
    auditor_reasoning: null, resolved,
  }
}

function pass(id: number): RowFinding {
  return {
    id, layer: 1, check_name: 't', metric: 'm', strategy: null,
    severity: 'info', status: 'pass',
    platform_value: null, auditor_value: null, discrepancy: null,
    auditor_reasoning: null,
  }
}

// A bare AuditRun stub — the badge function only reads the aggregate
// counts on the row and the per-finding ack state in the array. The
// other fields don't influence the derivation.
function run(over: Partial<{ failed: number; warnings: number }>) {
  return {
    id: 1, triggered_by: 'manual', triggered_at: null,
    triggered_by_email: null, status: 'complete',
    layer_1_status: 'pass', layer_2_status: 'pass', layer_3_status: 'pass',
    total_checks: 10, passed: 10, failed: 0, warnings: 0,
    completed_at: null,
    ...over,
  }
}

describe('overallStatus — badge derivation', () => {
  it('renders PASS when no failures and no warnings', () => {
    const result = overallStatus(run({}), [])
    expect(result.label).toContain('PASS')
    expect(result.cls).toBe('text-success')
  })

  it('renders WARN amber when a failure is present', () => {
    // A Layer-2 recomputation mismatch — genuine blocker; per spec the
    // badge surfaces as amber WARN, with the detail line below the
    // badge spelling out the failure count for the operator.
    const result = overallStatus(run({ failed: 1, warnings: 0 }), [])
    expect(result.label).toContain('WARN')
    expect(result.cls).toBe('text-warning')
  })

  it('renders WARN amber when any warning is unacknowledged', () => {
    const findings = [warn(1, false), warn(2, true), pass(3)]
    const result = overallStatus(run({ warnings: 2 }), findings)
    expect(result.label).toContain('WARN')
    expect(result.cls).toBe('text-warning')
  })

  it('renders READY green when every warning is acknowledged', () => {
    const findings = [warn(1, true), warn(2, true), pass(3)]
    const result = overallStatus(run({ warnings: 2 }), findings)
    expect(result.label).toContain('READY')
    expect(result.cls).toBe('text-success')
  })

  it('renders WARN amber for a history row even if warnings count is set', () => {
    // The history view passes findings: [] (the per-row findings are
    // only loaded for the latest run). The function must NOT report
    // READY without per-finding ack state — better to under-claim than
    // surface a green badge for a row whose ack state is unknown.
    const result = overallStatus(run({ warnings: 3 }), [])
    expect(result.label).toContain('WARN')
    expect(result.cls).toBe('text-warning')
  })

  it('READY requires at least one warning finding in the list', () => {
    // Edge case: warnings count says 0, findings list is empty — the
    // run is genuinely clean and renders PASS, not READY.
    const result = overallStatus(run({ warnings: 0 }), [])
    expect(result.label).toContain('PASS')
  })
})
