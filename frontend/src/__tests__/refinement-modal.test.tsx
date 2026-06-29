/**
 * refinement-modal.test.tsx -- June 27 2026.
 *
 * Pins the multi-round refinement modal behaviour:
 *   - shows the current proposed fix text
 *   - 500-char counter + maxLength
 *   - round counter
 *   - Refine POSTs /apply-fix/refine, swaps the proposal panel,
 *     pushes to history, clears the input
 *   - Apply This Fix returns the current (possibly refined) text
 *   - Cancel / Esc / click-outside discard everything
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'

import RefinementModal from '../components/RefinementModal'


vi.mock('axios')


function _render(propsOverride: Record<string, unknown> = {}) {
  const onCancel = vi.fn()
  const onApply = vi.fn()
  const utils = render(
    <RefinementModal
      open
      originalProposalText={
        'Add a sentence noting drawdowns are gross of fees.'}
      fixProposalId={42}
      documentType="analytical_appendix"
      sectionName="Section B"
      onCancel={onCancel}
      onApply={onApply}
      {...propsOverride} />)
  return { onCancel, onApply, ...utils }
}


describe('RefinementModal', () => {
  beforeEach(() => {
    vi.mocked(axios.post).mockReset()
  })

  it('renders the header + current proposal + round counter + buttons', () => {
    _render()
    expect(
      screen.getByText('Refine Fix Proposal'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('refinement-modal-current-proposal'),
    ).toHaveTextContent(/drawdowns are gross of fees/)
    expect(
      screen.getByTestId('refinement-modal-round'),
    ).toHaveTextContent(/Round 1/)
    expect(
      screen.getByTestId('refinement-modal-cancel'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('refinement-modal-refine'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('refinement-modal-apply'),
    ).toBeInTheDocument()
  })

  it('counter starts at 500 and updates as the user types', async () => {
    const user = userEvent.setup()
    _render()
    expect(
      screen.getByTestId('refinement-modal-counter'),
    ).toHaveTextContent(/500 chars remaining/)
    await user.type(
      screen.getByTestId('refinement-modal-note'), 'hello')
    expect(
      screen.getByTestId('refinement-modal-counter'),
    ).toHaveTextContent(/495 chars remaining/)
  })

  it('textarea maxLength = 500', () => {
    _render()
    const t = screen.getByTestId(
      'refinement-modal-note') as HTMLTextAreaElement
    expect(t.maxLength).toBe(500)
  })

  it('Refine button is disabled until the note has non-whitespace text', async () => {
    const user = userEvent.setup()
    _render()
    const refine = screen.getByTestId('refinement-modal-refine')
    expect(refine).toBeDisabled()
    await user.type(
      screen.getByTestId('refinement-modal-note'), '   ')
    expect(refine).toBeDisabled()
    await user.type(
      screen.getByTestId('refinement-modal-note'), 'X')
    expect(refine).not.toBeDisabled()
  })

  it('Refine POSTs /apply-fix/refine and updates the proposal panel + history', async () => {
    vi.mocked(axios.post).mockResolvedValueOnce({
      data: {
        refined_proposal_text: (
          'Add a sentence noting drawdowns are gross of fees, '
          + 'matching Table B.1.'),
      },
    })
    const user = userEvent.setup()
    _render()
    await user.type(
      screen.getByTestId('refinement-modal-note'),
      'Match Table B.1')
    await user.click(screen.getByTestId('refinement-modal-refine'))

    await waitFor(() => {
      expect(
        screen.getByTestId('refinement-modal-current-proposal'),
      ).toHaveTextContent(/matching Table B\.1/)
    })

    // Round counter advances.
    expect(
      screen.getByTestId('refinement-modal-round'),
    ).toHaveTextContent(/Round 2/)
    // History toggle reveals the round-1 row.
    await user.click(
      screen.getByTestId('refinement-modal-history-toggle'))
    expect(
      screen.getByTestId('refinement-modal-history-row-1'),
    ).toHaveTextContent(/Match Table B\.1/)

    // Note input cleared.
    expect(
      (screen.getByTestId(
        'refinement-modal-note') as HTMLTextAreaElement).value,
    ).toBe('')

    // Correct body shape sent.
    expect(axios.post).toHaveBeenCalledWith(
      '/api/v1/apply-fix/refine',
      expect.objectContaining({
        fix_proposal_id: 42,
        current_proposal_text: (
          'Add a sentence noting drawdowns are gross of fees.'),
        refinement_note: 'Match Table B.1',
        document_type: 'analytical_appendix',
        section_name: 'Section B',
        refinement_round: 1,
      }))
  })

  it('Refine error surfaces in-modal without dismissing', async () => {
    vi.mocked(axios.post).mockRejectedValueOnce({
      isAxiosError: true,
      response: { data: { detail: 'boom' } },
      message: 'boom',
    })
    // Tell axios.isAxiosError to recognise our reject as an axios
    // error (the mock module won't provide this automatically).
    vi.spyOn(axios, 'isAxiosError').mockReturnValue(true)
    const user = userEvent.setup()
    const { onCancel } = _render()
    await user.type(
      screen.getByTestId('refinement-modal-note'),
      'broken refinement')
    await user.click(screen.getByTestId('refinement-modal-refine'))
    await waitFor(() => {
      expect(
        screen.getByTestId('refinement-modal-error'),
      ).toHaveTextContent(/boom/)
    })
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('Apply This Fix returns the ORIGINAL text when no refinement has run', async () => {
    const user = userEvent.setup()
    const { onApply } = _render()
    await user.click(screen.getByTestId('refinement-modal-apply'))
    expect(onApply).toHaveBeenCalledWith(
      'Add a sentence noting drawdowns are gross of fees.')
  })

  it('Apply This Fix returns the REFINED text after a successful refine round', async () => {
    vi.mocked(axios.post).mockResolvedValueOnce({
      data: {
        refined_proposal_text: 'REFINED TEXT',
      },
    })
    const user = userEvent.setup()
    const { onApply } = _render()
    await user.type(
      screen.getByTestId('refinement-modal-note'), 'tweak')
    await user.click(screen.getByTestId('refinement-modal-refine'))
    await waitFor(() => {
      expect(
        screen.getByTestId('refinement-modal-current-proposal'),
      ).toHaveTextContent(/REFINED TEXT/)
    })
    await user.click(screen.getByTestId('refinement-modal-apply'))
    expect(onApply).toHaveBeenCalledWith('REFINED TEXT')
  })

  it('Cancel discards refinements and fires onCancel', async () => {
    const user = userEvent.setup()
    const { onCancel } = _render()
    await user.click(screen.getByTestId('refinement-modal-cancel'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('Esc fires Cancel', () => {
    const { onCancel } = _render()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('click-outside fires Cancel', () => {
    const { onCancel } = _render()
    fireEvent.click(screen.getByTestId('refinement-modal'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('close-X fires Cancel', async () => {
    const user = userEvent.setup()
    const { onCancel } = _render()
    await user.click(
      screen.getByTestId('refinement-modal-close'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('open=false renders nothing', () => {
    const { queryByTestId } = _render({ open: false })
    expect(queryByTestId('refinement-modal')).toBeNull()
  })
})
