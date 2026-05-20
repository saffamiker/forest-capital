import { useState, useEffect } from 'react'
import { CheckCircle, XCircle, AlertTriangle, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { Verdict, QACheck } from '../types/agents'
import type { QAItemExplanation } from '../types/glossary'
import { useQAStore } from '../stores/qaStore'
import { useGlossaryStore } from '../stores/glossaryStore'

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
  // Commentary-mode QA narrative, loaded per audit run from the Explainer
  // Agent. Optional — when undefined (audit hasn't been explained yet, or
  // the Explainer call failed), the row falls back to evidence/fix only.
  explanation?: QAItemExplanation
}

function CheckRow({ check, open, onToggle, explanation }: CheckRowProps) {
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
        <div className="px-3 py-2 border-t border-border/50 bg-navy-900 space-y-2">
          {/* evidence is this check's own analysis section — the backend
              splits the QA analysis per check id, so each tile shows only
              its relevant reasoning, not the whole blob. */}
          <p className="text-slate-300 text-xs whitespace-pre-wrap leading-relaxed">
            {check.evidence}
          </p>
          {check.fix && (
            <p className="text-warning text-xs"><strong>Fix:</strong> {check.fix}</p>
          )}
          {explanation && (
            <div className="pt-2 mt-1 border-t border-border/40 space-y-2">
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">What this check tests</div>
                <p className="text-slate-300 text-xs">{explanation.what}</p>
              </div>
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">Why it matters</div>
                <p className="text-slate-300 text-xs">{explanation.why}</p>
              </div>
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">What a failure would mean</div>
                <p className="text-slate-300 text-xs">{explanation.failure_meaning}</p>
              </div>
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">How it was tested</div>
                <p className="text-slate-300 text-xs">{explanation.how_tested}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * CategoryAccordion — mobile-only grouped view of QA checks.
 *
 * Collapsed: a tappable header with the category name and three count
 * pills (pass / warn / fail). Tap the header to expand; expanded view
 * renders the same CheckRow each check uses on desktop. Per-category
 * open state is local — opening one category does not close another.
 */
function CategoryAccordion(
  { category, items, openChecks, onToggleCheck, explanations }: {
    category: string
    items: QACheck[]
    openChecks: Set<string>
    onToggleCheck: (id: string) => void
    explanations: Record<string, QAItemExplanation>
  },
) {
  const [open, setOpen] = useState(false)
  const pass = items.filter((i) => i.status === 'PASS').length
  const warn = items.filter((i) => i.status === 'WARN').length
  const fail = items.filter((i) => i.status === 'FAIL').length
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left
                   bg-navy-800 hover:bg-navy-700 transition-colors
                   min-h-[44px]">
        <span className="text-white text-xs font-semibold flex-1
                         truncate">
          {category}
        </span>
        <div className="flex items-center gap-1.5 shrink-0 text-2xs
                        font-mono">
          {pass > 0 && (
            <span className="text-success">{pass}P</span>
          )}
          {warn > 0 && (
            <span className="text-warning">{warn}W</span>
          )}
          {fail > 0 && (
            <span className="text-danger">{fail}F</span>
          )}
        </div>
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-muted shrink-0" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-muted shrink-0" />
        )}
      </button>
      {open && (
        <div className="p-2 bg-navy-900">
          {items.map((check) => (
            <CheckRow
              key={check.check_id}
              check={check}
              open={openChecks.has(check.check_id)}
              onToggle={() => onToggleCheck(check.check_id)}
              explanation={explanations[check.check_id]}
            />
          ))}
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
  // Per-audit Commentary narrative. Loaded once per audit-items array —
  // the Explainer Agent generates fresh what/why/failure/how text for
  // the checks based on their actual pass/warn/fail state in this run.
  const qaExplanations = useGlossaryStore((s) => s.qa)
  const loadQA = useGlossaryStore((s) => s.loadQA)
  const [openChecks, setOpenChecks] = useState<Set<string>>(new Set())
  const [activeCategory, setActiveCategory] = useState('ALL')

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!audit?.items?.length) return
    void loadQA(audit.items as unknown as Array<Record<string, unknown>>)
  }, [audit, loadQA])

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
      Running methodology audit…
    </div>
  )

  if (!audit) return null

  const items = audit.items
  const categories = ['ALL', ...new Set(items.map((c) => c.category))]
  const filtered = activeCategory === 'ALL' ? items : items.filter((c) => c.category === activeCategory)

  const overallCfg = VERDICT_CONFIG[audit.verdict]
  const { Icon: OverallIcon } = overallCfg

  return (
    // No page chrome here — QAHub owns the page container so this panel
    // embeds cleanly as the hub's Methodology Review section.
    <div className="space-y-5">
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
              {audit.checks_total}-point methodology checklist · Sprint {audit.sprint ?? '4'} results
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

      {/* Category filter — sm: and up only. Below sm: the checklist
          renders as a grouped accordion (see the mobile block below). */}
      <div className="hidden sm:flex gap-1.5 flex-wrap">
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

      {/* Checklist — desktop flat list. */}
      <div className="hidden sm:block">
        {filtered.map((check) => (
          <CheckRow
            key={check.check_id}
            check={check}
            open={openChecks.has(check.check_id)}
            onToggle={() => toggleCheck(check.check_id)}
            explanation={qaExplanations[check.check_id]}
          />
        ))}
      </div>

      {/* Checklist — mobile (below sm:) grouped accordion view. Each
          category becomes a collapsible section; the header carries
          the pass/warn/fail count badges for that group. Tapping the
          header expands the group to the same CheckRow set used on
          desktop. Keeps the QA checklist scannable on a narrow phone
          where 39 checks in a flat list would require a long scroll. */}
      <div className="sm:hidden space-y-2">
        {categories
          .filter((c) => c !== 'ALL')
          .map((cat) => (
            <CategoryAccordion
              key={cat}
              category={cat}
              items={items.filter((c) => c.category === cat)}
              openChecks={openChecks}
              onToggleCheck={toggleCheck}
              explanations={qaExplanations}
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
