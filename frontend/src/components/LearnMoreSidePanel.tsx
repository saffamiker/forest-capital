/**
 * frontend/src/components/LearnMoreSidePanel.tsx
 *
 * Level 3 of ExplainableText: side drawer with the academic context for
 * a term, including the canonical citation from references.json. Slides
 * in from the right; closes on outside-click, Escape, or the X button.
 *
 * The body of the panel is divided into three sections:
 *   - In this session: the glossary entry's "this_session" field
 *   - The mechanism: glossary entry's "why" field expanded
 *   - Further reading: APA citation from references.json (when found)
 *
 * The citation lookup is best-effort. If no reference matches the term,
 * the section is omitted rather than rendered empty — failing silent
 * keeps the side panel useful even before references.json is fully
 * populated.
 */
import { useEffect, useState } from 'react'
import { X, BookOpen, ExternalLink } from 'lucide-react'
import { ModalCloseButton } from './ModalControls'
import type { GlossaryTerm } from '../types/glossary'
import { loadReferences, findReferenceFor, type Reference } from '../lib/references'

interface Props {
  term: string
  entry: GlossaryTerm
  onClose: () => void
}

export default function LearnMoreSidePanel({ term, entry, onClose }: Props) {
  const [reference, setReference] = useState<Reference | null>(null)

  // Fetch references.json on mount. Memoised at the module level so
  // the second open is instant.
  useEffect(() => {
    let cancelled = false
    void loadReferences().then((refs) => {
      if (cancelled) return
      setReference(findReferenceFor(refs, term))
    })
    return () => { cancelled = true }
  }, [term])

  // Escape closes the panel — same affordance as ExplainableText's panel.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      {/* Backdrop — clicking dismisses, mirrors the modal-dialog pattern. */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Drawer */}
      <aside
        className="fixed top-0 right-0 z-50 h-full w-full sm:w-96 bg-navy-900 border-l border-border shadow-2xl flex flex-col"
        role="dialog"
        aria-label={`Learn more about ${term}`}
      >
        <header className="px-5 py-4 border-b border-border flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-electric" />
            <h2 className="text-white font-semibold text-sm">{term.replace(/_/g, ' ')}</h2>
          </div>
          <ModalCloseButton
            onClose={onClose}
            className="hover:bg-navy-700"
          />
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5 text-sm leading-relaxed">
          <section>
            <h3 className="text-2xs uppercase tracking-wide text-muted mb-1.5">In this session</h3>
            <p className="text-white">
              {entry.this_session ?? 'No session-specific context available.'}
            </p>
          </section>

          <section>
            <h3 className="text-2xs uppercase tracking-wide text-muted mb-1.5">The mechanism</h3>
            <p className="text-white">{entry.why}</p>
          </section>

          {entry.verdict && (
            <section>
              <h3 className="text-2xs uppercase tracking-wide text-muted mb-1.5">For our portfolio</h3>
              <p className="text-white">{entry.verdict}</p>
            </section>
          )}

          {reference && (
            <section className="pt-2 border-t border-border">
              <h3 className="text-2xs uppercase tracking-wide text-muted mb-1.5">Further reading</h3>
              <p className="text-white text-xs">{reference.apa}</p>
              <p className="text-muted text-2xs mt-2">
                Cited in the Forest Capital references catalog as
                <code className="text-electric ml-1">{reference.year} · {reference.author.split(',')[0]}</code>
              </p>
            </section>
          )}

          {!reference && (
            <section className="pt-2 border-t border-border">
              <h3 className="text-2xs uppercase tracking-wide text-muted mb-1.5">Further reading</h3>
              <p className="text-muted text-xs italic">
                No canonical reference cataloged for this term yet.
              </p>
            </section>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-border shrink-0">
          <a
            href="/TEAM_PRIMER.md"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-electric text-2xs hover:text-blue-300"
          >
            <ExternalLink className="w-3 h-3" />
            Open the Team Primer for the full Commentary-mode guide
          </a>
        </footer>
      </aside>
    </>
  )
}
