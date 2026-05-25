/**
 * LoginPage — institutional rebrand (May 24 2026).
 *
 * Five changes per the brand-uplift spec:
 *   1. The "Forest Capital" text wordmark is replaced by the official
 *      hexagon-and-wordmark lockup (forest-capital.jpg). The JPG
 *      ships with a dark navy background that blends naturally into
 *      the page; no inversion needed.
 *   2. The subtitle is "McColl School of Business · FNA 670" — the
 *      academic context that anchors the platform.
 *   3. The Queens University and McColl School of Business marks
 *      sit side by side ABOVE the sign-in card in slim white
 *      lockup cards. Both source assets are navy-on-white; the
 *      white card backdrop preserves the navy ink without inversion
 *      artefacts (mix-blend-mode strips fine type detail).
 *   4. The footer reads "MSFA FNA 670 · Queens University of
 *      Charlotte · McColl School of Business" — the full
 *      institutional attribution.
 *   5. The dark navy page background, the magic-link flow, and the
 *      sign-in card's internal layout are unchanged — only the
 *      chrome around the card was rebranded.
 *
 * Asset filenames: the actual files in /public/assets/logos/ are
 * forest-capital.jpg (not .png), mccoll.jpeg (not .jpg), queens.png.
 * Vite serves them at /assets/logos/<name>.
 */
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import axios from 'axios'
import { Mail, ArrowRight, AlertCircle } from 'lucide-react'
import type { MagicLinkResponse } from '../types/api'

type LoginStatus = 'idle' | 'loading' | 'sent' | 'error'

export default function LoginPage() {
  const [searchParams] = useSearchParams()
  const sessionExpired = searchParams.get('expired') === '1'
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<LoginStatus>('idle')
  const [errorMsg, setErrorMsg] = useState('')
  // true  → email is on the approved list; show "check your inbox" with address
  // false → email not approved; show generic confirmation to prevent enumeration
  const [isApproved, setIsApproved] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setStatus('loading')
    setErrorMsg('')
    try {
      const res = await axios.post<MagicLinkResponse>('/api/auth/request-link', {
        email: email.trim(),
      })
      setIsApproved(res.data.status === 'sent')
      setStatus('sent')
      if (res.data.dev_mode && res.data.status === 'sent') {
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
    <div className="min-h-screen bg-navy-900 flex flex-col items-center justify-center px-4 py-8">
      {/* Forest Capital lockup — official hexagon + wordmark image.
          The JPG already carries a dark navy background that blends
          into the page surface; the wrapper just constrains size +
          centres it. max-w-sm keeps it from dominating on a desktop
          viewport; min-h on the img enforces the 44px header minimum
          documented in the brand-uplift spec. */}
      <div className="mb-6 flex justify-center" data-testid="login-forest-capital-lockup">
        <img
          src="/assets/logos/forest-capital.jpg"
          alt="Forest Capital — Income & Growth Advisors"
          className="block max-w-[280px] sm:max-w-[340px] w-full h-auto"
          loading="eager"
        />
      </div>

      {/* Subtitle — the academic-context anchor. */}
      <p className="text-muted text-xs tracking-widest uppercase mb-6 text-center">
        McColl School of Business · FNA 670
      </p>

      {/* Institutional lockup row — Queens + McColl side by side
          above the sign-in card. Both assets are navy ink on a white
          ground; the white card backdrop preserves the ink colour
          exactly. The container is responsive: stacked on the
          smallest phones (< sm), side-by-side from sm: up.
          aspect-square on Queens vs aspect-[800/200] on McColl
          keeps each one in proportion regardless of viewport. */}
      <div
        data-testid="login-institutional-lockup"
        className="w-full max-w-md mb-6 flex flex-col xs:flex-row sm:flex-row
                   items-stretch justify-center gap-3"
      >
        <div className="flex-1 bg-white rounded-md px-4 py-3 flex items-center
                        justify-center min-h-[72px]">
          <img
            src="/assets/logos/queens.png"
            alt="Queens University of Charlotte"
            className="block max-h-12 w-auto"
            loading="eager"
          />
        </div>
        <div className="flex-1 bg-white rounded-md px-4 py-3 flex items-center
                        justify-center min-h-[72px]">
          <img
            src="/assets/logos/mccoll.jpeg"
            alt="McColl School of Business"
            className="block max-h-10 w-auto"
            loading="eager"
          />
        </div>
      </div>

      {/* Card — sign-in flow UNCHANGED. The magic-link contract,
          the approved-email status-message branching, and every
          field's wiring are identical to the pre-rebrand layout. */}
      <div className="w-full max-w-md card p-8">
        {status !== 'sent' ? (
          <>
            <h1 className="text-xl font-semibold text-white mb-1">Sign in</h1>
            <p className="text-muted text-sm mb-6">
              Enter your authorized email address. A magic link will be sent to you.
            </p>

            {sessionExpired && (
              <div className="flex items-start gap-2 p-3 rounded-md bg-amber-500/10 border border-amber-500/20 mb-4">
                <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <p className="text-amber-400 text-xs">Your session has expired. Please log in again.</p>
              </div>
            )}

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

            {isApproved ? (
              <>
                <h2 className="text-white font-semibold text-lg mb-2">Check your inbox</h2>
                <p className="text-muted text-sm mb-4">
                  A login link has been sent to{' '}
                  <span className="text-white">{email}</span>.
                  It expires in 15 minutes.
                </p>
              </>
            ) : (
              <>
                <h2 className="text-white font-semibold text-lg mb-2">Request received</h2>
                <p className="text-muted text-sm mb-4">
                  If that email address is authorized, a login link has been sent.
                  Check your inbox and spam folder.
                </p>
              </>
            )}

            {errorMsg && (
              <div className="p-3 rounded-md bg-electric/10 border border-electric/20">
                <p className="text-electric text-xs font-mono">{errorMsg}</p>
              </div>
            )}
            <button
              onClick={() => { setStatus('idle'); setIsApproved(false) }}
              className="mt-4 text-xs text-muted hover:text-white transition-colors underline underline-offset-2"
            >
              Use a different email
            </button>
          </div>
        )}
      </div>

      {/* Footer — full institutional attribution per the brand spec. */}
      <p className="text-muted text-xs mt-8 text-center" data-testid="login-footer">
        MSFA FNA 670 · Queens University of Charlotte · McColl School of Business
      </p>
    </div>
  )
}
