/**
 * FixProposalCard -- June 27 2026.
 *
 * The per-finding fix proposal block that renders inside the
 * critic findings panel for Fatal + Major severity. Two shapes:
 *
 *   Fatal (auto_proposed=true)
 *     The arbiter already generated a proposal during the debate
 *     round. The card is pre-populated; the user only sees the
 *     "Apply Fix" button.
 *
 *   Major (auto_proposed=false)
 *     The card shows a "Propose Fix" button that calls
 *     /api/v1/documents/propose-fix on click. Once the proposal
 *     lands, the card morphs to the Fatal shape with an Apply Fix
 *     button.
 *
 * Minor findings render no card -- they're not eligible for
 * fix-proposal injection.
 *
 * June 27 2026 -- the legacy regenerate path is gone. Clicking
 * Apply Fix now ALWAYS runs the surgical splice flow:
 *   1. /propose-fix-text -- backend extracts the flagged section
 *      from content_json, Sonnet-patches it, returns a preview
 *      (no draft mutation).
 *   2. User reviews the diff inline (original vs suggested).
 *   3. Accept -> /accept-fix-text -- backend splices the patched
 *      section back into content_json at the same position, writes
 *      via update_draft. All other sections preserved verbatim.
 *   4. Reject -> discard the preview, no changes made.
 *
 * If the section can't be located in the current draft, the
 * backend returns a 422/409 with a structured detail; we surface
 * an "Open in editor" deep-link instead of falling back to any
 * regeneration. The Apply-Fix-and-Regenerate confirmation modal
 * + the full-document regen path have been removed.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import axios from 'axios'
import {
  Wrench, Loader2, AlertCircle, CheckCircle, ChevronDown,
  ChevronRight, Check, X, ExternalLink,
} from 'lucide-react'

import type {
  CriticFinding,
} from '../stores/academicReviewStore'


// June 27 2026 -- the suggestion shape returned by POST
// /api/v1/council/debates/{id}/propose-fix-text. original_text +
// suggested_text are PLAIN-TEXT renderings of the located JSON
// section, intended only for the diff display. The actual content_
// json splice is cached server-side and written via
// /accept-fix-text; the frontend no longer round-trips
// content_text.
interface InlineFixSuggestion {
  finding_id:     number
  section_name:   string | null
  original_text:  string
  suggested_text: string
  proposal_id:    number
  cached?:        boolean
  document_type?: string
}

// Structured error detail returned by the inline-fix endpoints
// when the section can't be located / preview / splice fails.
// Surfaced as an actionable "Open in editor" link instead of any
// regenerate fallback.
interface InlineFixErrorDetail {
  detail:        string
  hint?:         string | null
  section_name?: string | null
  document_type?: string | null
}

// Pull the structured detail out of an axios error response. The
// backend sends either {detail: <string>} or {detail: <object>}
// depending on the failure mode.
function _parseInlineErr(
  err: unknown,
): { message: string; structured?: InlineFixErrorDetail } {
  if (!axios.isAxiosError(err)) {
    return { message: String(err) }
  }
  const d = err.response?.data?.detail
  if (typeof d === 'string') return { message: d }
  if (d && typeof d === 'object' && 'detail' in d) {
    const so = d as InlineFixErrorDetail
    return { message: so.detail, structured: so }
  }
  return { message: err.message || 'Inline fix failed.' }
}

// Find a current draft id for a document type so we can build a
// deep-link to the editor when the inline patch can't be applied.
// Returns null when the draft isn't loaded / no current draft.
async function _findCurrentDraftId(
  documentType: string,
): Promise<number | null> {
  try {
    const res = await axios.get<{
      drafts: Array<{
        id: number; document_type: string; is_current?: boolean
      }>
    }>('/api/v1/documents/drafts')
    const draft = (res.data?.drafts ?? []).find(
      (d) => d.document_type === documentType
        && d.is_current !== false)
    return draft ? draft.id : null
  } catch {
    return null
  }
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
  const [proposingBusy, setProposingBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // June 27 2026 -- structured error from the inline-fix endpoints
  // when the section can't be located. Carries detail / hint /
  // section_name so we render an actionable editor deep-link
  // instead of a generic toast + no fallback to regeneration.
  const [errorDetail, setErrorDetail] =
    useState<InlineFixErrorDetail | null>(null)
  // The editor deep-link target the structured error resolves to.
  const [editorLink, setEditorLink] =
    useState<{ href: string; label: string } | null>(null)

  // Inline-edit flow state. suggestion holds the diff payload from
  // /propose-fix-text; inlineBusy covers both fetch + accept calls.
  // rejected swaps the card to a 'Re-propose' state on Reject.
  const [suggestion, setSuggestion] =
    useState<InlineFixSuggestion | null>(null)
  const [inlineBusy, setInlineBusy] = useState(false)
  const [rejected, setRejected] = useState(false)
  const [inlineApplied, setInlineApplied] = useState(false)

  // Surface a structured 422/409 from the inline-fix endpoints
  // with an actionable editor deep-link. Replaces every previous
  // "regenerate" code path -- the user always edits directly when
  // the inline splice can't apply.
  const _surfaceInlineError = async (
    err: unknown, fallbackMsg: string,
  ): Promise<void> => {
    const parsed = _parseInlineErr(err)
    setError(parsed.message || fallbackMsg)
    setErrorDetail(parsed.structured ?? null)
    if (parsed.structured?.section_name) {
      const docType = (
        parsed.structured.document_type || documentType)
      const draftId = await _findCurrentDraftId(docType)
      if (draftId) {
        setEditorLink({
          href: `/editor/${draftId}`,
          label: `Open ${
            parsed.structured.section_name} in editor`,
        })
        return
      }
    }
    setEditorLink(null)
  }

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

  // June 27 2026 -- handleApply REMOVED. The legacy "Apply Fix
  // and Regenerate" path that POSTed /apply-fix and kicked off a
  // full document regen is gone. Apply Fix now ALWAYS runs the
  // surgical splice flow below (propose -> preview -> accept).

  // Step 1 of Apply Fix -- fetch the section-scoped preview from
  // /propose-fix-text. Backend extracts the section from
  // content_json, Sonnet-patches it, returns the original +
  // suggested as plain text for the diff display. The patched
  // JSON is cached server-side; accept-fix-text reads it back to
  // perform the splice. Nothing in the draft mutates here.
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
    setErrorDetail(null)
    setEditorLink(null)
    try {
      const res = await axios.post<InlineFixSuggestion>(
        `/api/v1/council/debates/${debateId}/propose-fix-text`,
        { finding_id: findingId })
      setSuggestion(res.data)
      ACTIVE_SUGGESTION_REGISTRY.findingId = findingId
      ACTIVE_SUGGESTION_REGISTRY.documentType = documentType
    } catch (err) {
      await _surfaceInlineError(
        err, 'Could not preview inline edit.')
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

  // Step 3 of Apply Fix -- POST /accept-fix-text and let the
  // backend splice the cached suggested_section_json into the
  // CURRENT draft's content_json. The backend re-locates the
  // section in current state (so any manual edits between preview
  // and accept are respected), splices, writes via update_draft.
  // Frontend no longer touches content_text or content_json
  // directly -- one POST and we're done.
  //
  // June 27 2026 -- replaces the previous PATCH /drafts/{id} with
  // content_text string-replace approach that could silently
  // overwrite the whole document when the section couldn't be
  // located cleanly.
  const handleAcceptInline = async (): Promise<void> => {
    if (!suggestion || !debateId) return
    setInlineBusy(true)
    setError(null)
    setErrorDetail(null)
    setEditorLink(null)
    try {
      const res = await axios.post<{
        ok: boolean
        new_draft_id: number
        section_name: string | null
        applied_at: string
      }>(
        `/api/v1/council/debates/${debateId}/accept-fix-text`,
        { finding_id: findingId })
      setInlineApplied(true)
      setSuggestion(null)
      ACTIVE_SUGGESTION_REGISTRY.findingId = null
      ACTIVE_SUGGESTION_REGISTRY.documentType = null
      onApplied?.(
        `Section ${res.data.section_name || ''} updated`.trim())
    } catch (err) {
      await _surfaceInlineError(
        err, 'Could not accept the inline edit.')
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
          {/* June 27 2026 -- removed the "applied + regenerating"
              status block. Apply Fix is now a synchronous splice
              (no async draft regen), so the success state lives
              on inlineApplied below in the diff-panel branch. */}
          {!localProposal && (
            <div className="text-2xs text-slate-300 leading-relaxed">
              The arbiter can propose a section-scoped patch to
              address this finding. The proposal is shown to you
              for review; applying splices the patched section
              into the draft (no full-document regeneration).
            </div>
          )}
          {!localProposal && (
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
          {localProposal && (
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
                {/* June 27 2026 -- the legacy "Apply Fix
                    (regenerate)" button + confirmation modal have
                    been removed. Apply Fix now ALWAYS runs the
                    surgical splice flow: preview the patched
                    section, then Accept (write) or Reject
                    (discard). No regeneration under any
                    circumstance. */}
                {!inlineApplied && (
                  <button
                    type="button"
                    onClick={() => { void handlePreviewInline() }}
                    disabled={inlineBusy || suggestion !== null}
                    data-testid={`apply-fix-${findingId}`}
                    title="Preview a section-scoped patch you can accept or reject. No full-document regeneration."
                    className="flex items-center gap-1.5 text-2xs
                                px-2 py-1 rounded bg-warning
                                text-navy-900 hover:bg-amber-400
                                disabled:opacity-50
                                disabled:cursor-not-allowed">
                    {inlineBusy
                      ? <><Loader2 className="w-3 h-3 animate-spin" />
                          Building patch…</>
                      : (rejected
                          ? <><Wrench className="w-3 h-3" />
                              Re-preview Fix</>
                          : <><Wrench className="w-3 h-3" />
                              Apply Fix</>)}
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
            <div className="text-2xs text-danger flex flex-col
                            gap-1.5"
              data-testid={`apply-fix-error-${findingId}`}>
              <div className="flex items-start gap-1.5">
                <AlertCircle
                  className="w-3 h-3 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
              {errorDetail?.hint && (
                <div className="text-2xs text-muted italic ml-4.5">
                  {errorDetail.hint}
                </div>
              )}
              {editorLink && (
                <Link
                  to={editorLink.href}
                  data-testid={
                    `apply-fix-editor-link-${findingId}`}
                  className="ml-4.5 inline-flex items-center
                             gap-1 text-2xs text-electric
                             hover:text-electric/80 underline">
                  <ExternalLink className="w-3 h-3" />
                  {editorLink.label}
                </Link>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
