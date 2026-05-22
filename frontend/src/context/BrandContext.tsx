/**
 * BrandContext — controls which organization brand the platform shows
 * in the nav header (McColl School of Business vs. Forest Capital).
 *
 * SCOPE — what the brand switcher DOES change:
 *   - The organization name shown in the top-of-page header
 *   - The brand icon / favicon
 *
 * SCOPE — what it explicitly does NOT change:
 *   - The layout of any page
 *   - The colour scheme, typography, or other design tokens
 *   - The navigation structure (tabs, links, pages)
 *   - Any data shown to the user (analytics, charts, tables)
 *
 * Visual branding beyond name and icon is not in scope for this
 * release. UAT feedback (May 22 2026) surfaced a tester asking whether
 * switching organizations would change layout / colour / nav — the
 * answer is no, and this docstring is the canonical statement of that
 * scope so the next contributor doesn't have to ask.
 *
 * The switcher is sysadmin / team-only via the TeamGate wrapper in
 * Settings → Organization; viewers see the active brand but cannot
 * change it.
 */
import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'

export const BRANDS = {
  MCCOLL: 'mccoll',
  FOREST_CAPITAL: 'forest_capital',
} as const

export type BrandMode = (typeof BRANDS)[keyof typeof BRANDS]

interface BrandContextType {
  brand: BrandMode
  setBrand: (brand: BrandMode) => void
}

const BrandContext = createContext<BrandContextType | null>(null)

export function BrandProvider({ children }: { children: ReactNode }) {
  const [brand, setBrand] = useState<BrandMode>(BRANDS.MCCOLL)
  return (
    <BrandContext.Provider value={{ brand, setBrand }}>
      {children}
    </BrandContext.Provider>
  )
}

export function useBrand(): BrandContextType {
  const ctx = useContext(BrandContext)
  if (!ctx) throw new Error('useBrand must be used within BrandProvider')
  return ctx
}
