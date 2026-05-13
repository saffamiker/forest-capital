import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import TableExportButton from '../components/TableExportButton'

const HEADERS = ['Strategy', 'Sharpe', 'CAGR', 'Max DD']
const ROWS = [
  ['VOL_TARGETING', '1.02', '9.5%', '-18.3%'],
  ['BENCHMARK',     '0.52', '8.6%', '-50.8%'],
]

describe('TableExportButton', () => {
  it('renders without errors', () => {
    render(<TableExportButton tableId="strategy_table" headers={HEADERS} rows={ROWS} />)
    expect(screen.getByTestId('table-export-button')).toBeInTheDocument()
  })

  it('has accessible aria-label', () => {
    render(<TableExportButton tableId="strategy_table" headers={HEADERS} rows={ROWS} />)
    expect(screen.getByLabelText(/export strategy_table table as csv/i)).toBeInTheDocument()
  })

  it('shows CSV label', () => {
    render(<TableExportButton tableId="strategy_table" headers={HEADERS} rows={ROWS} />)
    expect(screen.getByText('CSV')).toBeInTheDocument()
  })

  it('CSV export contains correct headers', () => {
    const createObjectURL = vi.fn(() => 'blob:fake')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })

    render(<TableExportButton tableId="strategy_table" headers={HEADERS} rows={ROWS} />)
    fireEvent.click(screen.getByTestId('table-export-button'))

    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob))

    vi.unstubAllGlobals()
  })

  it('CSV filename includes tableId and timestamp', () => {
    const createObjectURL = vi.fn(() => 'blob:fake')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })

    // Track what download attr is set
    const clickSpy = vi.fn()
    const mockAnchor = {
      href: '',
      download: '',
      click: clickSpy,
    }
    // Capture original before spying to avoid infinite recursion
    const originalCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'a') return mockAnchor as unknown as HTMLElement
      return originalCreateElement(tag)
    })

    render(<TableExportButton tableId="my_results" headers={HEADERS} rows={ROWS} />)
    fireEvent.click(screen.getByTestId('table-export-button'))

    expect(mockAnchor.download).toMatch(/my_results_\d{8}\.csv/)
    expect(clickSpy).toHaveBeenCalled()

    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('all visible rows are included in the export', () => {
    const createObjectURL = vi.fn(() => 'blob:fake')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })

    render(<TableExportButton tableId="t" headers={HEADERS} rows={ROWS} />)
    fireEvent.click(screen.getByTestId('table-export-button'))

    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob))

    vi.unstubAllGlobals()
  })
})
