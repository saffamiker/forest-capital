import { useState } from 'react'
import { Bot, TrendingUp, AlertTriangle, Loader2, Send } from 'lucide-react'
import axios from 'axios'
import DisagreementHeatmap from './DisagreementHeatmap'
import type { AgentMessage, CouncilResponse } from '../types/agents'

interface AgentStyleConfig {
  accent: string
  label: string
  tag: string
  note?: string
}

const AGENT_STYLE: Record<string, AgentStyleConfig> = {
  'Equity Analyst':                { accent: '#60a5fa', label: 'Equity Analyst',          tag: 'SPECIALIST' },
  'Fixed Income Analyst':          { accent: '#34d399', label: 'Fixed Income Analyst',     tag: 'SPECIALIST' },
  'Risk Manager':                  { accent: '#f59e0b', label: 'Risk Manager',             tag: 'SPECIALIST' },
  'Quant Backtester':              { accent: '#a78bfa', label: 'Quant / Backtester',       tag: 'SPECIALIST' },
  'Independent Analyst (Gemini)':  { accent: '#c084fc', label: 'Independent Analyst',      tag: 'DISSENTER', note: 'Gemini Pro' },
  'CIO':                           { accent: '#3b82f6', label: 'Chief Investment Officer', tag: 'CIO' },
}

function AgentCard({ message, streaming = false }: { message: AgentMessage; streaming?: boolean }) {
  const style = AGENT_STYLE[message.agent] ?? { accent: '#64748b', label: message.agent, tag: 'AGENT' }
  const isGemini = message.agent.includes('Gemini')
  const isCIO = message.agent === 'CIO'

  return (
    <div className="card overflow-hidden" style={{ borderColor: `${style.accent}30` }}>
      {/* Agent header */}
      <div
        className="px-4 py-3 flex items-center gap-3 border-b border-border"
        style={{ backgroundColor: `${style.accent}08` }}
      >
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center shrink-0"
          style={{ backgroundColor: `${style.accent}20`, border: `1px solid ${style.accent}40` }}
        >
          {isCIO ? (
            <TrendingUp className="w-3.5 h-3.5" style={{ color: style.accent }} />
          ) : (
            <Bot className="w-3.5 h-3.5" style={{ color: style.accent }} />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-white font-semibold text-sm">{style.label}</span>
            {style.note && (
              <span
                className="text-2xs px-1.5 py-0.5 rounded border"
                style={{ color: style.accent, borderColor: `${style.accent}30`, backgroundColor: `${style.accent}10` }}
              >
                {style.note}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-2xs font-semibold tracking-widest" style={{ color: style.accent }}>
              {style.tag}
            </span>
            <span className="text-muted text-2xs font-mono">· {message.model}</span>
          </div>
        </div>
        {isGemini && (
          <div className="flex items-center gap-1 text-purple-400 border border-purple-400/20 rounded px-2 py-0.5 bg-purple-400/10">
            <AlertTriangle className="w-3 h-3" />
            <span className="text-2xs font-semibold">Dissenting View</span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="px-4 py-3">
        {streaming ? (
          <div className="flex items-center gap-2 text-muted text-sm">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            <span>Analysing…</span>
          </div>
        ) : (
          <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">{message.content}</p>
        )}
      </div>

      {/* CIO final marker */}
      {message.is_final && !streaming && (
        <div
          className="px-4 py-2 border-t border-border flex items-center gap-2"
          style={{ backgroundColor: `${style.accent}08` }}
        >
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: style.accent }} />
          <span className="text-xs font-semibold tracking-wide" style={{ color: style.accent }}>
            FINAL RECOMMENDATION
          </span>
        </div>
      )}
    </div>
  )
}

interface CouncilResult extends CouncilResponse {
  error?: boolean
}

export default function CouncilDebate() {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<CouncilResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<'debate' | 'heatmap'>('debate')

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!query.trim() || loading) return
    setLoading(true)
    setResult(null)
    try {
      const res = await axios.post<CouncilResponse>('/api/council/query', { query })
      setResult(res.data)
    } catch {
      setResult({ error: true, query: '', messages: [], final_recommendation: '', consensus_reached: false })
    } finally {
      setLoading(false)
    }
  }

  const agentOrder = ['Equity Analyst', 'Fixed Income Analyst', 'Risk Manager', 'Quant Backtester', 'Independent Analyst (Gemini)', 'CIO']
  const messages = result?.messages ?? []
  const orderedMessages = agentOrder
    .map((name) => messages.find((m) => m.agent === name))
    .filter((m): m is AgentMessage => m !== undefined)

  return (
    <div className="p-4 md:p-6 max-w-screen-xl mx-auto space-y-4">
      <div>
        <h1 className="text-white font-bold text-xl">Investment Council</h1>
        <p className="text-muted text-sm mt-0.5">
          Six AI agents deliberate on your portfolio question. Gemini provides an independent dissenting view.
        </p>
      </div>

      {/* Query input */}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask the council a portfolio analysis question…"
          maxLength={500}
          className="flex-1 bg-navy-800 border border-border rounded-lg px-4 py-2.5 text-sm text-white placeholder-muted focus:outline-none focus:border-electric transition-colors"
        />
        <button
          type="submit"
          disabled={!query.trim() || loading}
          className="flex items-center gap-2 px-4 py-2.5 bg-electric hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          <span className="hidden sm:inline">Convene</span>
        </button>
      </form>

      {/* Tabs */}
      {(result ?? loading) && (
        <div className="flex gap-1">
          {(['debate', 'heatmap'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                activeTab === tab
                  ? 'border-electric bg-electric/10 text-electric'
                  : 'border-border text-muted hover:text-white'
              }`}
            >
              {tab === 'debate' ? 'Debate' : 'Disagreement Heatmap'}
            </button>
          ))}
        </div>
      )}

      {/* Agent cards */}
      {activeTab === 'debate' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {loading && !result && agentOrder.map((name) => (
            <AgentCard
              key={name}
              message={{ agent: name, role: 'specialist', model: AGENT_STYLE[name]?.note ?? 'claude-sonnet-4-20250514', content: '', is_final: name === 'CIO' }}
              streaming
            />
          ))}
          {orderedMessages.map((msg) => (
            <AgentCard key={msg.agent} message={msg} />
          ))}
        </div>
      )}

      {/* Final summary banner */}
      {result && !result.error && result.final_recommendation && activeTab === 'debate' && (
        <div className="card border border-electric/20 bg-electric/5 p-4">
          <div className="section-header mb-2">Council Consensus</div>
          <p className="text-white text-sm">{result.final_recommendation}</p>
        </div>
      )}

      {activeTab === 'heatmap' && <DisagreementHeatmap />}

      {/* Empty state */}
      {!result && !loading && (
        <div className="card p-10 text-center">
          <Bot className="w-10 h-10 text-muted mx-auto mb-3" />
          <p className="text-muted text-sm">
            Submit a portfolio analysis question to convene the council.
          </p>
          <div className="mt-4 space-y-1.5 max-w-sm mx-auto">
            {[
              'Which strategies pass all Tier 1 significance gates?',
              'Evaluate REGIME_SWITCHING vs VOL_TARGETING',
              'Does the 2022 correlation breakdown invalidate 60/40?',
            ].map((q) => (
              <button
                key={q}
                onClick={() => setQuery(q)}
                className="w-full text-left text-xs text-muted hover:text-white border border-border hover:border-border/80 rounded px-3 py-2 transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
