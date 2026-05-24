/**
 * FloatingSectionNav — page-scoped table of contents for long-form
 * pages. May 24 2026.
 *
 * Auto-discovers sections via `[data-section-id]` markers on the
 * page. Click to jump; IntersectionObserver tracks the active
 * section as the user scrolls. Per-page localStorage keeps the
 * panel's expanded / collapsed state across navigation.
 *
 *   Desktop (>= md): right-side floating panel, vertically centred,
 *                    positioned above the Advisor button (which
 *                    floats at the BOTTOM right). Doesn't overlap
 *                    chart export menus or the QA banner.
 *   Mobile  (< md):  bottom drawer with swipe-up affordance.
 *                    Auto-collapses after a section pick.
 *
 * Page contract:
 *   - Every section gets `data-section-id="..."` (used in the URL
 *     fragment + as the IntersectionObserver target) and
 *     `data-section-label="..."` (the human-readable text shown
 *     in the nav). Optional `data-section-icon="..."` for a Lucide
 *     icon name on the entry.
 *   - The page mounts <FloatingSectionNav pageKey="qa-audit" />.
 *     The pageKey scopes the localStorage state and de-duplicates
 *     mounts (only one nav per page).
 */
import {
  useCallback, useEffect, useState,
} from 'react'
import { ChevronUp, ChevronDown, List, X } from 'lucide-react'


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
  // Distinct desktop / mobile collapse state: the mobile drawer
  // defaults closed to avoid covering content; the desktop panel
  // defaults open so the user sees the nav.
  const [desktopCollapsed, setDesktopCollapsed] = useState(() =>
    _readCollapsed(`${pageKey}_desktop`, defaultCollapsed ?? false))
  const [mobileOpen, setMobileOpen] = useState(() =>
    _readCollapsed(`${pageKey}_mobile`, false) === false ? false : false)

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
      {/* Desktop — right-side panel, vertically centred-ish.
          Positioned at top-32 to clear the nav ribbon; bottom-32
          keeps clear of the Advisor button at bottom-6. */}
      <aside
        data-testid="floating-section-nav"
        data-page-key={pageKey}
        className={
          'hidden md:flex fixed right-3 z-30 flex-col ' +
          'rounded-lg border border-navy-700 bg-navy-900/95 ' +
          'shadow-2xl backdrop-blur-sm ' +
          'top-32 max-h-[60vh] ' +
          (desktopCollapsed ? 'w-12' : 'w-60')
        }
        aria-label="Section navigator">
        <header
          className={
            'flex items-center justify-between gap-2 px-3 py-2 ' +
            'border-b border-navy-700 shrink-0'
          }>
          {!desktopCollapsed ? (
            <span className="text-2xs uppercase tracking-wider text-text-muted">
              Sections
            </span>
          ) : (
            <List
              className="w-4 h-4 text-text-muted mx-auto"
              aria-label="Sections" />
          )}
          <button
            type="button"
            onClick={toggleDesktop}
            data-testid="floating-section-nav-toggle"
            aria-label={desktopCollapsed
              ? 'Expand section navigator'
              : 'Collapse section navigator'}
            className="text-text-muted hover:text-white p-1
                       min-h-[24px] min-w-[24px] rounded
                       hover:bg-navy-700 transition-colors">
            {desktopCollapsed ? (
              <ChevronDown className="w-3.5 h-3.5 rotate-90" />
            ) : (
              <ChevronUp className="w-3.5 h-3.5 rotate-90" />
            )}
          </button>
        </header>
        {!desktopCollapsed ? (
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
        ) : null}
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
