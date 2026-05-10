import { createContext, useContext, useState, useEffect } from 'react'
import type { ReactNode } from 'react'

export type UIMode = 'analyst' | 'commentary' | 'present'

interface UIContextType {
  mode: UIMode
  setMode: (mode: UIMode) => void
}

const UIContext = createContext<UIContextType | null>(null)

const SESSION_KEY = 'fc_ui_mode'

export function UIProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<UIMode>(() => {
    const stored = sessionStorage.getItem(SESSION_KEY)
    if (stored === 'analyst' || stored === 'commentary' || stored === 'present') {
      return stored
    }
    return 'analyst'
  })

  useEffect(() => {
    sessionStorage.setItem(SESSION_KEY, mode)
  }, [mode])

  const setMode = (m: UIMode) => setModeState(m)

  return (
    <UIContext.Provider value={{ mode, setMode }}>
      {children}
    </UIContext.Provider>
  )
}

export function useUI(): UIContextType {
  const ctx = useContext(UIContext)
  if (!ctx) throw new Error('useUI must be used within UIProvider')
  return ctx
}
