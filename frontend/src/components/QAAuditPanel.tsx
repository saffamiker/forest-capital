import { useState, useEffect } from 'react'
import { CheckCircle, XCircle, AlertTriangle, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { Verdict, QACheck } from '../types/agents'
import { useQAStore } from '../stores/qaStore'

interface VerdictStyle {
  Icon: LucideIcon
  color: string
  bg: string
  border: string
  badge: string
}

const VERDICT_CONFIG: Record<Verdict, VerdictStyle> = {
  PASS: { Icon: CheckCircle,   color: 'text-success', bg: 'bg-success/10', border: 'border-success/20', badge: 'badge-pass' },
  FAIL: { Icon: XCircle,       color: 'text-danger',  bg: 'bg-danger/10',  border: 'border-danger/20',  badge: 'badge-fail' },
  WARN: { Icon: AlertTriangle, color: 'text-warning', bg: 'bg-warning/10', border: 'border-warning/20', badge: 'badge-warn' },
}

function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const cfg = VERDICT_CONFIG[verdict]
  return <span className={cfg.badge}>{verdict}</span>
}

interface CheckRowProps {
  check: QACheck
  open: boolean
  onToggle: () => void
}

function CheckRow({ check, open, onToggle }: CheckRowProps) {
  const cfg = VERDICT_CONFIG[check.status]
  const { Icon } = cfg
  return (
    <div className={`border rounded-lg overflow-hidden mb-1.5 ${cfg.border}`}>
      <button
        onClick={onToggle}
        className={`w-full flex items-center gap-3 px-3 py-2.5 text-left hover:opacity-90 transition-opacity ${cfg.bg}`}
      >
        <Icon className={`w-4 h-4 shrink-0 ${cfg.color}`} />
        <span className="font-mono text-2xs text-muted w-7 shrink-0">{check.check_id}</span>
        <span className="text-white text-xs flex-1">{check.description}</span>
        <VerdictBadge verdict={check.status} />
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-muted ml-1 shrink-0" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-muted ml-1 shrink-0" />
        )}
      </button>
      {open && (
        <div className="px-3 py-2 border-t border-border/50 bg-navy-900">
          <p className="text-slate-300 text-xs">{check.evidence}</p>
          {check.fix && (
            <p className="text-warning text-xs mt-1"><strong>Fix:</strong> {check.fix}</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function QAAuditPanel() {
  // Audit result lives in qaStore — survives navigation away and back.
  // load() is a no-op when loaded=true, so re-entering this tab is instant
  // and never triggers a second 10-second audit run.
  const { result: audit, loading, load, reload } = useQAStore()
  const [openChecks, setOpenChecks] = useState<Set<string>>(new Set())
  const [activeCategory, setActiveCategory] = useState('ALL')

  useEffect(() => { void load() }, [load])

  const toggleCheck = (id: string) => {
    setOpenChecks((prev) => {
      const next = new Set(prev)
      if (next.has(id)) { next.delete(id) } else { next.add(id) }
      return next
    })
  }

  if (!audit && !loading) return (
    <div className="p-6 text-center text-muted text-sm">
      <button onClick={() => void reload()} className="text-electric underline">Load QA Audit</button>
    </div>
  )

  if (loading && !audit) return (
    <div className="p-6 flex items-center gap-2 text-muted text-sm">
      <RefreshCw className="w-4 h-4 animate-spin" />
      Running 30-point audit…
    </div>
  )

  if (!audit) return null

  const items = audit.items
  const categories = ['ALL', ...new Set(items.map((c) => c.category))]
  const filtered = activeCategory === 'ALL' ? items : items.filter((c) => c.category === activeCategory)

  const overallCfg = VERDICT_CONFIG[audit.verdict]
  const { Icon: OverallIcon } = overallCfg

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-5">
      {/* Summary card */}
      <div className={`card p-5 border ${overallCfg.border} ${overallCfg.bg}`}>
        <div className="flex items-center gap-4">
          <OverallIcon className={`w-8 h-8 ${overallCfg.color}`} />
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <h2 className="text-white font-bold text-lg">QA Audit Report</h2>
              <VerdictBadge verdict={audit.verdict} />
            </div>
            <p className="text-muted text-sm mt-0.5">
              30-point methodology checklist · Sprint {audit.sprint ?? '4'} results
            </p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-3xl font-mono font-bold text-white">
              {audit.checks_passed}<span className="text-muted text-xl">/{audit.checks_total}</span>
            </div>
            <div className="text-xs text-muted mt-0.5">checks passed</div>
          </div>
        </div>

        {/* Mini breakdown */}
        <div className="flex gap-3 mt-4 pt-4 border-t border-border/50">
          <div className="flex items-center gap-1.5">
            <CheckCircle className="w-3.5 h-3.5 text-success" />
            <span className="font-mono text-sm text-success">{audit.checks_passed}</span>
            <span className="text-muted text-xs">passed</span>
          </div>
          <div className="flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 text-warning" />
            <span className="font-mono text-sm text-warning">{audit.checks_warned}</span>
            <span className="text-muted text-xs">warned</span>
          </div>
          <div className="flex items-center gap-1.5">
            <XCircle className="w-3.5 h-3.5 text-danger" />
            <span className="font-mono text-sm text-danger">{audit.checks_failed}</span>
            <span className="text-muted text-xs">failed</span>
          </div>
          <button
            onClick={() => void reload()}
            disabled={loading}
            className="ml-auto flex items-center gap-1.5 text-muted hover:text-white text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Re-run audit
          </button>
        </div>
      </div>

      {/* Category filter */}
      <div className="flex gap-1.5 flex-wrap">
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            className={`text-xs px-2.5 py-1 rounded border transition-colors ${
              activeCategory === cat
                ? 'border-electric bg-electric/10 text-electric'
                : 'border-border text-muted hover:text-white hover:border-border/80'
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Checklist */}
      <div>
        {filtered.map((check) => (
          <CheckRow
            key={check.check_id}
            check={check}
            open={openChecks.has(check.check_id)}
            onToggle={() => toggleCheck(check.check_id)}
          />
        ))}
      </div>

      {/* Legend */}
      <div className="card p-4">
        <div className="section-header mb-3">Verdict Definitions</div>
        <div className="space-y-2">
          {([
            { v: 'PASS' as Verdict, d: 'Methodology is sound on this dimension.' },
            { v: 'WARN' as Verdict, d: 'Should be addressed or explicitly disclosed as a limitation.' },
            { v: 'FAIL' as Verdict, d: 'Must be fixed before presenting. A professional quant would catch and criticise this.' },
          ]).map(({ v, d }) => (
            <div key={v} className="flex items-start gap-2">
              <VerdictBadge verdict={v} />
              <span className="text-muted text-xs">{d}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
