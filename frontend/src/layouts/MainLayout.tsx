import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard, Users, ShieldCheck, Settings, HelpCircle, BarChart3,
  Activity, FileText, LineChart, Menu, X, LogOut,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAuth } from '../App'
import { useSession } from '../context/SessionContext'
import { useActivityTracking } from '../lib/useActivityTracking'
import { useBrand, BRANDS } from '../context/BrandContext'
import { useUI } from '../context/UIContext'
import type { UIMode } from '../context/UIContext'
import { useQAStore } from '../stores/qaStore'
import { useReportWriterStore } from '../stores/reportWriterStore'
import { Loader2, CheckCircle, XCircle } from 'lucide-react'
import QAStatusBadge from '../components/QAStatusBadge'
import LearnModeToggle from '../components/LearnModeToggle'
import AdvisorPanel from '../components/AdvisorPanel'
import WhatsNewModal from '../components/WhatsNewModal'
import GenerationToast from '../components/GenerationToast'
import SiteTour from '../components/SiteTour'
import TestRunner from '../components/TestRunner'
import TestNotifications from '../components/TestNotifications'
import VisitorWelcomeBanner from '../components/VisitorWelcomeBanner'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

interface NavGroup {
  label: string
  items: NavItem[]
}

// The nav is grouped into three sections. Desktop renders the items
// flat (NAV_ITEMS); the mobile drawer renders them grouped with a
// section label per group.
const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Analysis',
    items: [
      { to: '/',                     label: 'Dashboard',            icon: LayoutDashboard, end: true },
      { to: '/analytics',            label: 'Analytics',            icon: LineChart },
      { to: '/statistical-evidence', label: 'Statistical Evidence', icon: BarChart3 },
      { to: '/regime-analysis',      label: 'Regime Analysis',      icon: Activity },
    ],
  },
  {
    label: 'AI and Review',
    items: [
      { to: '/council', label: 'Council',  icon: Users },
      { to: '/qa',      label: 'QA Audit', icon: ShieldCheck },
    ],
  },
  {
    label: 'Output',
    items: [
      { to: '/reports', label: 'Reports', icon: FileText },
    ],
  },
]

const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items)

interface ModeOption {
  value: UIMode
  label: string
}

const MODE_OPTIONS: ModeOption[] = [
  { value: 'analyst',     label: 'Analyst' },
  { value: 'commentary',  label: '💬 Commentary' },
  { value: 'present',     label: '⊞ Present' },
]

function QuLogo() {
  return (
    <div
      className="w-8 h-8 rounded flex items-center justify-center shrink-0 text-xs font-bold tracking-tight"
      style={{ background: 'rgba(180,83,9,0.12)', border: '1px solid rgba(180,83,9,0.35)', color: '#b45309' }}
    >
      QU
    </div>
  )
}

function FcLogo() {
  return (
    <div
      className="w-8 h-8 rounded flex items-center justify-center shrink-0 text-xs font-bold tracking-tight"
      style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.3)', color: '#3b82f6' }}
    >
      FC
    </div>
  )
}

/**
 * The three-mode selector (Analyst / Commentary / Present). Used both in
 * the desktop nav bar (horizontal, bordered) and the mobile drawer
 * (vertical, full-width). Present mode is gated on the QA audit status —
 * the gate logic is identical in both layouts.
 */
