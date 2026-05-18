/**
 * frontend/src/pages/Reports.tsx
 *
 * Sprint 6 — Reports screen. Two deliverable sections: Bob's written
 * documents (analytical appendix, executive brief, midpoint paper) and
 * Molly's presentation artifacts (storyboard, deck, Q&A).
 *
 * Each card represents a generator endpoint defined server-side at
 * /api/reports/manifest. The manifest is the source of truth for
 * which deliverables exist and what status each is in. Changing the
 * card list never requires a frontend redeploy — just edit the
 * manifest response in backend/main.py.
 *
 * Status semantics:
 *   available — endpoint is wired and ready. Card has an active
 *               Generate button that triggers the download.
 *   planned   — endpoint will exist by July 1 but the generator isn't
 *               wired yet. Button is disabled with a tooltip explaining
 *               the deadline.
 */
import { useEffect, useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import axios from 'axios'
import {
  FileText, Presentation, Download, Loader2, Calendar, AlertCircle,
  CheckCircle, Clock, ArrowRight, GraduationCap, Edit3, Info, FileArchive,
} from 'lucide-react'
import AdvisorPanel from '../components/AdvisorPanel'
import TeamActivityPanel from '../components/TeamActivityPanel'
import AcademicExportModal from '../components/AcademicExportModal'
import DocumentGenerationPanel from '../components/DocumentGenerationPanel'
import SubmissionGuides from '../components/SubmissionGuides'
import TeamGate from '../components/TeamGate'
import type { DeliverableType } from '../types/advisor'
import type { SectionDocType } from '../types/documents'

interface ReportCard {
  id: string
  title: string
  description: string
  endpoint: string
  method: 'GET' | 'POST'
  format: 'docx' | 'pptx' | 'html' | 'json'
  status: 'available' | 'planned'
  deadline: string
}

interface ManifestResponse {
  owner_bob: ReportCard[]
  owner_molly: ReportCard[]
}

// Map deliverable id → icon. Keeps the card list above readable.
const ICON_FOR_ID: Record<string, typeof FileText> = {
  midpoint_template:    FileText,
  executive_brief:      FileText,
  analytical_appendix:  FileText,
  storyboard_draft:     Presentation,
  presentation_deck:    Presentation,
  qa_preparation:       FileText,
}

// Map deliverable id → Academic Advisor deliverable type. Both presentation
// artifacts (deck, Q&A, storyboard) share the same advisor context — the
// advisor doesn't need separate prompts for each Molly artifact, it just
// needs to know we're prepping for the final presentation.
const ADVISOR_TYPE_FOR_ID: Record<string, DeliverableType> = {
  midpoint_template:    'midpoint',
  executive_brief:      'brief',
  analytical_appendix:  'appendix',
  storyboard_draft:     'presentation',
  presentation_deck:    'presentation',
  qa_preparation:       'presentation',
}

// Map Bob's three deliverable IDs to the section-doc type the SectionEditor
// expects. Other IDs (Molly's) don't open in the section editor — they
// either route to the Storyboard Editor or trigger a direct download.
const SECTION_DOC_TYPE_FOR_ID: Record<string, SectionDocType | undefined> = {
  midpoint_template:    'midpoint_paper',
  executive_brief:      'executive_brief',
  analytical_appendix:  'analytical_appendix',
}

// The StoryboardEditor writes the active document_id here so deck / Q&A
// generators on this page can target the right document. When unset (no
// storyboard yet) we route the user to the editor first.
const STORYBOARD_ID_KEY = 'fc_active_storyboard_id'

async function downloadFromGenerator(outputType: 'deck' | 'qa'): Promise<void> {
  const docId = localStorage.getItem(STORYBOARD_ID_KEY)
  if (!docId) {
    throw new Error(
      'No storyboard saved yet. Open the Storyboard Editor first to create one.',
    )
  }
  const res = await axios({
    url: `/api/reports/generate-from-storyboard/${docId}`,
    method: 'POST',
    responseType: 'blob',
    data: { output_type: outputType },
  })
  const dispo = String(res.headers['content-disposition'] ?? '')
  const filenameMatch = /filename="?([^";]+)"?/i.exec(dispo)
  const fmt = outputType === 'deck' ? 'pptx' : 'docx'
  const filename = filenameMatch?.[1] ?? `forest-capital-${outputType}.${fmt}`
  const contentType = String(res.headers['content-type'] ?? 'application/octet-stream')
  const blob = new Blob([res.data], { type: contentType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}


async function downloadDocxResponse(card: ReportCard): Promise<void> {
  // axios responseType: 'blob' is required for the browser to download
  // binary content rather than try to parse as JSON and corrupt it.
  // The Content-Disposition header from the server carries the filename;
  // we extract it so the user's download folder is named meaningfully.
  const res = await axios({
    url: card.endpoint,
    method: card.method,
    responseType: 'blob',
  })
  // axios headers can be string | number | boolean | string[] | AxiosHeaders.
  // The .docx server responses we care about always set these as strings,
  // but TS demands we narrow before passing to RegExp.exec / Blob({ type }).
  const dispo = String(res.headers['content-disposition'] ?? '')
  const filenameMatch = /filename="?([^";]+)"?/i.exec(dispo)
  const filename = filenameMatch?.[1] ?? `${card.id}.${card.format}`

  const contentType = String(res.headers['content-type'] ?? 'application/octet-stream')
  const blob = new Blob([res.data], { type: contentType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function DeliverableCard({ card, onGenerate, isGenerating, onAdvisor, onOpenSectionEditor }: {
  card: ReportCard
  onGenerate: (c: ReportCard) => void
  isGenerating: boolean
  // Opens the Academic Advisor pre-pinned to this card's deliverable type.
  // The button below the Generate button surfaces grade-aware guidance
  // before the team commits to a draft.
  onAdvisor: (c: ReportCard) => void
  // Bob's section editor — only wired for the three doc types that
  // have a SECTION_DOC_TYPE_FOR_ID entry. Molly's cards pass undefined
  // and the button doesn't render.
  onOpenSectionEditor?: (c: ReportCard) => void
}) {
  const Icon = ICON_FOR_ID[card.id] ?? FileText
  const isAvailable = card.status === 'available'
  // Storyboard draft opens the editor rather than triggering a download —
  // the JSON payload is meant to be edited, not saved to disk.
  const opensEditor = card.id === 'storyboard_draft'

  return (
    <div className="card p-4 flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <div
          className="w-9 h-9 rounded flex items-center justify-center shrink-0"
          style={{
            background: isAvailable ? '#3b82f620' : '#64748b20',
            border: `1px solid ${isAvailable ? '#3b82f640' : '#64748b40'}`,
          }}
        >
          <Icon
            className="w-4 h-4"
            style={{ color: isAvailable ? '#3b82f6' : '#64748b' }}
          />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-white font-semibold text-sm">{card.title}</h3>
          <p className="text-muted text-xs mt-1 leading-relaxed">{card.description}</p>
        </div>
      </div>

      <div className="flex items-center gap-2 text-2xs flex-wrap">
        <span className="px-1.5 py-0.5 rounded border border-border text-muted uppercase tracking-wide">
          {card.format}
        </span>
        <span className="flex items-center gap-1 text-muted">
          <Calendar className="w-3 h-3" />
          {card.deadline}
        </span>
        {isAvailable ? (
          <span className="flex items-center gap-1 text-success">
            <CheckCircle className="w-3 h-3" />
            Available
          </span>
        ) : (
          <span className="flex items-center gap-1 text-muted">
            <Clock className="w-3 h-3" />
            Planned
          </span>
        )}
      </div>

      <button
        type="button"
        onClick={() => isAvailable && onGenerate(card)}
        disabled={!isAvailable || isGenerating}
        className={`w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded text-xs font-medium transition-colors ${
          isAvailable
            ? 'bg-electric/10 border border-electric/30 text-electric hover:bg-electric/20'
            : 'bg-navy-800 border border-border text-muted cursor-not-allowed'
        }`}
        title={isAvailable ? `Generate ${card.format.toUpperCase()}` : 'Planned for next sprint'}
      >
        {isGenerating
          ? <><Loader2 className="w-3 h-3 animate-spin" /> Generating…</>
          : isAvailable
            ? (opensEditor
                ? <><ArrowRight className="w-3 h-3" /> Open Editor</>
                : <><Download className="w-3 h-3" /> Generate {card.format.toUpperCase()}</>)
            : <>Planned</>}
      </button>

      {/* Edit in Section Editor — only for Bob's three section-structured
          deliverables. Opens the in-browser editor where Bob can edit each
          section against the immutable AI draft. */}
      {onOpenSectionEditor && isAvailable && (
        <button
          type="button"
          onClick={() => onOpenSectionEditor(card)}
          data-testid={`edit-button-${card.id}`}
          className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded text-xs border border-border text-slate-300 hover:bg-navy-700 transition-colors"
          title="Open this draft in the section editor"
        >
          <Edit3 className="w-3 h-3" />
          Edit in Section Editor
        </button>
      )}

      {/* Get Advisor Feedback — gold accent, always available regardless of
          card status. The team should be able to consult the advisor on a
          deliverable even when its generator isn't wired yet. */}
      <button
        type="button"
        onClick={() => onAdvisor(card)}
        data-testid={`advisor-button-${card.id}`}
        className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded text-xs transition-colors"
        style={{
          backgroundColor: 'rgba(245,158,11,0.08)',
          border: '1px solid rgba(245,158,11,0.3)',
          color: '#f59e0b',
        }}
        title="Open Academic Advisor with this deliverable preselected"
      >
        <GraduationCap className="w-3 h-3" />
        Get Advisor Feedback
      </button>
    </div>
  )
}

export default function Reports() {
  const navigate = useNavigate()
  const [manifest, setManifest] = useState<ManifestResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [generatingId, setGeneratingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Active deliverable for the advisor panel — null when the panel is
  // closed, set to a DeliverableType when a card's "Get Advisor Feedback"
  // button is clicked. We use the controlled-open form of AdvisorPanel
  // here so the panel opens to the right card's context rather than the
  // default midpoint deliverable that the floating button would pick.
  const [advisorDeliverable, setAdvisorDeliverable] = useState<DeliverableType | null>(null)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    let cancelled = false
    void axios.get<ManifestResponse>('/api/reports/manifest')
      .then((res) => { if (!cancelled) setManifest(res.data) })
      .catch((err) => {
        if (cancelled) return
        const msg = axios.isAxiosError(err)
          ? (err.response?.data?.detail ?? err.message)
          : 'Failed to load reports manifest'
        setError(String(msg))
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const handleAdvisor = (card: ReportCard) => {
    const dt = ADVISOR_TYPE_FOR_ID[card.id] ?? 'presentation'
    setAdvisorDeliverable(dt)
  }

  const handleOpenSectionEditor = async (card: ReportCard) => {
    const docType = SECTION_DOC_TYPE_FOR_ID[card.id]
    if (!docType) return
    setGeneratingId(card.id)
    setError(null)
    try {
      // Create a new section-structured draft for this deliverable.
      // The backend returns the document_id and the AI-drafted content;
      // we navigate to the SectionEditor which loads it via GET.
      const res = await axios.post<{
        document_id: string | null
        persistence: 'saved' | 'unavailable'
      }>('/api/documents/section-doc/draft', { doc_type: docType })
      if (res.data.document_id) {
        navigate(`/reports/document/${res.data.document_id}`)
      } else {
        setError(
          'Section editor requires database persistence. ' +
          'Run `alembic upgrade head` on the server first.',
        )
      }
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to open section editor'
      setError(String(msg))
    } finally {
      setGeneratingId(null)
    }
  }

  const handleGenerate = async (card: ReportCard) => {
    // Storyboard draft opens the editor, not a download. The editor
    // calls /api/documents/storyboard/draft on first mount.
    if (card.id === 'storyboard_draft') {
      navigate('/reports/storyboard')
      return
    }

    setGeneratingId(card.id)
    setError(null)
    try {
      // Deck and Q&A cards call generate-from-storyboard with a specific
      // output_type. They depend on a saved storyboard — if there isn't
      // one yet, the server returns 404 and we surface the error so the
      // user goes to the editor first.
      if (card.id === 'presentation_deck' || card.id === 'qa_preparation') {
        await downloadFromGenerator(card.id === 'presentation_deck' ? 'deck' : 'qa')
      } else {
        await downloadDocxResponse(card)
      }
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Generation failed'
      setError(String(msg))
    } finally {
      setGeneratingId(null)
    }
  }

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-screen-xl mx-auto">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">Reports & Deliverables</h1>
          <p className="text-sm text-muted mt-1">
            AI-drafted documents for the FNA 670 practicum. Every output is
            labelled <strong className="text-warning">AI DRAFT — REQUIRES HUMAN REVIEW</strong> —
            edit before submitting.
          </p>
        </div>
        {/* Academic Export Package — light-mode charts + CSV tables zipped
            for paper submission. A team action. */}
        <TeamGate permission="export_package"
          tooltip="Exporting the academic package is available to the project team">
          <button
            type="button"
            onClick={() => setExporting(true)}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold
                       bg-electric text-white hover:bg-blue-500 transition-colors shrink-0"
          >
            <FileArchive className="w-4 h-4" />
            Export Academic Package
          </button>
        </TeamGate>
      </div>

      {exporting && <AcademicExportModal onClose={() => setExporting(false)} />}

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 rounded border border-danger/30 bg-danger/5 text-danger text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {loading && (
        <div className="card p-8 text-center text-muted text-sm">Loading deliverables…</div>
      )}

      {/* Generate Documents — one-click first-draft .docx / .pptx of the
          three graded deliverables, assembled server-side from real
          platform data. Sits above Team Activity per the spec. */}
      <DocumentGenerationPanel />

      {/* Submission Guides — the editor-based workflow for the midpoint
          paper (Bob) and the final presentation (Molly). */}
      <SubmissionGuides />

      {/* Team Activity — the evidence behind the Roles & Division-of-Labor
          deliverable and the AI-use narrative, so it leads the page.
          Independent of the deliverables manifest — renders regardless of
          whether the manifest loaded. */}
      <TeamActivityPanel />

      {/* Academic documents moved to Settings (commit 5/7). A muted info
          banner points there; the hash anchor scrolls to the section. */}
      <div className="flex items-start gap-2 px-3 py-2.5 rounded border border-border
                      bg-navy-800 text-muted text-xs">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Project requirements and agent context documents are managed in{' '}
          <Link
            to="/settings#academic-documents"
            className="text-electric hover:underline"
          >
            Settings
          </Link>
          . Documents uploaded there are automatically injected into all AI
          agent sessions.
        </span>
      </div>

      {/* Controlled advisor panel — opened from a card's "Get Advisor Feedback"
          button with the card's deliverable type preselected. The global
          floating advisor (mounted in MainLayout) remains visible alongside
          this; opening either one is equivalent from the user's perspective.
          We use the controlled API here so the panel knows which deliverable
          to pin to, which the floating button can't infer. */}
      {advisorDeliverable && (
        <AdvisorPanel
          initialDeliverable={advisorDeliverable}
          open
          onClose={() => setAdvisorDeliverable(null)}
        />
      )}

      {!loading && manifest && (
        <>
          <section>
            <div className="flex items-baseline gap-3 mb-3">
              <h2 className="text-white font-semibold text-sm">
                Bob's Deliverables
              </h2>
              <span className="text-2xs text-muted uppercase tracking-wide">
                Written report · APA 7th edition
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {manifest.owner_bob.map((card) => {
                // Only Bob's section-structured deliverables get the
                // editor entry point. Cards whose ID isn't in
                // SECTION_DOC_TYPE_FOR_ID omit the prop entirely —
                // exactOptionalPropertyTypes disallows passing undefined
                // explicitly when the type is `((c) => void) | undefined`.
                const supportsSectionEditor = !!SECTION_DOC_TYPE_FOR_ID[card.id]
                return supportsSectionEditor ? (
                  <DeliverableCard
                    key={card.id}
                    card={card}
                    onGenerate={handleGenerate}
                    isGenerating={generatingId === card.id}
                    onAdvisor={handleAdvisor}
                    onOpenSectionEditor={(c) => void handleOpenSectionEditor(c)}
                  />
                ) : (
                  <DeliverableCard
                    key={card.id}
                    card={card}
                    onGenerate={handleGenerate}
                    isGenerating={generatingId === card.id}
                    onAdvisor={handleAdvisor}
                  />
                )
              })}
            </div>
          </section>

          <section>
            <div className="flex items-baseline gap-3 mb-3">
              <h2 className="text-white font-semibold text-sm">
                Molly's Deliverables
              </h2>
              <span className="text-2xs text-muted uppercase tracking-wide">
                Presentation · July 1 demo
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {manifest.owner_molly.map((card) => (
                <DeliverableCard
                  key={card.id}
                  card={card}
                  onGenerate={handleGenerate}
                  isGenerating={generatingId === card.id}
                  onAdvisor={handleAdvisor}
                />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
