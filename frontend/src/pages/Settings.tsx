/**
 * Settings — a single scrollable page (no tabs) with five sections:
 *   1. Organisation            — reporting-context / brand switcher
 *   2. Data and Study Period   — read-only data-table status
 *   3. Analytics Configuration — the risk-free rate assumption
 *   4. Academic Documents      — agent-context document upload
 *   5. Account                 — signed-in email + sign out
 *
 * Reached from the nav-ribbon gear icon (route /settings). The Academic
 * Documents section carries id="academic-documents" so /settings#academic-documents
 * deep-links straight to it.
 */
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import axios from 'axios'
import { Check, Compass, LogOut, Sparkles } from 'lucide-react'
import type {
  ChangelogEntry, AllChangelogResponse, UnseenChangelogResponse,
} from '../types/changelog'
import { useAuth } from '../App'
import { startTour } from '../lib/tourBus'
import { useBrand, BRANDS } from '../context/BrandContext'
import type { BrandMode } from '../context/BrandContext'
import { useSession } from '../context/SessionContext'
import AcademicDocumentsPanel from '../components/AcademicDocumentsPanel'

interface SettingsSectionProps {
  id: string
  title: string
  description: string
  children: React.ReactNode
}

function SettingsSection({ id, title, description, children }: SettingsSectionProps) {
  // scroll-mt keeps the heading clear of the fixed 56px nav bar when a
  // hash anchor scrolls the section to the top of the viewport.
  return (
    <section id={id} className="scroll-mt-20">
      <h2 className="text-base font-semibold text-white">{title}</h2>
      <p className="text-xs text-muted mt-0.5">{description}</p>
      <div className="border-t border-border mt-3 pt-4">{children}</div>
    </section>
  )
}

const Placeholder = ({ children }: { children: React.ReactNode }) => (
  <p className="text-xs text-muted italic">{children}</p>
)

// ── 1. Organisation ───────────────────────────────────────────────────────────

const BRAND_OPTIONS: { value: BrandMode; label: string; sub: string }[] = [
  { value: BRANDS.MCCOLL, label: 'McColl School of Business',
    sub: 'Queens University academic context' },
  { value: BRANDS.FOREST_CAPITAL, label: 'Forest Capital (co-branded)',
    sub: 'Industry-partner reporting context' },
]

function OrganisationSection() {
  // Same brand state as before — relocated from the nav gear dropdown,
  // logic unchanged: useBrand()/setBrand drive the header branding.
  const { brand, setBrand } = useBrand()
  return (
    <div className="space-y-2">
      {BRAND_OPTIONS.map((opt) => {
        const active = brand === opt.value
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => setBrand(opt.value)}
            className={`w-full flex items-center justify-between px-3 py-2.5 rounded
                        border transition-colors text-left ${
              active
                ? 'border-electric/40 bg-electric/10'
                : 'border-border bg-navy-800 hover:bg-navy-700'
            }`}
          >
            <span>
              <span className={`block text-sm ${active ? 'text-white' : 'text-slate-300'}`}>
                {opt.label}
              </span>
              <span className="block text-2xs text-muted mt-0.5">{opt.sub}</span>
            </span>
            {active && <Check className="w-4 h-4 text-electric shrink-0" />}
          </button>
        )
      })}
    </div>
  )
}

// ── 2. Data and Study Period ──────────────────────────────────────────────────

type Staleness = 'green' | 'amber' | 'red' | 'unknown'

interface TableStatus {
  name: string
  row_count: number
  min_date: string | null
  max_date: string | null
  last_updated: string | null
  staleness: Staleness
}

interface DataStatus {
  available: boolean
  study_period: { start: string; end: string; n_months: number } | null
  tables: TableStatus[]
}

const STALENESS_STYLE: Record<Staleness, { cls: string; label: string }> = {
  green:   { cls: 'bg-success/15 text-success border-success/30', label: 'Current' },
  amber:   { cls: 'bg-warning/15 text-warning border-warning/30', label: 'Ageing' },
  red:     { cls: 'bg-danger/15 text-danger border-danger/30',    label: 'Stale' },
  unknown: { cls: 'bg-navy-700 text-muted border-border',         label: 'Unknown' },
}

function StalenessPill({ staleness }: { staleness: Staleness }) {
  const s = STALENESS_STYLE[staleness] ?? STALENESS_STYLE.unknown
  return (
    <span className={`text-2xs px-2 py-0.5 rounded-full border ${s.cls}`}>
      {s.label}
    </span>
  )
}

