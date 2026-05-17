/**
 * WhatsNewModal — shown once after login when the changelog has entries
 * the user has not seen.
 *
 * On mount it calls GET /api/v1/changelog/unseen; if that returns one or
 * more entries the modal opens. Closing it — via "Got it", a backdrop
 * click, or Escape — POSTs /api/v1/changelog/mark-seen so it does not
 * reappear for already-seen entries. The modal never blocks the app:
 * every path is closeable, and any failure simply leaves it closed.
 *
 * The tour version is intentionally NOT recorded on close — that is
 * SiteTour's job, done when the tour itself completes or is skipped.
 * Closing the modal only marks the changelog entries seen.
 *
 * When a tour update is pending, the footer's "View updated site tour"
 * button closes the modal and force-starts the tour via tourBus, so the
 * tour never opens on top of the modal.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import { Sparkles, X, GraduationCap } from 'lucide-react'
import type { ChangelogEntry, UnseenChangelogResponse } from '../types/changelog'
import { startTour } from '../lib/tourBus'

export default function WhatsNewModal() {
  const [entries, setEntries] = useState<ChangelogEntry[]>([])
  const [hasTourUpdate, setHasTourUpdate] = useState(false)
  const [open, setOpen] = useState(false)

  // Fetch unseen entries once on mount (i.e. once per authenticated load).
  useEffect(() => {
    let cancelled = false
    axios.get<UnseenChangelogResponse>('/api/v1/changelog/unseen')
      .then((res) => {
        if (cancelled) return
        const list = res.data.entries ?? []
        if (list.length > 0) {
          setEntries(list)
          setHasTourUpdate(!!res.data.has_tour_update)
          setOpen(true)
        }
      })
      .catch(() => { /* silent — no modal on failure */ })
    return () => { cancelled = true }
  }, [])

  // Closing always marks the changelog seen so it does not reappear.
  // Fire-and-forget — a failed mark-seen must not keep the modal open.
  const close = () => {
    setOpen(false)
    void axios.post('/api/v1/changelog/mark-seen', {}).catch(() => { /* silent */ })
  }

  // Escape closes (and marks seen).
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  // close is stable enough for this one-shot modal; deps kept minimal.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open || entries.length === 0) return null

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center
                 bg-black/50 p-4"
      onClick={close}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label="What's New"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg max-h-[85vh] flex flex-col rounded-lg
                   border border-border bg-navy-800 shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-5 py-4
                        border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-electric" />
            <div>
              <h2 className="text-white font-semibold text-sm">What's New</h2>
              <p className="text-2xs text-muted mt-0.5">
                {entries.length} update{entries.length === 1 ? '' : 's'} since
                your last visit
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close"
            className="text-muted hover:text-white shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Entry list */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">
          {entries.map((e) => (
            <div key={e.id} className="rounded border border-border bg-navy-900 p-3">
              <h3 className="text-white font-semibold text-sm">{e.title}</h3>
              <p className="text-xs text-slate-300 leading-relaxed mt-1">
                {e.description}
              </p>
              {/* Academic rationale — distinct treatment, amber left accent. */}
              <div
                className="mt-2 pl-3 py-1.5"
                style={{ borderLeft: '3px solid #f59e0b' }}
              >
                <div className="text-2xs uppercase tracking-wide text-warning
                                font-medium">
                  Why this matters for your grade
                </div>
                <p className="text-xs text-slate-300 leading-relaxed mt-0.5">
                  {e.academic_rationale}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-3
                        border-t border-border shrink-0">
          {hasTourUpdate ? (
            <button
              type="button"
              onClick={() => { close(); startTour() }}
              title="Replay the site tour to see what's new"
              className="flex items-center gap-1.5 text-2xs text-electric
                         hover:text-blue-300 transition-colors"
            >
              <GraduationCap className="w-3.5 h-3.5" />
              View updated site tour
            </button>
          ) : <span />}
          <button
            type="button"
            onClick={close}
            className="px-4 py-1.5 rounded text-xs font-medium
                       bg-electric/15 text-electric border border-electric/30
                       hover:bg-electric/25 transition-colors"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  )
}
