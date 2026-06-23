/**
 * CriticFindingsPanel -- June 23 2026, Concern 7 (revised), Change 7j.
 *
 * Displays the adversarial critic + debate-round payload that the
 * academic review SSE stream now produces. NOT a trigger -- the
 * critic always runs as part of the academic review, so this panel
 * just renders what the store already carries:
 *
 *   - criticResult (from the `critic_findings` SSE frame)
 *   - debateRoundText (streamed from `debate_round_arbiter` chunks)
 *   - criticMinorOnly (the `critic_minor_only` marker frame)
 *
 * Rendered inside AcademicReviewSection (cross-document review) and
 * inside WritingAssistant (per-doc review) -- the same component
 * handles both surfaces.
 *
 * Concern 7j details:
 *   - Header: "Gemini + Grok | Fatal: N | Major: N | Minor: N"
 *   - Fatal findings get a non-blocking amber banner above the list
 *   - Findings grouped by severity; each card shows severity chip,
 *     category chip, document + location, description, evidence
 *     blockquote, recommendation, agreement badge
 *   - "Council Response to Critic" panel below the findings list,
 *     streaming the debate_round_arbiter text via the same Markdown
 *     renderer used for the arbiter verdict
 *   - critic_minor_only state shows a muted banner + collapsed
 *     panel of the minor findings only
 *   - When the store carries nothing yet, the panel renders null
 *     (no chrome, no headers) so it can be unconditionally mounted
 *     in surfaces where a review may or may not have run
 */
import { useState } from 'react'
import {
  AlertOctagon, AlertTriangle, AlertCircle, ChevronDown,
  ChevronRight, CheckCircle, Sword,
} from 'lucide-react'

import Markdown from './Markdown'
import type {
  CriticFinding, CriticResult, CriticSeverity,
} from '../stores/academicReviewStore'


export interface CriticFindingsPanelProps {
  /** The critic findings payload from the `critic_findings` SSE
   *  frame. Null when the critic step hasn't run (legacy review
   *  responses, or a review still in flight at the peer / arbiter
   *  stage). */
  criticResult:    CriticResult | null
  /** The streamed `debate_round_arbiter` text. Empty string when
   *  the debate round did not fire (minor-only findings). */
  debateRoundText: string
  /** True when the backend emitted `critic_minor_only` -- only
   *  minor findings, no debate round was triggered. */
  criticMinorOnly: boolean
  /** Compact mode for the editor's Writing Assistant (300px
   *  panel). False for the QA Hub / Submission Readiness Review
   *  surfaces where the panel has more room. */
  compact?:        boolean
}


