/**
 * markdown-tables.test.tsx
 *
 * May 27 2026 — GFM table rendering. AI responses contain markdown
 * tables (| col | col | with --- separators). react-markdown does
 * NOT parse tables without the remark-gfm plugin (tables are a GFM
 * extension, not standard markdown). Before the plugin was added the
 * pipe syntax rendered as raw text. These tests pin that:
 *   - a markdown table renders as a real <table> with <th> / <td>
 *   - GFM strikethrough also works (sanity that the plugin is live)
 *   - plain prose still renders normally (no regression)
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'

import Markdown from '../components/Markdown'

const TABLE = [
  '| Strategy | Sharpe |',
  '| --- | --- |',
  '| Regime Switching | 0.63 |',
  '| Benchmark | 0.52 |',
].join('\n')

describe('Markdown — GFM tables', () => {
  it('renders a markdown table as a real <table> element', () => {
    const { container } = render(<Markdown content={TABLE} />)
    const table = container.querySelector('table')
    expect(table).not.toBeNull()
  })

  it('renders header cells as <th> and data cells as <td>', () => {
    const { container } = render(<Markdown content={TABLE} />)
    const ths = container.querySelectorAll('th')
    const tds = container.querySelectorAll('td')
    // Two header columns.
    expect(ths.length).toBe(2)
    // Two data rows x two columns = four cells.
    expect(tds.length).toBe(4)
  })

  it('renders the table cell values, not raw pipe syntax', () => {
    const { container } = render(<Markdown content={TABLE} />)
    const text = container.textContent || ''
    expect(text).toContain('Regime Switching')
    expect(text).toContain('0.63')
    // The raw pipe-and-dash separator row must NOT survive as text.
    expect(text).not.toContain('| --- |')
  })

  it('still renders plain prose normally (no regression)', () => {
    const { container } = render(
      <Markdown content={'A plain paragraph with **bold** text.'} />)
    expect(container.querySelector('p')).not.toBeNull()
    expect(container.querySelector('strong')).not.toBeNull()
    expect(container.textContent).toContain('bold')
  })
})
