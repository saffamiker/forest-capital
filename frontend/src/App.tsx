import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import axios from 'axios'
import LoginPage from './pages/LoginPage'
import AuthVerify from './pages/AuthVerify'
import MainLayout from './layouts/MainLayout'
import Dashboard from './components/Dashboard'
import CouncilDebate from './components/CouncilDebate'
import QAAuditPanel from './components/QAAuditPanel'
import { BrandProvider } from './context/BrandContext'
import { UIProvider } from './context/UIContext'

// ── Auth context ──────────────────────────────────────────────────────────────

interface Session {
  token: string
  email: string
}

interface AuthContextType {
  session: Session | null
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

  // Stable helper that clears all session state without calling the logout endpoint
  // — used by the 401 interceptor where we can't await an API call
  const clearSession = useCallback(() => {
    localStorage.removeItem('fc_session_token')
    localStorage.removeItem('fc_email')
    delete axios.defaults.headers.common['X-API-Key']
    setSession(null)
  }, [])

  const login = (token: string, email: string) => {
    localStorage.setItem('fc_session_token', token)
    localStorage.setItem('fc_email', email)
    axios.defaults.headers.common['X-API-Key'] = token
    setSession({ token, email })
  }

  const logout = async () => {
    const token = session?.token
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

  // Restore axios header on mount (session persists across page refreshes)
  useEffect(() => {
    const token = localStorage.getItem('fc_session_token')
    if (token) axios.defaults.headers.common['X-API-Key'] = token
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
    <AuthContext.Provider value={{ session, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// ── Route guard ───────────────────────────────────────────────────────────────

function RequireAuth({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const location = useLocation()
  if (!session) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <AuthProvider>
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
              <Route path="council" element={<CouncilDebate />} />
              <Route path="qa" element={<QAAuditPanel />} />
            </Route>
          </Routes>
        </UIProvider>
      </BrandProvider>
    </AuthProvider>
  )
}
