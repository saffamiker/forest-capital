/**
 * EditorNavigator — the editor's left panel (220px, collapsible).
 *
 * Three sections: document info (title, last-saved, word count vs
 * target), a section navigator with a per-section progress indicator
 * (driven by how many [[BOB]] / [[VERIFY]] markers remain), and the
 * version history with Save Version and Restore.
 */
import { useState } from 'react'
import { History, Save, RotateCcw, FileText, Loader2 } from 'lucide-react'

import type { EditorDraftVersion, SaveState } from '../../types/editor'

export interface NavSection {
  heading: string
  markersRemaining: number
  totalMarkers: number
}

interface Props {
  title: string
  wordCount: number
  wordTarget: number
  lastSavedLabel: string
  saveState: SaveState
  sections: NavSection[]
  versions: EditorDraftVersion[]
  onJumpToSection: (heading: string) => void
  onSaveVersion: (label: string) => void
  onRestoreVersion: (versionId: number) => void
}

export default function EditorNavigator({
  title, wordCount, wordTarget, lastSavedLabel, saveState, sections,
  versions, onJumpToSection, onSaveVersion, onRestoreVersion,
}: Props) {
  const [showSave, setShowSave] = useState(false)
  const [label, setLabel] = useState('')

  const pct = (s: NavSection): number => {
    if (s.totalMarkers === 0) return 100
    return Math.round(
      ((s.totalMarkers - s.markersRemaining) / s.totalMarkers) * 100)
  }

  return (
    <div className="h-full overflow-y-auto p-3 space-y-4 text-xs">
      {/* Document info */}
      <div>
        <div className="flex items-center gap-1.5 text-white font-medium mb-1">
          <FileText className="w-3.5 h-3.5 text-electric" />
          <span className="truncate">{title}</span>
        </div>
        <div className="text-2xs text-muted">
          {saveState === 'saving'
            ? <span className="flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" /> Saving…</span>
            : `Last saved: ${lastSavedLabel}`}
        </div>
        <div className="text-2xs text-muted mt-0.5">
          Word count: <span className="font-mono text-slate-300">{wordCount}</span>
          {' '}/ ~{wordTarget} target
        </div>
      </div>

      {/* Section navigator */}
      {sections.length > 0 && (
        <div>
          <div className="text-2xs text-muted uppercase tracking-wide mb-1.5">
            Sections
          </div>
          <div className="space-y-1.5">
            {sections.map((s) => (
              <button key={s.heading} type="button"
                onClick={() => onJumpToSection(s.heading)}
                className="w-full text-left group">
                <div className="text-slate-300 group-hover:text-white truncate">
                  {s.heading}
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <div className="flex-1 h-1 rounded bg-navy-700 overflow-hidden">
                    <div className="h-full bg-electric"
                      style={{ width: `${pct(s)}%` }} />
                  </div>
                  <span className="text-2xs text-muted shrink-0">{pct(s)}%</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Version history */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-2xs text-muted uppercase tracking-wide
                           flex items-center gap-1">
            <History className="w-3 h-3" /> Versions
          </span>
          <button type="button" onClick={() => setShowSave((v) => !v)}
            className="text-2xs text-electric hover:underline flex items-center gap-1">
            <Save className="w-3 h-3" /> Save
          </button>
        </div>
        {showSave && (
          <div className="mb-2 space-y-1">
            <input value={label} onChange={(e) => setLabel(e.target.value)}
              placeholder="Version label (e.g. Final submission)"
              className="w-full bg-navy-800 border border-border rounded
                         text-2xs text-white px-1.5 py-1" />
            <button type="button"
              onClick={() => { onSaveVersion(label); setLabel(''); setShowSave(false) }}
              className="w-full text-2xs bg-electric/15 text-electric border
                         border-electric/30 rounded py-1 hover:bg-electric/25">
              Save version
            </button>
          </div>
        )}
        {versions.length === 0 ? (
          <p className="text-2xs text-muted italic">No saved versions yet.</p>
        ) : (
          <div className="space-y-1">
            {versions.map((v) => (
              <div key={v.id}
                className="flex items-center justify-between gap-2
                           border-b border-border/40 pb-1 last:border-0">
                <div className="min-w-0">
                  <div className="text-slate-300 truncate">
                    v{v.version}{v.version_label ? ` · ${v.version_label}` : ''}
                  </div>
                  <div className="text-2xs text-muted">
                    {v.saved_at
                      ? new Date(v.saved_at).toLocaleString(undefined,
                          { month: 'short', day: 'numeric',
                            hour: '2-digit', minute: '2-digit' })
                      : '—'}
                  </div>
                </div>
                <button type="button" onClick={() => onRestoreVersion(v.id)}
                  aria-label={`Restore version ${v.version}`}
                  className="text-muted hover:text-electric shrink-0">
                  <RotateCcw className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
