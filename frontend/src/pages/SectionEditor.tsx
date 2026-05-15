/**
 * frontend/src/pages/SectionEditor.tsx
 *
 * Bob's section editor — opens a section-structured document (midpoint
 * paper, executive brief, or analytical appendix) for in-browser editing.
 *
 * Layout: left column = section list, right column = section editor
 * surface for the selected section with View AI Draft side panel,
 * Regenerate AI button, Revert button. Right edge = Version History
 * (collapsible).
 *
 * Each section card shows:
 *   - Title heading + word count
 *   - Editable textarea bound to documentsStore.updateSection
 *   - View AI Draft button → slide-out panel showing the immutable
 *     ai_draft alongside Bob's content
 *   - Regenerate AI button → POSTs to backend, replaces ai_draft, asks
 *     Bob whether to replace his content too
 *   - Revert button → confirmation dialog → copies ai_draft into content
 *
 * The AI DRAFT banner is permanent at the top of the page — Bob can't
 * dismiss it. Word export pulls the current draft from the backend so
 * the downloaded file always reflects whatever's been saved.
 */
import { useEffect, useState, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  ArrowLeft, AlertTriangle, BookOpen, RefreshCcw, RotateCcw, Save,
  Download, History, X, Loader2, FileText,
} from 'lucide-react'

import { useDocumentsStore } from '../stores/documentsStore'
import type { DocumentSection, DocumentVersion } from '../types/documents'


const AI_DRAFT_BANNER_TEXT = 'AI DRAFT — REQUIRES HUMAN REVIEW'


function countWords(text: string): number {
  // Trim + split on whitespace runs. Empty string → 0 (split('') would
  // return [''] which is length 1, hence the early-out).
  const trimmed = text.trim()
  if (!trimmed) return 0
  return trimmed.split(/\s+/).length
}


