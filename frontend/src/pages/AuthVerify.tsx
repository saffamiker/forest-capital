import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import axios from 'axios'
import { TrendingUp, CheckCircle, XCircle } from 'lucide-react'
import { useAuth } from '../App'

type VerifyStatus = 'verifying' | 'success' | 'error'

interface SessionResponse {
  session_token: string
  email: string
}

export default function AuthVerify() {
  const [searchParams] = useSearchParams()
  const { login } = useAuth()
  const navigate = useNavigate()
  const [status, setStatus] = useState<VerifyStatus>('verifying')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    const token = searchParams.get('token')
    if (!token) {
      setStatus('error')
      setErrorMsg('No token provided in the link.')
      return
    }

    axios
      .get<SessionResponse>(`/api/auth/verify?token=${encodeURIComponent(token)}`)
      .then((res) => {
        login(res.data.session_token, res.data.email)
        setStatus('success')
        setTimeout(() => navigate('/'), 1200)
      })
      .catch((err: unknown) => {
        setStatus('error')
        const detail = axios.isAxiosError(err)
          ? (err.response?.data as { detail?: string } | undefined)?.detail
          : undefined
        setErrorMsg(detail ?? 'Verification failed. The link may have expired.')
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="min-h-screen bg-navy-900 flex flex-col items-center justify-center px-4">
      <div className="flex items-center gap-3 mb-10">
        <div className="w-10 h-10 rounded-lg bg-electric/10 border border-electric/30 flex items-center justify-center">
          <TrendingUp className="w-5 h-5 text-electric" />
        </div>
        <div>
          <div className="text-white font-semibold tracking-wide text-lg leading-none">Forest Capital</div>
          <div className="text-muted text-xs tracking-widest uppercase mt-0.5">Portfolio Intelligence System</div>
        </div>
      </div>

      <div className="w-full max-w-sm card p-8 text-center">
        {status === 'verifying' && (
          <>
            <div className="w-10 h-10 border-2 border-electric/30 border-t-electric rounded-full animate-spin mx-auto mb-4" />
            <p className="text-white font-medium">Verifying your link…</p>
          </>
        )}
        {status === 'success' && (
          <>
            <CheckCircle className="w-10 h-10 text-success mx-auto mb-4" />
            <p className="text-white font-medium">Verified. Redirecting to dashboard…</p>
          </>
        )}
        {status === 'error' && (
          <>
            <XCircle className="w-10 h-10 text-danger mx-auto mb-4" />
            <p className="text-white font-medium mb-2">Verification failed</p>
            <p className="text-muted text-sm mb-4">{errorMsg}</p>
            <button
              onClick={() => navigate('/login')}
              className="text-electric text-sm hover:underline"
            >
              Request a new link
            </button>
          </>
        )}
      </div>
    </div>
  )
}
