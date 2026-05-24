/**
 * FloatingSectionNav — page-scoped table of contents for long-form
 * pages. May 24 2026 — UAT redesign.
 *
 * Auto-discovers sections via `[data-section-id]` markers on the
 * page. Click to jump; IntersectionObserver tracks the active
 * section as the user scrolls.
 *
 *   Desktop (>= md): right-side floating control. COLLAPSED by
 *                    default — a small icon-only tab anchored to
 *                    the viewport right edge. Click the tab to
 *                    expand the section list; click outside (or
 *                    press Esc / click the toggle again) to
 *                    collapse. No hover-expand — interaction is
 *                    purely click-driven so the panel never
 *                    obscures a chart on a casual mouse-over.
 *   Mobile  (< md):  bottom drawer with swipe-up affordance.
 *                    Auto-collapses after a section pick.
 *
 * Page contract:
 *   - Every section gets `data-section-id="..."` (used in the URL
 *     fragment + as the IntersectionObserver target) and
 *     `data-section-label="..."` (the human-readable text shown
 *     in the nav).
 *   - The page mounts <FloatingSectionNav pageKey="qa-audit" />.
 *     The pageKey scopes the localStorage state and de-duplicates
 *     mounts (only one nav per page).
 *
 * The COLLAPSED state is the new default (UAT 2026-05-24): testers
 * reported the prior always-expanded panel obscured the right edge
 * of charts on the analytics screens. The collapsed tab carries a
 * 60% opacity tint that lifts to 100% on hover so it remains
 * discoverable without competing with the chart for attention.
 */
import {
  useCallback, useEffect, useRef, useState,
} from 'react'
import { ChevronUp, List, X } from 'lucide-react'


interface DiscoveredSection {
  id: string
  label: string
}


interface FloatingSectionNavProps {
  pageKey: string
  // The minimum number of sections required for the nav to render.
  // Defaults to 3 — a page with only 1-2 sections doesn't benefit
  // from a TOC, and a stray nav on a short page is visual noise.
  minSections?: number
  // Optional initial collapsed state on first render. Subsequent
  // user toggles persist via localStorage and override this.
  defaultCollapsed?: boolean
}


function _storageKey(pageKey: string): string {
  return `fc_floating_nav_collapsed_${pageKey}`
}


function _readCollapsed(pageKey: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(_storageKey(pageKey))
    if (raw === '1') return true
    if (raw === '0') return false
  } catch { /* noop */ }
  return fallback
}


function _writeCollapsed(pageKey: string, collapsed: boolean): void {
  try {
    localStorage.setItem(_storageKey(pageKey), collapsed ? '1' : '0')
  } catch { /* noop */ }
}


