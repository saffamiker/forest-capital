/**
 * AcademicDocumentsPanel — upload reference documents that every AI agent
 * receives as system context.
 *
 * Uploaded PDFs and text files (the midpoint rubric, the final-presentation
 * requirements) are extracted server-side; the text is then injected into
 * every agent's system prompt so the council, advisors, writers and QA
 * agents are always aware of the academic evaluation criteria.
 *
 * Lives in the Reports view. Lists uploaded documents with a delete option.
 */
import { useEffect, useState, useRef } from 'react'
import axios from 'axios'
import { Upload, Trash2, FileText, Loader2, AlertCircle } from 'lucide-react'

interface AcademicDoc {
  id: string
  name: string
  document_type: string
  char_count: number
  uploaded_at: string | null
}

const DOC_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'midpoint_requirements',           label: 'Midpoint requirements' },
  { value: 'final_presentation_requirements', label: 'Final presentation requirements' },
  { value: 'midpoint_draft',                  label: 'Midpoint draft' },
  { value: 'presentation_slides',             label: 'Presentation slides' },
  { value: 'presentation_script',             label: 'Presentation script' },
  { value: 'other',                           label: 'Other' },
]

const TYPE_LABEL: Record<string, string> = Object.fromEntries(
  DOC_TYPE_OPTIONS.map((o) => [o.value, o.label]),
)

export default function AcademicDocumentsPanel() {
  const [docs, setDocs] = useState<AcademicDoc[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [docType, setDocType] = useState('midpoint_requirements')
  const fileRef = useRef<HTMLInputElement>(null)

  const refresh = async () => {
    try {
      const res = await axios.get<{ documents: AcademicDoc[] }>('/api/v1/documents/academic')
      setDocs(res.data.documents ?? [])
    } catch {
      setError('Could not load uploaded documents.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void refresh() }, [])

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) { setError('Choose a PDF or text file first.'); return }
    setUploading(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('document_type', docType)
      await axios.post('/api/v1/documents/academic/upload', form)
      if (fileRef.current) fileRef.current.value = ''
      await refresh()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Upload failed'
      setError(String(msg))
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (id: string) => {
    setError(null)
    try {
      await axios.delete(`/api/v1/documents/academic/${id}`)
      await refresh()
    } catch {
      setError('Could not delete the document.')
    }
  }

  return (
    <section className="card p-4" style={{ borderLeft: '3px solid #f59e0b' }}>
      <div className="flex items-baseline gap-3 mb-1">
        <h2 className="text-white font-semibold text-sm">Agent Context Documents</h2>
        <span className="text-2xs text-muted uppercase tracking-wide">
          Injected into every AI agent
        </span>
      </div>
      <p className="text-muted text-xs mb-3 leading-relaxed">
        Upload the midpoint rubric, final-presentation requirements, or any reference
        material. The text is extracted and added to every agent's system context, so
        analysis and feedback always reflect the academic evaluation criteria.
      </p>

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 mb-3 rounded border border-danger/30 bg-danger/5 text-danger text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Upload row */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select
          value={docType}
          onChange={(e) => setDocType(e.target.value)}
          className="bg-navy-800 border border-border rounded px-2 py-1.5 text-xs text-white"
        >
          {DOC_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.txt,.md,application/pdf,text/plain"
          className="text-xs text-muted file:mr-2 file:px-2 file:py-1 file:rounded
                     file:border file:border-border file:bg-navy-800 file:text-white
                     file:text-xs"
        />
        <button
          type="button"
          onClick={() => void handleUpload()}
          disabled={uploading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium
                     bg-electric/10 border border-electric/30 text-electric
                     hover:bg-electric/20 transition-colors disabled:opacity-50"
        >
          {uploading
            ? <><Loader2 className="w-3 h-3 animate-spin" /> Uploading…</>
            : <><Upload className="w-3 h-3" /> Upload</>}
        </button>
      </div>

      {/* Document list */}
      {loading ? (
        <div className="text-muted text-xs">Loading…</div>
      ) : docs.length === 0 ? (
        <div className="text-muted text-xs italic">No documents uploaded yet.</div>
      ) : (
        <ul className="space-y-1.5">
          {docs.map((d) => (
            <li
              key={d.id}
              className="flex items-center gap-2 px-3 py-2 rounded border border-border bg-navy-800"
            >
              <FileText className="w-3.5 h-3.5 text-muted shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-white text-xs truncate">{d.name}</div>
                <div className="text-2xs text-muted">
                  {TYPE_LABEL[d.document_type] ?? d.document_type}
                  {' · '}{d.char_count.toLocaleString()} chars
                  {d.uploaded_at ? ` · ${d.uploaded_at.slice(0, 10)}` : ''}
                </div>
              </div>
              <button
                type="button"
                onClick={() => void handleDelete(d.id)}
                aria-label={`Delete ${d.name}`}
                className="text-muted hover:text-danger transition-colors p-1"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
