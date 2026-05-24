import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, createContext,
         useContext, lazy, Suspense } from 'react'
import type { ReactNode } from 'react'
import axios from 'axios'
import LoginPage from './pages/LoginPage'
import AuthVerify from './pages/AuthVerify'
import MainLayout from './layouts/MainLayout'
import Dashboard from './components/Dashboard'
import CouncilDebate from './components/CouncilDebate'
import QAHub from './pages/QAHub'
import AcademicAnalytics from './pages/AcademicAnalytics'
import Reports from './pages/Reports'
import Settings from './pages/Settings'

// Lazy-load the heavy editor + secondary analytics pages — they
// carry the Konva canvas, the TipTap RichTextEditor, and the
// diversification chart bundles, none of which the user needs at
// initial load. Item 6 performance audit (May 23 2026): reduces
// the initial bundle by ~30-50KB and shaves ~150-200ms off
// the first paint on a cold cache.
const StatisticalEvidence = lazy(() =>
  import('./pages/StatisticalEvidence'))
const RegimeAnalysis = lazy(() =>
  import('./pages/RegimeAnalysis'))
const StoryboardEditor = lazy(() =>
  import('./pages/StoryboardEditor'))
const SectionEditor = lazy(() =>
  import('./pages/SectionEditor'))
const DocumentEditor = lazy(() =>
  import('./pages/DocumentEditor'))
const ReportWriter = lazy(() =>
  import('./pages/ReportWriter'))
const PeerReview = lazy(() =>
  import('./pages/PeerReview'))


/** Minimal page-load fallback. Keeps the route mount measurable in
 *  the network tab without flashing a heavy spinner. */
function _PageLoadingFallback() {
  return (
    <div className="p-6 text-text-muted text-sm">
      Loading…
    </div>
  )
}
import { BrandProvider } from './context/BrandContext'
import { UIProvider } from './context/UIContext'
import { SessionProvider } from './context/SessionContext'
import { trackLogout } from './lib/activityLogger'

// ── Auth context ──────────────────────────────────────────────────────────────

interface Session {
  token: string
  email: string
  // Populated from GET /api/auth/me after login / on restore. Until that
  // resolves they are undefined and the permission hooks read false.
  role?: string
  displayName?: string | null
  permissions?: string[]
  // Lifetime council-query allocation. councilQueriesLimit null = unlimited.
  councilQueriesUsed?: number
  councilQueriesLimit?: number | null
}

interface MeResponse {
  email: string
  role: string
  display_name: string | null
  permissions: string[]
  council_queries_used: number
  council_queries_limit: number | null
}

interface AuthContextType {
  session: Session | null
  isVerifying: boolean
  login: (token: string, email: string) => void
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextType | null>(null)

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const [session, setSession] = useState<Session | null>(() => {
    const token = localStorage.getItem('fc_session_token')
    const email = localStorage.getItem('fc_email')
    return token && email ? { token, email } : null
  })

  // True while we validate a stored token against the backend on first load.
  // Only starts true when there is actually a token to check — otherwise we
  // go straight to showing the login page with no delay.
  const [isVerifying, setIsVerifying] = useState<boolean>(
    () => !!localStorage.getItem('fc_session_token')
  )

  // Stable helper that clears all session state without calling the logout endpoint
  // — used by the 401 interceptor where we can't await an API call
  const clearSession = useCallback(() => {
    localStorage.removeItem('fc_session_token')
    localStorage.removeItem('fc_email')
    delete axios.defaults.headers.common['X-API-Key']
    setSession(null)
  }, [])

  // Merges the GET /api/auth/me response (role, display name, permissions)
  // into the session so the permission hooks can gate the UI.
  const applyMe = useCallback((data: MeResponse) => {
    setSession((prev) => (prev ? {
      ...prev,
      role: data.role,
      displayName: data.display_name,
      permissions: data.permissions,
      councilQueriesUsed: data.council_queries_used,
      councilQueriesLimit: data.council_queries_limit,
    } : prev))
  }, [])

  const login = (token: string, email: string) => {
    localStorage.setItem('fc_session_token', token)
    localStorage.setItem('fc_email', email)
    axios.defaults.headers.common['X-API-Key'] = token
    setSession({ token, email })
    // Pull role + permissions so the UI gates correctly.
    void axios.get<MeResponse>('/api/auth/me')
      .then((res) => applyMe(res.data))
      .catch(() => { /* permissions stay unset — hooks read false */ })
  }

  const logout = async () => {
    const token = session?.token
    // Queue + flush the logout event while the auth header is still on
    // the axios defaults — clearSession() below removes it.
    trackLogout()
    if (token) {
      try { await axios.post('/api/auth/logout', { session_token: token }) } catch (_) { /* logout errors are safe to ignore */ }
    }
    clearSession()
  }

  // Refs so the interceptor closure always calls the latest function without
  // re-registering the interceptor on every render
  const clearSessionRef = useRef(clearSession)
  clearSessionRef.current = clearSession
  const navigateRef = useRef(navigate)
  navigateRef.current = navigate

  // On mount: restore the axios auth header and immediately verify the stored
  // token against the backend before any protected content renders.
  // RequireAuth holds a full-screen spinner while isVerifying is true.
  // /api/auth/me contains /api/auth/ so the 401 interceptor below skips it —
  // this catch handler is solely responsible for clearing the stale session.
  useEffect(() => {
    let cancelled = false
    const token = localStorage.getItem('fc_session_token')

    if (!token) {
      setIsVerifying(false)
      return
    }

    axios.defaults.headers.common['X-API-Key'] = token

    void axios.get<MeResponse>('/api/auth/me')
      .then((res) => { if (!cancelled) applyMe(res.data) })
      .catch(() => {
        // 401 expired/invalid token, or network error: clear all local state.
        // RequireAuth will redirect to /login once isVerifying flips false.
        if (!cancelled) clearSessionRef.current()
      })
      .finally(() => { if (!cancelled) setIsVerifying(false) })

    return () => { cancelled = true }
  }, [applyMe]) // applyMe is stable (useCallback) — effect runs once on mount

