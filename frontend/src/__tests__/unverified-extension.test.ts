/**
 * unverified-extension.test.ts -- June 28 2026 (PR #479).
 *
 * Pins the UnverifiedExtension TipTap schema definition.
 * Focused unit tests; the NodeView + popover get covered by
 * higher-level integration tests once Bob/Molly exercise the
 * editor flow on Render.
 */
import { describe, it, expect } from 'vitest'

import {
  UnverifiedExtension,
  type UnverifiedAttrs,
} from '../components/editor/unverifiedExtension'


describe('UnverifiedExtension', () => {
  it('registers the node type name as "unverified"', () => {
    expect(UnverifiedExtension.name).toBe('unverified')
  })

  it('is an inline atom (cursor skips, whole-node delete)', () => {
    const cfg = UnverifiedExtension.config
    expect(cfg.group).toBe('inline')
    expect(cfg.inline).toBe(true)
    expect(cfg.atom).toBe(true)
    expect(cfg.selectable).toBe(true)
  })

  it('declares the expected attribute schema', () => {
    const attrs = UnverifiedExtension.config.addAttributes?.()
    expect(attrs).toBeDefined()
    if (!attrs) return
    expect(Object.keys(attrs).sort()).toEqual(
      ['accepted', 'accepted_at', 'accepted_by', 'value'])
  })

  it('parses HTML span[data-unverified]', () => {
    const parsers = UnverifiedExtension.config.parseHTML?.()
    expect(parsers).toEqual([{ tag: 'span[data-unverified]' }])
  })

  it('renderText emits [UNVERIFIED: VALUE] when default', () => {
    const renderText = UnverifiedExtension.config.renderText
    expect(renderText).toBeDefined()
    if (!renderText) return
    const node = {
      attrs: { value: '+0.5' } as UnverifiedAttrs,
    }
    const out = renderText({
      node: node as Parameters<typeof renderText>[0]['node'],
      pos: 0,
    } as Parameters<typeof renderText>[0])
    expect(out).toBe('[UNVERIFIED: +0.5]')
  })

  it('renderText emits raw value when accepted', () => {
    const renderText = UnverifiedExtension.config.renderText
    expect(renderText).toBeDefined()
    if (!renderText) return
    const node = {
      attrs: {
        value: '+0.5', accepted: true,
      } as UnverifiedAttrs,
    }
    const out = renderText({
      node: node as Parameters<typeof renderText>[0]['node'],
      pos: 0,
    } as Parameters<typeof renderText>[0])
    expect(out).toBe('+0.5')
  })
})