export default function SectionEditor() {
  const { documentId } = useParams<{ documentId: string }>()
  const navigate = useNavigate()

  const {
    document, versions, loading, saving, lastSavedAt, error,
    selectedSectionId,
    loadDocument, setSelectedSection, updateSection,
    regenerateSection, revertSection,
    saveNamedVersion, restoreVersion, clear,
  } = useDocumentsStore()

  const [showAiDraft, setShowAiDraft] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [saveDialogName, setSaveDialogName] = useState('')
  const [saveDialogSummary, setSaveDialogSummary] = useState('')
  const [confirmRevertId, setConfirmRevertId] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    if (documentId) void loadDocument(documentId)
    return () => clear()
  }, [documentId, loadDocument, clear])

  const selectedSection: DocumentSection | undefined = useMemo(
    () => document?.sections.find((s) => s.id === selectedSectionId),
    [document, selectedSectionId],
  )

  const handleExport = async () => {
    if (!documentId) return
    setExporting(true)
    try {
      const res = await axios.post(
        `/api/documents/${documentId}/export`,
        {},
        { responseType: 'blob' },
      )
      const dispo = String(res.headers['content-disposition'] ?? '')
      const filenameMatch = /filename="?([^";]+)"?/i.exec(dispo)
      const fallback = document?.doc_type === 'analytical_appendix'
        ? 'document.html' : 'document.docx'
      const filename = filenameMatch?.[1] ?? fallback
      const contentType = String(res.headers['content-type'] ?? 'application/octet-stream')
      const blob = new Blob([res.data], { type: contentType })
      const url = URL.createObjectURL(blob)
      const a = window.document.createElement('a')
      a.href = url
      a.download = filename
      window.document.body.appendChild(a)
      a.click()
      window.document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch {
      // Surface via store error state; no console noise.
    } finally {
      setExporting(false)
    }
  }

  const handleRegenerate = async (sectionId: string) => {
    setRegenerating(sectionId)
    try {
      await regenerateSection(sectionId)
    } finally {
      setRegenerating(null)
    }
  }

  const handleConfirmRevert = () => {
    if (confirmRevertId) revertSection(confirmRevertId)
    setConfirmRevertId(null)
  }

  const handleSaveVersion = async () => {
    await saveNamedVersion(
      saveDialogName.trim() || 'Untitled version',
      saveDialogSummary.trim() || undefined,
    )
    setSaveDialogName('')
    setSaveDialogSummary('')
    setShowSaveDialog(false)
  }

  if (loading && !document) {
    return (
      <div className="p-6 flex items-center gap-2 text-muted text-sm">
        <Loader2 className="w-4 h-4 animate-spin" />
        Loading document…
      </div>
    )
  }

  if (!document) {
    return (
      <div className="p-6 max-w-2xl mx-auto space-y-3">
        <button
          onClick={() => navigate('/reports')}
          className="flex items-center gap-1.5 text-electric text-xs hover:underline"
        >
          <ArrowLeft className="w-3 h-3" /> Back to Reports
        </button>
        <div className="card p-6 border border-danger/30 bg-danger/5">
          <p className="text-danger text-sm">
            {error ?? 'Document not found.'}
          </p>
        </div>
      </div>
    )
  }

  const totalWords = document.sections.reduce((sum, s) => sum + countWords(s.content), 0)
  const namedVersions = versions.filter((v) => !v.is_auto_save)
  const autoSaves = versions.filter((v) => v.is_auto_save)

  return (
    <div className="flex flex-col min-h-screen">
      {/* AI DRAFT banner — sticky, never dismissable */}
      <div
        className="px-6 py-2 text-center border-b"
        style={{
          backgroundColor: '#f59e0b',
          color: '#0a0e1a',
          borderBottomColor: '#b45309',
        }}
        data-testid="section-editor-ai-draft-banner"
      >
        <strong className="text-xs uppercase tracking-widest">
          {AI_DRAFT_BANNER_TEXT}
        </strong>
        <span className="text-2xs ml-3 opacity-80">
          Every section is a starting point — review before submitting.
        </span>
      </div>

      <div className="flex-1 flex">
        {/* Left: section list */}
        <aside className="w-64 border-r border-border bg-navy-900 p-3 space-y-1 shrink-0">
          <button
            onClick={() => navigate('/reports')}
            className="flex items-center gap-1.5 text-muted hover:text-white text-xs mb-3"
          >
            <ArrowLeft className="w-3 h-3" /> Back to Reports
          </button>

          <h1 className="text-white font-semibold text-sm">{document.title}</h1>
          <p className="text-muted text-2xs">{document.subtitle}</p>

          <div className="mt-3 pt-3 border-t border-border space-y-0.5">
            {document.sections.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelectedSection(s.id)}
                data-testid={`section-tab-${s.id}`}
                className={`w-full text-left px-2 py-1.5 rounded text-xs transition-colors ${
                  selectedSectionId === s.id
                    ? 'bg-electric/15 text-electric'
                    : 'text-slate-300 hover:bg-navy-700'
                }`}
              >
                <div className="font-medium">{s.title}</div>
                <div className="text-2xs text-muted mt-0.5">
                  {countWords(s.content)} words
                </div>
              </button>
            ))}
          </div>

          <div className="mt-3 pt-3 border-t border-border space-y-1.5">
            <button
              onClick={() => setShowSaveDialog(true)}
              className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded text-xs bg-electric/10 border border-electric/30 text-electric hover:bg-electric/20"
              data-testid="save-version-button"
            >
              <Save className="w-3 h-3" /> Save Version
            </button>
            <button
              onClick={() => void handleExport()}
              disabled={exporting}
              className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded text-xs border border-border text-slate-300 hover:bg-navy-700 disabled:opacity-50"
              data-testid="export-button"
            >
              {exporting
                ? <><Loader2 className="w-3 h-3 animate-spin" /> Exporting…</>
                : <><Download className="w-3 h-3" /> Export {document.doc_type === 'analytical_appendix' ? 'HTML' : 'DOCX'}</>}
            </button>
            <button
              onClick={() => setShowVersions((v) => !v)}
              className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded text-xs border border-border text-muted hover:bg-navy-700"
              data-testid="toggle-versions-button"
            >
              <History className="w-3 h-3" />
              {showVersions ? 'Hide' : 'Show'} versions ({namedVersions.length})
            </button>
          </div>

          <div className="mt-3 pt-3 border-t border-border text-2xs space-y-1">
            <div className="flex justify-between text-muted">
              <span>Total</span>
              <span className="font-mono text-slate-300">{totalWords} words</span>
            </div>
            {saving && (
              <div className="flex items-center gap-1 text-muted">
                <Loader2 className="w-2.5 h-2.5 animate-spin" />
                Auto-saving…
              </div>
            )}
            {lastSavedAt && !saving && (
              <div className="text-muted">
                Saved {lastSavedAt.toLocaleTimeString()}
              </div>
            )}
            {error && (
              <div className="flex items-start gap-1 text-warning">
                <AlertTriangle className="w-2.5 h-2.5 mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}
          </div>
        </aside>

        {/* Middle: section editor surface */}
        <main className="flex-1 overflow-y-auto p-6 max-w-4xl">
          {selectedSection ? (
            <SectionEditorSurface
              section={selectedSection}
              onChange={(patch) => updateSection(selectedSection.id, patch)}
              onRegenerate={() => handleRegenerate(selectedSection.id)}
              regenerating={regenerating === selectedSection.id}
              onRevert={() => setConfirmRevertId(selectedSection.id)}
              onViewAiDraft={() => setShowAiDraft(true)}
            />
          ) : (
            <p className="text-muted text-sm">Select a section to edit.</p>
          )}
        </main>

        {/* Right: AI draft side panel — appears when toggled */}
        {showAiDraft && selectedSection && (
          <aside
            className="w-96 border-l border-border bg-navy-900 p-4 overflow-y-auto"
            data-testid="ai-draft-panel"
          >
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-white font-semibold text-sm flex items-center gap-1.5">
                <BookOpen className="w-3.5 h-3.5 text-electric" />
                Original AI draft
              </h2>
              <button
                onClick={() => setShowAiDraft(false)}
                className="text-muted hover:text-white"
                aria-label="Close AI draft panel"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-muted text-2xs mb-3">
              {selectedSection.title} · immutable original from the Academic Writer
            </p>
            <div className="text-slate-300 text-xs whitespace-pre-wrap leading-relaxed">
              {selectedSection.ai_draft}
            </div>
          </aside>
        )}

        {/* Right: Version History panel — appears when toggled */}
        {showVersions && !showAiDraft && (
          <aside
            className="w-80 border-l border-border bg-navy-900 p-4 overflow-y-auto"
            data-testid="version-history-panel"
          >
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-white font-semibold text-sm flex items-center gap-1.5">
                <History className="w-3.5 h-3.5 text-electric" />
                Version history
              </h2>
              <button
                onClick={() => setShowVersions(false)}
                className="text-muted hover:text-white"
                aria-label="Close version history"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {namedVersions.length === 0 && autoSaves.length === 0 && (
              <p className="text-muted text-xs">
                No versions saved yet. Use Save Version to bookmark
                the current draft.
              </p>
            )}

            {namedVersions.length > 0 && (
              <div className="space-y-2 mb-4">
                <div className="text-2xs uppercase tracking-wide text-muted">
                  Named versions
                </div>
                {namedVersions.map((v) => (
                  <VersionRow
                    key={v.id}
                    version={v}
                    onRestore={() => void restoreVersion(v.id)}
                  />
                ))}
              </div>
            )}

            {autoSaves.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted hover:text-white">
                  Auto-saves ({autoSaves.length})
                </summary>
                <div className="mt-2 space-y-2">
                  {autoSaves.map((v) => (
                    <VersionRow
                      key={v.id}
                      version={v}
                      onRestore={() => void restoreVersion(v.id)}
                    />
                  ))}
                </div>
              </details>
            )}
          </aside>
        )}
      </div>

      {/* Save Version dialog */}
      {showSaveDialog && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-navy-800 border border-border rounded-lg w-full max-w-md p-4 space-y-3">
            <h2 className="text-white font-semibold text-sm">Save version</h2>
            <div>
              <label className="block text-2xs uppercase tracking-wide text-muted mb-1">
                Version name
              </label>
              <input
                value={saveDialogName}
                onChange={(e) => setSaveDialogName(e.target.value)}
                placeholder="e.g. After team review"
                className="w-full bg-navy-900 border border-border rounded px-2 py-1.5 text-sm text-white"
                data-testid="save-version-name-input"
              />
            </div>
            <div>
              <label className="block text-2xs uppercase tracking-wide text-muted mb-1">
                Notes (optional)
              </label>
              <textarea
                value={saveDialogSummary}
                onChange={(e) => setSaveDialogSummary(e.target.value)}
                rows={2}
                className="w-full bg-navy-900 border border-border rounded px-2 py-1.5 text-sm text-white resize-none"
              />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setShowSaveDialog(false)}
                className="text-xs px-3 py-1.5 rounded border border-border text-muted hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleSaveVersion()}
                className="text-xs px-3 py-1.5 rounded bg-electric/15 border border-electric/30 text-electric hover:bg-electric/20"
                data-testid="confirm-save-version-button"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Revert dialog */}
      {confirmRevertId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-navy-800 border border-warning/40 rounded-lg w-full max-w-md p-4 space-y-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
              <div>
                <h2 className="text-white font-semibold text-sm">Revert section to AI draft?</h2>
                <p className="text-muted text-xs mt-1">
                  This replaces your current edits with the original AI draft
                  for this section only. Other sections are unaffected. Save
                  a version first if you might want your edits back.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setConfirmRevertId(null)}
                className="text-xs px-3 py-1.5 rounded border border-border text-muted hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmRevert}
                data-testid="confirm-revert-button"
                className="text-xs px-3 py-1.5 rounded bg-warning/15 border border-warning/40 text-warning hover:bg-warning/25"
              >
                Revert
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


