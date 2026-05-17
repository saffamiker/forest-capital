/**
 * tourBus — a tiny module-level bridge so any component can start the
 * site tour without prop-drilling or a context.
 *
 * SiteTour registers its start function on mount; the What's New modal
 * (on close, when a tour update is pending) and the Settings "Retake
 * Site Tour" button call startTour(). This keeps SiteTour the single
 * owner of the Joyride run state while letting unrelated components
 * trigger it.
 */
type Starter = () => void

let _starter: Starter | null = null

/** SiteTour calls this on mount (and with null on unmount). */
export function registerTourStarter(fn: Starter | null): void {
  _starter = fn
}

/** Starts the site tour from step 1, if SiteTour is mounted. */
export function startTour(): void {
  _starter?.()
}

/** True once SiteTour has registered — lets a caller decide whether to
 *  enable a "start tour" affordance. */
export function isTourReady(): boolean {
  return _starter !== null
}
