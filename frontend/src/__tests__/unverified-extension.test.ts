/**
 * unverified-extension.test.ts -- June 28 2026 (PR #479).
 *
 * Schema-level pins for UnverifiedExtension. The extension's
 * config methods (addAttributes / renderText / etc.) are
 * TipTap-bound `this` contexts that can't be invoked directly
 * from tests under strict TypeScript -- those get covered by
 * the higher-level RichTextEditor integration tests once
 * Bob/Molly exercise the editor flow.
 */
import { describe, it, expect } from 'vitest'

import {
  UnverifiedExtension,
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

  it('declares parseHTML / renderHTML / renderText hooks', () => {
    const cfg = UnverifiedExtension.config
    expect(typeof cfg.parseHTML).toBe('function')
    expect(typeof cfg.renderHTML).toBe('function')
    expect(typeof cfg.renderText).toBe('function')
    expect(typeof cfg.addAttributes).toBe('function')
    expect(typeof cfg.addNodeView).toBe('function')
  })
})
