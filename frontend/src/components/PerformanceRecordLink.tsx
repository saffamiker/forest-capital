/**
 * Performance Record link/preview — the "past" panel of the landing-page
 * arc, rendered third. A compact preview of the Council Performance
 * Record (the scorecard) with a link to the full /performance-record
 * page. Reads GET /api/v1/play-by-play for the scorecard only.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'

interface Scorecard {
  n_total?: number
  n_evaluable?: number
  n_value_added?: number
  framing?: string
}
interface Payload { available: boolean; scorecard: Scorecard | null }

export default function PerformanceRecordLink() {
  const [data, setData] = useState<Payload | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/v1/play-by-play', { credentials: 'include' })
      .then((r) => r.json())
      .then((d) => { if (alive) setData(d) })
      .catch(() => { if (alive) setData({ available: false, scorecard: null }) })
    return () => { alive = false }
  }, [])

  const sc = data?.scorecard

  return (
    <Link
      to="/performance-record"
      className="card p-5 m-4 md:m-6 border-l-2 border-electric block
                 hover:bg-navy-700/40 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-2xs text-muted uppercase tracking-wide">
            Council Performance Record
          </div>
          {sc && sc.n_evaluable ? (
            <div className="flex items-baseline gap-2 mt-1">
              <span className="text-2xl font-bold text-electric">
                {sc.n_value_added}/{sc.n_evaluable}
              </span>
              <span className="text-sm text-muted">
                events where the council added value
              </span>
            </div>
          ) : (
            <div className="text-sm text-muted mt-1">
              The event-by-event track record across the post-2022 events.
            </div>
          )}
        </div>
        <ArrowRight className="w-5 h-5 text-electric shrink-0 mt-1" />
      </div>
      {sc?.framing && (
        <p className="mt-2 text-xs text-muted leading-relaxed line-clamp-2">
          {sc.framing}
        </p>
      )}
      <span className="mt-3 inline-block text-xs text-electric">
        View the full record →
      </span>
    </Link>
  )
}
