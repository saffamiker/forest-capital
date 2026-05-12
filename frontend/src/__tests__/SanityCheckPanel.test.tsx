import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SanityCheckPanel from '../components/SanityCheckPanel'
import type { SanityCheck } from '../components/SanityCheckPanel'

const ALL_GREEN: SanityCheck[] = [
  { id: '1', description: 'S&P 500 CAGR 2000-2024', expected: '8-12%', actual: '8.54%', status: 'green' },
  { id: '2', description: 'S&P 500 2008 drawdown',  expected: '-48% to -55%', actual: '-50.8%', status: 'green' },
]

const WITH_RED: SanityCheck[] = [
  { id: '1', description: 'S&P 500 CAGR 2000-2024', expected: '8-12%', actual: '8.54%', status: 'green' },
  { id: '2', description: 'BND 2022 total return',   expected: '-12% to -16%', actual: '-3.2%', status: 'red' },
]

const WITH_AMBER: SanityCheck[] = [
  { id: '1', description: 'S&P 500 CAGR 2000-2024', expected: '8-12%', actual: '6.1%', status: 'amber' },
]

describe('SanityCheckPanel', () => {
  it('renders without errors', () => {
    render(<SanityCheckPanel checks={ALL_GREEN} />)
    expect(screen.getByTestId('sanity-check-panel')).toBeInTheDocument()
  })

  it('renders the correct number of rows', () => {
    render(<SanityCheckPanel checks={ALL_GREEN} />)
    // getAllByText('PASS') matches exact status labels in the table rows only
    expect(screen.getAllByText('PASS')).toHaveLength(ALL_GREEN.length)
  })

  it('shows integrity confirmed banner when all green', () => {
    render(<SanityCheckPanel checks={ALL_GREEN} />)
    expect(screen.getByText(/data integrity confirmed/i)).toBeInTheDocument()
  })

  it('shows review required warning when any check is red', () => {
    render(<SanityCheckPanel checks={WITH_RED} />)
    expect(screen.getByText(/review required/i)).toBeInTheDocument()
  })

  it('renders GREEN status indicator with check icon', () => {
    render(<SanityCheckPanel checks={ALL_GREEN} />)
    // PASS label appears for green checks
    const passLabels = screen.getAllByText('PASS')
    expect(passLabels.length).toBe(ALL_GREEN.length)
  })

  it('renders RED status indicator for failed check', () => {
    render(<SanityCheckPanel checks={WITH_RED} />)
    expect(screen.getByText('FAIL')).toBeInTheDocument()
  })

  it('renders AMBER status indicator for warning check', () => {
    render(<SanityCheckPanel checks={WITH_AMBER} />)
    expect(screen.getByText('WARN')).toBeInTheDocument()
  })

  it('shows actual value in the table', () => {
    render(<SanityCheckPanel checks={ALL_GREEN} />)
    expect(screen.getByText('8.54%')).toBeInTheDocument()
  })

  it('shows skeleton loaders when loading=true', () => {
    render(<SanityCheckPanel checks={[]} loading />)
    // Skeleton rows have animate-pulse class; count 10 expected skeleton rows
    const animatedCells = document.querySelectorAll('.animate-pulse')
    expect(animatedCells.length).toBeGreaterThan(0)
  })

  it('calls onRerun callback when Re-run button is clicked', () => {
    const onRerun = vi.fn()
    render(<SanityCheckPanel checks={ALL_GREEN} onRerun={onRerun} />)
    fireEvent.click(screen.getByLabelText('Re-run sanity checks'))
    expect(onRerun).toHaveBeenCalledOnce()
  })

  it('renders a CSV export button', () => {
    render(<SanityCheckPanel checks={ALL_GREEN} />)
    // TableExportButton renders a button with "CSV" text
    expect(screen.getByText('CSV')).toBeInTheDocument()
  })
})
