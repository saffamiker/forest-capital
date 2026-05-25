/**
 * modal-controls.test.tsx — ModalCloseButton + KeyboardHint contracts.
 *
 * Pins the mobile-UX fix (May 24 2026): every modal's close button is
 * a 44×44 touch target on mobile and shrinks to 32×32 on desktop, and
 * the "Press Esc to close" hint renders ONLY at md: breakpoint or
 * above so a mobile user never sees the misleading keyboard prompt.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ModalCloseButton, KeyboardHint } from '../components/ModalControls'


describe('ModalCloseButton', () => {
  it('renders an X button with the default aria-label', () => {
    render(<ModalCloseButton onClose={() => {}} />)
    expect(screen.getByRole('button', { name: /close/i }))
      .toBeInTheDocument()
  })

  it('honours a custom aria-label', () => {
    render(<ModalCloseButton
      onClose={() => {}}
      ariaLabel="Close persona modal" />)
    expect(screen.getByRole('button', { name: /close persona modal/i }))
      .toBeInTheDocument()
  })

  it('invokes onClose on click', () => {
    const onClose = vi.fn()
    render(<ModalCloseButton onClose={onClose} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('carries the 44×44 mobile touch-target classes', () => {
    // WCAG / Apple HIG / Material minimum. The Tailwind utilities
    // resolve to min-h-[44px] min-w-[44px] on mobile.
    render(<ModalCloseButton onClose={() => {}} />)
    const btn = screen.getByRole('button')
    expect(btn.className).toContain('min-h-[44px]')
    expect(btn.className).toContain('min-w-[44px]')
    // And the desktop shrink classes — the chrome stays tight at
    // sm: and above.
    expect(btn.className).toContain('sm:min-h-[32px]')
    expect(btn.className).toContain('sm:min-w-[32px]')
  })

  it('honours a custom testId', () => {
    render(<ModalCloseButton onClose={() => {}} testId="my-close-btn" />)
    expect(screen.getByTestId('my-close-btn')).toBeInTheDocument()
  })
})


describe('KeyboardHint', () => {
  it('renders the hint text inside a span', () => {
    render(<KeyboardHint hint="Press Esc to close" />)
    expect(screen.getByText('Press Esc to close')).toBeInTheDocument()
  })

  it('carries `hidden md:inline` so mobile users never see the prompt',
    () => {
      // Tailwind: `hidden` removes the element from layout on mobile;
      // `md:inline` restores it at md: breakpoint (≥ 768px). This is
      // the documented responsive-visibility pattern used across the
      // codebase (see MobileNavDrawer for the nav-bar equivalent).
      render(<KeyboardHint hint="Press Esc to close" />)
      const hint = screen.getByText('Press Esc to close')
      expect(hint.className).toContain('hidden')
      expect(hint.className).toContain('md:inline')
    })

  it('honours a custom testId', () => {
    render(<KeyboardHint hint="x" testId="my-hint" />)
    expect(screen.getByTestId('my-hint')).toBeInTheDocument()
  })
})
