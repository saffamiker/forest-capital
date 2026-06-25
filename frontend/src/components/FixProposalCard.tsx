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
  ChevronRight, Check, X, FileEdit,
} from 'lucide-react'

import type {
  CriticFinding,
} from '../stores/academicReviewStore'


// June 25 2026 -- Copilot-style inline-edit flow shape. Returned by
// POST /api/v1/council/debates/{id}/propose-fix-text.
interface InlineFixSuggestion {
  finding_id:     number
  section_name:   string | null
  original_text:  string
  suggested_text: string
  proposal_id:    number
  cached?:        boolean
}


// Module-level mutex enforces 'one active suggestion at a time per
// document'. The active card's findingId is set on Preview; cleared
// on Accept / Reject / unmount. A second Preview prompts a toast
// via the rejection path instead of stacking suggestions.
const ACTIVE_SUGGESTION_REGISTRY: {
  findingId: number | null
  documentType: string | null
} = { findingId: null, documentType: null }


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

  // Inline-edit flow state. suggestion holds the diff payload from
  // /propose-fix-text; inlineBusy covers both fetch + accept calls.
  // rejected swaps the card to a 'Re-propose' state on Reject.
  const [suggestion, setSuggestion] =
    useState<InlineFixSuggestion | null>(null)
  const [inlineBusy, setInlineBusy] = useState(false)
  const [rejected, setRejected] = useState(false)
  const [inlineApplied, setInlineApplied] = useState(false)

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

  // Copilot-style inline edit: fetch the section-scoped diff.
  const handlePreviewInline = async (): Promise<void> => {
    if (!debateId) {
      setError('No debate id -- cannot preview inline edit.')
      return
    }
    // One active suggestion per document. A second preview while
    // another is active surfaces a soft error rather than
    // stacking suggestions.
    if (
      ACTIVE_SUGGESTION_REGISTRY.findingId !== null
      && ACTIVE_SUGGESTION_REGISTRY.findingId !== findingId
      && ACTIVE_SUGGESTION_REGISTRY.documentType === documentType
    ) {
      setError(
        'Accept or reject the current suggestion before previewing '
        + 'another inline edit.')
      return
    }
    setInlineBusy(true)
    setError(null)
    try {
      const res = await axios.post<InlineFixSuggestion>(
        `/api/v1/council/debates/${debateId}/propose-fix-text`,
        { finding_id: findingId })
      setSuggestion(res.data)
      ACTIVE_SUGGESTION_REGISTRY.findingId = findingId
      ACTIVE_SUGGESTION_REGISTRY.documentType = documentType
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Could not preview inline edit.'
      setError(String(msg))
    } finally {
      setInlineBusy(false)
    }
  }

  const handleRejectInline = (): void => {
    setSuggestion(null)
    setRejected(true)
    ACTIVE_SUGGESTION_REGISTRY.findingId = null
    ACTIVE_SUGGESTION_REGISTRY.documentType = null
  }

  // Accept = PATCH the current draft with content_text rewritten
  // (original swapped for suggested), then mark fix_applied on
  // council_debates. The frontend reloads the draft afterwards via
  // the parent's onApplied callback so the editor reflects the
  // change immediately. Caveats:
  //   - The string replace runs against content_text. content_json
  //     is regenerated server-side by the next save via the
  //     existing tooling that derives content_json from
  //     content_text on PATCH. If that derivation is absent for a
  //     given doc type, the diff still reflects content_text -- the
  //     editor renders content_json which would be stale until the
  //     next regen. Acceptable MVP behaviour for the brief / script
  //     / appendix; the deck (canvas) doesn't surface this flow.
  const handleAcceptInline = async (): Promise<void> => {
    if (!suggestion || !debateId) return
    setInlineBusy(true)
    setError(null)
    try {
      // 1. Fetch the current draft + patch content_text.
      const draftsRes = await axios.get<{
        drafts: Array<{
          id: number; document_type: string
          is_current?: boolean
          content_text: string | null
        }>
      }>('/api/v1/documents/drafts')
      const draft = (draftsRes.data?.drafts ?? []).find(
        (d) => d.document_type === documentType
          && d.is_current !== false)
      if (!draft) {
        setError('No current draft to patch.')
        setInlineBusy(false)
        return
      }
      const ct = draft.content_text || ''
      if (!ct.includes(suggestion.original_text)) {
        setError(
          'The original text could not be located in the current '
          + 'draft -- it may have been edited since the suggestion '
          + 'was generated. Re-preview to refresh.')
        setInlineBusy(false)
        return
      }
      const newContentText = ct.replace(
        suggestion.original_text, suggestion.suggested_text)
      await axios.patch(
        `/api/v1/documents/drafts/${draft.id}`,
        { content_text: newContentText })

      // 2. Mark fix_applied on council_debates.
      await axios.post(
        `/api/v1/council/debates/${debateId}/accept-fix-text`,
        { finding_id: findingId, new_draft_id: draft.id })

      setInlineApplied(true)
      setSuggestion(null)
      ACTIVE_SUGGESTION_REGISTRY.findingId = null
      ACTIVE_SUGGESTION_REGISTRY.documentType = null
      onApplied?.(`Section ${suggestion.section_name || ''} updated`)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Could not accept the inline edit.'
      setError(String(msg))
    } finally {
      setInlineBusy(false)
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
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  onClick={() => setConfirmOpen(true)}
                  disabled={applyingBusy || inlineApplied}
                  data-testid={`apply-fix-${findingId}`}
                  className="flex items-center gap-1.5 text-2xs
                              px-2 py-1 rounded bg-warning
                              text-navy-900 hover:bg-amber-400
                              disabled:opacity-50
                              disabled:cursor-not-allowed">
                  {applyingBusy
                    ? <><Loader2 className="w-3 h-3 animate-spin" />
                        Applying…</>
                    : <><Wrench className="w-3 h-3" /> Apply Fix
                        (regenerate)</>}
                </button>
                {!inlineApplied && (
                  <button
                    type="button"
                    onClick={() => { void handlePreviewInline() }}
                    disabled={inlineBusy || suggestion !== null}
                    data-testid={`preview-inline-fix-${findingId}`}
                    title="Preview a section-scoped text edit you can accept or reject before any regeneration runs."
                    className="flex items-center gap-1.5 text-2xs
                                px-2 py-1 rounded
                                border border-electric/50
                                text-electric hover:bg-electric/10
                                disabled:opacity-50
                                disabled:cursor-not-allowed">
                    {inlineBusy
                      ? <><Loader2 className="w-3 h-3 animate-spin" />
                          Building diff…</>
                      : (rejected
                          ? <><FileEdit className="w-3 h-3" />
                              Re-propose Inline Edit</>
                          : <><FileEdit className="w-3 h-3" />
                              Preview Inline Edit</>)}
                  </button>
                )}
              </div>

              {/* Copilot-style diff panel. Shows the affected
                  section's existing prose with a red strikethrough,
                  then the suggested replacement in green. The
                  Accept / Reject buttons live inside the panel
                  rather than the parent so the user's eye stays on
                  the diff while making the decision. */}
              {suggestion && (
                <div
                  data-testid={`inline-fix-diff-${findingId}`}
                  className="mt-2 rounded border border-electric/40
                             bg-navy-900/50 p-2 space-y-2">
                  <div className="text-2xs text-muted">
                    Section: <span className="text-slate-200">
                      {suggestion.section_name || '(full document)'}
                    </span>
                    {suggestion.cached && (
                      <span className="ml-2 text-2xs
                                       text-muted italic">
                        (cached)
                      </span>
                    )}
                  </div>
                  <div className="text-2xs">
                    <div className="text-danger/80 font-semibold
                                    mb-0.5">
                      Original
                    </div>
                    <pre className="whitespace-pre-wrap text-danger
                                    line-through bg-danger/5
                                    border border-danger/20
                                    rounded p-1.5 max-h-40
                                    overflow-y-auto font-sans"
                      data-testid={`inline-fix-original-${findingId}`}>
                      {suggestion.original_text}
                    </pre>
                  </div>
                  <div className="text-2xs">
                    <div className="text-success font-semibold
                                    mb-0.5">
                      Suggested replacement
                    </div>
                    <pre className="whitespace-pre-wrap text-success
                                    bg-success/5 border
                                    border-success/30 rounded p-1.5
                                    max-h-40 overflow-y-auto
                                    font-sans"
                      data-testid={`inline-fix-suggested-${findingId}`}>
                      {suggestion.suggested_text}
                    </pre>
                  </div>
                  <div className="flex gap-1.5">
                    <button type="button"
                      onClick={() => { void handleAcceptInline() }}
                      disabled={inlineBusy}
                      data-testid={`accept-inline-fix-${findingId}`}
                      className="flex items-center gap-1.5 text-2xs
                                 px-2 py-1 rounded bg-success
                                 text-navy-900 hover:bg-green-400
                                 disabled:opacity-50">
                      {inlineBusy
                        ? <><Loader2 className="w-3 h-3
                                                 animate-spin" />
                            Applying…</>
                        : <><Check className="w-3 h-3" />
                            Accept</>}
                    </button>
                    <button type="button"
                      onClick={handleRejectInline}
                      disabled={inlineBusy}
                      data-testid={`reject-inline-fix-${findingId}`}
                      className="flex items-center gap-1.5 text-2xs
                                 px-2 py-1 rounded border
                                 border-danger/50 text-danger
                                 hover:bg-danger/10
                                 disabled:opacity-50">
                      <X className="w-3 h-3" />
                      Reject
                    </button>
                  </div>
                </div>
              )}

              {inlineApplied && (
                <div
                  data-testid={`inline-fix-applied-${findingId}`}
                  className="text-2xs text-success flex items-start
                             gap-1.5 mt-1.5">
                  <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>
                    Inline edit applied. The draft now reflects the
                    accepted change; the finding is marked resolved.
                  </span>
                </div>
              )}
              {rejected && !suggestion && !inlineApplied && (
                <div
                  data-testid={`inline-fix-rejected-${findingId}`}
                  className="text-2xs text-muted italic mt-1">
                  Suggestion rejected. Re-propose to try another
                  edit.
                </div>
              )}
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
