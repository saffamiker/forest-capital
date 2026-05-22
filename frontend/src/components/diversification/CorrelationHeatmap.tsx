/**
 * CorrelationHeatmap — the "Strategy Correlations" section.
 *
 * 11x11 diverging blue-white-red heatmap of pairwise Pearson
 * correlations across the ten strategies plus the benchmark, with
 * a Full / Pre-2022 / Post-2022 period toggle and an auto-generated
 * insight callout below.
 *
 * Backend payload from /api/v1/analytics/correlation (item 8
 * commit 1; a239843). The diverging scale maps -1.0 to deep blue,
 * 0 to white, +1.0 to deep red. Text colour adapts: white on dark
 * cells, dark on light cells. The diagonal is always 1.00 in
 * neutral grey so it does NOT read as "the strategy is most
 * correlated with itself" (which is trivially true and would
 * dominate visual attention).
 *
 * The insight callout below the heatmap auto-updates with the
 * period toggle and surfaces:
 *   - lowest correlation pair (best diversification)
 *   - highest non-self correlation (worst diversification)
 * Computed client-side from the matrix payload.
 */
import { useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useCorrelationMatrices } from '../../lib/useDiversificationData'
import type { CorrelationMatrixPayload } from '../../types/diversification'


type PeriodKey = 'full' | 'pre_2022' | 'post_2022'

const PERIOD_LABEL: Record<PeriodKey, string> = {
  full:      'Full period',
  pre_2022:  'Pre-2022',
  post_2022: 'Post-2022',
}


/**
 * Diverging colour scale: -1.0 → deep blue, 0 → white, +1.0 → deep red.
 *
 * Linear interpolation in RGB. The endpoints are chosen for
 * sufficient contrast against the dark Forest Capital theme: the
 * deepest blue (#1d4ed8) reads clearly distinct from the deepest
 * red (#b91c1c), and the white midpoint (#f1f5f9) is muted slate
 * rather than pure #fff (which would glow against the navy
 * background). Returns a CSS rgb() string.
 *
 * Null input → a transparent dim grey so a missing sub-period cell
 * (< 2 observations) reads as "no data" rather than 0.
 */
function correlationColour(r: number | null): string {
  if (r === null || Number.isNaN(r)) return 'rgba(100, 116, 139, 0.15)'
  const clamped = Math.max(-1, Math.min(1, r))
  // -1 → #1d4ed8 (29, 78, 216)
  // 0  → #f1f5f9 (241, 245, 249)
  // +1 → #b91c1c (185, 28, 28)
  if (clamped < 0) {
    const t = 1 + clamped  // -1→0, 0→1
    const lerp = (a: number, b: number) => Math.round(a + (b - a) * t)
    return `rgb(${lerp(29, 241)}, ${lerp(78, 245)}, ${lerp(216, 249)})`
  }
  const t = clamped  // 0→0, +1→1
  const lerp = (a: number, b: number) => Math.round(a + (b - a) * t)
  return `rgb(${lerp(241, 185)}, ${lerp(245, 28)}, ${lerp(249, 28)})`
}


/**
 * Decides text colour based on the cell background luminance. A
 * cell darker than the threshold gets white text; otherwise dark
 * slate. The threshold is empirically chosen so the white midpoint
 * (#f1f5f9, luminance ≈ 0.95) gets dark text and the deep red
 * (#b91c1c, luminance ≈ 0.16) gets white text.
 */
function cellTextColour(r: number | null): string {
  if (r === null) return 'rgb(148, 163, 184)'  // slate-400
  const absR = Math.abs(r)
  // |r| > 0.5 means the cell is markedly coloured (not near-white) →
  // dark blue or dark red, both of which need white text for
  // readability against the dark Forest Capital theme.
  return absR > 0.5 ? '#ffffff' : '#1e293b'  // slate-900
}


interface InsightCalloutFacts {
  lowestPair: { a: string; b: string; r: number } | null
  highestPair: { a: string; b: string; r: number } | null
}

/**
 * Extracts the lowest and highest non-self correlation pairs from
 * a symmetric matrix. Order-stable on the labels so ties resolve
 * deterministically.
 */
function computeInsightFacts(
  labels: string[],
  matrix: Array<Array<number | null>>,
): InsightCalloutFacts {
  let lowest: InsightCalloutFacts['lowestPair'] = null
  let highest: InsightCalloutFacts['highestPair'] = null
  for (let i = 0; i < labels.length; i++) {
    for (let j = i + 1; j < labels.length; j++) {
      const r = matrix[i]?.[j]
      if (r === null || r === undefined || Number.isNaN(r)) continue
      if (lowest === null || r < lowest.r) {
        lowest = { a: labels[i], b: labels[j], r }
      }
      if (highest === null || r > highest.r) {
        highest = { a: labels[i], b: labels[j], r }
      }
    }
  }
  return { lowestPair: lowest, highestPair: highest }
}


