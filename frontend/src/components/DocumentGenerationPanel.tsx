/**
 * DocumentGenerationPanel — the "Generate Documents" section on the
 * Reports screen.
 *
 * Three cards, one per graded deliverable, each backed by a server-side
 * generation endpoint that assembles a first-draft .docx / .pptx from
 * real platform data, light-mode charts and AI-written narrative:
 *
 *   Midpoint Submission Paper  → POST /api/v1/export/midpoint-paper
 *   Executive Brief            → POST /api/v1/export/executive-brief
 *   Final Presentation Deck    → POST /api/v1/export/presentation-deck
 *
 * The midpoint paper and the deck also load the generated content into
 * an editor draft (the endpoint returns the new draft id in the
 * X-Draft-Id header); the card then offers Open in Editor as the
 * primary CTA, with Download as a secondary action. The downloaded
 * files are FIRST DRAFTS — every one carries the AI DRAFT banner.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  FileText, Presentation, Download, Loader2, AlertCircle, CheckCircle,
  PenLine,
} from 'lucide-react'
import TeamGate from './TeamGate'

interface DocSpec {
  id: string
  title: string
  description: string
  endpoint: string
  icon: typeof FileText
}

const DOCS: DocSpec[] = [
  {
    id: 'midpoint',
    title: 'Midpoint Submission Paper',
    description:
      'Three-page academic paper formatted to midpoint requirements. '
      + 'Double-spaced, 12pt, ready to refine in the editor.',
    endpoint: '/api/v1/export/midpoint-paper',
    icon: FileText,
  },
  {
    id: 'brief',
    title: 'Executive Brief',
    description:
      'Five-page investment-audience brief covering methodology, '
      + 'findings, and recommendations.',
    endpoint: '/api/v1/export/executive-brief',
    icon: FileText,
  },
  {
    id: 'deck',
    title: 'Final Presentation Deck',
    description:
      '16-slide deck with real data charts in light mode, ready to '
      + 'refine in the editor.',
    endpoint: '/api/v1/export/presentation-deck',
    icon: Presentation,
  },
]

const LS_KEY = 'fc_doc_generated_at'

function readGeneratedAt(): Record<string, string> {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? (JSON.parse(raw) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

/** A completed generation — the file is held in memory until the user
 *  downloads it or opens the draft in the editor. */
