/**
 * StrategyTypeBadge — small DYNAMIC/STATIC pill next to a strategy name.
 * Identical styling to the badges in the Dashboard strategy table and
 * the DisagreementHeatmap left column, so the visual language is
 * consistent across the whole product.
 *
 * Renders nothing when the strategy isn't in STRATEGY_TYPES — failing
 * silent rather than rendering a placeholder keeps the chart legend
 * clean for any future strategy not yet classified.
 */
import { typeFor } from '../lib/strategyColors'

interface Props {
  strategy: string
  size?: 'sm' | 'xs'
}

export default function StrategyTypeBadge({ strategy, size = 'xs' }: Props) {
  const t = typeFor(strategy)
  if (!t) return null

  const baseClasses = size === 'sm'
    ? 'text-xs px-1.5 py-0.5'
    : 'text-2xs px-1 py-0.5'

  if (t === 'dynamic') {
    return (
      <span className={`${baseClasses} rounded border border-electric/30 bg-electric/10 text-electric font-medium`}>
        DYNAMIC
      </span>
    )
  }
  return (
    <span className={`${baseClasses} rounded border border-border bg-navy-700 text-muted font-medium`}>
      STATIC
    </span>
  )
}
