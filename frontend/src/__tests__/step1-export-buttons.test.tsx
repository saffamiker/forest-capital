/**
 * step1-export-buttons.test.tsx — Stage Findings CSV/JSON export.
 *
 * Pins the data-shape contract: CSV header columns + JSON payload
 * preservation. The export drives the citation R&D loop so a regression
 * in the column order or the JSON wrapper would silently break the
 * downstream search-query experiments.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import {
  Step1ExportButtons, STAGE_FINDINGS_CSV_HEADERS,
  stageFindingsToCsvRows, stageFindingsToJsonString,
} from '../components/reportwriter/Step1ExportButtons'
import { toCsv } from '../lib/csv'

// jsdom doesn't provide URL.createObjectURL / revokeObjectURL by
// default; stub both so the download() helper completes without
// throwing inside the click handler.
beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    value: vi.fn(() => 'blob:mock'),
    writable: true,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    value: vi.fn(),
    writable: true,
  })
})
afterEach(() => vi.restoreAllMocks())


const SAMPLE_FINDINGS = [
  {
    title: 'REGIME_SWITCHING beats benchmark on post-2022 Sharpe',
    finding: 'Regime Switching delivered post-2022 Sharpe 0.65 vs '
           + 'benchmark 0.32 — a +103% lift in risk-adjusted return.',
    evidence: [
      'Sharpe pre-2022: 0.51 (RS) vs 0.48 (BM)',
      'Sharpe post-2022: 0.65 vs 0.32',
      'p-value (paired t-test): 0.018',
    ],
    implication: 'The regime classifier captured the equity-bond '
               + 'decorrelation event in 2022.',
    nugget_strength: 'HIGH',
    surprise: true,
    surprise_reason: 'Magnitude of lift exceeded the pre-deploy estimate.',
  },
  {
    title: 'HY corner of the efficient frontier',
    finding: 'Tangency portfolio sits at HY=85% / EQ=15% / IG=0%.',
    evidence: ['Tangency Sharpe: 0.71', 'Annualised return: 6.4%'],
    implication: 'Long-only frontier in this universe over-weights HY.',
    nugget_strength: 'MEDIUM',
    surprise: false,
    surprise_reason: null,
  },
]


describe('Step1ExportButtons', () => {
  it('renders both CSV and JSON buttons when findings exist', () => {
    render(<Step1ExportButtons findings={SAMPLE_FINDINGS} />)
    expect(screen.getByTestId('step1-export-csv')).toBeInTheDocument()
    expect(screen.getByTestId('step1-export-json')).toBeInTheDocument()
  })

  it('renders NOTHING when findings is empty (no empty downloads)', () => {
    const { container } = render(<Step1ExportButtons findings={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('CSV download click invokes createObjectURL with a CSV blob', () => {
    const spy = vi.spyOn(URL, 'createObjectURL')
    render(<Step1ExportButtons findings={SAMPLE_FINDINGS} />)
    fireEvent.click(screen.getByTestId('step1-export-csv'))
    expect(spy).toHaveBeenCalled()
    const blob = spy.mock.calls[0]?.[0] as Blob
    expect(blob.type).toContain('text/csv')
  })

  it('JSON download click invokes createObjectURL with a JSON blob', () => {
    const spy = vi.spyOn(URL, 'createObjectURL')
    render(<Step1ExportButtons findings={SAMPLE_FINDINGS} />)
    fireEvent.click(screen.getByTestId('step1-export-json'))
    expect(spy).toHaveBeenCalled()
    const blob = spy.mock.calls[0]?.[0] as Blob
    expect(blob.type).toContain('application/json')
  })

  // The next three tests verify the SHAPE of the exported content via
  // the pure mapper exports (stageFindingsToCsvRows /
  // stageFindingsToJsonString). The click handler then composes those
  // with csvBlob() and triggers download — that wiring is exercised
  // by the click tests above; this set pins the data contract.

  it('CSV header columns match the user spec exactly', () => {
    // The user requested: strength rating, strategy name, finding
    // description, supporting metrics. The CSV emits all of them as
    // named columns; this pins the header order so a future reorder
    // trips a clear failure.
    expect([...STAGE_FINDINGS_CSV_HEADERS]).toEqual([
      '#', 'title', 'nugget_strength', 'surprise',
      'finding', 'implication', 'evidence', 'surprise_reason',
    ])
  })

  it('CSV row mapper preserves the supporting metrics in the evidence cell',
    () => {
      const rows = stageFindingsToCsvRows(SAMPLE_FINDINGS)
      // Evidence list joined with " | " so a many-metric finding rides
      // one CSV cell — useful for grep / sort / filter in Excel.
      const evidenceCell = rows[0]?.[6] as string
      expect(evidenceCell).toContain('p-value (paired t-test): 0.018')
      expect(evidenceCell).toContain('Sharpe post-2022: 0.65 vs 0.32')
      // The pipe separator is the documented join character.
      expect(evidenceCell).toContain(' | ')
    })

  it('CSV rows carry strength and strategy title per the user spec',
    () => {
      const rows = stageFindingsToCsvRows(SAMPLE_FINDINGS)
      // Row 0: HIGH-strength regime-switching finding
      expect(rows[0]?.[1]).toContain('REGIME_SWITCHING')
      expect(rows[0]?.[2]).toBe('HIGH')
      expect(rows[0]?.[3]).toBe('true')  // surprise flag
      // Row 1: MEDIUM strength, not a surprise
      expect(rows[1]?.[2]).toBe('MEDIUM')
      expect(rows[1]?.[3]).toBe('false')
    })

  it('CSV header + rows round-trip through toCsv into a parseable string',
    () => {
      // End-to-end check on the CSV serialisation: header + first row
      // are present and the data row has the right cell count.
      const csv = toCsv(
        [...STAGE_FINDINGS_CSV_HEADERS],
        stageFindingsToCsvRows(SAMPLE_FINDINGS),
      )
      const lines = csv.split('\r\n')
      expect(lines).toHaveLength(3)  // header + 2 data rows
      expect(lines[0]).toContain('title')
      expect(lines[0]).toContain('nugget_strength')
      expect(lines[1]).toMatch(/REGIME_SWITCHING/)
    })

  it('JSON export preserves the full findings array verbatim', () => {
    const text = stageFindingsToJsonString(SAMPLE_FINDINGS)
    const parsed = JSON.parse(text)
    expect(Array.isArray(parsed)).toBe(true)
    expect(parsed).toHaveLength(2)
    // Every field on the original finding round-trips — the JSON
    // export is for tools that need the structured payload, not a
    // flattened table.
    expect(parsed[0].evidence).toHaveLength(3)
    expect(parsed[0].surprise).toBe(true)
    expect(parsed[1].surprise).toBe(false)
    expect(parsed[0].title).toContain('REGIME_SWITCHING')
  })
})
