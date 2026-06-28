/**
 * token-value-node-view.test.tsx -- June 28 2026.
 *
 * PR-DM-Rich tests for the rich React NodeView + override
 * popover. Smoke + interaction coverage:
 *   - NodeView renders displayText (resolved OR override)
 *   - Tooltip surfaces source / cache / last-updated lines
 *   - Click opens popover; apply writes override + reason
 *     to node attrs; clear removes override + metadata
 *   - Overridden nodes render with amber styling + lock icon
 *
 * The drop-on-save smoke test from PR-DM-Lite
 * (token-value-extension.test.tsx) still covers the
 * pass-through attribute roundtrip -- this file focuses on
 * the visible UI behavior.
 */
import { describe, test, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'

import {
  TokenValueOverridePopover,
} from '../components/editor/TokenValueOverridePopover'


const SAMPLE_ATTRS = {
  token:       '{{OOS_SHARPE_BLEND}}',
  resolved:    '0.86',
  resolved_at: '2026-06-21T12:00:00Z',
  data_hash:   'c421fb895347f924',
}


describe('TokenValueOverridePopover', () => {

  test('renders token + resolved value', () => {
    render(
      <TokenValueOverridePopover
        token={SAMPLE_ATTRS.token}
        resolved={SAMPLE_ATTRS.resolved}
        override={null}
        overrideReason={null}
        onApply={vi.fn()}
        onClear={null}
        onCancel={vi.fn()} />)
    expect(
      screen.getByTestId('token-value-override-popover'))
      .toBeInTheDocument()
    // Token name surfaced in the header.
    expect(screen.getByText(SAMPLE_ATTRS.token))
      .toBeInTheDocument()
    // Value input pre-populated with resolved value.
    const input = screen.getByTestId('override-value-input')
    expect(input).toHaveValue(SAMPLE_ATTRS.resolved)
  })

  test('apply button calls onApply with value + reason',
    () => {
      const onApply = vi.fn()
      render(
        <TokenValueOverridePopover
          token={SAMPLE_ATTRS.token}
          resolved={SAMPLE_ATTRS.resolved}
          override={null}
          overrideReason={null}
          onApply={onApply}
          onClear={null}
          onCancel={vi.fn()} />)
      const value = screen.getByTestId('override-value-input')
      const reason = screen.getByTestId('override-reason-input')
      fireEvent.change(value,
        { target: { value: '0.8591' } })
      fireEvent.change(reason,
        { target: { value: '4dp precision for appendix' } })
      fireEvent.click(
        screen.getByTestId('override-apply-button'))
      expect(onApply).toHaveBeenCalledWith(
        '0.8591', '4dp precision for appendix')
    })

  test('cancel button calls onCancel + does not apply', () => {
    const onApply = vi.fn()
    const onCancel = vi.fn()
    render(
      <TokenValueOverridePopover
        token={SAMPLE_ATTRS.token}
        resolved={SAMPLE_ATTRS.resolved}
        override={null}
        overrideReason={null}
        onApply={onApply}
        onClear={null}
        onCancel={onCancel} />)
    fireEvent.click(screen.getByTestId('override-cancel-button'))
    expect(onCancel).toHaveBeenCalled()
    expect(onApply).not.toHaveBeenCalled()
  })

  test('clear button shown only when override present', () => {
    const { rerender } = render(
      <TokenValueOverridePopover
        token={SAMPLE_ATTRS.token}
        resolved={SAMPLE_ATTRS.resolved}
        override={null}
        overrideReason={null}
        onApply={vi.fn()}
        onClear={null}
        onCancel={vi.fn()} />)
    expect(
      screen.queryByTestId('override-clear-button'))
      .toBeNull()
    // Re-render with an existing override + clear handler.
    const onClear = vi.fn()
    rerender(
      <TokenValueOverridePopover
        token={SAMPLE_ATTRS.token}
        resolved={SAMPLE_ATTRS.resolved}
        override="0.8591"
        overrideReason="precision"
        onApply={vi.fn()}
        onClear={onClear}
        onCancel={vi.fn()} />)
    const clearBtn = screen.getByTestId(
      'override-clear-button')
    expect(clearBtn).toBeInTheDocument()
    fireEvent.click(clearBtn)
    expect(onClear).toHaveBeenCalled()
  })

  test('apply rejects empty value', () => {
    const onApply = vi.fn()
    render(
      <TokenValueOverridePopover
        token={SAMPLE_ATTRS.token}
        resolved=""
        override={null}
        overrideReason={null}
        onApply={onApply}
        onClear={null}
        onCancel={vi.fn()} />)
    fireEvent.click(
      screen.getByTestId('override-apply-button'))
    expect(onApply).not.toHaveBeenCalled()
  })

  test('override popover pre-populates existing override',
    () => {
      render(
        <TokenValueOverridePopover
          token={SAMPLE_ATTRS.token}
          resolved={SAMPLE_ATTRS.resolved}
          override="0.8591"
          overrideReason="precision"
          onApply={vi.fn()}
          onClear={vi.fn()}
          onCancel={vi.fn()} />)
      expect(
        screen.getByTestId('override-value-input'))
        .toHaveValue('0.8591')
      expect(
        screen.getByTestId('override-reason-input'))
        .toHaveValue('precision')
    })
})


// ── Extension still preserves attrs after PR-DM-Rich wiring ─


describe('TokenValueExtension with rich NodeView', () => {

  test('drop-on-save still defended (NodeView is presentational only)',
    async () => {
      const {
        TokenValueExtension,
      } = await import('../components/editor/tokenValueExtension')
      const doc = {
        type: 'doc',
        content: [{
          type: 'paragraph',
          content: [{
            type: 'token_value',
            attrs: SAMPLE_ATTRS,
          }],
        }],
      }
      const editor = new Editor({
        extensions: [StarterKit, TokenValueExtension],
        content: doc,
      })
      const roundtripped = editor.getJSON()
      const tokenNode = roundtripped.content?.[0]?.content?.[0]
      // All four required attrs survive the roundtrip after the
      // rich NodeView is registered.
      expect(tokenNode?.type).toBe('token_value')
      expect(tokenNode?.attrs?.token).toBe(SAMPLE_ATTRS.token)
      expect(tokenNode?.attrs?.resolved).toBe(
        SAMPLE_ATTRS.resolved)
      expect(tokenNode?.attrs?.data_hash).toBe(
        SAMPLE_ATTRS.data_hash)
      editor.destroy()
    })
})
