import { useState, useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { Bot, TrendingUp, AlertTriangle, Loader2, Send, BookOpen, X, RotateCw } from 'lucide-react'
import DisagreementHeatmap from './DisagreementHeatmap'
import PersonaModal from './PersonaModal'
import AcademicReviewButton from './AcademicReviewButton'
import Markdown from './Markdown'
import TeamGate from './TeamGate'
import type { AgentMessage } from '../types/agents'
import { useCouncilStore } from '../stores/councilStore'
import { trackFeature } from '../lib/activityLogger'

interface AgentStyleConfig {
  accent: string
  label: string
  tag: string
  note?: string
}

const AGENT_STYLE: Record<string, AgentStyleConfig> = {
  'Equity Analyst':                { accent: '#60a5fa', label: 'Equity Analyst',          tag: 'SPECIALIST', note: 'claude-sonnet-4-6' },
  'Fixed Income Analyst':          { accent: '#34d399', label: 'Fixed Income Analyst',     tag: 'SPECIALIST', note: 'claude-sonnet-4-6' },
  'Risk Manager':                  { accent: '#f59e0b', label: 'Risk Manager',             tag: 'SPECIALIST', note: 'claude-sonnet-4-6' },
  'Quant Backtester':              { accent: '#a78bfa', label: 'Quant / Backtester',       tag: 'SPECIALIST', note: 'claude-sonnet-4-6' },
  'Independent Analyst (Gemini)':  { accent: '#c084fc', label: 'Independent Analyst',      tag: 'DISSENTER', note: 'gemini-1.5-pro' },
  'Contrarian Analyst (Grok)':     { accent: '#f97316', label: 'Contrarian Analyst',       tag: 'DISSENTER', note: 'grok-4.3' },
  'CIO':                           { accent: '#3b82f6', label: 'Chief Investment Officer', tag: 'CIO',       note: 'claude-opus-4-7' },
}

function AgentCard({
  message, streaming = false, onViewPrompt,
}: {
  message: AgentMessage
  streaming?: boolean
  // Opens the PersonaModal for this agent. The card doesn't own modal
  // state — it lifts the click up to CouncilDebate which mounts the
  // modal in a single shared slot.
  onViewPrompt?: (message: AgentMessage) => void
}) {
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
          <Markdown content={message.content} />
        )}
      </div>

      {/* View system prompt — opens the three-tab PersonaModal. Hidden
          while streaming; the agent's persona is meaningless if the
          card hasn't returned content yet. */}
      {!streaming && onViewPrompt && (
        <div className="px-4 pb-2.5 -mt-1">
          <button
            type="button"
            onClick={() => onViewPrompt(message)}
            data-testid={`view-prompt-${message.agent}`}
            className="flex items-center gap-1 text-2xs text-muted hover:text-white transition-colors"
          >
            <BookOpen className="w-3 h-3" />
            View system prompt
          </button>
        </div>
      )}

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

