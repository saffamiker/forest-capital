import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Users, ShieldCheck, Settings, HelpCircle, BarChart3, Activity, FileText, LineChart } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAuth } from '../App'
import { useBrand, BRANDS } from '../context/BrandContext'
import { useUI } from '../context/UIContext'
import type { UIMode } from '../context/UIContext'
import { useQAStore } from '../stores/qaStore'
import QAStatusBadge from '../components/QAStatusBadge'
import LearnModeToggle from '../components/LearnModeToggle'
import AdvisorPanel from '../components/AdvisorPanel'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/',                      label: 'Dashboard',            icon: LayoutDashboard, end: true },
  { to: '/statistical-evidence',  label: 'Statistical Evidence', icon: BarChart3 },
  { to: '/regime-analysis',       label: 'Regime Analysis',      icon: Activity },
  { to: '/analytics',             label: 'Analytics',            icon: LineChart },
  { to: '/council',               label: 'Council',              icon: Users },
  { to: '/qa',                    label: 'QA Audit',             icon: ShieldCheck },
  { to: '/reports',               label: 'Reports',              icon: FileText },
]

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

export default function MainLayout() {
  const { session, logout } = useAuth()
  const { brand } = useBrand()
  const { mode, setMode } = useUI()
  const navigate = useNavigate()
  // Read both the legacy qaStatus (driven by the local QA audit panel) and
  // the new tieredStatus (driven by /api/v1/qa/status polling). The Present-mode
  // gate trusts tieredStatus first when available — that's what enforces the
  // ≥WARN + 48h + hash-match contract from CLAUDE.md Section 14.
  const { status: qaStatus, tieredStatus } = useQAStore()

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  const isMcColl = brand === BRANDS.MCCOLL

  return (
    <div className="h-screen bg-navy-900 flex flex-col overflow-hidden">
      {/* Top nav — shrink-0 ensures it never scrolls out of view */}
      <header className="h-14 border-b border-border flex items-center px-6 shrink-0 bg-navy-800 z-50">
        {/* Brand */}
        <div className="flex items-center gap-2.5 mr-8">
          {isMcColl ? <QuLogo /> : <FcLogo />}
          <span className="text-white font-semibold text-sm tracking-wide">
            {isMcColl ? 'McColl School of Business' : 'Forest Capital'}
          </span>
          <span className="hidden sm:inline text-muted text-xs ml-1">Portfolio Intelligence System</span>
        </div>

        {/* Nav links */}
        <nav className="flex items-center gap-1 flex-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end ?? false}
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
            </NavLink>
          ))}
        </nav>

        {/* Right side: mode selector + user + settings + logout */}
        <div className="flex items-center gap-3 ml-4">
          {/* Three-mode selector — Present mode gated on QA audit status */}
          <div className="hidden sm:flex items-center rounded border border-border overflow-hidden shrink-0">
            {MODE_OPTIONS.map((opt) => {
              const isActive = mode === opt.value
              const isPresent = opt.value === 'present'

              // Present-mode gate — three sources of truth, in priority order:
              //   1. tieredStatus.present_mode_allowed (≥WARN + <48h + hash match)
              //   2. tieredStatus.verdict (informs the icon/tooltip)
              //   3. qaStatus (legacy local-audit fallback, kept until both
              //      panels share the same store path)
              const tieredAllowed = tieredStatus?.present_mode_allowed
              const tieredVerdict = tieredStatus?.verdict   // PASS|WARN|FAIL|UNKNOWN
              const ageHours = tieredStatus?.age_hours ?? null

              // Derive effective status: prefer tieredStatus when available.
              const effectiveStatus: typeof qaStatus = tieredVerdict
                ? (tieredVerdict === 'PASS' ? 'pass'
                  : tieredVerdict === 'WARN' ? 'warn'
                  : tieredVerdict === 'FAIL' ? 'fail'
                  : 'unknown')
                : qaStatus

              // Blocked when either explicit verdict is FAIL/unknown OR the
              // tiered gate says no (covers the ">48h stale" case where the
              // verdict is PASS but age is too old).
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
                  // Send the user to the QA tab so they can run/refresh the audit
                  navigate('/qa')
                  return
                }
                if (isPresent && effectiveStatus === 'fail') {
                  // Blocked — do nothing (title tooltip explains why)
                  return
                }
                setMode(opt.value)
              }

              return (
                <button
                  key={opt.value}
                  onClick={handleClick}
                  title={presentTitle}
                  className={`px-2.5 py-1 text-xs transition-colors whitespace-nowrap flex items-center gap-1 ${
                    presentBlocked
                      ? 'text-muted/50 cursor-not-allowed'
                      : isActive
                        ? opt.value === 'present'
                          ? 'bg-warning/20 text-warning font-medium'
                          : 'bg-electric/15 text-electric font-medium'
                        : 'text-muted hover:text-white hover:bg-navy-700'
                  }`}
                >
                  {opt.label}
                  {/* QA status indicators on the Present button only */}
                  {isPresent && effectiveStatus === 'fail'  && <span className="text-red-400 text-[10px]">🔒</span>}
                  {isPresent && effectiveStatus === 'unknown' && <span className="text-muted/60 text-[10px]">○</span>}
                  {presentWarn && <span className="text-warning text-[10px]">⚠</span>}
                </button>
              )
            })}
          </div>

          {/* Technical/Plain-English sub-toggle — only rendered in Commentary
              mode. Persists per session so label preference survives navigation. */}
          <LearnModeToggle />

          {/* QA status badge — polls /api/v1/qa/status every 30s.
              Click to open the QA Audit screen. Hidden on mobile to
              keep the nav uncluttered at narrow breakpoints. */}
          <QAStatusBadge />

          <span className="text-muted text-xs hidden sm:inline font-mono">{session?.email}</span>

          {/* Help icon — opens Team Primer in a new tab */}
          <a
            href="/TEAM_PRIMER.md"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center text-muted hover:text-white p-1 rounded hover:bg-navy-700 transition-colors"
            title="Team Primer — how to use the three modes"
          >
            <HelpCircle className="w-3.5 h-3.5" />
          </a>

          {/* Settings — full page at /settings. The gear gains the same
              active treatment as the nav-ribbon links when /settings is
              the current route. */}
          <NavLink
            to="/settings"
            aria-label="Settings"
            title="Settings"
            className={({ isActive }) =>
              `flex items-center p-1 rounded transition-colors ${
                isActive
                  ? 'text-electric bg-electric/10 border border-electric/20'
                  : 'text-muted hover:text-white hover:bg-navy-700'
              }`
            }
          >
            <Settings className="w-3.5 h-3.5" />
          </NavLink>

          <button
            onClick={handleLogout}
            className="flex items-center gap-1.5 text-muted hover:text-white text-xs px-2 py-1 rounded hover:bg-navy-700 transition-colors"
            title="Sign out"
          >
            <span>Sign out</span>
          </button>
        </div>
      </header>

      {/* Page content — scrolls independently; nav never moves */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden">
        <Outlet />
      </main>

      {/* Academic Advisor floating button — visible on every screen except
          Present mode (the panel hides itself when mode === 'present').
          Mounted at layout level so the button persists across navigation
          and any deliverable-specific dialog can dismiss into the same
          floating affordance. */}
      <AdvisorPanel />
    </div>
  )
}
