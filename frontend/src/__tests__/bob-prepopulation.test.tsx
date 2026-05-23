/**
 * bob-prepopulation.test.tsx — Item 1 contract.
 *
 * Verifies the new BOB pre-populated draft badge:
 *
 *   1. Renders the agent draft inline (textarea pre-filled).
 *   2. Header reads "Review and personalise" (not the legacy
 *      "Your input needed").
 *   3. Word count surfaces.
 *   4. [Mark as reviewed] sends the CURRENT textarea content
 *      to onResolve (Bob's edits, if any).
 *   5. [Accept draft as-is] sends the ORIGINAL description
 *      verbatim (ignoring any edits).
 *   6. [Rephrase] calls onIterate('rephrase', current_draft)
 *      and updates the textarea with the response.
 *   7. [Expand] calls onIterate('expand', current_draft).
 *   8. Rephrase / Expand are disabled when no onIterate is wired.
 *   9. Iteration warns when new unverified numbers are introduced.
 *  10. Empty draft blocks Mark as reviewed.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import BobBlockBadge from '../components/reportwriter/BobBlockBadge'


function _draftBlock(description: string) {
  return {
    marker: `[BOB: ${description}]`,
    kind: 'BOB' as const,
    description,
    position: 0,
  }
}


describe('BobBlockBadge — BOB pre-populated draft', () => {
  it('renders the agent draft inline as editable content', () => {
    const draft =
      'The post-2022 correlation shift from -0.05 to +0.61 ' +
      'fundamentally alters the case for static diversification.'
    render(<BobBlockBadge
      block={_draftBlock(draft)}
      onResolve={vi.fn()}
    />)
    expect(screen.getByTestId('bob-draft-badge')).toBeInTheDocument()
    expect(screen.getByText('Review and personalise')).toBeInTheDocument()
    const ta = screen.getByTestId('bob-draft-textarea') as HTMLTextAreaElement
    expect(ta.value).toBe(draft)
  })

  it('shows a word count for the draft', () => {
    render(<BobBlockBadge
      block={_draftBlock('Five words in this draft.')}
      onResolve={vi.fn()}
    />)
    expect(screen.getByTestId('bob-draft-word-count'))
      .toHaveTextContent('5 words')
  })

  it('Mark as reviewed sends the current textarea content', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    const block = _draftBlock('Original agent draft.')
    render(<BobBlockBadge block={block} onResolve={onResolve} />)
    // Bob edits the draft before marking as reviewed.
    const ta = screen.getByTestId('bob-draft-textarea')
    fireEvent.change(ta, { target: { value: 'Bob\'s personalised version.' } })
    fireEvent.click(screen.getByTestId('bob-mark-reviewed'))
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        '[BOB: Original agent draft.]',
        'Bob\'s personalised version.')
    })
  })

  it('Accept draft as-is sends the ORIGINAL description verbatim', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    const block = _draftBlock('Original agent draft.')
    render(<BobBlockBadge block={block} onResolve={onResolve} />)
    // Bob edits but then clicks Accept draft as-is — original wins.
    const ta = screen.getByTestId('bob-draft-textarea')
    fireEvent.change(ta, { target: { value: 'Some edit Bob is discarding.' } })
    fireEvent.click(screen.getByTestId('bob-accept-as-is'))
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        '[BOB: Original agent draft.]',
        'Original agent draft.')
    })
  })

  it('Mark as reviewed refuses on empty draft', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    render(<BobBlockBadge
      block={_draftBlock('start')}
      onResolve={onResolve}
    />)
    const ta = screen.getByTestId('bob-draft-textarea')
    fireEvent.change(ta, { target: { value: '   ' } })
    fireEvent.click(screen.getByTestId('bob-mark-reviewed'))
    await waitFor(() => {
      expect(screen.getByTestId('bob-draft-error')).toBeInTheDocument()
    })
    expect(onResolve).not.toHaveBeenCalled()
  })

  it('Rephrase calls onIterate with the current draft', async () => {
    const onIterate = vi.fn().mockResolvedValue({
      original: 'old', rewritten: 'rephrased version',
      word_delta: 0,
      new_unverified_numbers: [], new_unverified_citations: [],
    })
    render(<BobBlockBadge
      block={_draftBlock('Agent draft content here.')}
      onResolve={vi.fn()}
      onIterate={onIterate}
    />)
    fireEvent.click(screen.getByTestId('bob-rephrase'))
    await waitFor(() => {
      expect(onIterate).toHaveBeenCalledWith(
        'rephrase', 'Agent draft content here.')
    })
    // Textarea content is replaced.
    await waitFor(() => {
      const ta = screen.getByTestId('bob-draft-textarea') as HTMLTextAreaElement
      expect(ta.value).toBe('rephrased version')
    })
  })

  it('Expand calls onIterate("expand", current_draft)', async () => {
    const onIterate = vi.fn().mockResolvedValue({
      original: 'old', rewritten: 'expanded version with more detail',
      word_delta: 5,
      new_unverified_numbers: [], new_unverified_citations: [],
    })
    render(<BobBlockBadge
      block={_draftBlock('Short draft.')}
      onResolve={vi.fn()}
      onIterate={onIterate}
    />)
    fireEvent.click(screen.getByTestId('bob-expand'))
    await waitFor(() => {
      expect(onIterate).toHaveBeenCalledWith('expand', 'Short draft.')
    })
  })

  it('disables Rephrase / Expand when no onIterate is wired', () => {
    render(<BobBlockBadge
      block={_draftBlock('Some draft.')}
      onResolve={vi.fn()}
    />)
    expect(screen.getByTestId('bob-rephrase')).toBeDisabled()
    expect(screen.getByTestId('bob-expand')).toBeDisabled()
  })

  it('warns when iteration introduces new unverified numbers', async () => {
    const onIterate = vi.fn().mockResolvedValue({
      original: 'old', rewritten: 'new with 99.9% confidence',
      word_delta: 2,
      new_unverified_numbers: [99.9],
      new_unverified_citations: [],
    })
    render(<BobBlockBadge
      block={_draftBlock('Original draft.')}
      onResolve={vi.fn()}
      onIterate={onIterate}
    />)
    fireEvent.click(screen.getByTestId('bob-rephrase'))
    await waitFor(() => {
      expect(screen.getByTestId('bob-draft-error'))
        .toHaveTextContent(/1 unverified number/i)
    })
  })

  it('iteration failure surfaces as an inline error', async () => {
    const onIterate = vi.fn().mockRejectedValue(new Error('network down'))
    render(<BobBlockBadge
      block={_draftBlock('Some draft.')}
      onResolve={vi.fn()}
      onIterate={onIterate}
    />)
    fireEvent.click(screen.getByTestId('bob-rephrase'))
    await waitFor(() => {
      expect(screen.getByTestId('bob-draft-error'))
        .toHaveTextContent('network down')
    })
  })
})


describe('Migration 035 — system prompt updates', () => {
  // This is a frontend test runner but the migration is backend.
  // The corresponding backend test lives in
  // tests/test_bob_prepopulation.py.
  it('placeholder — backend coverage lives in tests/test_bob_prepopulation.py', () => {
    expect(true).toBe(true)
  })
})