export default function CriticFindingsPanel(
  { criticResult, debateRoundText, criticMinorOnly,
    compact = false }: CriticFindingsPanelProps,
): React.ReactElement | null {
  const [findingsOpen, setFindingsOpen] = useState(true)
  const [debateOpen, setDebateOpen] = useState(true)

  // Nothing to render yet -- the SSE pass hasn't reached the critic
  // step. Returning null lets the parent unconditionally mount us.
  if (!criticResult && !debateRoundText && !criticMinorOnly) {
    return null
  }

  const hasFatal = (criticResult?.fatal_count ?? 0) > 0
  const findings = criticResult?.merged_findings ?? []
  const showNothingBanner =
    criticResult !== null
    && criticResult.fatal_count === 0
    && criticResult.major_count === 0
    && criticResult.minor_count === 0
    && !criticResult.partial_failure

  const headerCls = compact
    ? 'text-2xs font-semibold uppercase tracking-wide text-white'
    : 'text-sm font-semibold text-white'
  const sectionCls = compact
    ? 'border-t border-border pt-3 space-y-2'
    : 'card p-4 space-y-3'

  return (
    <section
      data-testid="critic-findings-panel"
      className={sectionCls}>
      {/* ── Header + severity counts ─────────────────────────── */}
      <div>
        <h3 className={
          `${headerCls} flex items-center gap-1.5`}>
          <Sword className={compact
            ? 'w-3 h-3 text-danger'
            : 'w-4 h-4 text-danger'} />
          Adversarial Critic Findings
        </h3>
        {criticResult && (
          <div className="flex flex-wrap items-center gap-1.5
                          mt-1.5 text-2xs">
            <span className={`px-2 py-0.5 rounded font-semibold ${
              hasFatal ? 'bg-danger/20 text-danger'
                : 'bg-slate-700/40 text-slate-300'}`}>
              Fatal: {criticResult.fatal_count}
            </span>
            <span className={`px-2 py-0.5 rounded font-semibold ${
              criticResult.major_count > 0
                ? 'bg-warning/20 text-warning'
                : 'bg-slate-700/40 text-slate-300'}`}>
              Major: {criticResult.major_count}
            </span>
            <span className="px-2 py-0.5 rounded font-semibold
                             bg-slate-700/40 text-slate-300">
              Minor: {criticResult.minor_count}
            </span>
            <span className="px-2 py-0.5 rounded font-semibold
                             bg-electric/10 text-electric">
              Gemini + Grok
            </span>
            {criticResult.partial_failure && (
              <span
                data-testid="critic-partial-failure"
                className="px-2 py-0.5 rounded font-semibold
                           bg-warning/15 text-warning"
                title="One of the two models did not return a parseable response">
                Partial result
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Fatal banner ──────────────────────────────────────── */}
      {hasFatal && (
        <div
          data-testid="critic-fatal-banner"
          className="rounded border border-warning/40 bg-warning/5
                     p-2.5 text-2xs text-warning leading-relaxed
                     flex items-start gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>
            Fatal findings were raised. Review the council response
            below before submitting. The team makes the final call.
          </span>
        </div>
      )}

      {/* ── Minor-only banner (no debate round fired) ───────── */}
      {criticMinorOnly && (
        <div
          data-testid="critic-minor-only-banner"
          className="rounded border border-border bg-navy-800
                     p-2.5 text-2xs text-muted leading-relaxed
                     italic">
          Critic review complete. Minor findings only -- no debate
          round triggered.
        </div>
      )}

      {/* ── No findings at all ───────────────────────────────── */}
      {showNothingBanner && (
        <div className="text-2xs text-success flex items-start
                        gap-1.5">
          <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>
            No findings raised. Both critics report no significant
            issues at this pass.
          </span>
        </div>
      )}

      {/* ── Findings grouped by severity ─────────────────────── */}
      {findings.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setFindingsOpen(!findingsOpen)}
            className="flex items-center gap-1.5 text-2xs
                       text-slate-300 hover:text-white">
            {findingsOpen
              ? <ChevronDown className="w-3 h-3" />
              : <ChevronRight className="w-3 h-3" />}
            Findings ({findings.length})
          </button>
          {findingsOpen && (
            <div className="mt-2 space-y-2">
              {(['Fatal', 'Major', 'Minor'] as const).map((sev) => {
                const items = findings.filter(
                  (f) => f.severity === sev)
                if (items.length === 0) return null
                return (
                  <div key={sev}
                    data-testid={
                      `critic-group-${sev.toLowerCase()}`}>
                    <h4 className="text-2xs font-semibold
                                   uppercase tracking-wide
                                   text-slate-300 mb-1.5">
                      {sev} ({items.length})
                    </h4>
                    <div className="space-y-1.5">
                      {items.map((f, i) => (
                        <FindingCard
                          key={`${sev}-${i}`} finding={f}
                          compact={compact} />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Council response to critic (debate round arbiter) ── */}
      {debateRoundText && (
        <div className="border-t border-border pt-2"
          data-testid="critic-debate-section">
          <button
            type="button"
            onClick={() => setDebateOpen(!debateOpen)}
            className="flex items-center gap-1.5 text-2xs
                       text-slate-300 hover:text-white">
            {debateOpen
              ? <ChevronDown className="w-3 h-3" />
              : <ChevronRight className="w-3 h-3" />}
            Council Response to Critic
          </button>
          {debateOpen && (
            <div className={compact
              ? 'mt-2 text-2xs text-slate-300 leading-relaxed'
              : 'mt-2 text-xs text-slate-300 leading-relaxed '
                + 'max-h-96 overflow-y-auto'}>
              <Markdown content={debateRoundText} />
            </div>
          )}
        </div>
      )}

      {/* ── Overall assessment ───────────────────────────────── */}
      {criticResult
        && (criticResult.gemini_prose
          || criticResult.grok_prose) && (
        <div className="border-t border-border pt-2 text-2xs
                        text-muted leading-relaxed italic">
          {criticResult.gemini_prose && (
            <div>
              <span className="font-semibold not-italic
                               text-slate-300">Gemini:</span>{' '}
              {criticResult.gemini_prose}
            </div>
          )}
          {criticResult.grok_prose && (
            <div className="mt-1">
              <span className="font-semibold not-italic
                               text-slate-300">Grok:</span>{' '}
              {criticResult.grok_prose}
            </div>
          )}
        </div>
      )}
    </section>
  )
}


function FindingCard(
  { finding, compact }: {
    finding: CriticFinding; compact: boolean
  },
): React.ReactElement {
  const sev: CriticSeverity = finding.severity
  const sevCls = sev === 'Fatal'
    ? 'bg-danger/20 text-danger'
    : sev === 'Major'
      ? 'bg-warning/20 text-warning'
      : 'bg-slate-700/40 text-slate-300'
  const Icon = sev === 'Fatal'
    ? AlertOctagon
    : sev === 'Major'
      ? AlertTriangle
      : AlertCircle
  const agreeLabel = finding.agreed
    ? 'Both critics agreed'
    : finding.raised_by === 'gemini'
      ? 'Gemini only'
      : finding.raised_by === 'grok'
        ? 'Grok only'
        : ''
  return (
    <div className={compact
      ? 'rounded border border-border bg-navy-800 p-2 space-y-1'
      : 'rounded border border-border bg-navy-800 p-2.5 space-y-1'}>
      <div className="flex flex-wrap items-center gap-1.5">
        <Icon className={`w-3 h-3 shrink-0 ${
          sev === 'Fatal' ? 'text-danger'
            : sev === 'Major' ? 'text-warning'
              : 'text-muted'}`} />
        <span className={`text-2xs px-1.5 py-0.5 rounded
                          font-semibold ${sevCls}`}>
          {sev}
        </span>
        {finding.category && (
          <span className="text-2xs px-1.5 py-0.5 rounded
                            bg-electric/10 text-electric
                            font-semibold">
            {finding.category}
          </span>
        )}
        {agreeLabel && (
          <span className={`text-2xs px-1.5 py-0.5 rounded
                            font-semibold ${finding.agreed
                              ? 'bg-success/15 text-success'
                              : 'bg-slate-700/40 text-slate-300'}`}>
            {agreeLabel}
          </span>
        )}
      </div>
      {(finding.document || finding.location) && (
        <div className="text-2xs text-muted font-mono">
          {finding.document}
          {finding.document && finding.location ? ' · ' : ''}
          {finding.location}
        </div>
      )}
      {finding.description && (
        <div className="text-2xs text-slate-200">
          {finding.description}
        </div>
      )}
      {finding.evidence && (
        <blockquote className="text-2xs text-slate-300 italic
                                border-l-2 border-electric/40 pl-2">
          {finding.evidence}
        </blockquote>
      )}
      {finding.recommendation && (
        <div className="text-2xs text-slate-300">
          <span className="text-electric font-semibold">
            Recommendation:
          </span>{' '}
          {finding.recommendation}
        </div>
      )}
    </div>
  )
}
