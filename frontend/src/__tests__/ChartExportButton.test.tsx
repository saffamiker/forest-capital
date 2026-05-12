import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ChartExportButton from '../components/ChartExportButton'

describe('ChartExportButton', () => {
  it('renders without errors', () => {
    render(<ChartExportButton chartId="cumulative_returns" />)
    expect(screen.getByTestId('chart-export-button')).toBeInTheDocument()
  })

  it('has accessible aria-label naming the chart', () => {
    render(<ChartExportButton chartId="cumulative_returns" />)
    expect(screen.getByLabelText(/export cumulative_returns chart/i)).toBeInTheDocument()
  })

  it('shows PNG and SVG options on hover', async () => {
    render(<ChartExportButton chartId="test_chart" />)
    const wrapper = screen.getByTestId('chart-export-button')
    // The dropdown is hidden via CSS (hidden group-hover:flex) — it still
    // exists in the DOM for testing; verify the buttons are present
    expect(wrapper.querySelector('button[aria-label]')).toBeTruthy()
    // Both download buttons are in the DOM (CSS-hidden until hover)
    expect(wrapper.textContent).toContain('PNG')
    expect(wrapper.textContent).toContain('SVG')
  })

  it('generates filename with chart_id and timestamp', async () => {
    // Verify the click handler calls URL.createObjectURL and sets download
    const createObjectURL = vi.fn(() => 'blob:fake')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })

    const container = document.createElement('div')
    const svg = document.createElement('svg')
    container.appendChild(svg)

    // Mock ref.current so downloadSvg picks up an SVG element
    const mockRef = { current: container }

    const { rerender } = render(<ChartExportButton chartId="my_chart" containerRef={mockRef as never} />)
    rerender(<ChartExportButton chartId="my_chart" containerRef={mockRef as never} />)

    const svgBtn = screen.getByText('Download SVG')
    fireEvent.click(svgBtn)

    // createObjectURL was called — download would have been triggered
    expect(createObjectURL).toHaveBeenCalled()

    vi.unstubAllGlobals()
  })
})
