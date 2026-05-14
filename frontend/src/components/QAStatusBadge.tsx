/**
 * QAStatusBadge — small pill in the nav bar showing the current QA
 * tiered-cache verdict for the active strategy_hash.
 *
 * Polls /api/v1/qa/status every 30 seconds so a Tier 2 audit that lands
 * in the background updates the badge without a page reload. The badge
 * is also the source of truth for the Present-mode gate — the MainLayout
 * mode selector reads the same tieredStatus from qaStore.
 */
import { useEffect } from 'react'
import { ShieldCheck, ShieldAlert, Shield, Loader2 } from 'lucide-react'
import { useQAStore } from '../stores/qaStore'

const POLL_INTERVAL_MS = 30_000

export default function QAStatusBadge() {
  const status = useQAStore((s) => s.status)
  const tieredStatus = useQAStore((s) => s.tieredStatus)
  const pollStatus = useQAStore((s) => s.pollStatus)

  // Single shared interval so multiple consumers don't duplicate the poll.
  // The store is a singleton — pollStatus is stable across re-renders.
  useEffect(() => {
    void pollStatus()
    const id = setInterval(() => { void pollStatus() }, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [pollStatus])

  // Visual mapping mirrors the QA Audit panel verdict pills so the badge
  // and the full panel never disagree about what a "WARN" looks like.
  const config = (() => {
    switch (status) {
      case 'pass':
        return {
          Icon: ShieldCheck,
          colorClass: 'text-success',
          bgClass: 'bg-success/10',
          borderClass: 'border-success/30',
          label: 'QA: PASS',
        }
      case 'warn':
        return {
          Icon: ShieldAlert,
          colorClass: 'text-warning',
          bgClass: 'bg-warning/10',
          borderClass: 'border-warning/30',
          label: 'QA: WARN',
        }
      case 'fail':
        return {
          Icon: ShieldAlert,
          colorClass: 'text-danger',
          bgClass: 'bg-danger/10',
          borderClass: 'border-danger/30',
          label: 'QA: FAIL',
        }
      case 'running':
        return {
          Icon: Loader2,
          colorClass: 'text-electric',
          bgClass: 'bg-electric/10',
          borderClass: 'border-electric/30',
          label: 'QA: Running…',
        }
      default:
        return {
          Icon: Shield,
          colorClass: 'text-muted',
          bgClass: 'bg-navy-800',
          borderClass: 'border-border',
          label: 'QA: —',
        }
    }
  })()

  const tierSuffix = tieredStatus?.tier ? ` · T${tieredStatus.tier}` : ''
  const ageSuffix =
    tieredStatus?.age_hours != null && tieredStatus.age_hours < 48
      ? ` · ${tieredStatus.age_hours.toFixed(0)}h ago`
      : ''
  const title = tieredStatus
    ? `${config.label}${tierSuffix}${ageSuffix} · hash ${tieredStatus.strategy_hash.slice(0, 8)}`
    : config.label

  const Icon = config.Icon
  const isSpinning = status === 'running'

  return (
    <a
      href="/qa"
      className={`hidden sm:flex items-center gap-1.5 px-2 py-1 rounded border transition-colors hover:opacity-90 ${config.bgClass} ${config.borderClass}`}
      title={title}
      data-testid="qa-status-badge"
    >
      <Icon className={`w-3 h-3 shrink-0 ${config.colorClass} ${isSpinning ? 'animate-spin' : ''}`} />
      <span className={`text-2xs font-semibold ${config.colorClass}`}>
        {config.label}
      </span>
    </a>
  )
}
