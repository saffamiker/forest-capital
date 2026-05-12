import { CheckCircle, AlertTriangle, XCircle, RefreshCw } from 'lucide-react'
import TableExportButton from './TableExportButton'

export interface SanityCheck {
  id: string
  description: string
  expected: string
  actual: string | number | null
  status: 'green' | 'amber' | 'red'
}

interface SanityCheckPanelProps {
  checks: SanityCheck[]
  loading?: boolean
  onRerun?: () => void
}

function StatusIcon({ status }: { status: SanityCheck['status'] }) {
  if (status === 'green') return <CheckCircle className="w-4 h-4 text-success shrink-0" />
  if (status === 'amber') return <AlertTriangle className="w-4 h-4 text-warning shrink-0" />
  return <XCircle className="w-4 h-4 text-danger shrink-0" />
}

function statusLabel(s: SanityCheck['status']) {
  return s === 'green' ? 'PASS' : s === 'amber' ? 'WARN' : 'FAIL'
}

function statusClass(s: SanityCheck['status']) {
  if (s === 'green') return 'text-success'
  if (s === 'amber') return 'text-warning'
  return 'text-danger'
}

/**
 * Renders the 10-headline sanity checks that verify known historical values
 * (S&P 500 CAGR, GFC drawdown, BND 2022 loss, etc.) against expected ranges.
 *
 * All red items block an "integrity confirmed" banner — a single wrong number
 * means the data pipeline has a problem that must be resolved before presenting.
 */
export default function SanityCheckPanel({ checks, loading, onRerun }: SanityCheckPanelProps) {
  const greenCount = checks.filter((c) => c.status === 'green').length
  const redCount = checks.filter((c) => c.status === 'red').length
  const allGreen = checks.length > 0 && greenCount === checks.length

  const csvRows = checks.map((c) => [
    c.description,
    c.expected,
    c.actual ?? '—',
    statusLabel(c.status),
  ])

  return (
    <div className="space-y-3" data-testid="sanity-check-panel">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-white">Sanity Checks</h3>
          <span className="text-2xs text-muted font-mono">
            {loading ? '—' : `${greenCount} / ${checks.length}`}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <TableExportButton
            tableId="sanity_checks"
            headers={['Check', 'Expected', 'Actual', 'Status']}
            rows={csvRows}
          />
          {onRerun && (
            <button
              onClick={onRerun}
              className="flex items-center gap-1 text-xs text-muted hover:text-white transition-colors"
              aria-label="Re-run sanity checks"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              Re-run
            </button>
          )}
        </div>
      </div>

      {/* Overall banner */}
      {!loading && checks.length > 0 && (
        <div className={`rounded px-3 py-2 text-xs font-medium border ${
          allGreen
            ? 'bg-success/10 border-success/30 text-success'
            : redCount > 0
              ? 'bg-danger/10 border-danger/30 text-danger'
              : 'bg-warning/10 border-warning/30 text-warning'
        }`}>
          {allGreen
            ? 'Data integrity confirmed — all checks pass.'
            : redCount > 0
              ? `${redCount} check${redCount > 1 ? 's' : ''} failed — review required before submission.`
              : 'Some checks require attention — review before presenting.'}
        </div>
      )}

      {/* Checks table */}
      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-navy-800 text-left">
              <th className="px-3 py-2 text-muted uppercase tracking-wide font-medium">Check</th>
              <th className="px-3 py-2 text-muted uppercase tracking-wide font-medium">Expected</th>
              <th className="px-3 py-2 text-muted uppercase tracking-wide font-medium font-mono">Actual</th>
              <th className="px-3 py-2 text-muted uppercase tracking-wide font-medium w-16">Status</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="border-t border-border animate-pulse">
                    <td className="px-3 py-2"><div className="h-3 bg-navy-700 rounded w-3/4" /></td>
                    <td className="px-3 py-2"><div className="h-3 bg-navy-700 rounded w-1/2" /></td>
                    <td className="px-3 py-2"><div className="h-3 bg-navy-700 rounded w-1/4" /></td>
                    <td className="px-3 py-2"><div className="h-3 bg-navy-700 rounded w-12" /></td>
                  </tr>
                ))
              : checks.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="px-3 py-2 text-white">{c.description}</td>
                    <td className="px-3 py-2 text-muted">{c.expected}</td>
                    <td className={`px-3 py-2 font-mono ${statusClass(c.status)}`}>
                      {c.actual ?? '—'}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1">
                        <StatusIcon status={c.status} />
                        <span className={`font-medium ${statusClass(c.status)}`}>
                          {statusLabel(c.status)}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
