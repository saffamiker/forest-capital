/**
 * ModalControls — shared close-button + keyboard-hint primitives.
 *
 * MOBILE UX FIX (May 24 2026):
 * Modal/drawer/overlay components in the platform displayed "Press
 * Esc to close" text everywhere, which assumes keyboard access mobile
 * users don't have. The fix has two parts:
 *
 *   1. ModalCloseButton — visible X (44×44 touch target on mobile,
 *      32×32 on desktop). Always rendered top-right of the modal.
 *      The button is the PRIMARY close affordance; the Esc handler
 *      below is a desktop convenience.
 *
 *   2. KeyboardHint — text like "Press Esc to close" that surfaces
 *      ONLY at md: breakpoint or above (`hidden md:inline`). A
 *      mobile user never sees the misleading prompt; a desktop user
 *      still does. The Esc key handler in each modal remains in
 *      place — only the text hint is conditionally hidden.
 *
 * 44×44 is the WCAG / Apple HIG / Material minimum tap target. Using
 * `min-h-[44px] min-w-[44px]` on mobile and shrinking with
 * `sm:min-h-[32px] sm:min-w-[32px]` on tablet/desktop keeps the
 * desktop chrome tight while the mobile target stays accessible.
 *
 * Drop-in usage:
 *
 *   <ModalCloseButton onClose={onClose} />
 *   <KeyboardHint hint="Press Esc to close" />
 *
 * Both components are pure visual primitives — no key handlers, no
 * focus management. The hosting modal owns its own Esc useEffect and
 * focus-trap behaviour; these helpers just render the chrome.
 */
import { X } from 'lucide-react'

interface ModalCloseButtonProps {
  onClose: () => void
  /** Override the default aria-label. Defaults to "Close". */
  ariaLabel?: string
  /** Override the testid. Defaults to "modal-close-button". */
  testId?: string
  /** Extra classes — appended to the default styling. Use sparingly
   *  (positioning is the modal's job, not the button's). */
  className?: string
}

export function ModalCloseButton({
  onClose,
  ariaLabel = 'Close',
  testId = 'modal-close-button',
  className = '',
}: ModalCloseButtonProps) {
  return (
    <button
      type="button"
      onClick={onClose}
      aria-label={ariaLabel}
      data-testid={testId}
      className={
        'inline-flex items-center justify-center rounded ' +
        // 44×44 touch target on mobile (WCAG / Apple HIG / Material).
        // Tighter 32×32 from sm: up so the desktop chrome stays tight.
        'min-h-[44px] min-w-[44px] sm:min-h-[32px] sm:min-w-[32px] ' +
        'text-text-muted hover:text-text-primary ' +
        'transition-colors ' +
        className
      }
    >
      <X className="w-4 h-4 sm:w-4 sm:h-4" />
    </button>
  )
}


interface KeyboardHintProps {
  /** The hint text, e.g. "Press Esc to close". */
  hint: string
  /** Optional className override. Default is small-muted typography. */
  className?: string
  /** Optional testid. Defaults to "keyboard-hint". */
  testId?: string
}

/** A keyboard-hint text element that only renders at md: breakpoint
 *  or above. Mobile users never see the misleading prompt; desktop
 *  users still do. The hosting modal's Esc handler is independent
 *  of this element and continues to work on every breakpoint. */
export function KeyboardHint({
  hint,
  className = '',
  testId = 'keyboard-hint',
}: KeyboardHintProps) {
  return (
    <span
      data-testid={testId}
      className={
        // Hidden on mobile, inline from md: up. `hidden md:inline`
        // is the documented Tailwind responsive-visibility pattern
        // and is used throughout the codebase (see MobileNavDrawer
        // for the equivalent on the nav bar).
        'hidden md:inline text-2xs text-text-muted ' + className
      }
    >
      {hint}
    </span>
  )
}
