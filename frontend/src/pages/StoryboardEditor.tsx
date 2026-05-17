/**
 * frontend/src/pages/StoryboardEditor.tsx
 *
 * Molly's editing screen for the 15-slide presentation storyboard.
 *
 * Layout (CLAUDE.md Section 14):
 *   Left column (320px):
 *     - Running timing bar (GREEN ≤20min, AMBER 20-21, RED >21)
 *     - Slide cards with drag-to-reorder
 *     - "Add slide" button at the bottom
 *
 *   Centre column (flex-1):
 *     - Expanded editor for the currently-selected slide
 *     - Headline, key point, owner, timing, chart, speaker note,
 *       live-demo toggle, transition, Regenerate-speaker-note,
 *       Remove-slide
 *     - "AI DRAFT — REQUIRES HUMAN REVIEW" sticky banner at top
 *
 *   Right column (280px):
 *     - Version History panel: named versions, auto-saves collapsed,
 *       Preview / Restore buttons, Save Version dialog
 *     - Gemini Assistant toggle button
 *
 * Drag-to-reorder uses native HTML5 drag events — no extra dependency.
 * dnd-kit would be richer but adding ~30kb for this one screen isn't
 * worth it; the slide list is short and the native UX is sufficient.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Plus, Trash2, GripVertical, Save, RotateCcw,
  Sparkles, ChevronDown, ChevronUp, AlertCircle, Bot,
  ArrowLeft, History, Loader2,
} from 'lucide-react'
import { useStoryboardStore } from '../stores/storyboardStore'
import GeminiAssistantPanel from '../components/GeminiAssistantPanel'
import type { Slide, SlideOwner } from '../types/storyboard'

const OWNERS: SlideOwner[] = ['Molly', 'Michael', 'Bob', 'All']
const TIMING_OPTIONS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
const AI_DRAFT_BANNER =
  'AI DRAFT — REQUIRES HUMAN REVIEW · Verify every number before generating the deck'


export default function StoryboardEditor() {
  const navigate = useNavigate()
  const {
    documentId, storyboard, versions, loading, saving, lastSavedAt, error,
    selectedSlideId, createDraft, setSelectedSlide, updateSlide, reorderSlides,
    addSlide, removeSlide, saveNamedVersion, restoreVersion,
  } = useStoryboardStore()

  const [showVersions, setShowVersions] = useState(true)
  const [showAssistant, setShowAssistant] = useState(false)
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [showAutoSaves, setShowAutoSaves] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [draftSummary, setDraftSummary] = useState('')
  const [draggedSlideId, setDraggedSlideId] = useState<string | null>(null)

  // First-mount: kick off the AI draft when no storyboard is loaded yet.
  // If the user navigated here from a deep link with an existing
  // document_id, the Reports screen would call loadDocument before
  // routing — that's a follow-up feature. For now createDraft is the
  // only entry point.
  useEffect(() => {
    if (!storyboard && !loading) {
      void createDraft()
    }
  }, [storyboard, loading, createDraft])

  const selectedSlide = storyboard?.slides.find((s) => s.id === selectedSlideId) ?? null
  const totalMins = storyboard?.total_timing_mins ?? 0
  const timingColor =
    totalMins <= 20 ? 'bg-success' :
    totalMins <= 21 ? 'bg-warning' :
    'bg-danger'
  const timingTextClass =
    totalMins <= 20 ? 'text-success' :
    totalMins <= 21 ? 'text-warning' :
    'text-danger'

  const namedVersions = versions.filter((v) => !v.is_auto_save)
  const autoSaves = versions.filter((v) => v.is_auto_save)

  const handleSaveClick = () => {
    setDraftName('')
    setDraftSummary('')
    setShowSaveDialog(true)
  }

  const handleConfirmSave = async () => {
    await saveNamedVersion(draftName || 'Untitled version', draftSummary || undefined)
    setShowSaveDialog(false)
  }

  // Drag handlers — minimal and dependency-free. dataTransfer carries
  // the dragged slide id so onDrop can compute the new order.
  const handleDragStart = (slideId: string) => (e: React.DragEvent) => {
    setDraggedSlideId(slideId)
    e.dataTransfer.effectAllowed = 'move'
  }
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }
  const handleDrop = (targetId: string) => (e: React.DragEvent) => {
    e.preventDefault()
    if (!draggedSlideId || draggedSlideId === targetId || !storyboard) return
    const ids = storyboard.slides.map((s) => s.id)
    const fromIdx = ids.indexOf(draggedSlideId)
    const toIdx = ids.indexOf(targetId)
    if (fromIdx === -1 || toIdx === -1) return
    const reordered = [...ids]
    const [moved] = reordered.splice(fromIdx, 1)
    reordered.splice(toIdx, 0, moved)
    reorderSlides(reordered)
    setDraggedSlideId(null)
  }

  if (loading && !storyboard) {
    return (
      <div className="h-full flex items-center justify-center text-muted text-sm">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Generating 15-slide AI draft…
      </div>
    )
  }

  if (!storyboard) {
    return (
      <div className="p-6 text-center text-muted text-sm">
        <button onClick={() => void createDraft()} className="text-electric underline">
          Create storyboard draft
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* AI DRAFT banner — sticky at the top */}
      <div className="px-4 py-2 bg-warning/10 border-b border-warning/30 text-warning text-2xs font-semibold uppercase tracking-wide shrink-0">
        {AI_DRAFT_BANNER}
      </div>

      {/* Toolbar */}
      <div className="px-4 py-2 border-b border-border flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/reports')}
            className="text-muted hover:text-white text-xs flex items-center gap-1"
          >
            <ArrowLeft className="w-3 h-3" /> Reports
          </button>
          <h1 className="text-white text-sm font-semibold">Storyboard Editor</h1>
          {documentId && (
            <span className="text-2xs text-muted font-mono">
              {documentId.slice(0, 8)}…
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {saving && <span className="text-2xs text-muted">Saving…</span>}
          {!saving && lastSavedAt && (
            <span className="text-2xs text-muted">
              Saved {lastSavedAt.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={() => setShowAssistant((v) => !v)}
            className="text-2xs px-2 py-1 rounded border transition-colors flex items-center gap-1"
            style={{
              color: '#8b5cf6',
              borderColor: showAssistant ? '#8b5cf680' : '#8b5cf640',
              background: showAssistant ? '#8b5cf625' : '#8b5cf610',
            }}
          >
            <Bot className="w-3 h-3" /> Gemini
          </button>
          <button
            onClick={handleSaveClick}
            className="text-2xs px-2 py-1 rounded border border-electric/40 bg-electric/10 text-electric flex items-center gap-1 hover:bg-electric/20"
          >
            <Save className="w-3 h-3" /> Save Version
          </button>
        </div>
      </div>

      {error && (
        <div className="px-4 py-2 bg-danger/10 border-b border-danger/30 text-danger text-xs flex items-center gap-2 shrink-0">
          <AlertCircle className="w-3.5 h-3.5" /> {error}
        </div>
      )}

      {/* Body — three columns */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left column: slide list */}
        <aside className="w-80 border-r border-border flex flex-col shrink-0">
          {/* Timing bar */}
          <div className="px-3 py-2 border-b border-border">
            <div className="flex items-center justify-between text-2xs mb-1">
              <span className="uppercase tracking-wide text-muted">Total time</span>
              <span className={`font-mono ${timingTextClass}`}>
                {totalMins.toFixed(1)} / 20.0 min
              </span>
            </div>
            <div className="h-1.5 bg-navy-800 rounded overflow-hidden">
              <div
                className={`h-full ${timingColor} transition-all`}
                style={{ width: `${Math.min((totalMins / 20) * 100, 110)}%` }}
              />
            </div>
          </div>

          {/* Slide cards */}
          <div className="flex-1 overflow-y-auto px-2 py-2 space-y-1.5">
            {storyboard.slides.map((s) => (
              <SlideCard
                key={s.id}
                slide={s}
                selected={s.id === selectedSlideId}
                onSelect={() => setSelectedSlide(s.id)}
                onDragStart={handleDragStart(s.id)}
                onDragOver={handleDragOver}
                onDrop={handleDrop(s.id)}
              />
            ))}
          </div>

          {/* Add slide */}
          <div className="px-2 pb-3 pt-1 border-t border-border">
            <button
              onClick={() => addSlide(selectedSlide?.order ?? storyboard.slides.length)}
              className="w-full flex items-center justify-center gap-1.5 text-xs py-1.5 rounded border border-border text-muted hover:text-white hover:border-electric/40"
            >
              <Plus className="w-3 h-3" /> Add slide
            </button>
          </div>
        </aside>

        {/* Centre column: slide editor */}
        <main className="flex-1 overflow-y-auto">
          {selectedSlide ? (
            <SlideEditor
              key={selectedSlide.id}
              slide={selectedSlide}
              onChange={(patch) => updateSlide(selectedSlide.id, patch)}
              onRemove={() => removeSlide(selectedSlide.id)}
            />
          ) : (
            <div className="p-8 text-center text-muted text-sm">
              Select a slide on the left to edit.
            </div>
          )}
        </main>

        {/* Right column: version history (collapsible) */}
        {showVersions && (
          <aside className="w-72 border-l border-border flex flex-col shrink-0">
            <div className="px-3 py-2 border-b border-border flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <History className="w-3.5 h-3.5 text-muted" />
                <span className="text-2xs uppercase tracking-wide text-muted">
                  Version history
                </span>
              </div>
              <button
                onClick={() => setShowVersions(false)}
                className="text-muted hover:text-white"
                aria-label="Hide version history"
              >
                <ChevronUp className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-2">
              {namedVersions.length === 0 ? (
                <p className="text-2xs text-muted italic px-1 py-2">
                  No named versions yet. Use the Save Version button to bookmark
                  the current state.
                </p>
              ) : (
                namedVersions.map((v) => (
                  <VersionRow
                    key={v.id}
                    version={v}
                    onRestore={() => void restoreVersion(v.id)}
                  />
                ))
              )}

              {/* Auto-saves — collapsed by default per CLAUDE.md spec */}
              {autoSaves.length > 0 && (
                <div className="pt-2 mt-2 border-t border-border/40">
                  <button
                    onClick={() => setShowAutoSaves((v) => !v)}
                    className="w-full flex items-center justify-between text-2xs text-muted hover:text-white"
                  >
                    <span>Auto-saves ({autoSaves.length})</span>
                    {showAutoSaves ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  </button>
                  {showAutoSaves && (
                    <div className="mt-2 space-y-1.5">
                      {autoSaves.map((v) => (
                        <VersionRow
                          key={v.id}
                          version={v}
                          onRestore={() => void restoreVersion(v.id)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </aside>
        )}

        {/* Gemini assistant panel — modal-ish overlay on the right */}
        {showAssistant && selectedSlide && (
          <GeminiAssistantPanel
            documentId={documentId}
            contextType="slide"
            contextContent={
              `Headline: ${selectedSlide.headline}\n\n` +
              `Key point: ${selectedSlide.key_point}\n\n` +
              `Speaker note: ${selectedSlide.speaker_note}`
            }
            onApply={(newContent) => {
              // Naive heuristic: the assistant rewrites prose — apply the
              // suggestion as the new speaker note. The headline/key-point
              // are short enough that the user typically edits them
              // directly rather than via Gemini.
              updateSlide(selectedSlide.id, { speaker_note: newContent, ai_draft: false })
            }}
            onClose={() => setShowAssistant(false)}
          />
        )}
      </div>

      {/* Save Version dialog */}
      {showSaveDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          onClick={() => setShowSaveDialog(false)}
        >
          <div
            className="bg-navy-800 border border-border rounded-lg p-5 w-full max-w-md"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-white font-semibold text-sm mb-3">Save version</h2>
            <label className="block text-2xs uppercase tracking-wide text-muted mb-1">
              Version name
            </label>
            <input
              type="text"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              placeholder="After team review"
              className="w-full bg-navy-700 border border-border rounded px-2 py-1.5 text-xs text-white mb-3 focus:outline-none focus:border-electric"
              autoFocus
            />
            <label className="block text-2xs uppercase tracking-wide text-muted mb-1">
              Change summary (optional)
            </label>
            <textarea
              value={draftSummary}
              onChange={(e) => setDraftSummary(e.target.value)}
              rows={3}
              className="w-full bg-navy-700 border border-border rounded px-2 py-1.5 text-xs text-white mb-4 resize-none focus:outline-none focus:border-electric"
              placeholder="Reordered slides 5 and 6, tightened the 2022 narrative"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowSaveDialog(false)}
                className="text-xs px-3 py-1.5 rounded border border-border text-muted hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleConfirmSave()}
                className="text-xs px-3 py-1.5 rounded bg-electric text-white hover:bg-blue-500"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Subcomponents ───────────────────────────────────────────────────────────

function SlideCard({
  slide, selected, onSelect, onDragStart, onDragOver, onDrop,
}: {
  slide: Slide
  selected: boolean
  onSelect: () => void
  onDragStart: (e: React.DragEvent) => void
  onDragOver: (e: React.DragEvent) => void
  onDrop: (e: React.DragEvent) => void
}) {
  return (
    <button
      type="button"
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onClick={onSelect}
      className={`w-full text-left px-2.5 py-1.5 rounded border transition-colors ${
        selected
          ? 'border-electric bg-electric/10'
          : 'border-border bg-navy-800/40 hover:border-border/80 hover:bg-navy-800'
      }`}
      data-testid={`slide-card-${slide.order}`}
    >
      <div className="flex items-start gap-1.5">
        <GripVertical className="w-3 h-3 text-muted shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 text-2xs">
            <span className="font-mono text-muted">#{slide.order}</span>
            <span className="text-muted">·</span>
            <span className="text-slate-300">{slide.owner}</span>
            <span className="text-muted">·</span>
            <span className="text-slate-300 font-mono">{slide.timing_mins.toFixed(1)}m</span>
          </div>
          <div className={`text-xs mt-0.5 truncate ${selected ? 'text-white' : 'text-slate-300'}`}>
            {slide.headline}
          </div>
        </div>
      </div>
    </button>
  )
}


function SlideEditor({
  slide, onChange, onRemove,
}: {
  slide: Slide
  onChange: (patch: Partial<Slide>) => void
  onRemove: () => void
}) {
  return (
    <div className="p-5 max-w-2xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-white text-base font-semibold">
          Slide {slide.order}
        </h2>
        <button
          onClick={onRemove}
          className="text-2xs flex items-center gap-1 text-danger/70 hover:text-danger"
        >
          <Trash2 className="w-3 h-3" /> Remove
        </button>
      </div>

      <Field label="Headline">
        <input
          type="text"
          value={slide.headline}
          onChange={(e) => onChange({ headline: e.target.value })}
          className="w-full bg-navy-800 border border-border rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-electric"
        />
      </Field>

      <div className="grid grid-cols-3 gap-3">
        <Field label="Owner">
          <select
            value={slide.owner}
            onChange={(e) => onChange({ owner: e.target.value as SlideOwner })}
            className="w-full bg-navy-800 border border-border rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-electric"
          >
            {OWNERS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </Field>
        <Field label="Timing (min)">
          <select
            value={slide.timing_mins}
            onChange={(e) => onChange({ timing_mins: Number(e.target.value) })}
            className="w-full bg-navy-800 border border-border rounded px-2 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-electric"
          >
            {TIMING_OPTIONS.map((t) => <option key={t} value={t}>{t.toFixed(1)}</option>)}
          </select>
        </Field>
        <Field label="Chart reference">
          <input
            type="text"
            value={slide.chart_ref ?? ''}
            placeholder="e.g. cumulative_returns.png"
            onChange={(e) => onChange({ chart_ref: e.target.value || null })}
            className="w-full bg-navy-800 border border-border rounded px-2 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-electric"
          />
        </Field>
      </div>

      <Field label="Key point">
        <input
          type="text"
          value={slide.key_point}
          onChange={(e) => onChange({ key_point: e.target.value })}
          className="w-full bg-navy-800 border border-border rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-electric"
        />
      </Field>

      <Field label="Speaker note">
        <textarea
          value={slide.speaker_note}
          onChange={(e) => onChange({ speaker_note: e.target.value, ai_draft: false })}
          rows={6}
          className="w-full bg-navy-800 border border-border rounded px-2 py-1.5 text-xs text-white leading-relaxed focus:outline-none focus:border-electric"
        />
      </Field>

      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={slide.live_demo}
            onChange={(e) => onChange({ live_demo: e.target.checked })}
          />
          Live demo on this slide
        </label>
        {slide.ai_draft && (
          <span className="text-2xs flex items-center gap-1 text-warning">
            <Sparkles className="w-3 h-3" /> AI draft
          </span>
        )}
      </div>

      <Field label="Transition to next slide">
        <input
          type="text"
          value={slide.transition}
          onChange={(e) => onChange({ transition: e.target.value })}
          placeholder="Now Michael will walk through the AI council…"
          className="w-full bg-navy-800 border border-border rounded px-2 py-1.5 text-xs text-white italic focus:outline-none focus:border-electric"
        />
      </Field>
    </div>
  )
}


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-2xs uppercase tracking-wide text-muted mb-1">{label}</div>
      {children}
    </label>
  )
}


function VersionRow({
  version, onRestore,
}: { version: import('../types/storyboard').DocumentVersion; onRestore: () => void }) {
  return (
    <div className="bg-navy-800/50 border border-border/40 rounded px-2 py-1.5 text-2xs">
      <div className="flex items-center justify-between mb-0.5">
        <span className="font-mono text-slate-300">
          v{version.version_number}
        </span>
        <button
          onClick={onRestore}
          className="text-electric hover:text-blue-300 flex items-center gap-1"
          title="Restore this version as new draft"
        >
          <RotateCcw className="w-2.5 h-2.5" /> Restore
        </button>
      </div>
      {version.version_name && (
        <div className="text-white truncate">{version.version_name}</div>
      )}
      {version.change_summary && (
        <div className="text-muted italic truncate" title={version.change_summary}>
          {version.change_summary}
        </div>
      )}
      <div className="text-muted/60 mt-0.5">
        {version.created_at?.slice(0, 16).replace('T', ' ')}
      </div>
    </div>
  )
}
