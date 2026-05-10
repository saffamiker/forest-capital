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
