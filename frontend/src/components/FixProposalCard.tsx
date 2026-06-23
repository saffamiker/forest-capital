/**
 * FixProposalCard -- June 23 2026, Concern 7k-v + 7k-vi.
 *
 * The per-finding fix proposal block that renders inside the
 * critic findings panel for Fatal + Major severity. Two shapes:
 *
 *   Fatal (auto_proposed=true)
 *     The arbiter already generated a proposal during the debate
 *     round. The card is pre-populated; the user only sees the
 *     "Apply Fix" button + confirmation modal.
 *
 *   Major (auto_proposed=false)
 *     The card shows a "Propose Fix" button that calls
 *     /api/v1/documents/propose-fix on click. Once the proposal
 *     lands, the card morphs to the Fatal shape with an Apply Fix
 *     button.
 *
 * Minor findings render no card -- they're not eligible for
 * story-plan injection.
 *
 * The confirmation modal lives in this component and fires on
 * Apply Fix; on confirm, /api/v1/documents/apply-fix is called
 * with the proposal payload. A spinner + status row replaces the
 * action area while the regen is in flight.
 */
import { useState } from 'react'
import axios from 'axios'
import {
  Wrench, Loader2, AlertCircle, CheckCircle, ChevronDown,
  ChevronRight,
} from 'lucide-react'

import type {
  CriticFinding,
} from '../stores/academicReviewStore'


export interface FixProposal {
  finding_id:               number
  target:                   'section' | 'document'
  section_name:             string | null
  rationale:                string
  patch_instruction:        string
  severity:                 string
  auto_proposed:            boolean
  target_document?:         string | null | undefined
  source_of_truth_document?: string | null | undefined
}

export interface FixProposalCardProps {
  finding:        CriticFinding
  findingId:      number
  documentType:   string
  debateId:       number | null
  /** Pre-populated when auto_proposed (Fatal); null otherwise.
   *  When null and severity is Major, the card shows a Propose
   *  Fix button instead of Apply Fix. */
  proposal:       FixProposal | null
  /** Callback fired after a successful apply-fix POST. The parent
   *  uses it to refresh the version selector + close the panel. */
  onApplied?:     ((newDraftLabel: string) => void) | undefined
}


