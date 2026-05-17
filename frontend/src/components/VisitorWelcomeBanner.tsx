/**
 * VisitorWelcomeBanner — a one-time welcome shown to non-team users.
 *
 * The platform has two access tiers; this banner sets a guest's
 * expectations on first visit without being restrictive in tone: the
 * analytics, charts and AI council are open to explore, some action
 * features are reserved for the project team.
 *
 * Shown once per browser (a localStorage flag — proportionate for a
 * dismissible guest banner; a guest realistically uses one browser).
 * Team members never see it. It is a banner, not a modal — it never
 * blocks the app, and it sits behind the What's New modal / site tour
 * if those are also showing on first login.
 */
import { useState } from 'react'
import { Compass, X } from 'lucide-react'
import { useIsTeamMember } from '../hooks/usePermissions'
import { startTour } from '../lib/tourBus'

const SEEN_KEY = 'fc_visitor_welcomed'

export default function VisitorWelcomeBanner() {
  const isTeam = useIsTeamMember()
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(SEEN_KEY) === '1',
  )

  // Team members never see it; nor does a guest who has dismissed it.
  if (isTeam || dismissed) return null

  const close = () => {
    try {
      localStorage.setItem(SEEN_KEY, '1')
    } catch {
      /* localStorage unavailable — the banner simply shows again */
    }
    setDismissed(true)
  }

  return (
    <div
      role="status"
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[40]
                 w-full max-w-lg px-4"
    >
      <div className="rounded-lg border border-electric/30 bg-navy-800
                      shadow-2xl p-4">
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-white font-semibold text-sm">
            Welcome to the Forest Capital Portfolio Intelligence System
          </h2>
          <button
            type="button"
            onClick={close}
            aria-label="Dismiss welcome"
            className="text-muted hover:text-white shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-slate-300 leading-relaxed mt-1.5">
          You are viewing this platform as a guest. All analytics, charts,
          and the AI council are available to explore. Some features are
          reserved for the project team.
        </p>
        <div className="flex items-center gap-2 mt-3">
          <button
            type="button"
            onClick={() => { close(); startTour() }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                       font-medium bg-electric/15 text-electric
                       border border-electric/30 hover:bg-electric/25
                       transition-colors"
          >
            <Compass className="w-3.5 h-3.5" />
            Start Tour
          </button>
          <button
            type="button"
            onClick={close}
            className="px-3 py-1.5 rounded text-xs text-muted hover:text-white
                       transition-colors"
          >
            Explore
          </button>
        </div>
      </div>
    </div>
  )
}