interface SectionEditorSurfaceProps {
  section:      DocumentSection
  onChange:     (patch: Partial<DocumentSection>) => void
  onRegenerate: () => void
  regenerating: boolean
  onRevert:     () => void
  onViewAiDraft: () => void
}

function SectionEditorSurface({
  section, onChange, onRegenerate, regenerating, onRevert, onViewAiDraft,
}: SectionEditorSurfaceProps) {
  const wordCount = countWords(section.content)
  const edited = section.content !== section.ai_draft

  return (
    <div className="space-y-3" data-testid={`section-editor-${section.id}`}>
      <div className="flex items-start justify-between gap-3 pb-3 border-b border-border">
        <div>
          <h2 className="text-white font-semibold text-base flex items-center gap-2">
            <FileText className="w-4 h-4 text-electric" />
            {section.title}
          </h2>
          <p className="text-muted text-2xs mt-1">
            {wordCount} words
            {edited
              ? ' · edited from AI draft'
              : ' · matches AI draft'}
          </p>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            onClick={onViewAiDraft}
            className="flex items-center gap-1 text-2xs px-2 py-1 rounded border border-border text-slate-300 hover:bg-navy-700"
            data-testid="view-ai-draft-button"
          >
            <BookOpen className="w-3 h-3" /> View AI Draft
          </button>
          <button
            onClick={onRegenerate}
            disabled={regenerating}
            className="flex items-center gap-1 text-2xs px-2 py-1 rounded border border-electric/30 text-electric hover:bg-electric/10 disabled:opacity-50"
            data-testid="regenerate-button"
          >
            {regenerating
              ? <><Loader2 className="w-3 h-3 animate-spin" /> Regenerating…</>
              : <><RefreshCcw className="w-3 h-3" /> Regenerate AI</>}
          </button>
          <button
            onClick={onRevert}
            className="flex items-center gap-1 text-2xs px-2 py-1 rounded border border-warning/40 text-warning hover:bg-warning/10"
            data-testid="revert-button"
          >
            <RotateCcw className="w-3 h-3" /> Revert
          </button>
        </div>
      </div>

      <textarea
        value={section.content}
        onChange={(e) => onChange({ content: e.target.value })}
        rows={20}
        className="w-full bg-navy-900 border border-border rounded p-3 text-sm text-white font-sans leading-relaxed resize-none focus:outline-none focus:border-electric"
        placeholder="Edit this section's prose…"
        data-testid={`section-textarea-${section.id}`}
      />
    </div>
  )
}


function VersionRow({
  version, onRestore,
}: { version: DocumentVersion; onRestore: () => void }) {
  return (
    <div className="border border-border rounded p-2 bg-navy-800">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-electric">
          v{version.version_number}
        </span>
        <button
          onClick={onRestore}
          className="text-2xs text-muted hover:text-white"
        >
          Restore
        </button>
      </div>
      <div className="text-2xs text-white mt-0.5 truncate">
        {version.version_name ?? 'Untitled'}
      </div>
      {version.change_summary && (
        <div className="text-2xs text-muted mt-0.5 truncate">
          {version.change_summary}
        </div>
      )}
    </div>
  )
}
