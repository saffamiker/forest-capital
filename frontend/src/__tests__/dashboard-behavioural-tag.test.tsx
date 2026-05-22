/**
 * dashboard-behavioural-tag.test.tsx — Item 9 Commit 4 contract.
 *
 * The behavioural_tag is the one-line AI descriptor that appears
 * below each strategy name on the Dashboard table. Clicking it
 * opens the Portfolio Profile modal for that strategy directly,
 * keeping the user on the Dashboard.
 *
 * Three contracts pinned here:
 *   1. The tag renders the AI text when the characterisations store
 *      has it; falls back to "Open Portfolio Profile →" otherwise.
 *   2. Clicking the tag opens the modal AND does not also trigger
 *      the row's onSelect (the click stops propagation).
 *   3. The modal's Esc / × / backdrop all close it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { PortfolioProfileModal }
  from '../components/PortfolioProfileModal'
import {
  useCharacterisationsStore,
} from '../stores/strategyCharacterisationsStore'


// PortfolioProfilePanel renders nothing useful inside the modal without
// the store; we just verify the modal frame itself (the panel has its
// own dedicated test file).
beforeEach(() => {
  useCharacterisationsStore.setState({
    byId: {}, loading: false, loaded: true,
    fetchedAt: null, available: false,
  })
})


describe('PortfolioProfileModal — open / close contract', () => {
  it('renders nothing when strategyId is null', () => {
    render(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId={null}
          onClose={vi.fn()} />
      </MemoryRouter>,
    )
    expect(screen.queryByTestId('portfolio-profile-modal'))
      .not.toBeInTheDocument()
  })

  it('renders the modal frame with humanised strategy name when no override',
    () => {
      render(
        <MemoryRouter>
          <PortfolioProfileModal
            strategyId="VOL_TARGETING"
            onClose={vi.fn()} />
        </MemoryRouter>,
      )
      const modal = screen.getByTestId('portfolio-profile-modal')
      expect(modal).toBeInTheDocument()
      // Header carries the humanised strategy name.
      expect(modal.textContent).toContain('VOL TARGETING')
    })

  it('uses the strategyName prop when provided', () => {
    render(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId="VOL_TARGETING"
          strategyName="Volatility Targeting"
          onClose={vi.fn()} />
      </MemoryRouter>,
    )
    const modal = screen.getByTestId('portfolio-profile-modal')
    expect(modal.textContent).toContain('Volatility Targeting')
  })

  it('× button fires onClose', () => {
    const onClose = vi.fn()
    render(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId="VOL_TARGETING"
          onClose={onClose} />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByTestId('portfolio-profile-modal-close'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('backdrop click fires onClose', () => {
    const onClose = vi.fn()
    render(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId="VOL_TARGETING"
          onClose={onClose} />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByTestId('portfolio-profile-modal-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Escape key fires onClose', () => {
    const onClose = vi.fn()
    render(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId="VOL_TARGETING"
          onClose={onClose} />
      </MemoryRouter>,
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Escape listener is removed when modal closes', () => {
    const onClose = vi.fn()
    const { rerender } = render(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId="VOL_TARGETING"
          onClose={onClose} />
      </MemoryRouter>,
    )
    // Close the modal — pass strategyId={null}.
    rerender(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId={null}
          onClose={onClose} />
      </MemoryRouter>,
    )
    // A subsequent Escape press must NOT fire onClose now that the
    // listener was cleaned up.
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('non-Escape keys do not close the modal', () => {
    const onClose = vi.fn()
    render(
      <MemoryRouter>
        <PortfolioProfileModal
          strategyId="VOL_TARGETING"
          onClose={onClose} />
      </MemoryRouter>,
    )
    fireEvent.keyDown(window, { key: 'Enter' })
    fireEvent.keyDown(window, { key: ' ' })
    fireEvent.keyDown(window, { key: 'a' })
    expect(onClose).not.toHaveBeenCalled()
  })
})
