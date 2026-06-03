/**
 * audit-warnings-banner.test.tsx — the post-generation audit banner.
 *
 * Renders when a draft carries non-zero audit flag counts. Each
 * check group displays only when its flag list is non-empty.
 * Dismissal is session-scoped via sessionStorage.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import AuditWarningsBanner from '../components/editor/AuditWarningsBanner'
import type { AuditWarnings } from '../types/editor'


function makeAudit(overrides: Partial<AuditWarnings> = {}): AuditWarnings {
  return {
    flags_by_check: {
      numeric: [],
      direction: [],
      consistency: [],
      citation: [],
    },
    flag_counts: {
      numeric: 0, direction: 0, consistency: 0,
      citation: 0, total: 0,
    },
    ...overrides,
  }
}


beforeEach(() => {
  sessionStorage.clear()
})


describe('AuditWarningsBanner', () => {
  it('renders nothing when there are zero flags', () => {
    render(<AuditWarningsBanner draftId={1} audit={makeAudit()} />)
    expect(screen.queryByTestId('audit-warnings-banner')).toBeNull()
  })

  it('renders a summary line with the per-check counts', () => {
    render(<AuditWarningsBanner draftId={2} audit={makeAudit({
      flags_by_check: {
        numeric: [{ strategy: 'X', metric: 'sharpe_ratio',
                    generated: 0.75, cache: 0.6291 }],
        direction: [{ superlative: 'lowest', metric: 'max_drawdown',
                      sentence: 'It has the lowest drawdown.' }],
        consistency: [],
        citation: [{ author: 'Bailey', year: '2014' }],
      },
      flag_counts: {
        numeric: 1, direction: 1, consistency: 0,
        citation: 1, total: 3,
      },
    })} />)
    expect(screen.getByTestId('audit-warnings-banner'))
      .toBeInTheDocument()
    expect(screen.getByText(/Audit flagged 3 items/))
      .toBeInTheDocument()
  })

  it('expands to show per-check flag details', () => {
    render(<AuditWarningsBanner draftId={3} audit={makeAudit({
      flags_by_check: {
        numeric: [{ strategy: 'Regime Switching',
                    metric: 'sharpe_ratio',
                    generated: 0.75, cache: 0.6291 }],
        direction: [], consistency: [],
        citation: [{ author: 'Bailey', year: '2014' }],
      },
      flag_counts: {
        numeric: 1, direction: 0, consistency: 0,
        citation: 1, total: 2,
      },
    })} />)
    // Initial: collapsed.
    expect(screen.queryByText('Regime Switching · sharpe_ratio'))
      .toBeNull()
    // Click the expander.
    fireEvent.click(screen.getByText(/Audit flagged 2 items/))
    // After expand: per-check detail visible.
    expect(screen.getByText(/Regime Switching · sharpe_ratio/))
      .toBeInTheDocument()
    expect(screen.getByText(/Bailey \(2014\)/))
      .toBeInTheDocument()
  })

  it('only renders check groups that have flags', () => {
    render(<AuditWarningsBanner draftId={4} audit={makeAudit({
      flags_by_check: {
        numeric: [{ strategy: 'X', metric: 'sharpe_ratio',
                    generated: 0.75, cache: 0.6291 }],
        direction: [], consistency: [], citation: [],
      },
      flag_counts: {
        numeric: 1, direction: 0, consistency: 0,
        citation: 0, total: 1,
      },
    })} />)
    fireEvent.click(screen.getByText(/Audit flagged 1 item/))
    // Numeric group renders; the other three don't.
    expect(screen.getByText(/Numeric cross-reference \(1\)/))
      .toBeInTheDocument()
    expect(screen.queryByText(/Label direction/)).toBeNull()
    expect(screen.queryByText(/Cross-section consistency/)).toBeNull()
    expect(screen.queryByText(/Citation completeness/)).toBeNull()
  })

  it('dismisses for the session', () => {
    const { unmount } = render(<AuditWarningsBanner draftId={5}
      audit={makeAudit({
        flag_counts: {
          numeric: 1, direction: 0, consistency: 0,
          citation: 0, total: 1,
        },
        flags_by_check: {
          numeric: [{ strategy: 'X', metric: 'sharpe_ratio',
                      generated: 0.75, cache: 0.6291 }],
          direction: [], consistency: [], citation: [],
        },
      })} />)
    expect(screen.getByTestId('audit-warnings-banner'))
      .toBeInTheDocument()
    fireEvent.click(
      screen.getByLabelText('Dismiss for this session'))
    // Re-mount with same draft id: banner stays hidden.
    unmount()
    render(<AuditWarningsBanner draftId={5} audit={makeAudit({
      flag_counts: {
        numeric: 1, direction: 0, consistency: 0,
        citation: 0, total: 1,
      },
      flags_by_check: {
        numeric: [{ strategy: 'X', metric: 'sharpe_ratio',
                    generated: 0.75, cache: 0.6291 }],
        direction: [], consistency: [], citation: [],
      },
    })} />)
    expect(screen.queryByTestId('audit-warnings-banner')).toBeNull()
  })

  it('renders skipped-checks footer when present', () => {
    render(<AuditWarningsBanner draftId={6} audit={makeAudit({
      flag_counts: {
        numeric: 1, direction: 0, consistency: 0,
        citation: 0, total: 1,
      },
      flags_by_check: {
        numeric: [{ strategy: 'X', metric: 'sharpe_ratio',
                    generated: 0.75, cache: 0.6291 }],
        direction: [], consistency: [], citation: [],
      },
      skipped: { citation: 'no References section found' },
    })} />)
    fireEvent.click(screen.getByText(/Audit flagged 1 item/))
    expect(screen.getByText(/Skipped:/)).toBeInTheDocument()
    expect(screen.getByText(/citation \(no References section found\)/))
      .toBeInTheDocument()
  })
})
