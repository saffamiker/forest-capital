/**
 * frontend/src/components/PersonaModal.tsx
 *
 * Opens when the user clicks "View system prompt" on a council agent
 * card. Three tabs (CLAUDE.md Section 15):
 *
 *   PROMPT          — verbatim system_prompt from /api/agents/personas.
 *                     The auditable artefact: every word Forest Capital
 *                     might want to inspect lives here.
 *   PLAIN ENGLISH   — Explainer Agent's narrative (glossaryStore.loadPersona)
 *                     describing what the prompt instructed the agent to do.
 *                     Streams in after a brief loading state.
 *   THIS SESSION    — the agent's actual contribution to the current council
 *                     run — what the prompt produced when applied to the
 *                     latest question.
 *
 * The modal owns its own data-fetching for the personas list. The
 * Explainer-generated content is cached in glossaryStore so re-opening
 * the same agent's modal doesn't re-fire the LLM call.
 */
import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { Copy, Loader2, AlertTriangle } from 'lucide-react'
import { ModalCloseButton } from './ModalControls'

import { useGlossaryStore } from '../stores/glossaryStore'


export interface AgentPersona {
  agent:                          string
  model:                          string
  module:                         string
  system_prompt:                  string
  prompt_summary_first_sentence:  string
}

interface PersonaModalProps {
  agentName:       string
  sessionContent?: string   // The agent's content in the current council run.
  onClose:         () => void
}

type TabKey = 'prompt' | 'plain' | 'session'

const TAB_LABELS: Record<TabKey, string> = {
  prompt:  'Prompt',
  plain:   'Plain English',
  session: 'This Session',
}


export default function PersonaModal({
  agentName, sessionContent, onClose,
}: PersonaModalProps) {
  const [personas, setPersonas] = useState<AgentPersona[] | null>(null)
  const [personasError, setPersonasError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabKey>('prompt')
  const [copied, setCopied] = useState(false)

  const personasInGlossary = useGlossaryStore((s) => s.personas)
  const loadPersona = useGlossaryStore((s) => s.loadPersona)
  const plainEnglish = personasInGlossary[agentName]

  // Find this agent's persona record once the list is loaded.
  const persona: AgentPersona | undefined = useMemo(
    () => personas?.find((p) => p.agent === agentName),
    [personas, agentName],
  )

  // Fetch the personas list on first render. The endpoint is cheap
  // (no LLM call) so we don't bother caching it across re-mounts — a
  // user opening the modal weeks apart should see the current prompts.
  useEffect(() => {
    let cancelled = false
    void axios.get<{ agents: AgentPersona[] }>('/api/agents/personas')
      .then((res) => { if (!cancelled) setPersonas(res.data.agents) })
      .catch((err) => {
        if (cancelled) return
        const msg = axios.isAxiosError(err)
          ? (err.response?.data?.detail ?? err.message)
          : 'Failed to load agent personas'
        setPersonasError(String(msg))
      })
    return () => { cancelled = true }
  }, [])

  // Once we have the persona's prompt, fire the Explainer call for the
  // plain-English narrative. loadPersona is idempotent — re-clicking
  // the same agent doesn't re-fire the LLM call.
  useEffect(() => {
    if (!persona) return
    void loadPersona(
      persona.agent,
      persona.system_prompt,
      sessionContent ? { summary: sessionContent } : {},
    )
  }, [persona, sessionContent, loadPersona])

  // Close on Escape — matches the rest of the modal stack.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleCopyPrompt = async () => {
    if (!persona) return
    try {
      await navigator.clipboard.writeText(persona.system_prompt)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API can fail in sandboxed iframes — silently no-op.
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
      data-testid="persona-modal"
    >
      <div className="bg-navy-800 border border-border rounded-lg w-full max-w-3xl max-h-[85vh] flex flex-col">
        {/* Header */}
        <header className="flex items-start justify-between px-5 py-3 border-b border-border shrink-0">
          <div>
            <h2 className="text-white font-semibold text-base">
              {agentName}
            </h2>
            {persona?.model && (
              <p className="text-muted text-2xs font-mono mt-0.5">{persona.model}</p>
            )}
          </div>
          <ModalCloseButton
            onClose={onClose}
            ariaLabel="Close persona modal"
          />
        </header>

        {/* Tabs */}
        <div className="px-5 pt-2 border-b border-border shrink-0 flex gap-1">
          {(Object.keys(TAB_LABELS) as TabKey[]).map((t) => (
            <button
              key={t}
              onClick={() => setActiveTab(t)}
              data-testid={`persona-tab-${t}`}
              className={`text-xs px-3 py-1.5 border-b-2 transition-colors ${
                activeTab === t
                  ? 'border-electric text-electric'
                  : 'border-transparent text-muted hover:text-white'
              }`}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          {personasError && (
            <div className="flex items-start gap-2 px-3 py-2 rounded border border-danger/30 bg-danger/5 text-danger text-xs mb-3">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>{personasError}</span>
            </div>
          )}

          {!personas && !personasError && (
            <div className="flex items-center gap-2 text-muted text-sm">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Loading agent personas…
            </div>
          )}

          {persona && activeTab === 'prompt' && (
            <div data-testid="persona-tab-content-prompt">
              <div className="flex items-center justify-between mb-2">
                <span className="text-2xs uppercase tracking-wide text-muted">
                  Verbatim system prompt
                </span>
                <button
                  onClick={() => void handleCopyPrompt()}
                  className="flex items-center gap-1 text-2xs text-muted hover:text-white"
                  data-testid="copy-prompt-button"
                >
                  <Copy className="w-3 h-3" />
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <pre className="bg-navy-900 border border-border rounded p-3 text-2xs text-slate-300 whitespace-pre-wrap font-mono max-h-[55vh] overflow-y-auto">
                {persona.system_prompt}
              </pre>
            </div>
          )}

          {persona && activeTab === 'plain' && (
            <div data-testid="persona-tab-content-plain">
              {!plainEnglish && (
                <div className="flex items-center gap-2 text-muted text-sm">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Generating plain-English explanation…
                </div>
              )}
              {plainEnglish && (
                <div className="space-y-4">
                  <div>
                    <div className="text-2xs uppercase tracking-wide text-muted mb-1.5">
                      What the prompt instructs
                    </div>
                    <p className="text-slate-300 text-sm leading-relaxed">
                      {plainEnglish.plain_english}
                    </p>
                  </div>
                  <div>
                    <div className="text-2xs uppercase tracking-wide text-muted mb-1.5">
                      Design decisions
                    </div>
                    <p className="text-slate-300 text-sm leading-relaxed">
                      {plainEnglish.design_decisions}
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {persona && activeTab === 'session' && (
            <div data-testid="persona-tab-content-session">
              <div className="text-2xs uppercase tracking-wide text-muted mb-1.5">
                What this agent contributed
              </div>
              {plainEnglish?.this_session
                ? (
                  <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
                    {plainEnglish.this_session}
                  </p>
                ) : sessionContent
                  ? (
                    <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
                      {sessionContent}
                    </p>
                  ) : (
                    <p className="text-muted text-sm">
                      No session content available — run a council query
                      to populate this tab.
                    </p>
                  )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
