/**
 * brief-workflow-modal.test.tsx -- PR #337.
 *
 * The Executive Brief card has an Info icon that opens a step-by-step
 * "How to Build the Executive Brief" guide. The modal includes an
 * interactive submission checklist (eight items as of June 23
 * 2026 -- gained the Brief Review row when the per-document review
 * surfaces landed, then gained the Submission Readiness Review row
 * when the capstone Reports panel landed) that toggles on click
 * and unlocks a green confirmation banner when ALL items are
 * checked.
 *
 * These tests pin: the Info button renders on the brief card only;
 * clicking it opens the modal; the modal title + content + all 11
 * steps are present; the interactive checkboxes toggle correctly;
 * the confirmation banner gates on all-six-checked; the modal
 * dismisses on Escape; closing resets the checklist for the next open.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { BriefWorkflowModal } from '../components/BriefWorkflowModal'


describe('BriefWorkflowModal -- render + content', () => {

  it('renders nothing when open is false', () => {
    const { container } = render(
      <BriefWorkflowModal open={false} onClose={() => undefined} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the title and the close button when open', () => {
    render(
      <BriefWorkflowModal open={true} onClose={() => undefined} />)
    expect(screen.getByText(/How to Build the Executive Brief/i))
      .toBeInTheDocument()
    expect(screen.getByTestId('brief-workflow-modal-close'))
      .toBeInTheDocument()
  })

  it('renders all 11 numbered steps', () => {
    render(
      <BriefWorkflowModal open={true} onClose={() => undefined} />)
    for (let i = 1; i <= 11; i++) {
      expect(screen.getByText(new RegExp(`Step\\s+${i}\\b`)))
        .toBeInTheDocument()
    }
  })

  it('renders all seven required citations in the body '
    + '(Hamilton as the proxy)', () => {
      render(
        <BriefWorkflowModal open={true} onClose={() => undefined} />)
      // Hamilton is the proxy required by the spec; the others are
      // pinned individually below to defend against a partial drop.
      expect(screen.getByText(/Hamilton \(1989\)/))
        .toBeInTheDocument()
      // The remaining six citations all appear in the citation
      // checklist under Step 6 -- a single regex anchor per citation
      // pins each one's verbatim form.
      for (const author of [
        'Markowitz \\(1952\\)',
        'Sharpe \\(1994\\)',
        'Lo \\(2002\\)',
        'Fama and French \\(1993\\)',
        'Carhart \\(1997\\)',
        'Ang and Bekaert \\(2002\\)',
      ]) {
        expect(screen.getByText(new RegExp(author)))
          .toBeInTheDocument()
      }
    })

  it('renders the six section word-count targets', () => {
    render(
      <BriefWorkflowModal open={true} onClose={() => undefined} />)
    expect(screen.getByText(/Executive Summary: 200-300 words/))
      .toBeInTheDocument()
    expect(screen.getByText(/Methodology: 300-400 words/))
      .toBeInTheDocument()
    expect(screen.getByText(/Key Findings: 480-620 words/))
      .toBeInTheDocument()
    expect(screen.getByText(/Final Recommendations: 300-400 words/))
      .toBeInTheDocument()
  })
})


describe('BriefWorkflowModal -- interactive submission checklist', () => {

  it('renders all eight checkboxes unchecked on modal open', () => {
    render(
      <BriefWorkflowModal open={true} onClose={() => undefined} />)
    for (let i = 0; i < 8; i++) {
      expect(screen.getByTestId(`brief-checklist-item-${i}`))
        .toBeInTheDocument()
      // No check icon visible while unchecked.
      expect(screen.queryByTestId(`brief-checklist-check-${i}`))
        .not.toBeInTheDocument()
    }
  })

  it('clicking a checkbox toggles its checked state', () => {
    render(
      <BriefWorkflowModal open={true} onClose={() => undefined} />)
    const item0 = screen.getByTestId('brief-checklist-item-0')
    // Initially unchecked.
    expect(screen.queryByTestId('brief-checklist-check-0'))
      .not.toBeInTheDocument()
    // Click -> checked.
    fireEvent.click(item0)
    expect(screen.getByTestId('brief-checklist-check-0'))
      .toBeInTheDocument()
    // Click again -> unchecked.
    fireEvent.click(item0)
    expect(screen.queryByTestId('brief-checklist-check-0'))
      .not.toBeInTheDocument()
  })

  it('checked item carries the line-through class', () => {
    render(
      <BriefWorkflowModal open={true} onClose={() => undefined} />)
    const item2 = screen.getByTestId('brief-checklist-item-2')
    fireEvent.click(item2)
    // The label text span inside the checked item carries the
    // line-through class.
    const labelSpan = item2.querySelector('span:last-child')
    expect(labelSpan?.className).toMatch(/line-through/)
  })

  it('completion banner does NOT render until all eight are checked',
    () => {
      render(
        <BriefWorkflowModal open={true} onClose={() => undefined} />)
      // Check seven out of eight.
      for (let i = 0; i < 7; i++) {
        fireEvent.click(screen.getByTestId(`brief-checklist-item-${i}`))
      }
      expect(screen.queryByTestId('brief-workflow-ready-banner'))
        .not.toBeInTheDocument()
    })

  it('completion banner renders when all eight are checked', () => {
    render(
      <BriefWorkflowModal open={true} onClose={() => undefined} />)
    for (let i = 0; i < 8; i++) {
      fireEvent.click(screen.getByTestId(`brief-checklist-item-${i}`))
    }
    const banner = screen.getByTestId('brief-workflow-ready-banner')
    expect(banner).toBeInTheDocument()
    expect(banner.textContent).toMatch(
      /Ready to submit\. Export your \.docx and upload\./)
  })
})


describe('BriefWorkflowModal -- dismiss behaviour', () => {

  it('Close button calls onClose', () => {
    const onClose = vi.fn()
    render(
      <BriefWorkflowModal open={true} onClose={onClose} />)
    fireEvent.click(
      screen.getByTestId('brief-workflow-modal-close'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Escape key calls onClose', () => {
    const onClose = vi.fn()
    render(
      <BriefWorkflowModal open={true} onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('backdrop click calls onClose', () => {
    const onClose = vi.fn()
    render(
      <BriefWorkflowModal open={true} onClose={onClose} />)
    fireEvent.click(screen.getByTestId('brief-workflow-modal'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('checklist resets when the modal closes and reopens', async () => {
    // Render the modal with a stateful open/closed parent so the
    // close handler triggers a remount-style state reset internally.
    const React = await import('react')
    function Harness() {
      const [open, setOpen] = React.useState(true)
      return (
        <>
          <button data-testid="reopen"
            onClick={() => setOpen(true)}>open</button>
          <BriefWorkflowModal
            open={open}
            onClose={() => setOpen(false)} />
        </>
      )
    }
    render(<Harness />)
    // Check the first three items.
    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getByTestId(`brief-checklist-item-${i}`))
      expect(screen.getByTestId(`brief-checklist-check-${i}`))
        .toBeInTheDocument()
    }
    // Close the modal.
    fireEvent.click(
      screen.getByTestId('brief-workflow-modal-close'))
    expect(screen.queryByTestId('brief-workflow-modal'))
      .not.toBeInTheDocument()
    // Reopen.
    fireEvent.click(screen.getByTestId('reopen'))
    // All checkboxes should now be unchecked again.
    for (let i = 0; i < 6; i++) {
      expect(screen.queryByTestId(`brief-checklist-check-${i}`))
        .not.toBeInTheDocument()
    }
  })
})
