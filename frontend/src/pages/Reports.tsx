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
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  FileText, Presentation, Download, Loader2, Calendar, AlertCircle,
  CheckCircle, Clock, ArrowRight,
} from 'lucide-react'

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

function DeliverableCard({ card, onGenerate, isGenerating }: {
  card: ReportCard
  onGenerate: (c: ReportCard) => void
  isGenerating: boolean
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
    </div>
  )
}

export default function Reports() {
  const navigate = useNavigate()
  const [manifest, setManifest] = useState<ManifestResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [generatingId, setGeneratingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

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
      <div>
        <h1 className="text-xl font-semibold text-white">Reports & Deliverables</h1>
        <p className="text-sm text-muted mt-1">
          AI-drafted documents for the FNA 670 practicum. Every output is
          labelled <strong className="text-warning">AI DRAFT — REQUIRES HUMAN REVIEW</strong> —
          edit before submitting.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 rounded border border-danger/30 bg-danger/5 text-danger text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {loading && (
        <div className="card p-8 text-center text-muted text-sm">Loading deliverables…</div>
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
              {manifest.owner_bob.map((card) => (
                <DeliverableCard
                  key={card.id}
                  card={card}
                  onGenerate={handleGenerate}
                  isGenerating={generatingId === card.id}
                />
              ))}
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
                />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
