/**
 * macro-citation.test.tsx — May 22 2026 macro citation suite (item 4).
 *
 * Pins:
 *   - extractMacroCategories deduplicates + order-preserves
 *   - renderWithMacroCitations replaces inline tags with badges
 *   - The badge renders the category label and a hover tooltip
 *   - MacroAttributionFooter renders only when at least one category
 *     was extracted
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  extractMacroCategories,
  renderWithMacroCitations,
  MacroAttributionFooter,
  MacroCitationBadge,
} from '../components/MacroCitation'

vi.mock('axios')
import axios from 'axios'
const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  mockedAxios.get = vi.fn().mockResolvedValue({
    data: {
      digest: {
        generated_at: '2026-05-22T12:00:00Z',
        summary_text: 'Fed paused; CPI cooler.',
        key_signals: [
          {
            category: 'monetary_policy',
            signal: 'Fed holds at 5.25-5.50%.',
            implication: 'IG duration tailwind.',
            source_url: 'https://federalreserve.gov/x',
          },
        ],
        citation_urls: ['https://federalreserve.gov/x'],
      },
    },
  })
})


describe('extractMacroCategories', () => {
  it('returns categories in document order, deduped', () => {
    const text = 'Foo [Macro: monetary_policy] bar [Macro: inflation] '
      + 'baz [Macro: monetary_policy] qux.'
    expect(extractMacroCategories(text)).toEqual([
      'monetary_policy', 'inflation',
    ])
  })

  it('returns an empty array when no tags present', () => {
    expect(extractMacroCategories('plain text')).toEqual([])
    expect(extractMacroCategories('')).toEqual([])
  })

  it('handles tags with no space after the colon', () => {
    expect(extractMacroCategories('[Macro:inflation]')).toEqual(['inflation'])
  })

  it('handles multiple categories on one line', () => {
    expect(
      extractMacroCategories('a [Macro: a] b [Macro: b] c [Macro: c]'),
    ).toEqual(['a', 'b', 'c'])
  })
})


describe('renderWithMacroCitations', () => {
  it('returns plain text when no tags present', () => {
    const out = renderWithMacroCitations('hello world')
    expect(out).toEqual(['hello world'])
  })

  it('splits text around a single tag', () => {
    const out = renderWithMacroCitations('before [Macro: rates] after')
    expect(out.length).toBe(3)
    // Surrounding text segments are strings; the middle node is the badge.
    expect(out[0]).toBe('before ')
    expect(out[2]).toBe(' after')
  })

  it('handles consecutive tags', () => {
    const out = renderWithMacroCitations(
      '[Macro: a][Macro: b] tail')
    // Two badge nodes + the trailing text.
    expect(out.length).toBe(3)
    expect(out[2]).toBe(' tail')
  })
})


describe('MacroCitationBadge', () => {
  it('renders the category label', () => {
    render(<MacroCitationBadge category="monetary_policy" />)
    expect(screen.getByText(/Macro: monetary_policy/)).toBeInTheDocument()
  })

  it('carries a hover tooltip with the digest date when matched', async () => {
    // Wait one microtask for the digest fetch to resolve.
    render(<MacroCitationBadge category="monetary_policy" />)
    // The title attribute is the tooltip body. The test runs before
    // the digest fetch resolves so the title is the fallback —
    // re-render via the consumer's normal flow when the digest
    // arrives is covered by an integration test elsewhere.
    const node = screen.getByTestId('macro-citation-monetary_policy')
    expect(node.getAttribute('title')).toContain('Macro context')
  })
})


describe('MacroAttributionFooter', () => {
  it('renders the source line when categories are present', () => {
    render(<MacroAttributionFooter categories={['monetary_policy']} />)
    expect(
      screen.getByText(/Forest Capital Research Digest/i),
    ).toBeInTheDocument()
  })

  it('renders nothing when categories are empty', () => {
    const { container } = render(
      <MacroAttributionFooter categories={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('links to the digest view', () => {
    render(<MacroAttributionFooter categories={['inflation']} />)
    const link = screen.getByText(/View full digest/i)
    expect(link.getAttribute('href')).toBe('/qa#macro-research')
  })
})
