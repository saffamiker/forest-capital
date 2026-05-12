import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrandProvider, useBrand, BRANDS } from '../context/BrandContext'
import type { BrandMode } from '../context/BrandContext'

function BrandDisplay() {
  const { brand, setBrand } = useBrand()
  return (
    <div>
      <span data-testid="brand">{brand}</span>
      <button onClick={() => setBrand(BRANDS.FOREST_CAPITAL)}>Set Forest Capital</button>
      <button onClick={() => setBrand(BRANDS.MCCOLL)}>Set McColl</button>
    </div>
  )
}

describe('BrandContext', () => {
  it('provides MCCOLL as the default brand mode', () => {
    render(
      <BrandProvider>
        <BrandDisplay />
      </BrandProvider>
    )
    expect(screen.getByTestId('brand')).toHaveTextContent(BRANDS.MCCOLL)
  })

  it('switches to FOREST_CAPITAL on toggle', () => {
    render(
      <BrandProvider>
        <BrandDisplay />
      </BrandProvider>
    )
    fireEvent.click(screen.getByText('Set Forest Capital'))
    expect(screen.getByTestId('brand')).toHaveTextContent(BRANDS.FOREST_CAPITAL)
  })

  it('switches back to MCCOLL after toggling twice', () => {
    render(
      <BrandProvider>
        <BrandDisplay />
      </BrandProvider>
    )
    fireEvent.click(screen.getByText('Set Forest Capital'))
    fireEvent.click(screen.getByText('Set McColl'))
    expect(screen.getByTestId('brand')).toHaveTextContent(BRANDS.MCCOLL)
  })

  it('MCCOLL brand value is "mccoll"', () => {
    expect(BRANDS.MCCOLL).toBe('mccoll')
  })

  it('FOREST_CAPITAL brand value is "forest_capital"', () => {
    expect(BRANDS.FOREST_CAPITAL).toBe('forest_capital')
  })

  it('BRANDS has exactly two modes', () => {
    expect(Object.keys(BRANDS)).toHaveLength(2)
  })

  it('useBrand throws when used outside BrandProvider', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<BrandDisplay />)).toThrow('useBrand must be used within BrandProvider')
    consoleSpy.mockRestore()
  })

  it('setBrand accepts both valid brand modes', () => {
    const modes: BrandMode[] = [BRANDS.MCCOLL, BRANDS.FOREST_CAPITAL]
    render(
      <BrandProvider>
        <BrandDisplay />
      </BrandProvider>
    )
    modes.forEach((mode) => {
      if (mode === BRANDS.FOREST_CAPITAL) {
        fireEvent.click(screen.getByText('Set Forest Capital'))
      } else {
        fireEvent.click(screen.getByText('Set McColl'))
      }
      expect(screen.getByTestId('brand')).toHaveTextContent(mode)
    })
  })
})
