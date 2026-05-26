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


// One-time-ever discovery pulse. UAT 2026-05-24 — the redesigned
// collapsed tab is intentionally subtle so it never competes with
// the dashboard charts for attention; a first-time visitor might
// not notice it. A gentle pulse on the FIRST mount EVER (per
// browser, across every page in the app) draws the eye once and
// then never again. The flag is written to localStorage the moment
// the pulse class is applied, so subsequent navigations and future
// sessions render the tab statically.
const _PULSE_FLAG_KEY = 'fc_floating_nav_pulse_shown_v1'

function _shouldPulseOnce(): boolean {
  try {
    return localStorage.getItem(_PULSE_FLAG_KEY) !== '1'
  } catch {
    return false
  }
}

function _markPulseShown(): void {
  try {
    localStorage.setItem(_PULSE_FLAG_KEY, '1')
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

  // Discovery pulse — true on the FIRST mount EVER (per browser).
  // Initialised from localStorage so a remount on the same page
  // immediately reads "already pulsed" and renders the tab
  // statically. The pulse class is applied to the collapsed tab
  // for two seconds, then a setTimeout clears it AND writes the
  // localStorage flag — guaranteeing the animation runs exactly
  // once across every page in the app.
  const [showPulse, setShowPulse] = useState<boolean>(() =>
    _shouldPulseOnce())
  useEffect(() => {
    if (!showPulse) return
    const t = setTimeout(() => {
      setShowPulse(false)
      _markPulseShown()
    }, 2500)
    return () => clearTimeout(t)
  }, [showPulse])

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
    // SCROLL-TARGET FIX (UAT 2026-05-24): the page content scrolls
    // INSIDE MainLayout's <main class="flex-1 overflow-y-auto">,
    // NOT the window. The window itself never scrolls — its height
    // is locked at 100vh by the app-shell flex layout. The prior
    // implementation called window.scrollTo() which is a silent
    // no-op against an unscrollable window, so clicking a section
    // appeared to do nothing.
    //
    // Find the nearest scrollable ancestor (<main>) and scroll IT.
    // The scroll target is computed in the scrollable element's
    // local coordinates: parent.scrollTop + (rect.top - parentRect.top)
    // gives the section's absolute offset within the scrollable
    // container. A small 16px breathing room above the section
    // keeps its heading clear of any sticky chrome inside <main>.
    //
    // Fall through to el.scrollIntoView for any future layout
    // where <main> isn't present (login page, full-screen modals)
    // — scrollIntoView finds the nearest scrollable ancestor on
    // its own, so the worst case is "no scroll target found, no
    // scroll happens" — the same as before, never an exception.
    const scrollable = el.closest('main') as HTMLElement | null
    if (scrollable) {
      const rect = el.getBoundingClientRect()
      const parentRect = scrollable.getBoundingClientRect()
      const target = scrollable.scrollTop
        + (rect.top - parentRect.top) - 16
      scrollable.scrollTo({ top: target, behavior: 'smooth' })
    } else {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    // Collapse on pick — UAT directive: clicking a section should
    // scroll AND collapse the nav, not just one or the other.
    // Both desktop and mobile collapse flags are written so the
    // localStorage state matches the UI state for the next mount.
    setMobileOpen(false)
    _writeCollapsed(`${pageKey}_mobile`, false)
    setDesktopCollapsed(true)
    _writeCollapsed(`${pageKey}_desktop`, true)
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

  // Mobile-drawer clearance for the scrollable main column. The
  // mobile drawer is `fixed bottom-0` and 44px tall when collapsed;
  // without bottom padding on <main>, the page's last content (the
  // Academic Review red error card the user reported as 'hidden')
  // sits behind it. Inject a class onto the page's scrollable
  // ancestor while this component is mounted; remove on unmount so
  // pages without a section nav don't keep the padding. md:hidden
  // (no drawer on desktop) is enforced via the data attribute the
  // CSS rule targets.
  useEffect(() => {
    if (sections.length < minSections) return
    const scrollable = (document.querySelector('main') as HTMLElement | null)
    if (!scrollable) return
    scrollable.dataset.fcMobileNavSpacer = 'true'
    return () => {
      delete scrollable.dataset.fcMobileNavSpacer
    }
  }, [sections, minSections])

  // Suppress the nav on a page with too few sections.
  if (sections.length < minSections) return null

  return (
    <>
      {/* Desktop — two visual modes (UAT 2026-05-24 iteration 2).
          The first iteration was a 32px icon-only tab — testers
          reported it was too easy to miss. The second iteration
          (this one) strikes a balance: a small but visible PILL
          with a List icon + the literal text "Sections", a
          semi-transparent navy background and a 1px border so it
          reads as a clickable control rather than an icon floating
          in space. Anchored to the right edge of the viewport and
          vertically centered so it never overlaps the dashboard
          summary tiles (which sit in the top third) or the main
          chart column (which is centered).

          COLLAPSED: ~104px pill, vertically centred, semi-
                     transparent navy bg with visible border. Pulses
                     once on the user's first ever mount to draw the
                     eye (localStorage flag — never replays).
          EXPANDED:  220px panel at right-3, top-1/2 (vertically
                     centered), opacity-95 so it reads clearly
                     above content. Activated by an explicit click;
                     click-outside or Esc collapses it. NO hover-
                     expand — hover only highlights the toggle.

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
          'shadow-2xl backdrop-blur-sm ' +
          'transition-[width,opacity,right] duration-150 ease-out ' +
          // Vertical centering via top-1/2 + translate-y-1/2 keeps
          // the nav clear of both the page header (top third) AND
          // the AdvisorPanel button at the bottom-right. Same
          // anchor for both states so toggling doesn't reposition.
          'top-1/2 -translate-y-1/2 ' +
          (desktopCollapsed
            // Collapsed: pill anchored at right-0, rounded only on
            // the left so it reads as a tab clipped to the edge.
            // The pulse class is applied only on the first ever
            // mount; the keyframes live in index.css.
            ? 'right-0 w-auto rounded-l-lg ' +
              'border-y border-l border-electric-blue/40 ' +
              'bg-navy-900/90 hover:bg-navy-900/95 ' +
              (showPulse ? 'fc-floating-nav-pulse' : '')
            // Expanded: stepped in from the edge, wider, more
            // opaque. max-h-[70vh] caps the height so a page with
            // many sections stays scroll-contained.
            : 'right-3 w-56 max-h-[70vh] rounded-lg ' +
              'border border-navy-600 bg-navy-900/95 opacity-100'
          )
        }
        aria-label="Section navigator">
        {desktopCollapsed ? (
          /* Collapsed mode — a single pill with icon AND the
             literal text "Sections". The text is the affordance
             the icon-only version lacked: a first-time visitor
             reads it as "click here for a section navigator", not
             as "an icon floating in space". title= renders as the
             browser tooltip when the user hovers — answers the
             "Jump to section" hint the user requested without
             adding a custom tooltip layer. */
          <button
            type="button"
            onClick={toggleDesktop}
            data-testid="floating-section-nav-toggle"
            aria-label="Open section navigator"
            aria-expanded="false"
            title="Jump to section"
            className={
              'flex items-center gap-2 px-3 py-2 min-h-[40px] ' +
              'text-xs font-medium tracking-wide ' +
              'text-text-secondary hover:text-white ' +
              'transition-colors rounded-l-lg'
            }>
            <List className="w-3.5 h-3.5 text-electric-blue"
                  aria-hidden="true" />
            <span>Sections</span>
          </button>
        ) : (
          <>
            <header
              className={
                'flex items-center justify-between gap-2 px-3.5 py-2.5 ' +
                'border-b border-navy-700 shrink-0'
              }>
              <div className="flex items-center gap-2">
                <List className="w-3.5 h-3.5 text-electric-blue"
                      aria-hidden="true" />
                <span className="text-xs font-semibold text-white">
                  Sections
                </span>
              </div>
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
            <nav className="overflow-y-auto py-2">
              <ul className="space-y-0.5">
                {sections.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => scrollTo(s.id)}
                      data-testid={`floating-section-nav-link-${s.id}`}
                      data-active={s.id === activeId ? 'true' : 'false'}
                      className={
                        // Larger px/py + slightly bigger font + a
                        // proper hover/active contrast so the
                        // expanded list is easy to scan. UAT
                        // 2026-05-24 iteration 2: the previous
                        // px-3 py-1.5 text-xs was too cramped.
                        'w-full text-left px-3.5 py-2 text-sm ' +
                        'transition-colors truncate ' +
                        (s.id === activeId
                          ? 'text-electric-blue bg-electric-blue/15 ' +
                            'font-medium ' +
                            'border-l-2 border-electric-blue pl-[12px]'
                          : 'text-text-secondary border-l-2 border-transparent ' +
                            'hover:text-white hover:bg-navy-800/70')
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