function DataStudyPeriodSection() {
  const [data, setData] = useState<DataStatus | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    axios.get<DataStatus>('/api/v1/admin/data-status')
      .then((res) => { if (!cancelled) setData(res.data) })
      .catch(() => { if (!cancelled) setData(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  if (loading) return <Placeholder>Loading data status…</Placeholder>
  if (!data || !data.available) {
    return <Placeholder>Data status unavailable — the database is not reachable.</Placeholder>
  }

  return (
    <div className="space-y-3">
      {data.study_period && (
        <div className="text-sm text-white font-mono">
          Study period:{' '}
          <span className="text-electric">{data.study_period.start}</span>
          {' to '}
          <span className="text-electric">{data.study_period.end}</span>
          {' · '}{data.study_period.n_months} months
        </div>
      )}
      <div className="space-y-2">
        {data.tables.map((t) => (
          <div
            key={t.name}
            className="flex items-center justify-between gap-3 px-3 py-2.5 rounded
                       border border-border bg-navy-800"
          >
            <div className="min-w-0">
              <div className="text-sm text-white font-mono truncate">{t.name}</div>
              <div className="text-2xs text-muted mt-0.5">
                {t.row_count.toLocaleString()} rows
                {t.min_date && t.max_date
                  ? ` · ${t.min_date} → ${t.max_date}`
                  : ' · no rows'}
                {t.last_updated ? ` · updated ${t.last_updated.slice(0, 10)}` : ''}
              </div>
            </div>
            <StalenessPill staleness={t.staleness} />
          </div>
        ))}
      </div>
    </div>
  )
}

// ── 3. Analytics Configuration ────────────────────────────────────────────────

interface AnalyticsConfig {
  available: boolean
  risk_free_rate: number | null
  risk_free_source: string
}

function AnalyticsConfigurationSection() {
  const [config, setConfig] = useState<AnalyticsConfig | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    axios.get<AnalyticsConfig>('/api/v1/analytics/config')
      .then((res) => { if (!cancelled) setConfig(res.data) })
      .catch(() => { if (!cancelled) setConfig(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const rate = config?.risk_free_rate
  const source = config?.risk_free_source
    ?? 'FRED DTB3 (3-month T-bill, mean monthly rate, annualised)'

  return (
    <div>
      <h3 className="text-sm font-medium text-white">Risk-Free Rate</h3>
      <p className="text-xs text-muted mt-0.5">
        Used for all Sharpe ratio and efficient frontier calculations.
      </p>
      <div className="mt-3 px-3 py-3 rounded border border-border bg-navy-800 space-y-1.5">
        <div className="text-2xs text-muted">
          Source: <span className="text-slate-300">{source}</span>
        </div>
        <div className="text-sm text-white">
          Current value:{' '}
          <span className="font-mono text-electric text-base">
            {loading
              ? '…'
              : rate != null
                ? `${(rate * 100).toFixed(2)}%`
                : 'unavailable'}
          </span>
        </div>
        <div className="text-2xs text-muted italic">
          Read-only — not user editable at this stage.
        </div>
      </div>
    </div>
  )
}

// ── 5. Account ────────────────────────────────────────────────────────────────

function TestingModeToggle() {
  // Reads and writes SessionContext only — no API call, no persistence.
  // Testing Mode is session-scoped and resets to analytical on next login.
  const { sessionType, setTestingMode } = useSession()
  const testing = sessionType === 'testing'
  return (
    <div className="space-y-2" data-tour="testing-mode">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-white">Testing Mode</span>
        <button
          type="button"
          role="switch"
          aria-checked={testing}
          aria-label="Testing Mode"
          onClick={() => setTestingMode(!testing)}
          className={`relative inline-flex h-5 w-9 items-center rounded-full
                      shrink-0 transition-colors ${
            testing
              ? 'bg-warning'
              : 'bg-navy-700 border border-border'
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white
                        transition-transform ${
              testing ? 'translate-x-4' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>
      <p className="text-2xs text-muted leading-relaxed">
        When enabled, all activity in this session is logged as testing and
        excluded from the Team Activity analytical view by default. Testing
        Mode resets automatically on your next login.
      </p>
    </div>
  )
}

function AccountSection() {
  const { session, logout } = useAuth()
  const navigate = useNavigate()

  // Same behaviour as the nav-ribbon sign-out — this is a convenience
  // duplicate, not a replacement.
  const handleSignOut = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <div className="space-y-4">
      <div>
        <div className="text-2xs text-muted uppercase tracking-wide">Signed in as</div>
        <div className="text-sm text-white font-mono mt-0.5">
          {session?.email ?? '—'}
        </div>
      </div>

      <div className="border-t border-border pt-3">
        <TestingModeToggle />
      </div>

      {/* Site Tour — startTour() (via tourBus) force-starts SiteTour from
          step 1 regardless of seen/skip state: it clears the session skip
          flag, navigates to the Dashboard, and runs. */}
      <div className="border-t border-border pt-3 space-y-2">
        <div className="text-sm text-white">Site Tour</div>
        <p className="text-xs text-muted leading-relaxed">
          Relaunches the full platform tour. The tour covers every feature
          and how it maps to your project deliverables and grading criteria.
        </p>
        <button
          type="button"
          onClick={() => startTour()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                     border border-electric/30 bg-electric/10 text-electric
                     hover:bg-electric/20 transition-colors"
        >
          <Compass className="w-3.5 h-3.5" />
          Retake Site Tour
        </button>
      </div>

      {/* Jump to the Release History section below. */}
      <button
        type="button"
        onClick={() => document.getElementById('release-history')
          ?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
        className="flex items-center gap-1.5 text-2xs text-electric
                   hover:text-blue-300 transition-colors"
      >
        <Sparkles className="w-3 h-3" />
        What's New — see the release history
      </button>

      <button
        type="button"
        onClick={() => void handleSignOut()}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                   border border-border text-slate-300 hover:bg-navy-700
                   transition-colors"
      >
        <LogOut className="w-3.5 h-3.5" />
        Sign out
      </button>
    </div>
  )
}

// ── 6. Release History ────────────────────────────────────────────────────────

function ReleaseHistorySection() {
  const [entries, setEntries] = useState<ChangelogEntry[]>([])
  const [unseenVersions, setUnseenVersions] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    // All entries for display; the unseen set drives the "New" badge.
    void Promise.allSettled([
      axios.get<AllChangelogResponse>('/api/v1/changelog'),
      axios.get<UnseenChangelogResponse>('/api/v1/changelog/unseen'),
    ]).then(([all, unseen]) => {
      if (cancelled) return
      if (all.status === 'fulfilled') {
        setEntries(all.value.data.entries ?? [])
      }
      if (unseen.status === 'fulfilled') {
        setUnseenVersions(
          new Set((unseen.value.data.entries ?? []).map((e) => e.version)))
      }
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  if (loading) return <Placeholder>Loading release history…</Placeholder>
  if (entries.length === 0) {
    return <Placeholder>No release history available.</Placeholder>
  }

  return (
    <div className="space-y-3">
      {entries.map((e) => (
        <div key={e.id} className="rounded border border-border bg-navy-800 p-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm text-white font-semibold">{e.title}</span>
            {unseenVersions.has(e.version) && (
              <span className="text-2xs px-1.5 py-0.5 rounded-full
                               bg-electric/15 text-electric border border-electric/30">
                New
              </span>
            )}
            <span className="text-2xs text-muted font-mono ml-auto">
              v{e.version} · {new Date(e.released_at).toLocaleDateString()}
            </span>
          </div>
          <p className="text-xs text-slate-300 leading-relaxed mt-1">
            {e.description}
          </p>
          <div className="mt-2 pl-3 py-1" style={{ borderLeft: '3px solid #f59e0b' }}>
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
  )
}

export default function Settings() {
  const location = useLocation()

  // Deep-link support — /settings#academic-documents scrolls to that section.
  useEffect(() => {
    if (!location.hash) return
    const el = document.getElementById(location.hash.slice(1))
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [location.hash])

  return (
    <div className="p-4 md:p-6 max-w-screen-md mx-auto space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-white">Settings</h1>
        <p className="text-sm text-muted mt-1">
          Reporting context, data status, analytics assumptions, agent-context
          documents, and account.
        </p>
      </div>

      <SettingsSection
        id="organisation"
        title="Organisation"
        description="Select the reporting context for this session."
      >
        <OrganisationSection />
      </SettingsSection>

      <SettingsSection
        id="data-study-period"
        title="Data and Study Period"
        description="Read-only status of the data tables feeding the analytics layer."
      >
        <DataStudyPeriodSection />
      </SettingsSection>

      <SettingsSection
        id="analytics-configuration"
        title="Analytics Configuration"
        description="Assumptions applied across all analytics and the efficient frontier."
      >
        <AnalyticsConfigurationSection />
      </SettingsSection>

      <SettingsSection
        id="academic-documents"
        title="Academic Documents"
        description="Documents uploaded here are injected into every AI agent session."
      >
        <div className="mb-3 px-3 py-2.5 rounded border border-border bg-navy-800
                        text-muted text-xs leading-relaxed">
          Academic Review sessions use the documents uploaded here as context.
          Upload your project requirements and rubric before running your first
          review. Midpoint draft, presentation slides, and script can be added
          as they are written.
        </div>
        <AcademicDocumentsPanel />
      </SettingsSection>

      <SettingsSection
        id="account"
        title="Account"
        description="The account signed in to this session."
      >
        <AccountSection />
      </SettingsSection>

      <SettingsSection
        id="release-history"
        title="Release History"
        description="Every feature shipped to the platform, newest first — and why each one matters for your grade."
      >
        <ReleaseHistorySection />
      </SettingsSection>
    </div>
  )
}
