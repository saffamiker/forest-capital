/**
 * token-value-extension.test.tsx -- June 28 2026.
 *
 * CRITICAL smoke test for the TipTap pass-through extension.
 *
 * The test rationale: WITHOUT the TokenValueExtension
 * registered, TipTap's default behaviour drops unknown node
 * types on the first editor.getJSON() roundtrip. That would
 * silently corrupt any draft upgraded via
 * tools/draft_token_upgrade (the token reference would be lost
 * on the user's next edit + save). This test pins that the
 * extension preserves the node + every attr on a full
 * load -> serialize -> reload roundtrip.
 *
 * If this test goes red, DO NOT ship PR-DM-Lite -- the
 * upgrade pass is unsafe to run on a real draft.
 */
import { describe, test, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'

import { TokenValueExtension } from
  '../components/editor/tokenValueExtension'


const SAMPLE_TOKEN_VALUE_DOC = {
  type: 'doc',
  content: [
    {
      type: 'paragraph',
      content: [
        { type: 'text', text: 'The blend Sharpe is ' },
        {
          type: 'token_value',
          attrs: {
            token:       '{{OOS_SHARPE_BLEND}}',
            resolved:    '0.86',
            resolved_at: '2026-06-21T12:00:00Z',
            data_hash:   'c421fb895347f924',
          },
        },
        { type: 'text', text: ' versus the benchmark.' },
      ],
    },
  ],
}


describe('TokenValueExtension', () => {

  test('preserves token_value node on getJSON roundtrip', () => {
    const editor = new Editor({
      extensions: [StarterKit, TokenValueExtension],
      content: SAMPLE_TOKEN_VALUE_DOC,
    })

    const roundtripped = editor.getJSON()

    // Find the token_value node in the roundtripped doc.
    const paragraph = roundtripped.content?.[0]
    expect(paragraph?.type).toBe('paragraph')
    const tokenNode = paragraph?.content?.find(
      (n) => n.type === 'token_value')
    expect(tokenNode).toBeDefined()

    // All four required attrs must survive the roundtrip.
    expect(tokenNode?.attrs?.token).toBe('{{OOS_SHARPE_BLEND}}')
    expect(tokenNode?.attrs?.resolved).toBe('0.86')
    expect(tokenNode?.attrs?.resolved_at).toBe(
      '2026-06-21T12:00:00Z')
    expect(tokenNode?.attrs?.data_hash).toBe(
      'c421fb895347f924')

    editor.destroy()
  })

  test('preserves override attrs on getJSON roundtrip', () => {
    const overriddenDoc = {
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [
            {
              type: 'token_value',
              attrs: {
                token:           '{{REGIME_SWITCHING_SHARPE}}',
                resolved:        '0.63',
                resolved_at:     '2026-06-21T12:00:00Z',
                data_hash:       'c421fb89',
                override:        '0.6291',
                override_by:     'thaob@queens.edu',
                override_at:     '2026-06-28T01:00:00Z',
                override_reason: '4dp precision for appendix',
              },
            },
          ],
        },
      ],
    }

    const editor = new Editor({
      extensions: [StarterKit, TokenValueExtension],
      content: overriddenDoc,
    })

    const roundtripped = editor.getJSON()
    const tokenNode = roundtripped.content?.[0]?.content?.[0]

    expect(tokenNode?.attrs?.override).toBe('0.6291')
    expect(tokenNode?.attrs?.override_by).toBe('thaob@queens.edu')
    expect(tokenNode?.attrs?.override_at).toBe(
      '2026-06-28T01:00:00Z')
    expect(tokenNode?.attrs?.override_reason).toBe(
      '4dp precision for appendix')

    editor.destroy()
  })

  test('renders override value when present, resolved otherwise',
    () => {
      const docWithOverride = {
        type: 'doc',
        content: [{
          type: 'paragraph',
          content: [{
            type: 'token_value',
            attrs: {
              token: '{{T}}',
              resolved: '0.63',
              resolved_at: '',
              data_hash: '',
              override: '0.6291',
            },
          }],
        }],
      }
      const editor = new Editor({
        extensions: [StarterKit, TokenValueExtension],
        content: docWithOverride,
      })
      expect(editor.getText()).toContain('0.6291')
      expect(editor.getText()).not.toContain('0.63')
      editor.destroy()

      const docWithoutOverride = {
        type: 'doc',
        content: [{
          type: 'paragraph',
          content: [{
            type: 'token_value',
            attrs: {
              token: '{{T}}',
              resolved: '0.86',
              resolved_at: '',
              data_hash: '',
            },
          }],
        }],
      }
      const editor2 = new Editor({
        extensions: [StarterKit, TokenValueExtension],
        content: docWithoutOverride,
      })
      expect(editor2.getText()).toContain('0.86')
      editor2.destroy()
    })

  test('drop-on-save would happen WITHOUT the extension',
    () => {
      // Negative control -- confirms the test's premise.
      // Without TokenValueExtension, TipTap should drop the
      // token_value node on first getJSON. This test
      // documents the failure mode the extension defends
      // against.
      const editor = new Editor({
        extensions: [StarterKit],
        content: SAMPLE_TOKEN_VALUE_DOC,
      })

      const roundtripped = editor.getJSON()
      const paragraph = roundtripped.content?.[0]
      const tokenNode = paragraph?.content?.find(
        (n) => n.type === 'token_value')

      // Without the extension, TipTap drops the unknown node.
      // The plain text fragments around it are preserved.
      expect(tokenNode).toBeUndefined()

      editor.destroy()
    })
})