interface GenResult {
  blob: Blob
  filename: string
  draftId: number | null
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export default function DocumentGenerationPanel() {
  const navigate = useNavigate()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [results, setResults] = useState<Record<string, GenResult>>({})
  const [generatedAt, setGeneratedAt] =
    useState<Record<string, string>>(readGeneratedAt)

  const handleGenerate = async (doc: DocSpec) => {
    setBusyId(doc.id)
    setErrors((prev) => {
      const next = { ...prev }
      delete next[doc.id]
      return next
    })
    try {
      const res = await axios({
        url: doc.endpoint, method: 'POST', responseType: 'blob',
      })
      const dispo = String(res.headers['content-disposition'] ?? '')
      const match = /filename="?([^";]+)"?/i.exec(dispo)
      const filename = match?.[1] ?? `forest-capital-${doc.id}`
      const draftHeader = res.headers['x-draft-id']
      const draftId = draftHeader != null ? Number(draftHeader) : null
      const blob = new Blob([res.data as BlobPart], {
        type: String(res.headers['content-type']
          ?? 'application/octet-stream'),
      })

      // A document that did not produce an editor draft (the brief)
      // downloads immediately, as before. One that did keeps the file
      // in memory and offers Open in Editor as the primary action.
      if (draftId === null || Number.isNaN(draftId)) {
        triggerDownload(blob, filename)
      } else {
        setResults((prev) => ({
          ...prev, [doc.id]: { blob, filename, draftId },
        }))
      }

      const now = new Date().toISOString()
      setGeneratedAt((prev) => {
        const next = { ...prev, [doc.id]: now }
        try { localStorage.setItem(LS_KEY, JSON.stringify(next)) } catch { /* best-effort */ }
        return next
      })
    } catch (err) {
      let msg = 'Generation failed. Please try again.'
      if (axios.isAxiosError(err) && err.response?.data instanceof Blob) {
        try {
          const parsed = JSON.parse(await err.response.data.text()) as
            { detail?: string }
          if (parsed.detail) msg = parsed.detail
        } catch { /* keep the generic message */ }
      } else if (axios.isAxiosError(err)) {
        msg = err.message
      }
      setErrors((prev) => ({ ...prev, [doc.id]: msg }))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section data-tour="generate-documents">
      <div className="flex items-baseline gap-3 mb-3">
        <h2 className="text-white font-semibold text-sm">Generate Documents</h2>
        <span className="text-2xs text-muted uppercase tracking-wide">
          First-draft deliverables · real platform data
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {DOCS.map((doc) => {
          const Icon = doc.icon
          const busy = busyId === doc.id
          const anyBusy = busyId !== null
          const error = errors[doc.id]
          const ts = generatedAt[doc.id]
          const result = results[doc.id]
          return (
            <div key={doc.id} className="card p-4 flex flex-col gap-3">
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded flex items-center justify-center
                                shrink-0 bg-electric/10 text-electric">
                  <Icon className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-white font-semibold text-sm">{doc.title}</h3>
                  <p className="text-muted text-xs mt-1 leading-relaxed">
                    {doc.description}
                  </p>
                </div>
              </div>

              <div className="text-2xs text-muted flex items-center gap-1">
                {ts ? (
                  <><CheckCircle className="w-3 h-3 text-success" />
                    Last generated: {new Date(ts).toLocaleString()}</>
                ) : <>Last generated: Never</>}
              </div>

              {/* Post-generation: a draft-backed document offers Open in
                  Editor (primary), Download (secondary), View on Reports
                  (tertiary). */}
              {result ? (
                <div className="flex flex-col gap-1.5">
                  <button type="button"
                    onClick={() => navigate(`/editor/${result.draftId}`)}
                    className="w-full flex items-center justify-center gap-1.5
                               px-3 py-2 rounded text-xs font-semibold
                               bg-electric text-white hover:bg-blue-500">
                    <PenLine className="w-3 h-3" /> Open in Editor
                  </button>
                  <button type="button"
                    onClick={() => triggerDownload(result.blob, result.filename)}
                    className="w-full flex items-center justify-center gap-1.5
                               px-3 py-1.5 rounded text-xs border border-electric/40
                               text-electric hover:bg-electric/10">
                    <Download className="w-3 h-3" /> Download
                  </button>
                  <button type="button"
                    onClick={() => setResults((prev) => {
                      const next = { ...prev }
                      delete next[doc.id]
                      return next
                    })}
                    className="text-2xs text-muted hover:text-white">
                    View on Reports page
                  </button>
                </div>
              ) : (
                <TeamGate block permission="generate_documents"
                  tooltip="Document generation is available to the project team">
                  <button type="button" disabled={anyBusy}
                    onClick={() => void handleGenerate(doc)}
                    className="w-full flex items-center justify-center gap-1.5
                               px-3 py-2 rounded text-xs font-semibold
                               transition-colors bg-electric text-white
                               hover:bg-blue-500 disabled:opacity-50
                               disabled:cursor-not-allowed">
                    {busy ? (
                      <><Loader2 className="w-3 h-3 animate-spin" />
                        Generating… 30–60 seconds</>
                    ) : (
                      <><Download className="w-3 h-3" />
                        {ts ? 'Regenerate' : 'Generate'}</>
                    )}
                  </button>
                </TeamGate>
              )}

              {error && (
                <div className="flex items-start gap-1.5 px-2 py-1.5 rounded
                                text-2xs border border-danger/30 bg-danger/5
                                text-danger">
                  <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
                  <span className="flex-1">
                    {error}{' '}
                    <button type="button"
                      onClick={() => void handleGenerate(doc)}
                      className="underline hover:no-underline font-semibold">
                      Retry
                    </button>
                  </span>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <p className="text-2xs text-muted mt-2">
        Generated documents are first drafts for review — every file carries
        an <strong className="text-warning">AI DRAFT — REQUIRES HUMAN REVIEW</strong>{' '}
        banner. Verify every figure before submitting.
      </p>
    </section>
  )
}
