import { useState } from 'react'
import axios from 'axios'
import { TrendingUp, Mail, ArrowRight, AlertCircle } from 'lucide-react'

type LoginStatus = 'idle' | 'loading' | 'sent' | 'error'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<LoginStatus>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setStatus('loading')
    setErrorMsg('')
    try {
      const res = await axios.post('/api/auth/request-link', { email: email.trim() })
      setStatus('sent')
      if ((res.data as { dev_mode?: boolean }).dev_mode) {
        setErrorMsg('Dev mode: check the backend terminal for your login link.')
      }
    } catch (err: unknown) {
      setStatus('error')
      const detail = axios.isAxiosError(err)
        ? (err.response?.data as { detail?: string } | undefined)?.detail
        : undefined
      setErrorMsg(detail ?? 'Something went wrong. Please try again.')
    }
  }

  return (
    <div className="min-h-screen bg-navy-900 flex flex-col items-center justify-center px-4">
      {/* Logo */}
      <div className="flex items-center gap-3 mb-10">
        <div className="w-10 h-10 rounded-lg bg-electric/10 border border-electric/30 flex items-center justify-center">
          <TrendingUp className="w-5 h-5 text-electric" />
        </div>
        <div>
          <div className="text-white font-semibold tracking-wide text-lg leading-none">Forest Capital</div>
          <div className="text-muted text-xs tracking-widest uppercase mt-0.5">Portfolio Intelligence System</div>
        </div>
      </div>

      {/* Card */}
      <div className="w-full max-w-md card p-8">
        {status !== 'sent' ? (
          <>
            <h1 className="text-xl font-semibold text-white mb-1">Sign in</h1>
            <p className="text-muted text-sm mb-6">
              Enter your authorised email address. A magic link will be sent to you.
            </p>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="text-xs text-muted font-medium block mb-1.5">Email address</label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    placeholder="you@queens.edu"
                    className="w-full bg-navy-700 border border-border rounded-md pl-9 pr-4 py-2.5 text-sm text-white placeholder-muted focus:outline-none focus:border-electric transition-colors"
                  />
                </div>
              </div>

              {status === 'error' && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-danger/10 border border-danger/20">
                  <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
                  <p className="text-danger text-xs">{errorMsg}</p>
                </div>
              )}

              <button
                type="submit"
                disabled={status === 'loading' || !email}
                className="w-full flex items-center justify-center gap-2 bg-electric hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium text-sm rounded-md py-2.5 transition-colors"
              >
                {status === 'loading' ? (
                  <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <>Send Magic Link <ArrowRight className="w-4 h-4" /></>
                )}
              </button>
            </form>
          </>
        ) : (
          <div className="text-center py-4">
            <div className="w-12 h-12 rounded-full bg-success/10 border border-success/20 flex items-center justify-center mx-auto mb-4">
              <Mail className="w-6 h-6 text-success" />
            </div>
            <h2 className="text-white font-semibold text-lg mb-2">Check your inbox</h2>
            <p className="text-muted text-sm mb-4">
              A login link has been sent to <span className="text-white">{email}</span>.
              It expires in 15 minutes.
            </p>
            {errorMsg && (
              <div className="p-3 rounded-md bg-electric/10 border border-electric/20">
                <p className="text-electric text-xs font-mono">{errorMsg}</p>
              </div>
            )}
            <button
              onClick={() => setStatus('idle')}
              className="mt-4 text-xs text-muted hover:text-white transition-colors underline underline-offset-2"
            >
              Use a different email
            </button>
          </div>
        )}
      </div>

      {/* Footer */}
      <p className="text-muted text-xs mt-8">
        MSFA FNA 667 · Queens University of Charlotte · Forest Capital Practicum
      </p>
    </div>
  )
}
