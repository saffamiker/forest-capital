/**
 * CorrelationBreakdownChart — rolling 12-month equity-bond correlation.
 * The central project finding lives here: pre-2022 averaged around -0.31
 * (bonds diversified equities); 2022's hiking cycle pushed it to +0.48
 * (bonds and stocks fell together).
 *
 * Reference lines at the pre/post 2022 averages anchor the narrative.
 */
import { useMemo } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea } from 'recharts'
import type { CorrelationPoint } from '../../types/charts'

interface Props {
  correlation: CorrelationPoint[]
  /** Optional pre/post 2022 averages computed by the backend regime endpoint */
  pre2022?: number | null
  post2022?: number | null
}

export default function CorrelationBreakdownChart({ correlation, pre2022, post2022 }: Props) {
  // Compute summary if backend didn't provide
  const summary = useMemo(() => {
    if (correlation.length === 0) return { pre: pre2022 ?? null, post: post2022 ?? null }
    if (pre2022 != null && post2022 != null) return { pre: pre2022, post: post2022 }
    const pre = correlation.filter((p) => p.date < '2022-01-01')
    const post = correlation.filter((p) => p.date >= '2022-01-01')
    const mean = (arr: CorrelationPoint[]) =>
      arr.length ? arr.reduce((s, p) => s + p.rolling_12m, 0) / arr.length : null
    return { pre: mean(pre), post: mean(post) }
  }, [correlation, pre2022, post2022])

  if (correlation.length === 0) {
    return (
      <div className="card p-4" data-testid="correlation-breakdown-chart">
        <h3 className="text-white font-semibold text-sm">Equity-Bond Correlation — Rolling 12-Month</h3>
        <p className="text-muted text-xs mt-3">Loading correlation series…</p>
      </div>
    )
  }

  return (
    <div className="card p-4" data-testid="correlation-breakdown-chart">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <h3 className="text-white font-semibold text-sm">
            Equity-Bond Correlation — Rolling 12-Month
          </h3>
          <p className="text-muted text-xs mt-0.5">
            The central project finding: diversification broke down in 2022
          </p>
        </div>
        <div className="flex gap-4 text-2xs font-mono">
          <div className="text-muted">
            Pre-2022 avg: <span className="text-electric">{summary.pre?.toFixed(2) ?? '—'}</span>
          </div>
          <div className="text-muted">
            Post-2022 avg: <span className="text-warning">{summary.post?.toFixed(2) ?? '—'}</span>
          </div>
        </div>
      </div>

      <div style={{ width: '100%', height: 280 }}>
        <ResponsiveContainer>
          <LineChart data={correlation} margin={{ top: 8, right: 20, left: 0, bottom: 8 }}>
            <CartesianGrid stroke="#1e3a5c" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tick={{ fill: '#64748b', fontSize: 10 }}
              tickFormatter={(v) => String(v).slice(0, 4)}
              stroke="#1e3a5c"
            />
            <YAxis
              domain={[-0.8, 0.8]}
              tick={{ fill: '#64748b', fontSize: 10 }}
              stroke="#1e3a5c"
              tickFormatter={(v) => v.toFixed(1)}
            />
            <Tooltip
              contentStyle={{ background: '#0d1929', border: '1px solid #1e3a5c', fontSize: 11 }}
              labelStyle={{ color: '#cbd5e1' }}
              // Non-strategy chart — header is the date, single metric per row.
              // Keeps the same "Label · Metric: value" shape as the strategy
              // charts so the visual cadence is identical product-wide.
              formatter={(v: number) => [`12-month correlation: ${v.toFixed(3)}`, '']}
            />
            <ReferenceLine y={0} stroke="#cbd5e1" strokeOpacity={0.3} strokeDasharray="2 2" />
            {summary.pre != null && (
              <ReferenceLine y={summary.pre} stroke="#3b82f6" strokeOpacity={0.6} strokeDasharray="4 2" label={{ value: 'pre-2022', position: 'left', fill: '#3b82f6', fontSize: 9 }} />
            )}
            {summary.post != null && (
              <ReferenceLine y={summary.post} stroke="#f59e0b" strokeOpacity={0.6} strokeDasharray="4 2" label={{ value: 'post-2022', position: 'left', fill: '#f59e0b', fontSize: 9 }} />
            )}
            <ReferenceArea x1="2022-01-31" x2="2022-12-31" fill="#f59e0b" fillOpacity={0.1} />
            <Line
              type="monotone"
              dataKey="rolling_12m"
              stroke="#3b82f6"
              strokeWidth={1.6}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
