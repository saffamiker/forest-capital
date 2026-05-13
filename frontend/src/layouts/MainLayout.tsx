import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useRef, useState, useEffect } from 'react'
import { LayoutDashboard, Users, ShieldCheck, Settings, HelpCircle, BarChart3, Activity } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAuth } from '../App'
import { useBrand, BRANDS } from '../context/BrandContext'
import { useUI } from '../context/UIContext'
import type { UIMode } from '../context/UIContext'
import { useQAStore } from '../stores/qaStore'

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
  { to: '/council',               label: 'Council',              icon: Users },
  { to: '/qa',                    label: 'QA Audit',             icon: ShieldCheck },
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
  const { brand, setBrand } = useBrand()
  const { mode, setMode } = useUI()
  const navigate = useNavigate()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsRef = useRef<HTMLDivElement>(null)
  const { status: qaStatus } = useQAStore()

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setSettingsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

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

              // Derive QA gate state for the Present button
              const presentBlocked = isPresent && (qaStatus === 'unknown' || qaStatus === 'fail')
              const presentWarn = isPresent && qaStatus === 'warn'
              const presentTitle = isPresent
                ? qaStatus === 'unknown'
                  ? 'Run QA Audit before presenting'
                  : qaStatus === 'fail'
                    ? 'QA audit failed — review issues before presenting to Forest Capital'
                    : qaStatus === 'warn'
                      ? 'QA: WARN — review limitations before presenting'
                      : ''
                : ''

              const handleClick = () => {
                if (isPresent && qaStatus === 'unknown') {
                  // Navigate to QA tab so user can run the audit
                  navigate('/qa')
                  return
                }
                if (isPresent && qaStatus === 'fail') {
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
                  {isPresent && qaStatus === 'fail'  && <span className="text-red-400 text-[10px]">🔒</span>}
                  {isPresent && qaStatus === 'unknown' && <span className="text-muted/60 text-[10px]">○</span>}
                  {presentWarn && <span className="text-warning text-[10px]">⚠</span>}
                </button>
              )
            })}
          </div>

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

          {/* Settings cog — brand toggle */}
          <div className="relative" ref={settingsRef}>
            <button
              onClick={() => setSettingsOpen(o => !o)}
              className="flex items-center text-muted hover:text-white p-1 rounded hover:bg-navy-700 transition-colors"
              title="Brand settings"
            >
              <Settings className="w-3.5 h-3.5" />
            </button>

            {settingsOpen && (
              <div className="absolute right-0 top-full mt-1.5 w-52 bg-navy-800 border border-border rounded shadow-lg z-50 py-1">
                <div className="px-3 py-1.5 text-muted text-xs font-medium uppercase tracking-wider">Brand</div>
                <button
                  onClick={() => { setBrand(BRANDS.MCCOLL); setSettingsOpen(false) }}
                  className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-navy-700 transition-colors"
                >
                  <span className={isMcColl ? 'text-white' : 'text-muted'}>McColl School of Business</span>
                  {isMcColl && <span className="text-electric text-xs">✓</span>}
                </button>
                <button
                  onClick={() => { setBrand(BRANDS.FOREST_CAPITAL); setSettingsOpen(false) }}
                  className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-navy-700 transition-colors"
                >
                  <span className={!isMcColl ? 'text-white' : 'text-muted'}>Forest Capital (co-branded)</span>
                  {!isMcColl && <span className="text-electric text-xs">✓</span>}
                </button>
              </div>
            )}
          </div>

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
    </div>
  )
}