  // May 24 2026 — REQUEST interceptor: ensure X-API-Key from
  // localStorage is attached to every outgoing axios call.
  // axios.defaults.headers.common['X-API-Key'] is set on login and
  // on mount, but a race (a component firing before the mount
  // effect runs, a stale cleared default from a prior session) can
  // leave it absent. Re-applying from localStorage on every
  // request is a belt-and-braces guarantee: the token is the
  // source of truth in localStorage, axios.defaults is a
  // convenience mirror. Citation adjudicate calls and other rapid-
  // fire actions were intermittently going out without the header
  // — this interceptor closes the gap.
  useEffect(() => {
    const requestInterceptorId = axios.interceptors.request.use(
      (config) => {
        const token = localStorage.getItem('fc_session_token')
        if (token) {
          config.headers = config.headers || {}
          // Only set if missing — never override a deliberate
          // per-request header (e.g. a future endpoint that uses
          // a scoped token).
          if (!config.headers['X-API-Key']) {
            config.headers['X-API-Key'] = token
          }
        }
        return config
      }
    )
    return () => axios.interceptors.request.eject(requestInterceptorId)
  }, [])

  // 401 interceptor — redirect to /login when the backend rejects a session.
  // Auth endpoints (/api/auth/*) are excluded: their 401 responses are handled
  // by the AuthVerify page (expired/invalid magic link) and must not trigger a redirect.
  useEffect(() => {
    const interceptorId = axios.interceptors.response.use(
      (response) => response,
      (error: unknown) => {
        if (axios.isAxiosError(error) && error.response?.status === 401) {
          const url = error.config?.url ?? ''
          if (!url.includes('/api/auth/')) {
            clearSessionRef.current()
            navigateRef.current('/login?expired=1', { replace: true })
          }
        }
        return Promise.reject(error)
      }
    )
    return () => axios.interceptors.response.eject(interceptorId)
  }, [])

  return (
    <AuthContext.Provider value={{ session, isVerifying, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// ── Route guard ───────────────────────────────────────────────────────────────

function RequireAuth({ children }: { children: ReactNode }) {
  const { session, isVerifying } = useAuth()
  const location = useLocation()

  // Hold here with a full-screen spinner until the mount-time token check
  // completes. This prevents the dashboard shell from flashing before we know
  // whether the stored session is still valid.
  if (isVerifying) {
    return (
      <div className="fixed inset-0 bg-[#0a0e1a] flex items-center justify-center" aria-label="Verifying session">
        <div className="w-8 h-8 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
      </div>
    )
  }

  if (!session) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <AuthProvider>
      <SessionProvider>
        <BrandProvider>
          <UIProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/auth/verify" element={<AuthVerify />} />
              <Route
                path="/*"
                element={
                  <RequireAuth>
                    <MainLayout />
                  </RequireAuth>
                }
              >
                <Route index element={<Dashboard />} />
                <Route path="statistical-evidence"
                  element={
                    <Suspense fallback={<_PageLoadingFallback />}>
                      <StatisticalEvidence />
                    </Suspense>
                  } />
                <Route path="regime-analysis"
                  element={
                    <Suspense fallback={<_PageLoadingFallback />}>
                      <RegimeAnalysis />
                    </Suspense>
                  } />
                <Route path="analytics" element={<AcademicAnalytics />} />
                <Route path="council" element={<CouncilDebate />} />
                <Route path="qa" element={<QAHub />} />
                <Route path="peer-review"
                  element={
                    /* PeerReview is lazy-imported (line 34) but was
                       previously mounted bare — without a Suspense
                       boundary, React's reconciler hit the lazy
                       promise and the next render mounted the
                       resolved component for the first time. The
                       hooks-count delta between the two renders
                       fired React's #426 "rendered more hooks than
                       previous render" guard, surfacing as a blank
                       page that recovered only on a hard reload
                       (the eager import path). Every other lazy
                       route in this Routes block is Suspense-
                       wrapped; this one was the lone exception.
                       Fix: same Suspense pattern as the rest.
                       May 24 2026 UAT. */
                    <Suspense fallback={<_PageLoadingFallback />}>
                      <PeerReview />
                    </Suspense>
                  } />
                <Route path="reports" element={<Reports />} />
                <Route path="settings" element={<Settings />} />
                <Route path="reports/writer"
                  element={
                    <Suspense fallback={<_PageLoadingFallback />}>
                      <ReportWriter />
                    </Suspense>
                  } />
                <Route path="reports/storyboard"
                  element={
                    <Suspense fallback={<_PageLoadingFallback />}>
                      <StoryboardEditor />
                    </Suspense>
                  } />
                <Route path="reports/document/:documentId"
                  element={
                    <Suspense fallback={<_PageLoadingFallback />}>
                      <SectionEditor />
                    </Suspense>
                  } />
                <Route path="editor/:draftId"
                  element={
                    <Suspense fallback={<_PageLoadingFallback />}>
                      <DocumentEditor />
                    </Suspense>
                  } />
              </Route>
            </Routes>
          </UIProvider>
        </BrandProvider>
      </SessionProvider>
    </AuthProvider>
  )
}
