/**
 * PortfolioProfileModal — the Dashboard's modal wrapper around the
 * PortfolioProfilePanel.
 *
 * Item 9 Commit 4 (May 22 2026). Clicking the behavioural_tag on a
 * Dashboard strategy row opens this modal showing the three-card
 * Portfolio Profile for that strategy directly, keeping the user on
 * the Dashboard. Esc / click-on-backdrop / × button all close it.
 *
 * The PortfolioProfilePanel itself reads from the
 * useCharacterisationsStore — already loaded by the time the user
 * gets here because the Dashboard fires .load() on mount (the store
 * dedupes; one fetch covers the modal and the in-row tag both).
 */
import { useEffect } from 'react'
import { ModalCloseButton } from './ModalControls'
import { PortfolioProfilePanel } from './PortfolioProfilePanel'


interface PortfolioProfileModalProps {
  /** The strategy_id whose profile to show. Null = modal closed. */
  strategyId: string | null
  /** Display name shown in the header — falls back to humanised id. */
  strategyName?: string | undefined
  onClose: () => void
}


export function PortfolioProfileModal({
  strategyId, strategyName, onClose,
}: PortfolioProfileModalProps) {
  // Esc closes — standard modal contract. The handler is installed
  // only while the modal is open so it doesn't leak across mounts.
  useEffect(() => {
    if (!strategyId) return undefined
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [strategyId, onClose])

  if (!strategyId) return null

  const displayName = strategyName ?? strategyId.replace(/_/g, ' ')

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${displayName} Portfolio Profile`}
      data-testid="portfolio-profile-modal"
      className="fixed inset-0 z-[70] flex items-end sm:items-center
                  justify-center"
    >
      {/* Backdrop — click anywhere outside the modal to close. */}
      <button
        type="button"
        onClick={onClose}
        aria-label="Close Portfolio Profile"
        data-testid="portfolio-profile-modal-backdrop"
        className="absolute inset-0 bg-black/60 cursor-default"
      />
      {/* Modal card — bottom sheet on mobile, centred dialog from sm
          up. max-h with overflow so a long Profile stays scrollable. */}
      <div
        className="relative w-full sm:max-w-2xl max-h-[90vh]
                    overflow-y-auto bg-navy-900 border border-border
                    sm:rounded-lg shadow-2xl"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between
                         gap-2 px-4 py-3 bg-navy-900 border-b border-border">
          <h2 className="text-white font-semibold text-sm">
            {displayName} — Portfolio Profile
          </h2>
          <ModalCloseButton
            onClose={onClose}
            testId="portfolio-profile-modal-close"
            className="hover:bg-navy-700"
          />
        </div>
        <div className="p-4">
          <PortfolioProfilePanel
            strategyId={strategyId}
            strategyName={displayName} />
        </div>
      </div>
    </div>
  )
}
