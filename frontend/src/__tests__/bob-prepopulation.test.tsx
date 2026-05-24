/**
 * bob-prepopulation.test.tsx — Item 1 contract.
 *
 * UPDATED May 24 2026 (RW5 full spec) — the BOB block now has
 * three top-level actions (Accept / Edit / Reject). The textarea
 * and AI iterate buttons (Rephrase, Expand) live INSIDE the Edit
 * mode revealed by the Edit toggle. Tests click `bob-edit-toggle`
 * before reaching for the textarea / iterate controls.
 *
 * Pinned:
 *   - Three top-level actions render: Accept / Edit / Reject
 *   - Accept sends the ORIGINAL description verbatim
 *   - Edit reveals textarea + Confirm edits + Rephrase + Expand
 *   - Confirm edits sends the CURRENT textarea content
 *   - Reject calls onReject with the marker (no replacement)
 *   - Empty draft blocks Confirm edits
 *   - Rephrase / Expand call onIterate with the current draft
 *   - Rephrase / Expand disabled when no onIterate is wired
 *   - Iteration warns when new unverified numbers are introduced
 *   - Iteration failure surfaces as an inline error
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


/** RW5 full spec — clicks the Edit toggle so the textarea and the
 *  iterate buttons (Rephrase / Expand / Confirm edits) come into
 *  the DOM. */
function _enterEditMode() {
  fireEvent.click(screen.getByTestId('bob-edit-toggle'))
}


describe('BobBlockBadge — BOB pre-populated draft', () => {
  it('renders the three top-level actions (Accept / Edit / Reject)', () => {
    render(<BobBlockBadge
      block={_draftBlock('Some draft.')}
      onResolve={vi.fn()}
      onReject={vi.fn()}
    />)
    expect(screen.getByTestId('bob-draft-badge')).toBeInTheDocument()
    expect(screen.getByText('Review and personalise')).toBeInTheDocument()
    expect(screen.getByTestId('bob-accept')).toBeInTheDocument()
    expect(screen.getByTestId('bob-edit-toggle')).toBeInTheDocument()
    expect(screen.getByTestId('bob-reject')).toBeInTheDocument()
  })

  it('renders a read-only preview of the agent draft when not editing', () => {
    const draft = 'The post-2022 correlation shift fundamentally alters diversification.'
    render(<BobBlockBadge
      block={_draftBlock(draft)}
      onResolve={vi.fn()}
      onReject={vi.fn()}
    />)
    expect(screen.getByTestId('bob-draft-preview'))
      .toHaveTextContent(draft)
    // Textarea is NOT in the DOM until Edit is clicked.
    expect(screen.queryByTestId('bob-draft-textarea')).toBeNull()
  })

  it('Edit mode reveals the textarea pre-filled with the agent draft', () => {
    const draft = 'Some draft content.'
    render(<BobBlockBadge
      block={_draftBlock(draft)}
      onResolve={vi.fn()}
    />)
    _enterEditMode()
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

  it('Confirm edits sends the current textarea content', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    const block = _draftBlock('Original agent draft.')
    render(<BobBlockBadge block={block} onResolve={onResolve} />)
    _enterEditMode()
    const ta = screen.getByTestId('bob-draft-textarea')
    fireEvent.change(ta, { target: { value: "Bob's personalised version." } })
    fireEvent.click(screen.getByTestId('bob-mark-reviewed'))
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        '[BOB: Original agent draft.]',
        "Bob's personalised version.")
    })
  })

  it('Accept sends the ORIGINAL description verbatim', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    const block = _draftBlock('Original agent draft.')
    render(<BobBlockBadge block={block} onResolve={onResolve} />)
    fireEvent.click(screen.getByTestId('bob-accept'))
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        '[BOB: Original agent draft.]',
        'Original agent draft.')
    })
  })

  it('Reject calls onReject with the marker (no replacement)', async () => {
    const onReject = vi.fn().mockResolvedValue(undefined)
    const block = _draftBlock('Some draft.')
    render(<BobBlockBadge
      block={block}
      onResolve={vi.fn()}
      onReject={onReject}
    />)
    fireEvent.click(screen.getByTestId('bob-reject'))
    await waitFor(() => {
      expect(onReject).toHaveBeenCalledWith('[BOB: Some draft.]')
    })
  })

  it('Reject without onReject falls back to resolve with empty replacement', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    const block = _draftBlock('Some draft.')
    render(<BobBlockBadge block={block} onResolve={onResolve} />)
    fireEvent.click(screen.getByTestId('bob-reject'))
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith('[BOB: Some draft.]', '')
    })
  })

  it('Confirm edits refuses on empty draft', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    render(<BobBlockBadge
      block={_draftBlock('start')}
      onResolve={onResolve}
    />)
    _enterEditMode()
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
    _enterEditMode()
    fireEvent.click(screen.getByTestId('bob-rephrase'))
    await waitFor(() => {
      expect(onIterate).toHaveBeenCalledWith(
        'rephrase', 'Agent draft content here.')
    })
    // Textarea content is replaced with the iterate response.
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
    _enterEditMode()
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
    _enterEditMode()
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
    _enterEditMode()
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
    _enterEditMode()
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