export default function FloatingSectionNav({
  pageKey, minSections = 3, defaultCollapsed,
}: FloatingSectionNavProps) {
  const [sections, setSections] = useState<DiscoveredSection[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  // Distinct desktop / mobile collapse state. Both default to
  // COLLAPSED — UAT 2026-05-24: the previous always-expanded desktop
  // panel covered the right edge of analytics charts. A first-time
  // visitor sees the small anchor tab and clicks to expand only when
  // they want the TOC; subsequent toggles persist via localStorage.
  const [desktopCollapsed, setDesktopCollapsed] = useState(() =>
    _readCollapsed(`${pageKey}_desktop`, defaultCollapsed ?? true))
  const [mobileOpen, setMobileOpen] = useState(() =>
    _readCollapsed(`${pageKey}_mobile`, false) === false ? false : false)
  // Ref on the desktop panel so a click-outside listener can tell
  // whether a click landed inside the expanded TOC (preserve open
  // state — the user is interacting with the nav) or outside
  // (collapse — the user moved on). Attached only while expanded
  // so the listener isn't running pointlessly when the panel is
  // already closed.
  const desktopRef = useRef<HTMLElement | null>(null)

  // Discover sections from the DOM. Re-runs after every page mount
  // and whenever the route changes (the cleanup function clears
  // state so a navigation away resets cleanly).
  useEffect(() => {
    const discover = () => {
      const els = Array.from(
        document.querySelectorAll<HTMLElement>('[data-section-id]'))
      const found = els.map((el) => ({
        id:    el.getAttribute('data-section-id') || '',
        label: el.getAttribute('data-section-label') || el.id || '(section)',
      })).filter((s) => s.id)
      setSections(found)
    }
    // Discover synchronously, then again after a tick so lazily
    // rendered sections (e.g. tab switches, conditional renders)
    // catch up.
    discover()
    const t1 = setTimeout(discover, 100)
    const t2 = setTimeout(discover, 500)
    return () => {
      clearTimeout(t1)
      clearTimeout(t2)
      setSections([])
      setActiveId(null)
    }
  }, [pageKey])

  // Track which section is currently in view. The active section
  // is the one whose top is closest to (but not below) the
  // viewport top + 80px buffer (which accounts for the fixed
  // nav-ribbon height).
  useEffect(() => {
    if (sections.length === 0) return
    // Guard for environments without IntersectionObserver — jsdom
    // doesn't ship one, so the test suite would otherwise throw
    // on mount. In those environments the nav still renders + the
    // click-to-jump still works; only the active-section
    // highlight is suppressed.
    if (typeof IntersectionObserver === 'undefined') return
    const observed: Element[] = []
    const observer = new IntersectionObserver((entries) => {
      // Pick the topmost intersecting section. If none intersect
      // (e.g. between sections), keep the prior active state.
      const intersecting = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) =>
          a.boundingClientRect.top - b.boundingClientRect.top)
      if (intersecting.length > 0) {
        const id = (intersecting[0].target as HTMLElement)
          .getAttribute('data-section-id')
        if (id) setActiveId(id)
      }
    }, {
      // The rootMargin pushes the intersection zone DOWN by the
      // nav-ribbon height, so the "active" section is the one
      // visible below the fixed nav, not the one already scrolling
      // past underneath it.
      rootMargin: '-80px 0px -60% 0px',
      threshold: [0, 0.1, 0.5, 1.0],
    })
    for (const s of sections) {
      const el = document.querySelector(
        `[data-section-id="${s.id}"]`)
      if (el) {
        observer.observe(el)
        observed.push(el)
      }
    }
    return () => {
      for (const el of observed) observer.unobserve(el)
      observer.disconnect()
    }
  }, [sections])

  const scrollTo = useCallback((id: string) => {
    const el = document.querySelector(
      `[data-section-id="${id}"]`) as HTMLElement | null
    if (!el) return
    // 64px offset keeps the section heading clear of the fixed
    // nav ribbon when the browser lands.
    const top = el.getBoundingClientRect().top + window.scrollY - 64
    window.scrollTo({ top, behavior: 'smooth' })
    // On mobile, auto-collapse the drawer after a section pick
    // so the user sees the content they navigated to.
    setMobileOpen(false)
    _writeCollapsed(`${pageKey}_mobile`, false)
  }, [pageKey])

  const toggleDesktop = useCallback(() => {
    setDesktopCollapsed((prev) => {
      const next = !prev
      _writeCollapsed(`${pageKey}_desktop`, next)
      return next
    })
  }, [pageKey])

  // Click-outside collapses the expanded panel. Listener attached
  // ONLY while expanded so the listener isn't wasting cycles in
  // the collapsed state. Mousedown (not click) so a drag started
  // outside doesn't accidentally collapse mid-drag. Capture
  // phase: ensures we see the event before any inner onClick
  // could stopPropagation.
  useEffect(() => {
    if (desktopCollapsed) return
    const handler = (e: MouseEvent) => {
      const el = desktopRef.current
      if (!el) return
      if (el.contains(e.target as Node)) return
      setDesktopCollapsed(true)
      _writeCollapsed(`${pageKey}_desktop`, true)
    }
    document.addEventListener('mousedown', handler, true)
    return () => {
      document.removeEventListener('mousedown', handler, true)
    }
  }, [desktopCollapsed, pageKey])

  // Esc collapses. Same mount/unmount discipline as click-outside.
  useEffect(() => {
    if (desktopCollapsed) return
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setDesktopCollapsed(true)
      _writeCollapsed(`${pageKey}_desktop`, true)
    }
    document.addEventListener('keydown', handler)
    return () => {
      document.removeEventListener('keydown', handler)
    }
  }, [desktopCollapsed, pageKey])

  const toggleMobile = useCallback(() => {
    setMobileOpen((prev) => {
      const next = !prev
      _writeCollapsed(`${pageKey}_mobile`, next)
      return next
    })
  }, [pageKey])

  // Suppress the nav on a page with too few sections.
  if (sections.length < minSections) return null

  return (
    <>
      {/* Desktop — two distinct visual modes (UAT 2026-05-24).
          COLLAPSED: a small icon-only tab at the right edge, 60%
                     opacity at rest, 100% on hover. Sits flush to
                     the right viewport edge (right-0) with a
                     rounded-l-md so it reads as a tab clipped to
                     the page, not an opaque panel. The collapsed
                     footprint is 32px wide — narrow enough that
                     it never overlaps the main content column on
                     any of the wired pages.
          EXPANDED:  a 200px panel anchored at right-3, opacity-95
                     so it sits clearly above content but isn't
                     visually heavy. Activated only by an explicit
                     click on the collapsed tab; click-outside or
                     Esc collapses it. No hover-expand.

          Mounted unconditionally so the same DOM element drives
          both states — keeps the click-outside ref stable across
          toggles. */}
      <aside
        ref={(el) => { desktopRef.current = el }}
        data-testid="floating-section-nav"
        data-page-key={pageKey}
        data-collapsed={desktopCollapsed ? 'true' : 'false'}
        className={
          'hidden md:flex fixed z-30 flex-col ' +
          'border border-navy-700 shadow-2xl backdrop-blur-sm ' +
          'transition-[width,opacity] duration-150 ease-out ' +
          (desktopCollapsed
            // Collapsed: right-edge tab, narrow, low-opacity. The
            // hover opacity-100 keeps the affordance discoverable
            // without auto-expanding (per UAT directive: NO hover
            // expansion, hover only highlights the toggle).
            ? 'right-0 top-32 w-8 rounded-l-md bg-navy-900/80 ' +
              'opacity-60 hover:opacity-100'
            // Expanded: stepped in from the edge, 200px wide,
            // mostly opaque. max-h-[60vh] caps the height so a
            // page with many sections stays scroll-contained.
            : 'right-3 top-32 w-52 max-h-[60vh] rounded-lg ' +
              'bg-navy-900/95 opacity-100'
          )
        }
        aria-label="Section navigator">
        {desktopCollapsed ? (
          /* Collapsed mode — single tap target. Tall enough to be
             easy to hit (44px) without dominating the viewport. */
          <button
            type="button"
            onClick={toggleDesktop}
            data-testid="floating-section-nav-toggle"
            aria-label="Expand section navigator"
            aria-expanded="false"
            className={
              'flex items-center justify-center ' +
              'w-full h-11 text-text-muted ' +
              'hover:text-white hover:bg-navy-800/60 ' +
              'rounded-l-md transition-colors'
            }>
            <List className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
        ) : (
          <>
            <header
              className={
                'flex items-center justify-between gap-2 px-3 py-2 ' +
                'border-b border-navy-700 shrink-0'
              }>
              <span className="text-2xs uppercase tracking-wider text-text-muted">
                Sections
              </span>
              <button
                type="button"
                onClick={toggleDesktop}
                data-testid="floating-section-nav-toggle"
                aria-label="Collapse section navigator"
                aria-expanded="true"
                className="text-text-muted hover:text-white p-1
                           min-h-[24px] min-w-[24px] rounded
                           hover:bg-navy-700 transition-colors">
                <X className="w-3.5 h-3.5" />
              </button>
            </header>
            <nav className="overflow-y-auto py-1.5">
              <ul className="space-y-0.5">
                {sections.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => scrollTo(s.id)}
                      data-testid={`floating-section-nav-link-${s.id}`}
                      data-active={s.id === activeId ? 'true' : 'false'}
                      className={
                        'w-full text-left px-3 py-1.5 text-xs ' +
                        'transition-colors truncate ' +
                        (s.id === activeId
                          ? 'text-electric-blue bg-electric-blue/10 ' +
                            'border-l-2 border-electric-blue pl-[10px]'
                          : 'text-text-secondary border-l-2 border-transparent ' +
                            'hover:text-white hover:bg-navy-800')
                      }>
                      {s.label}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          </>
        )}
      </aside>

      {/* Mobile bottom drawer — collapsed by default, tap the
          handle to expand. Sits above the iOS Safari bottom
          safe area + above the Advisor button. */}
      <div
        data-testid="floating-section-nav-mobile"
        className="md:hidden fixed left-0 right-0 bottom-0 z-30
                   pointer-events-none">
        <div
          className={
            'pointer-events-auto mx-auto max-w-screen-md ' +
            'bg-navy-900 border-t border-navy-700 ' +
            'rounded-t-lg shadow-2xl ' +
            'transition-transform duration-200 ease-out ' +
            (mobileOpen
              ? 'translate-y-0'
              : 'translate-y-[calc(100%-44px)]')
          }
          style={{
            // Respect iOS bottom safe area so the drawer doesn't
            // hide behind the home indicator.
            paddingBottom: 'env(safe-area-inset-bottom)',
          }}>
          <button
            type="button"
            onClick={toggleMobile}
            data-testid="floating-section-nav-mobile-toggle"
            aria-expanded={mobileOpen}
            aria-label={mobileOpen
              ? 'Collapse section navigator'
              : 'Expand section navigator'}
            className="w-full flex items-center justify-between
                       gap-2 px-4 py-2.5 min-h-[44px]
                       border-b border-navy-700">
            <div className="flex items-center gap-2">
              <List className="w-4 h-4 text-electric-blue" />
              <span className="text-xs font-medium text-white">
                Sections
              </span>
              {activeId ? (
                <span className="text-2xs text-text-muted truncate
                                 max-w-[160px]">
                  · {
                    sections.find((s) => s.id === activeId)?.label
                    || ''
                  }
                </span>
              ) : null}
            </div>
            {mobileOpen ? (
              <X className="w-4 h-4 text-text-muted" />
            ) : (
              <ChevronUp className="w-4 h-4 text-text-muted" />
            )}
          </button>
          {mobileOpen ? (
            <nav className="overflow-y-auto max-h-[50vh] py-1.5">
              <ul className="space-y-0.5">
                {sections.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => scrollTo(s.id)}
                      data-testid={`floating-section-nav-mobile-link-${s.id}`}
                      data-active={s.id === activeId ? 'true' : 'false'}
                      className={
                        'w-full text-left px-4 py-3 text-sm ' +
                        'min-h-[44px] transition-colors ' +
                        (s.id === activeId
                          ? 'text-electric-blue bg-electric-blue/10 ' +
                            'border-l-4 border-electric-blue pl-3'
                          : 'text-text-secondary border-l-4 border-transparent ' +
                            'active:bg-navy-800')
                      }>
                      {s.label}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          ) : null}
        </div>
      </div>
    </>
  )
}
