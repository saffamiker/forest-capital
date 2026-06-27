/**
 * focus-brief-modal.test.tsx -- June 27 2026.
 *
 * Pins the optional pre-review focus-brief modal:
 *   - char counter + 1000-char cap
 *   - Skip emits null (counts toward "review without brief")
 *   - Submit emits the trimmed text
 *   - Esc / click-outside / close-X = Skip
 *   - Submit disabled when textarea is empty / whitespace
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import FocusBriefModal from '../components/FocusBriefModal'


function _render(propsOverride: Record<string, unknown> = {}) {
  const onSkip = vi.fn()
  const onSubmit = vi.fn()
  const utils = render(
    <FocusBriefModal
      open
      onSkip={onSkip}
      onSubmit={onSubmit}
      {...propsOverride} />)
  return { onSkip, onSubmit, ...utils }
}


describe('FocusBriefModal', () => {
  it('renders the header + textarea + Skip / Run Review buttons', () => {
    _render()
    expect(
      screen.getByText(/focus the council review/i),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('focus-brief-textarea'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('focus-brief-modal-skip'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('focus-brief-modal-submit'),
    ).toBeInTheDocument()
  })

  it('Run Review is disabled until the textarea has non-whitespace text', async () => {
    const user = userEvent.setup()
    _render()
    const submit = screen.getByTestId('focus-brief-modal-submit')
    expect(submit).toBeDisabled()
    // Whitespace doesn't enable.
    await user.type(
      screen.getByTestId('focus-brief-textarea'), '   ')
    expect(submit).toBeDisabled()
    // First real char enables.
    await user.type(
      screen.getByTestId('focus-brief-textarea'), 'X')
    expect(submit).not.toBeDisabled()
  })

  it('Skip emits null and does not submit text', async () => {
    const user = userEvent.setup()
    const { onSkip, onSubmit } = _render()
    await user.type(
      screen.getByTestId('focus-brief-textarea'),
      'this should not get sent')
    await user.click(screen.getByTestId('focus-brief-modal-skip'))
    expect(onSkip).toHaveBeenCalledTimes(1)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('Run Review emits the TRIMMED brief text', async () => {
    const user = userEvent.setup()
    const { onSubmit } = _render()
    await user.type(
      screen.getByTestId('focus-brief-textarea'),
      '  Check Section B Table B.1  ')
    await user.click(screen.getByTestId('focus-brief-modal-submit'))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith(
      'Check Section B Table B.1')
  })

  it('char counter shows remaining chars and turns warning < 50 left', () => {
    _render()
    const counter = screen.getByTestId('focus-brief-counter')
    expect(counter).toHaveTextContent(/1000 chars remaining/)
    // Setting via fireEvent.change is the fast path -- typing
    // ~1000 chars via userEvent.type would take many seconds.
    fireEvent.change(
      screen.getByTestId('focus-brief-textarea'),
      { target: { value: 'A'.repeat(955) } })
    expect(
      screen.getByTestId('focus-brief-counter'),
    ).toHaveTextContent(/45 chars remaining/)
    expect(
      screen.getByTestId('focus-brief-counter').className,
    ).toContain('text-warning')
  })

  it('textarea maxLength caps input at 1000 chars natively', () => {
    _render()
    const textarea = screen.getByTestId(
      'focus-brief-textarea') as HTMLTextAreaElement
    expect(textarea.maxLength).toBe(1000)
  })

  it('Esc triggers Skip', () => {
    const { onSkip } = _render()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onSkip).toHaveBeenCalledTimes(1)
  })

  it('click-outside (overlay) triggers Skip', () => {
    const { onSkip } = _render()
    const overlay = screen.getByTestId('focus-brief-modal')
    fireEvent.click(overlay)
    expect(onSkip).toHaveBeenCalledTimes(1)
  })

  it('close-X triggers Skip', async () => {
    const user = userEvent.setup()
    const { onSkip } = _render()
    await user.click(screen.getByTestId('focus-brief-modal-close'))
    expect(onSkip).toHaveBeenCalledTimes(1)
  })

  it('open=false renders nothing', () => {
    const { queryByTestId } = _render({ open: false })
    expect(queryByTestId('focus-brief-modal')).toBeNull()
  })
})