export default function CouncilDebate() {
  // Query text, last result, and loading state all live in councilStore so
  // navigating Council → Dashboard → Council preserves the previous response
  // without a re-fetch. The query input is wired to the store's `query`
  // field so half-typed text also survives navigation.
  const { query, result, loading, error, lastQuery, setQuery, runQuery, abort } = useCouncilStore()
  const [activeTab, setActiveTab] = useState<'debate' | 'heatmap'>('debate')
  // Active persona-modal target. Null = modal closed. Carries the
  // agent message so the THIS SESSION tab can render the agent's
  // contribution to the current council run.
  const [personaTarget, setPersonaTarget] = useState<AgentMessage | null>(null)
  const location = useLocation()
  const inputRef = useRef<HTMLInputElement>(null)

  // Pre-fill from the Explainer's "Ask the Council" hand-off: the
  // ExplainerPanel navigates here with a contextual question in route
  // state. We set the field and focus it — never auto-submit, so the
  // user reviews and confirms before convening the council.
  useEffect(() => {
    const prefill = (location.state as { prefillQuestion?: string } | null)?.prefillQuestion
    if (prefill) {
      setQuery(prefill)
      inputRef.current?.focus()
    }
    // Mount-only — a later navigation without state must not re-fire.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (query.trim()) trackFeature('council_query_submit')
    void runQuery(query)
  }

  const agentOrder = ['Equity Analyst', 'Fixed Income Analyst', 'Risk Manager', 'Quant Backtester', 'Independent Analyst (Gemini)', 'Contrarian Analyst (Grok)', 'CIO']
  const messages = result?.messages ?? []
  const orderedMessages = agentOrder
    .map((name) => messages.find((m) => m.agent === name))
    .filter((m): m is AgentMessage => m !== undefined)

  return (
    <div className="p-4 md:p-6 max-w-screen-xl mx-auto space-y-4" data-tour="council">
      <div>
        <h1 className="text-xl font-semibold text-white">Investment Council</h1>
        <p className="text-muted text-sm mt-0.5">
          Six AI agents deliberate on your portfolio question. Gemini provides an independent dissenting view.
        </p>
      </div>

      {/* Query input */}
      {/* Stacked full-width on mobile; the input and the action button
          sit side by side from sm: up. */}
      <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-2">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask the council a portfolio analysis question…"
          maxLength={500}
          className="flex-1 bg-navy-800 border border-border rounded-lg px-4 py-2.5 text-sm text-white placeholder-muted focus:outline-none focus:border-electric transition-colors"
        />
        {loading ? (
          <button
            type="button"
            onClick={abort}
            className="flex items-center justify-center gap-2 w-full sm:w-auto
                       px-4 py-2.5 rounded-lg text-sm font-medium
                       border border-danger/30 bg-danger/10 text-danger
                       hover:bg-danger/20 transition-colors"
          >
            <X className="w-4 h-4" />
            <span>Cancel</span>
          </button>
        ) : (
          <button
            type="submit"
            disabled={!query.trim()}
            className="flex items-center justify-center gap-2 w-full sm:w-auto
                       px-4 py-2.5 bg-electric hover:bg-blue-500 disabled:opacity-40
                       disabled:cursor-not-allowed text-white text-sm font-medium
                       rounded-lg transition-colors"
          >
            <Send className="w-4 h-4" />
            <span>Convene</span>
          </button>
        )}
      </form>

      {/* Academic Review — a secondary council action that evaluates the
          project's analytics, findings and deliverables against the
          uploaded project requirements. A team feature; the Ask Council
          input above stays open to every authenticated user. */}
      <TeamGate block tooltip="Academic Review is available to the project team">
        <AcademicReviewButton />
      </TeamGate>

      {/* Council failure — a failed query previously left a blank screen
          (the empty state is gated on `!result`, and an errored run sets
          a truthy `result`). This surfaces the store error with a retry. */}
      {result?.error && !loading && (
        <div className="card border border-danger/30 bg-danger/5 p-4">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="text-danger font-semibold text-sm">Council query failed</div>
              <p className="text-slate-300 text-sm mt-1">
                {error ?? 'The council could not be reached. Please try again.'}
              </p>
              <button
                type="button"
                onClick={() => void runQuery(lastQuery || query)}
                className="mt-2 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded
                           border border-danger/30 text-danger hover:bg-danger/10 transition-colors"
              >
                <RotateCw className="w-3.5 h-3.5" /> Retry
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      {((result && !result.error) || loading) && (
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
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {loading && !result && agentOrder.map((name) => (
              <AgentCard
                key={name}
                message={{ agent: name, role: name === 'CIO' ? 'cio' : 'specialist', model: AGENT_STYLE[name]?.note ?? '', content: '', is_final: name === 'CIO' }}
                streaming
              />
            ))}
            {orderedMessages.map((msg) => (
              <AgentCard
                key={msg.agent}
                message={msg}
                onViewPrompt={setPersonaTarget}
              />
            ))}
          </div>

          {/* Defensive empty state — fires when the council ran but every
              agent returned an empty narrative. The Debate tab used to
              render an invisible empty grid in this case, which looked
              like a frontend bug; this banner makes the failure mode
              explicit so the user can re-run or check backend logs. */}
          {result && !result.error && !loading && orderedMessages.length === 0 && (
            <div className="card border border-warning/30 bg-warning/5 p-4">
              <div className="section-header mb-2 text-warning">Council ran — narratives missing</div>
              <p className="text-white text-sm">
                The council deliberation completed but no agent narrative was
                returned. The heatmap and final recommendation may still be
                populated. Try re-running the query, or check backend logs
                for council_session entries.
              </p>
            </div>
          )}
        </>
      )}

      {/* Final summary banner */}
      {result && !result.error && result.final_recommendation && activeTab === 'debate' && (
        <div className="card border border-electric/20 bg-electric/5 p-4">
          <div className="section-header mb-2">Council Consensus</div>
          <Markdown content={result.final_recommendation} />
        </div>
      )}

      {activeTab === 'heatmap' && <DisagreementHeatmap />}

      {/* PersonaModal — mounted once. Open state is controlled by
          personaTarget; closing nulls it back. */}
      {personaTarget && (
        <PersonaModal
          agentName={personaTarget.agent}
          sessionContent={personaTarget.content}
          onClose={() => setPersonaTarget(null)}
        />
      )}

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