export function CorrelationHeatmap() {
  const { data, loading, error } = useCorrelationMatrices()
  const [period, setPeriod] = useState<PeriodKey>('full')

  const matrix = useMemo(() => {
    if (!data) return null
    return data[period] as CorrelationMatrixPayload['full']
  }, [data, period])

  const facts = useMemo(() => {
    if (!data || !matrix || !Array.isArray(data.labels)) {
      return { lowestPair: null, highestPair: null }
    }
    return computeInsightFacts(data.labels, matrix)
  }, [data, matrix])

  if (loading) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="correlation-heatmap-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading strategy correlations…
        </div>
      </div>
    )
  }
  if (error || !data || !data.labels || data.labels.length === 0) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}>
        <h2 className="text-base font-semibold text-white mb-2">
          Strategy Correlations
        </h2>
        <p className="text-sm text-muted">
          Correlation data unavailable.
          {error ? <span className="block mt-1 text-xs">{error}</span> : null}
        </p>
      </div>
    )
  }

  return (
    <div className="card p-5"
         style={{ borderLeft: '3px solid #3b82f6' }}
         data-testid="correlation-heatmap">
      <div className="flex items-start justify-between mb-3 gap-2 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-white">
            Strategy Correlations
          </h2>
          <p className="text-xs text-muted mt-0.5">
            Pairwise Pearson correlation across the ten strategies and the
            benchmark. Diverging blue-red scale; diagonal in grey is
            self-correlation (always 1.00).
          </p>
        </div>
        {/* Period toggle — three buttons. The active one carries the
            electric accent so the current view is unambiguous. */}
        <div className="flex gap-1 shrink-0"
             data-testid="correlation-period-toggle">
          {(['full', 'pre_2022', 'post_2022'] as PeriodKey[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPeriod(p)}
              data-testid={`correlation-period-${p}`}
              className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                period === p
                  ? 'border-electric bg-electric/10 text-electric'
                  : 'border-border text-muted hover:text-white hover:border-border/80'
              }`}>
              {PERIOD_LABEL[p]}
            </button>
          ))}
        </div>
      </div>

      <CorrelationGrid labels={data.labels} matrix={matrix!} />

      <InsightCallout
        period={period}
        facts={facts}
      />
    </div>
  )
}


function CorrelationGrid({
  labels, matrix,
}: {
  labels: string[]
  matrix: Array<Array<number | null>>
}) {
  // Layout: a CSS Grid with a header row + N data rows. Each row has
  // a label column + N data cells. The label column stays sticky-left
  // on horizontal scroll so the strategy names are always visible.
  // Cell dimensions: 56px x 36px on desktop, 44x32 on narrow screens.
  return (
    <div className="overflow-x-auto"
         data-testid="correlation-grid-scroll">
      <table className="border-separate" style={{ borderSpacing: 0 }}
             data-testid="correlation-grid">
        <thead>
          <tr>
            <th className="sticky left-0 z-10 bg-navy-800 text-2xs
                           font-medium text-muted uppercase tracking-wider
                           px-2 py-1 text-right"
                style={{ minWidth: '120px' }} />
            {labels.map((label) => (
              <th key={`col-${label}`}
                  className="text-2xs font-medium text-slate-300
                             px-1 py-1 whitespace-nowrap"
                  style={{ minWidth: '56px' }}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((rowLabel, i) => (
            <tr key={`row-${rowLabel}`}>
              <th className="sticky left-0 z-10 bg-navy-800 text-2xs
                             font-medium text-slate-300 whitespace-nowrap
                             px-2 py-1 text-right"
                  style={{ minWidth: '120px' }}>
                {rowLabel}
              </th>
              {labels.map((colLabel, j) => {
                const r = matrix[i]?.[j]
                const isDiagonal = i === j
                // Diagonal: neutral grey, value always 1.00 — does NOT
                // get the red endpoint of the scale (would over-dominate
                // the visual).
                const bg = isDiagonal
                  ? 'rgb(71, 85, 105)'  // slate-600 — neutral grey
                  : correlationColour(r)
                const fg = isDiagonal
                  ? '#cbd5e1'  // slate-300
                  : cellTextColour(r)
                const display = isDiagonal
                  ? '1.00'
                  : r === null || r === undefined
                    ? '—'
                    : r.toFixed(2)
                return (
                  <td
                    key={`cell-${i}-${j}`}
                    data-testid={`correlation-cell-${i}-${j}`}
                    title={isDiagonal
                      ? `${rowLabel} (self)`
                      : `${rowLabel} ↔ ${colLabel}: r=${display}`}
                    style={{
                      backgroundColor: bg,
                      color: fg,
                      minWidth: '56px',
                      height: '32px',
                    }}
                    className="text-2xs font-mono text-center
                               border border-navy-900/30">
                    {display}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


function InsightCallout({
  period, facts,
}: {
  period: PeriodKey
  facts: InsightCalloutFacts
}) {
  if (!facts.lowestPair || !facts.highestPair) {
    return (
      <p className="text-xs text-muted italic mt-3"
         data-testid="correlation-insight">
        Insufficient observations to compute pair statistics in this period.
      </p>
    )
  }
  const { lowestPair, highestPair } = facts
  return (
    <div className="mt-3 rounded border border-border bg-navy-900/40 px-3 py-2"
         data-testid="correlation-insight">
      <div className="text-2xs uppercase tracking-wide text-muted mb-1">
        Diversification readout — {PERIOD_LABEL[period]}
      </div>
      <p className="text-xs text-slate-200 leading-relaxed">
        <strong className="text-white font-semibold">
          Lowest correlation pair:
        </strong>{' '}
        {lowestPair.a} / {lowestPair.b} at r={lowestPair.r.toFixed(2)}.
        {' '}
        <strong className="text-white font-semibold">
          Highest non-self correlation:
        </strong>{' '}
        {highestPair.a} / {highestPair.b} at r={highestPair.r.toFixed(2)}.
      </p>
    </div>
  )
}