function ModeSelector({ vertical = false, onSelect }: {
  vertical?: boolean
  onSelect?: () => void
}) {
  const { mode, setMode } = useUI()
  const { status: qaStatus, tieredStatus } = useQAStore()
  const navigate = useNavigate()

  return (
    <div
      className={vertical
        ? 'flex flex-col gap-1'
        : 'hidden lg:flex items-center rounded border border-border overflow-hidden shrink-0'}
    >
      {MODE_OPTIONS.map((opt) => {
        const isActive = mode === opt.value
        const isPresent = opt.value === 'present'

        // Present-mode gate — prefer tieredStatus (≥WARN + <48h + hash
        // match) over the legacy local-audit qaStatus.
        const tieredAllowed = tieredStatus?.present_mode_allowed
        const tieredVerdict = tieredStatus?.verdict
        const ageHours = tieredStatus?.age_hours ?? null

        const effectiveStatus: typeof qaStatus = tieredVerdict
          ? (tieredVerdict === 'PASS' ? 'pass'
            : tieredVerdict === 'WARN' ? 'warn'
            : tieredVerdict === 'FAIL' ? 'fail'
            : 'unknown')
          : qaStatus

        const presentBlocked = isPresent && (
          effectiveStatus === 'unknown'
          || effectiveStatus === 'fail'
          || (tieredStatus !== null && !tieredAllowed && effectiveStatus !== 'running')
        )
        const presentWarn = isPresent && effectiveStatus === 'warn' && (tieredAllowed ?? true)

        const presentTitle = isPresent
          ? effectiveStatus === 'unknown'
            ? 'Run QA Audit before presenting'
            : effectiveStatus === 'fail'
              ? 'QA audit failed — review issues before presenting to Forest Capital'
              : ageHours !== null && ageHours >= 48
                ? `QA audit is ${ageHours.toFixed(0)}h old (>48h) — re-run before presenting`
                : effectiveStatus === 'warn'
                  ? 'QA: WARN — review limitations before presenting'
                  : ''
          : ''

        const handleClick = () => {
          if (isPresent && (effectiveStatus === 'unknown' || (ageHours !== null && ageHours >= 48))) {
            navigate('/qa')
            onSelect?.()
            return
          }
          if (isPresent && effectiveStatus === 'fail') {
            return
          }
          setMode(opt.value)
          onSelect?.()
        }

        const stateCls = presentBlocked
          ? 'text-muted/50 cursor-not-allowed'
          : isActive
            ? opt.value === 'present'
              ? 'bg-warning/20 text-warning font-medium'
              : 'bg-electric/15 text-electric font-medium'
            : 'text-muted hover:text-white hover:bg-navy-700'

        return (
          <button
            key={opt.value}
            onClick={handleClick}
            title={presentTitle}
            className={vertical
              ? `w-full min-h-[44px] flex items-center justify-between px-3
                 rounded text-sm transition-colors ${stateCls}`
              : `px-2.5 py-1 text-xs transition-colors whitespace-nowrap
                 flex items-center gap-1 ${stateCls}`}
          >
            <span>{opt.label}</span>
            <span className="flex items-center gap-1">
              {isPresent && effectiveStatus === 'fail' && <span className="text-red-400 text-[10px]">🔒</span>}
              {isPresent && effectiveStatus === 'unknown' && <span className="text-muted/60 text-[10px]">○</span>}
              {presentWarn && <span className="text-warning text-[10px]">⚠</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/**
 * The mobile / tablet navigation drawer — a left slide-in panel shown
 * below the lg breakpoint in place of the horizontal nav. Holds the
 * grouped nav items, the mode switcher, and the account controls.
 */
function MobileNavDrawer({ open, onClose }: {
  open: boolean
  onClose: () => void
}) {
  const { session, logout } = useAuth()
  const { brand } = useBrand()
  const navigate = useNavigate()
  const isMcColl = brand === BRANDS.MCCOLL

  // Escape closes the drawer.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const handleSignOut = async () => {
    onClose()
    await logout()
    navigate('/login')
  }

  // Wrapper is lg:hidden so the drawer can never appear on desktop even
  // if `open` is still true after a resize.
  if (!open) return null

  return (
    <div className="lg:hidden" role="dialog" aria-label="Navigation menu" aria-modal="true">
      {/* Dark overlay — click to close. */}
      <div
        className="fixed inset-0 z-[60] bg-black/60 animate-[fc-fade-in_150ms_ease-out]"
        onClick={onClose}
        aria-hidden="true"
        data-testid="nav-drawer-overlay"
      />
      {/* Drawer panel. */}
      <aside
        className="fixed inset-y-0 left-0 z-[61] w-[300px] max-w-[85vw]
                   bg-navy-800 border-r border-border flex flex-col
                   animate-[fc-slide-in-left_200ms_ease-out]"
        data-testid="nav-drawer"
      >
        {/* Header — logo + app name. The hamburger ✕ floats above this
            from the nav bar (z-[62]); pl-12 keeps the name clear of it. */}
        <div className="h-14 shrink-0 flex items-center gap-2.5 pl-12 pr-4
                        border-b border-border">
          {isMcColl ? <QuLogo /> : <FcLogo />}
          <span className="text-white font-semibold text-sm tracking-wide truncate">
            {isMcColl ? 'McColl School' : 'Forest Capital'}
          </span>
        </div>

        {/* Grouped nav items. */}
        <nav className="flex-1 overflow-y-auto py-2">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="py-1">
              <div className="px-4 py-1.5 text-2xs font-semibold uppercase
                              tracking-widest text-muted">
                {group.label}
              </div>
              {group.items.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end ?? false}
                  onClick={onClose}
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-4 min-h-[44px] text-sm
                     transition-colors ${
                      isActive
                        ? 'bg-electric/10 text-electric border-l-2 border-electric'
                        : 'text-slate-300 hover:text-white hover:bg-navy-700 border-l-2 border-transparent'
                    }`
                  }
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  {label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* Mode switcher. */}
        <div className="shrink-0 border-t border-border px-4 py-3">
          <div className="text-2xs font-semibold uppercase tracking-widest
                          text-muted mb-1.5">
            View mode
          </div>
          <ModeSelector vertical onSelect={onClose} />
        </div>

        {/* Account — email + sign out. */}
        <div className="shrink-0 border-t border-border px-4 py-3
                        flex items-center justify-between gap-2">
          <span className="text-2xs text-muted font-mono truncate">
            {session?.email}
          </span>
          <button
            onClick={() => void handleSignOut()}
            className="flex items-center gap-1.5 min-h-[44px] px-3 rounded
                       text-xs text-slate-300 hover:text-white hover:bg-navy-700
                       transition-colors shrink-0"
          >
            <LogOut className="w-3.5 h-3.5" />
            Sign out
          </button>
        </div>
      </aside>
    </div>
  )
}

export default function MainLayout() {
  const { session, logout } = useAuth()
  const { sessionType } = useSession()
  const { brand } = useBrand()
  const navigate = useNavigate()
  // Shared QA status — QAStatusBadge polls /api/v1/qa/status into this
  // store; the nav-ribbon "QA Running" pill reads it.
  const { status: qaStatus } = useQAStore()

  const [drawerOpen, setDrawerOpen] = useState(false)

  // Starts the batched activity logger and emits a page_view on every
  // route change. Mounted here so it runs for the whole authenticated app.
  useActivityTracking()

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  const isMcColl = brand === BRANDS.MCCOLL

  return (
    <div className="h-screen bg-navy-900 flex flex-col overflow-hidden">
      {/* Top nav — shrink-0 ensures it never scrolls out of view */}
      <header className="h-14 border-b border-border flex items-center
                         px-3 sm:px-6 shrink-0 bg-navy-800">
        {/* Hamburger — mobile/tablet only. z-[62] keeps it clickable above
            the open drawer so it can animate ☰ → ✕. */}
        <button
          type="button"
          onClick={() => setDrawerOpen((v) => !v)}
          aria-label={drawerOpen ? 'Close navigation menu' : 'Open navigation menu'}
          aria-expanded={drawerOpen}
          className="lg:hidden relative z-[62] flex items-center justify-center
                     w-11 h-11 -ml-1 mr-1 rounded text-muted hover:text-white
                     hover:bg-navy-700 transition-colors"
          data-testid="nav-hamburger"
        >
          {drawerOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>

        {/* Brand */}
        <div className="flex items-center gap-2.5 mr-4 lg:mr-8 min-w-0">
          {isMcColl ? <QuLogo /> : <FcLogo />}
          <span className="text-white font-semibold text-sm tracking-wide truncate">
            {isMcColl ? 'McColl School of Business' : 'Forest Capital'}
          </span>
          <span className="hidden sm:inline text-muted text-xs ml-1">Portfolio Intelligence System</span>
        </div>

        {/* Nav links — horizontal, desktop (lg) and up only */}
        <nav className="hidden lg:flex items-center gap-1 flex-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end ?? false}
              data-tour={to === '/' ? 'nav-dashboard' : undefined}
              className={({ isActive }) =>
                `flex items-center gap-1.5 px-3 py-1.5 rounded text-sm transition-colors ${
                  isActive
                    ? 'bg-electric/10 text-electric border border-electric/20'
                    : 'text-muted hover:text-white hover:bg-navy-700'
                }`
              }
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
              {to === '/reports' ? <ReportWriterBadge /> : null}
            </NavLink>
          ))}
        </nav>

        {/* Spacer pushes the right-side controls to the edge on mobile,
            where the horizontal nav (flex-1) is not rendered. */}
        <div className="flex-1 lg:hidden" />

        {/* Right side: mode selector + user + settings + logout.
            relative z-[100] keeps these controls clickable while the
            Site Tour is running — the Joyride overlay sits at z-90
            and otherwise intercepts every click on this cluster
            (UAT feedback #1 May 22 2026: account / Settings icon
            unresponsive during tour autolaunch). Sits below the
            mobile nav drawer (z-[61]) and hamburger (z-[62]) too,
            which is fine — those exit the tour by their own click
            handlers if they need to. */}
        <div className="relative z-[100] flex items-center gap-2 lg:gap-3 lg:ml-4">
          {/* Three-mode selector — desktop only; on mobile it lives in
              the drawer. (ModeSelector self-hides below lg.) */}
          <ModeSelector />

          {/* Commentary sub-toggle and QA badge — desktop only, to keep
              the mobile nav bar uncluttered. */}
          <div className="hidden lg:flex items-center gap-3">
            <LearnModeToggle />
            <QAStatusBadge />
          </div>

          {/* QA running indicator — a QA audit (methodology or
              statistical) is in progress. Driven by the same qaStore
              status QAStatusBadge polls, so it is visible to every
              logged-in user and disappears automatically when the run
              completes. Mirrors the Testing Mode pill below. */}
          {qaStatus === 'running' && (
            <button
              type="button"
              onClick={() => navigate('/qa')}
              title="A QA audit is running — click to view progress"
              className="flex items-center gap-1 min-h-[44px] sm:min-h-0
                         px-2 py-1 rounded-full text-2xs font-medium
                         bg-warning/15 text-warning border border-warning/40
                         hover:bg-warning/25 transition-colors
                         whitespace-nowrap shrink-0"
              style={{ boxShadow: '0 0 10px rgba(245,158,11,0.35)' }}
            >
              <span aria-hidden="true">⚙️</span>
              <span className="hidden min-[380px]:inline">QA Running</span>
            </button>
          )}

          {/* Testing Mode indicator — shown when the session is banded as
              testing. On the smallest screens (<380px) the label drops to
              the 🧪 glyph only. */}
          {sessionType === 'testing' && (
            <button
              type="button"
              onClick={() => navigate('/settings#account')}
              title="Testing Mode active — this session's activity is logged as testing. Click to manage."
              className="flex items-center gap-1 min-h-[44px] sm:min-h-0
                         px-2 py-1 rounded-full text-2xs font-medium
                         bg-warning/15 text-warning border border-warning/40
                         hover:bg-warning/25 transition-colors
                         whitespace-nowrap shrink-0"
              style={{ boxShadow: '0 0 10px rgba(245,158,11,0.35)' }}
            >
              <span aria-hidden="true">🧪</span>
              <span className="hidden min-[380px]:inline">Testing Mode</span>
            </button>
          )}

          {/* User email — truncated from sm:, hidden below 400px. */}
          <span className="hidden min-[400px]:inline text-muted text-xs
                           font-mono truncate max-w-[120px] lg:max-w-none">
            {session?.email}
          </span>

          {/* Help icon — desktop only (opens Team Primer in a new tab). */}
          <a
            href="/TEAM_PRIMER.md"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden lg:flex items-center text-muted hover:text-white p-1 rounded hover:bg-navy-700 transition-colors"
            title="Team Primer — how to use the three modes"
          >
            <HelpCircle className="w-3.5 h-3.5" />
          </a>

          {/* Settings — full page at /settings.
              UAT issue #49 reported the gear icon producing no response.
              The most reliable fix is to attach an explicit imperative
              handler alongside NavLink's anchor navigation — if
              anything (a tour overlay catching the click, a stale
              modal still in the DOM, a parent click-handler swallowing
              the default) prevents the anchor's navigation, the
              onClick fires navigate() explicitly. Belt-and-braces;
              the isActive styling stays intact. */}
          <NavLink
            to="/settings"
            aria-label="Settings"
            title="Settings"
            onClick={(e) => {
              // Guard: do not double-navigate on a meta/ctrl click
              // (those legitimately open in a new tab via the anchor).
              if (e.metaKey || e.ctrlKey || e.shiftKey) return
              e.preventDefault()
              navigate('/settings')
            }}
            className={({ isActive }) =>
              `flex items-center justify-center w-11 h-11 lg:w-auto lg:h-auto
               lg:p-1 rounded border transition-colors ${
                isActive
                  ? 'text-electric bg-electric/10 border-electric/20'
                  : 'text-muted border-transparent hover:text-white hover:bg-navy-700'
              }`
            }
          >
            <Settings className="w-4 h-4 lg:w-3.5 lg:h-3.5" />
          </NavLink>

          {/* Sign out — desktop only; the drawer carries it on mobile. */}
          <button
            onClick={handleLogout}
            className="hidden lg:flex items-center gap-1.5 text-muted hover:text-white text-xs px-2 py-1 rounded hover:bg-navy-700 transition-colors"
            title="Sign out"
          >
            <span>Sign out</span>
          </button>
        </div>
      </header>

      {/* Mobile / tablet navigation drawer. */}
      <MobileNavDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />

      {/* Page content — scrolls independently; nav never moves. The
          safe-area bottom padding keeps content clear of a phone's
          home-bar / gesture area. */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden
                        pb-[env(safe-area-inset-bottom)]">
        <Outlet />
      </main>

      {/* Academic Advisor floating button — visible on every screen except
          Present mode (the panel hides itself when mode === 'present').
          Mounted at layout level so the button persists across navigation
          and any deliverable-specific dialog can dismiss into the same
          floating affordance. */}
      <AdvisorPanel />

      {/* What's New — opens once after login if the changelog has
          entries this user has not seen; self-dismissing. */}
      <WhatsNewModal />

      {/* Document-generation toast — announces a generation job that
          finished while the user was away from the Reports page. */}
      <GenerationToast />

      {/* Guided platform walkthrough — controlled Joyride that spans
          every route. Auto-starts once per login session when a tour
          update is pending; the Settings "Retake" button and the
          What's New modal both force-start it via tourBus. */}
      <SiteTour />

      {/* Guided UAT test runner — never auto-starts; triggered from the
          Settings "Start Test Pass" button and the login notifications
          via testRunnerBus. */}
      <TestRunner />

      {/* Operational test-runner login notifications — new test cases,
          resolved failures, and feedback responses. Separate from the
          changelog What's New modal. */}
      <TestNotifications />

      {/* One-time welcome for non-team guests — sets expectations about
          the two access tiers. Team members never see it. */}
      <VisitorWelcomeBanner />
    </div>
  )
}


/**
 * Tiny inline badge sitting next to the Reports nav item. Renders
 * the report-writer pipeline status (running / complete / failed)
 * so Bob can navigate away from /reports/writer during a long run
 * and still see at a glance whether the draft is ready.
 *
 * Drawn from the reportWriterStore (Zustand) which the
 * /reports/writer page sets on every state transition. Idle state
 * renders nothing.
 */
function ReportWriterBadge() {
  const { badge, badgeDetail } = useReportWriterStore()
  if (badge === 'idle') return null
  if (badge === 'running') {
    return (
      <span
        data-testid="report-writer-badge-running"
        title={badgeDetail || 'Report writer pipeline running'}
        className="ml-1 inline-flex">
        <Loader2 className="w-3 h-3 animate-spin text-electric-blue" />
      </span>
    )
  }
  if (badge === 'complete') {
    return (
      <span
        data-testid="report-writer-badge-complete"
        title={badgeDetail || 'Report writer draft ready'}
        className="ml-1 inline-flex">
        <CheckCircle className="w-3 h-3 text-green-400" />
      </span>
    )
  }
  return (
    <span
      data-testid="report-writer-badge-failed"
      title={badgeDetail || 'Report writer pipeline failed'}
      className="ml-1 inline-flex">
      <XCircle className="w-3 h-3 text-red-400" />
    </span>
  )
}
