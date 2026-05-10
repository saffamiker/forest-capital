import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useState, useEffect, createContext, useContext } from 'react'
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
  const [session, setSession] = useState<Session | null>(() => {
    const token = localStorage.getItem('fc_session_token')
    const email = localStorage.getItem('fc_email')
    return token && email ? { token, email } : null
  })

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
    localStorage.removeItem('fc_session_token')
    localStorage.removeItem('fc_email')
    delete axios.defaults.headers.common['X-API-Key']
    setSession(null)
  }

  useEffect(() => {
    const token = localStorage.getItem('fc_session_token')
    if (token) axios.defaults.headers.common['X-API-Key'] = token
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
