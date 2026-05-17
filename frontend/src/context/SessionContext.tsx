/**
 * SessionContext — per-login session identity for activity tracking.
 *
 * Two values, both in-memory only (never localStorage, never a cookie):
 *
 *   sessionId    A UUID generated when an authenticated session first
 *                exists without one — a fresh login, or a page reload
 *                that restored the token. Cleared on logout, so the
 *                next login mints a new one.
 *
 *   sessionType  "analytical" (default) or "testing". Testing Mode is
 *                opt-in per session and is NEVER persisted — it resets
 *                to analytical on every (re)login by design.
 *
 * Both are mirrored onto the axios default headers (X-Session-ID,
 * X-Session-Type) so every API request carries them with no per-call
 * wiring — the backend reads them to attribute and band activity.
 */
import {
  createContext, useContext, useState, useEffect, useCallback,
} from 'react'
import type { ReactNode } from 'react'
import axios from 'axios'
import { useAuth } from '../App'

export type SessionType = 'analytical' | 'testing'

interface SessionContextValue {
  sessionId: string | null
  sessionType: SessionType
  setTestingMode: (on: boolean) => void
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used within SessionProvider')
  return ctx
}

/** A v4-style UUID. Uses crypto.randomUUID when available, with a
 *  Math.random fallback for environments that lack it (older jsdom). */
function generateSessionId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0
    const v = ch === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessionType, setSessionType] = useState<SessionType>('analytical')

  // Mint a session_id whenever an authenticated session exists without
  // one, and drop it on logout. session_type is reset to analytical at
  // the same moment — Testing Mode must not survive a login boundary.
  useEffect(() => {
    if (session && !sessionId) {
      setSessionId(generateSessionId())
      setSessionType('analytical')
    } else if (!session && sessionId) {
      setSessionId(null)
      setSessionType('analytical')
    }
  }, [session, sessionId])

  // Mirror onto axios defaults so every request carries the headers.
  useEffect(() => {
    if (sessionId) {
      axios.defaults.headers.common['X-Session-ID'] = sessionId
    } else {
      delete axios.defaults.headers.common['X-Session-ID']
    }
  }, [sessionId])

  // X-Session-Type is an ADVISORY, client-trusted header — it lets a user
  // opt their own session out of the analytical activity view (Testing
  // Mode). It is not a security boundary: any authenticated user could
  // send "testing" directly. That is acceptable — it only affects
  // self-attribution of activity, and team-email gating remains the
  // authoritative server-side filter.
  useEffect(() => {
    axios.defaults.headers.common['X-Session-Type'] = sessionType
  }, [sessionType])

  const setTestingMode = useCallback((on: boolean) => {
    setSessionType(on ? 'testing' : 'analytical')
  }, [])

  return (
    <SessionContext.Provider value={{ sessionId, sessionType, setTestingMode }}>
      {children}
    </SessionContext.Provider>
  )
}
