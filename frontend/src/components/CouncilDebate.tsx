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
import { useAuth } from '../App'

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
  const { query, result, loading, error, lastQuery, setQuery, runQuery, abort,
          councilUsage } = useCouncilStore()
  const { session } = useAuth()
  // Viewer council allocation — the store's post-query usage if a query
  // has run this session, otherwise the value from /api/auth/me. A null
  // limit means the user is unlimited (team member / sysadmin).
  const councilUsed = councilUsage?.used ?? session?.councilQueriesUsed ?? 0
  const councilLimit = councilUsage?.limit ?? session?.councilQueriesLimit ?? null
  const isLimited = councilLimit != null
  const limitReached = isLimited && councilUsed >= councilLimit
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
  //
  // May 22 2026 — the handoff also carries a structured handoff
  // package (explainer_topic, explainer_content, chart_context,
  // macro_summary, thread). We retain it in component state so the
  // Continuing-From banner can render the prior thread, AND so the
  // council session POST can include the package on submit (the
  // backend system prompt then references the prior CIO thread as
  // established context, not a fresh question).
  type HandoffPackage = {
    handoff_source?: string
    explainer_topic?: string
    explainer_content?: string
    chart_context?: { name?: string; values?: Record<string, unknown> }
    thread?: Array<{ role: 'user' | 'cio'; content: string }>
    handoff_question?: string
  }
  const [handoff, setHandoff] = useState<HandoffPackage | null>(null)
  const [handoffOpen, setHandoffOpen] = useState(true)

  useEffect(() => {
    const state = location.state as {
      prefillQuestion?: string
      handoff?: HandoffPackage
    } | null
    if (state?.prefillQuestion) {
      setQuery(state.prefillQuestion)
      inputRef.current?.focus()
    }
    if (state?.handoff) {
      setHandoff(state.handoff)
    }
    // May 24 2026 (ID 271) — dep on location.state.
    // The mount-only `[]` deps array meant that when the user was
    // ALREADY on /council and clicked the Explainer's "Ask the
    // Council" button, the navigation kept CouncilDebate mounted
    // and only updated location.state — so the effect never
    // refired and the prefill never landed. Tying the effect to
    // location.state makes every cross-screen handoff land its
    // contextual question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state])

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

      {/* Handoff context banner — when the user arrived here from an
          ExplainerPanel "Take this to the Council" click, the route
          state carries a handoff package (explainer_topic, prior
          thread, chart context). The banner makes that context
          visible to the user — the prior CIO exchanges are not
          buried, and collapsing the banner does not lose them. The
          handoff package is also sent on the council POST so the
          system prompt treats the prior thread as established
          context rather than asking the council to start from
          scratch. */}
      {handoff && handoff.explainer_topic && (
        <div
          data-testid="council-handoff-banner"
          className="rounded-lg border border-electric/30 bg-electric/5 p-3">
          <button
            type="button"
            onClick={() => setHandoffOpen((o) => !o)}
            className="w-full flex items-center justify-between gap-2 text-left">
            <div className="min-w-0 flex-1">
              <div className="text-2xs uppercase tracking-wide text-electric
                              font-semibold">
                Continuing from {handoff.explainer_topic} explainer
              </div>
              <div className="text-xs text-muted mt-0.5">
                {(handoff.thread?.length ?? 0) > 0
                  ? `${(handoff.thread ?? []).filter((e) => e.role === 'user').length} prior `
                    + `exchange${(handoff.thread ?? []).filter((e) => e.role === 'user').length === 1
                      ? '' : 's'} with the CIO included as context.`
                  : 'No prior follow-up exchanges — explainer content '
                    + 'included as context.'}
              </div>
            </div>
            <span className="text-2xs text-electric shrink-0">
              {handoffOpen ? 'Hide context ▴' : 'Show context ▾'}
            </span>
          </button>
          {handoffOpen && (
            <div className="mt-3 pt-3 border-t border-border space-y-2">
              {handoff.explainer_content && (
                <div className="rounded bg-navy-900/50 p-2">
                  <div className="text-2xs uppercase tracking-wide text-muted mb-1">
                    Explainer content the user has already read
                  </div>
                  <div className="text-xs text-slate-300 leading-relaxed
                                  break-words max-h-[120px] overflow-y-auto
                                  whitespace-pre-wrap">
                    {handoff.explainer_content}
                  </div>
                </div>
              )}
              {(handoff.thread ?? []).map((ex, i) => (
                <div key={i} className={ex.role === 'user'
                  ? 'rounded bg-navy-700/40 p-2'
                  : 'rounded bg-electric/5 border border-electric/20 p-2'}>
                  <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">
                    {ex.role === 'user' ? 'User' : 'CIO'}
                  </div>
                  <div className="text-xs text-slate-300 leading-relaxed
                                  break-words">
                    {ex.content}
                  </div>
                </div>
              ))}
              <button
                type="button"
                onClick={() => setHandoff(null)}
                className="text-2xs text-muted hover:text-warning underline">
                Discard handoff context
              </button>
            </div>
          )}
        </div>
      )}

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
            disabled={!query.trim() || limitReached}
            title={limitReached
              ? 'Council query allocation used — contact Michael Ruurds '
                + 'for additional access.'
              : undefined}
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

      {/* Viewer council allocation — shown only for a limited (non-
          unlimited) user; hidden entirely for team members and sysadmin. */}
      {isLimited && (
        <div className={`text-xs rounded-lg border px-3 py-2 ${
          limitReached
            ? 'border-warning/40 bg-warning/10 text-warning'
            : 'border-border bg-navy-800 text-muted'}`}>
          {limitReached ? (
            <span>
              You have used all {councilLimit} of your council queries.
              {' '}Please contact{' '}
              <a href="mailto:ruurdsm@queens.edu"
                 className="text-electric hover:underline">Michael Ruurds</a>
              {' '}to request additional access.
            </span>
          ) : (
            <span>Council queries: {councilUsed} of {councilLimit} used.</span>
          )}
        </div>
      )}

      {/* Academic Review — a secondary council action that evaluates the
          project's analytics, findings and deliverables against the
          uploaded project requirements. A team feature; the Ask Council
          input above stays open to every authenticated user. */}
      <TeamGate block tooltip="Academic Review is available to the project team">
        <AcademicReviewButton />
      </TeamGate>

      {/* Council failure — a failed query previously left a blank screen
          (the empty state is gated on `!result`, and an errored run sets
          a truthy `result`). This surfaces the store error with a retry.
          The council_limit_reached case is NOT an error — the viewer
          allocation banner above already explains it — so it is excluded. */}
      {result?.error && !loading && error !== 'council_limit_reached' && (
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

      {/* Convening indicator — visible the moment a query is submitted
          and stays up until council_complete fires (loading flips to
          false). The previous gate was `loading && !result`, but
          runQuery() now seeds an empty result skeleton up front so the
          orderedMessages map can render specialists as they stream —
          that made `!result` always false and the convening banner
          stopped appearing. The single source of truth is `loading`;
          this banner and the disagreement-heatmap skeleton below both
          gate on it. Restored May 24 2026. */}
      {loading && activeTab === 'debate' && (
        <div
          data-testid="council-convening-banner"
          className="card border border-electric/30 bg-electric/5 px-4 py-3
                     flex items-center gap-3"
        >
          <Loader2 className="w-4 h-4 text-electric animate-spin shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-electric font-semibold text-sm">
              Council is convening
            </div>
            <div className="text-xs text-muted mt-0.5">
              Six specialists are deliberating — typically 30-90 seconds.
              Their reports stream in as each completes.
            </div>
          </div>
        </div>
      )}

      {/* Agent cards */}
      {activeTab === 'debate' && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Skeleton card for every agent that hasn't streamed in yet —
                fires for any agent missing from orderedMessages WHILE
                loading is true. The grid fills in progressively as each
                specialist_complete frame arrives; once the streaming
                response is done, every slot has been replaced by the
                real AgentCard. */}
            {loading && agentOrder
              .filter((name) => !messages.some((m) => m.agent === name))
              .map((name) => (
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

      {/* Heatmap — only renders once the streaming response is complete
          AND the current run has produced at least one specialist
          message. The prior unconditional render leaked the mock-data
          fallback while the council was mid-stream, which read as
          "stale data from the previous session" to the user. The
          completion signal is `!loading && orderedMessages.length > 0`
          — the same loading flag that powers the convening banner
          above, so there is one source of truth for "decision
          complete". A loading skeleton is rendered in its place while
          the council is still streaming. May 24 2026. */}
      {activeTab === 'heatmap' && (
        loading ? (
          <div
            data-testid="council-heatmap-skeleton"
            className="card p-6 flex items-center justify-center gap-3"
          >
            <Loader2 className="w-4 h-4 text-electric animate-spin shrink-0" />
            <span className="text-muted text-sm">
              Waiting for the council to finish before computing
              disagreement.
            </span>
          </div>
        ) : orderedMessages.length > 0 ? (
          <DisagreementHeatmap />
        ) : (
          <div className="card p-6 text-center text-muted text-sm">
            Submit a query to populate the disagreement heatmap.
          </div>
        )
      )}

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
