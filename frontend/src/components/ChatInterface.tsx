import { useState, useRef, useEffect } from 'react'
import { Send, AlertOctagon, Bot, User, Loader2 } from 'lucide-react'
import axios from 'axios'
import type { AgentMessage, CouncilResponse } from '../types/agents'

const SUGGESTED_QUERIES = [
  'Does fixed income diversification improve Sharpe ratio vs the benchmark?',
  'Which strategies pass all five Tier 1 significance gates?',
  'Explain the 2022 equity-bond correlation breakdown.',
  'What is the CV Stability Score for VOL_TARGETING?',
  'How does REGIME_SWITCHING perform during the GFC stress scenario?',
]

const AGENT_COLORS: Record<string, string> = {
  'Equity Analyst':               'border-blue-400/40 bg-blue-400/5',
  'Fixed Income Analyst':         'border-emerald-400/40 bg-emerald-400/5',
  'Risk Manager':                 'border-amber-400/40 bg-amber-400/5',
  'Quant Backtester':             'border-violet-400/40 bg-violet-400/5',
  'Independent Analyst (Gemini)': 'border-purple-400/40 bg-purple-400/5',
  'CIO':                          'border-electric/40 bg-electric/5',
  'System':                       'border-border bg-navy-700',
}

interface UserMessage {
  role: 'user'
  content: string
}

interface OutOfScopeMessage {
  role: 'out_of_scope'
  content: string
}

interface CouncilMessage {
  role: 'council'
  messages: AgentMessage[]
  final: string
}

type ChatMessage = UserMessage | OutOfScopeMessage | CouncilMessage

function AgentTag({ agent }: { agent: string }) {
  const isGemini = agent.includes('Gemini')
  const isCIO = agent === 'CIO'
  return (
    <span className={`text-2xs font-semibold px-1.5 py-0.5 rounded border ${
      isGemini ? 'text-purple-400 border-purple-400/30 bg-purple-400/10' :
      isCIO    ? 'text-electric border-electric/30 bg-electric/10' :
                 'text-muted border-border bg-navy-700'
    }`}>
      {agent}
    </span>
  )
}

function Message({ msg }: { msg: ChatMessage }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end gap-2">
        <div className="max-w-[80%] bg-electric/10 border border-electric/20 rounded-lg px-3 py-2">
          <div className="flex items-center gap-1.5 mb-1">
            <User className="w-3 h-3 text-electric" />
            <span className="text-2xs text-electric">You</span>
          </div>
          <p className="text-white text-sm">{msg.content}</p>
        </div>
      </div>
    )
  }

  if (msg.role === 'out_of_scope') {
    return (
      <div className="flex items-start gap-2 p-3 rounded-lg border border-warning/20 bg-warning/5">
        <AlertOctagon className="w-4 h-4 text-warning shrink-0 mt-0.5" />
        <div>
          <p className="text-warning text-xs font-semibold mb-1">Out of Scope</p>
          <p className="text-slate-300 text-sm">{msg.content}</p>
          <div className="mt-2">
            <p className="text-muted text-xs mb-1">Try asking about:</p>
            <ul className="space-y-0.5">
              {SUGGESTED_QUERIES.slice(0, 3).map((q) => (
                <li key={q} className="text-2xs text-muted list-disc ml-3">{q}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {msg.messages.map((m, i) => (
        <div key={i} className={`rounded-lg border px-3 py-2 ${AGENT_COLORS[m.agent] ?? 'border-border bg-navy-700'}`}>
          <div className="flex items-center gap-2 mb-1.5">
            <Bot className="w-3 h-3 text-muted" />
            <AgentTag agent={m.agent} />
            <span className="text-2xs text-muted font-mono ml-auto">{m.model}</span>
          </div>
          <p className="text-slate-300 text-sm leading-relaxed">{m.content}</p>
          {m.is_final && (
            <div className="mt-2 pt-2 border-t border-border">
              <span className="text-2xs text-electric font-semibold tracking-wide">FINAL RECOMMENDATION</span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export default function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendQuery = async (query: string) => {
    if (!query.trim() || loading) return
    const userMsg: UserMessage = { role: 'user', content: query }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const res = await axios.post<CouncilResponse>('/api/council/query', { query })
      const councilMsg: CouncilMessage = {
        role: 'council',
        messages: res.data.messages,
        final: res.data.final_recommendation,
      }
      setMessages((prev) => [...prev, councilMsg])
    } catch (err: unknown) {
      if (
        axios.isAxiosError(err) &&
        err.response?.status === 422 &&
        (err.response.data as { error?: string }).error === 'out_of_scope'
      ) {
        const detail = (err.response.data as { message?: string }).message ?? 'This query is out of scope.'
        setMessages((prev) => [...prev, { role: 'out_of_scope', content: detail }])
      } else {
        setMessages((prev) => [...prev, { role: 'out_of_scope', content: 'Something went wrong. Please try again.' }])
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center py-12">
            <Bot className="w-10 h-10 text-muted mx-auto mb-3" />
            <p className="text-muted text-sm mb-1">Ask the Investment Council</p>
            <p className="text-muted text-xs mb-6">
              Questions are scoped to portfolio strategy analysis for the Forest Capital practicum.
            </p>
            <div className="space-y-2 max-w-lg mx-auto">
              {SUGGESTED_QUERIES.map((q) => (
                <button
                  key={q}
                  onClick={() => void sendQuery(q)}
                  className="w-full text-left text-xs text-slate-300 border border-border rounded-lg px-3 py-2 hover:border-electric/30 hover:bg-electric/5 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <Message key={i} msg={msg} />
        ))}
        {loading && (
          <div className="flex items-center gap-2 text-muted text-sm">
            <Loader2 className="w-4 h-4 animate-spin" />
            Council deliberating…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border p-4">
        <form
          onSubmit={(e) => { e.preventDefault(); void sendQuery(input) }}
          className="flex gap-2"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask the investment council…"
            maxLength={500}
            className="flex-1 bg-navy-700 border border-border rounded-lg px-3 py-2 text-sm text-white placeholder-muted focus:outline-none focus:border-electric transition-colors"
          />
          <button
            type="submit"
            disabled={!input.trim() || loading}
            className="px-3 py-2 bg-electric hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        </form>
        <p className="text-muted text-2xs mt-1.5">
          Scoped to portfolio analysis · Max 500 characters · Results are mock data in Sprint 1
        </p>
      </div>
    </div>
  )
}