export default function FixProposalCard(
  {
    finding, findingId, documentType, debateId, proposal,
    onApplied,
  }: FixProposalCardProps,
): React.ReactElement | null {
  // Hooks MUST be called unconditionally on every render -- the
  // Minor early-return below would otherwise trip ESLint's
  // react-hooks/rules-of-hooks. Concern 7k-v rule: Minor findings
  // render no card; we return null AFTER the hook calls.
  const [localProposal, setLocalProposal] = useState<
    FixProposal | null>(proposal)
  const [expanded, setExpanded] = useState(true)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [proposingBusy, setProposingBusy] = useState(false)
  const [applyingBusy, setApplyingBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [applied, setApplied] = useState(false)
  const [appliedLabel, setAppliedLabel] = useState<string | null>(
    null)

  const sev = finding.severity
  if (sev === 'Minor') return null

  const handlePropose = async (): Promise<void> => {
    setProposingBusy(true)
    setError(null)
    try {
      const res = await axios.post<{
        ok: boolean
        message?: string
      } & FixProposal>('/api/v1/documents/propose-fix', {
        document_type: documentType,
        finding_id: findingId,
        finding,
        debate_id: debateId,
      })
      if (!res.data.ok) {
        setError(res.data.message
          || 'Could not generate a fix proposal.')
      } else {
        setLocalProposal({
          finding_id: res.data.finding_id,
          target: res.data.target,
          section_name: res.data.section_name,
          rationale: res.data.rationale,
          patch_instruction: res.data.patch_instruction,
          severity: res.data.severity,
          auto_proposed: res.data.auto_proposed,
          target_document: res.data.target_document,
          source_of_truth_document:
            res.data.source_of_truth_document,
        })
      }
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Could not generate a fix proposal.'
      setError(String(msg))
    } finally {
      setProposingBusy(false)
    }
  }

  const handleApply = async (): Promise<void> => {
    if (!localProposal) return
    setConfirmOpen(false)
    setApplyingBusy(true)
    setError(null)
    try {
      const res = await axios.post<{
        ok: boolean
        draft_label?: string
        scope?: string
      }>('/api/v1/documents/apply-fix', {
        document_type: documentType,
        finding_id: findingId,
        fix_proposal: localProposal,
        debate_id: debateId,
        confirmed: true,
      })
      if (res.data.ok) {
        setApplied(true)
        const label = res.data.draft_label || 'Post-critic revision'
        setAppliedLabel(label)
        onApplied?.(label)
      } else {
        setError('Apply-fix returned a failure status.')
      }
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Apply-fix failed.'
      setError(String(msg))
    } finally {
      setApplyingBusy(false)
    }
  }

  const scopeLabel = localProposal?.target === 'document'
    ? 'Full document'
    : `Section -- ${localProposal?.section_name || '?'}`
  const isCross = (
    localProposal?.target_document
    && localProposal?.source_of_truth_document
    && localProposal.target_document
       !== localProposal.source_of_truth_document)

  return (
    <div
      className="rounded border border-electric/30 bg-electric/5
                 p-2 mt-2 space-y-1.5"
      data-testid={`fix-proposal-card-${findingId}`}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-2xs
                   text-electric font-semibold w-full text-left">
        {expanded
          ? <ChevronDown className="w-3 h-3" />
          : <ChevronRight className="w-3 h-3" />}
        <Wrench className="w-3 h-3" />
        {localProposal ? 'Proposed Fix' : 'Propose Fix'}
      </button>

      {expanded && (
        <>
          {applied && (
            <div className="text-2xs text-success flex items-start
                            gap-1.5">
              <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                Fix applied. A new draft is being generated:{' '}
                <strong>{appliedLabel}</strong>. The existing draft
                is preserved -- switch between versions in the
                editor toolbar.
              </span>
            </div>
          )}
          {!applied && !localProposal && (
            <div className="text-2xs text-slate-300 leading-relaxed">
              The arbiter can propose a story-plan patch to address
              this finding. The proposal is shown to you for
              review before any regeneration runs.
            </div>
          )}
          {!applied && !localProposal && (
            <button
              type="button"
              onClick={() => { void handlePropose() }}
              disabled={proposingBusy}
              data-testid={`propose-fix-${findingId}`}
              className="flex items-center gap-1.5 text-2xs
                          px-2 py-1 rounded bg-electric text-white
                          hover:bg-blue-500 disabled:opacity-50
                          disabled:cursor-not-allowed">
              {proposingBusy
                ? <><Loader2 className="w-3 h-3 animate-spin" />
                    Generating proposal…</>
                : <><Wrench className="w-3 h-3" /> Propose Fix</>}
            </button>
          )}
          {!applied && localProposal && (
            <>
              <div className="text-2xs space-y-1">
                <div>
                  <span className="text-muted">Scope:</span>{' '}
                  <span className="text-slate-200 font-semibold">
                    {scopeLabel}
                  </span>
                </div>
                {isCross && (
                  <div>
                    <span className="text-muted">
                      Source of truth:
                    </span>{' '}
                    <span className="text-slate-200">
                      {localProposal.source_of_truth_document}
                    </span>{' '}
                    <span className="text-muted">→ patching:</span>{' '}
                    <span className="text-slate-200">
                      {localProposal.target_document}
                    </span>
                  </div>
                )}
                <div className="text-slate-300 italic
                                leading-relaxed">
                  {localProposal.rationale}
                </div>
                <div className="text-slate-200 leading-relaxed">
                  <span className="text-electric font-semibold">
                    Patch:
                  </span>{' '}
                  {localProposal.patch_instruction}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setConfirmOpen(true)}
                disabled={applyingBusy}
                data-testid={`apply-fix-${findingId}`}
                className="flex items-center gap-1.5 text-2xs
                            px-2 py-1 rounded bg-warning
                            text-navy-900 hover:bg-amber-400
                            disabled:opacity-50
                            disabled:cursor-not-allowed">
                {applyingBusy
                  ? <><Loader2 className="w-3 h-3 animate-spin" />
                      Applying…</>
                  : <><Wrench className="w-3 h-3" /> Apply Fix</>}
              </button>
            </>
          )}
          {error && (
            <div className="text-2xs text-danger flex items-start
                            gap-1.5">
              <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </>
      )}

      {confirmOpen && localProposal && (
        <ApplyFixConfirmModal
          proposal={localProposal}
          isCross={!!isCross}
          documentType={documentType}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => { void handleApply() }} />
      )}
    </div>
  )
}


function ApplyFixConfirmModal(
  {
    proposal, isCross, documentType, onCancel, onConfirm,
  }: {
    proposal:     FixProposal
    isCross:      boolean
    documentType: string
    onCancel:     () => void
    onConfirm:    () => void
  },
): React.ReactElement {
  const scope = proposal.target === 'document'
    ? 'Full document'
    : `Section: "${proposal.section_name || '?'}"`
  const estimate = proposal.target === 'document'
    ? '60-120 seconds' : '15-30 seconds'
  const targetDocLabel = (
    isCross && proposal.target_document
      ? proposal.target_document
      : documentType)
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4"
      onClick={onCancel}
      data-testid="apply-fix-confirm-modal">
      <div
        className="card max-w-lg w-full p-5"
        onClick={(e) => e.stopPropagation()}>
        <h3 className="text-white font-semibold text-sm mb-3">
          {isCross
            ? `Apply Fix and Regenerate ${targetDocLabel}?`
            : 'Apply Fix and Regenerate?'}
        </h3>
        <div className="space-y-1.5 text-xs text-slate-300">
          <div>
            <span className="text-muted">Scope:</span>{' '}
            <strong>{scope}</strong>
          </div>
          <div>
            <span className="text-muted">Estimated time:</span>{' '}
            {estimate}
          </div>
          {isCross && (
            <div>
              <span className="text-muted">Source of truth:</span>{' '}
              <strong>
                {proposal.source_of_truth_document}
              </strong>
              {' '}<span className="text-muted">→ updating:</span>{' '}
              <strong>{proposal.target_document}</strong>
            </div>
          )}
          <div>
            <span className="text-muted">Rationale:</span>{' '}
            {proposal.rationale}
          </div>
          <div>
            <span className="text-muted">Change:</span>{' '}
            {proposal.patch_instruction}
          </div>
        </div>
        <p className="text-2xs text-muted italic mt-3
                      leading-relaxed">
          This will create a new draft version alongside your
          existing draft. Your current draft is preserved and you
          can switch between versions in the editor.
          {isCross && (
            <>
              {' '}
              The {proposal.source_of_truth_document} is not
              modified.
            </>
          )}
        </p>
        <div className="flex items-center justify-end gap-2 mt-4">
          <button
            type="button"
            onClick={onCancel}
            data-testid="apply-fix-confirm-cancel"
            className="px-3 py-1.5 rounded text-xs border
                       border-border text-muted hover:text-white
                       hover:bg-navy-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="apply-fix-confirm-apply"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-warning text-navy-900
                       hover:bg-amber-400">
            Apply and Regenerate
          </button>
        </div>
      </div>
    </div>
  )
}
