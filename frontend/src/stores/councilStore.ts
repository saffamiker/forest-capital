/**
 * frontend/src/stores/councilStore.ts
 *
 * Persists the last council query and response for the session.
 * When the user navigates away from the Council tab and returns,
 * they see the previous response instead of a blank screen.
 * A new query clears the previous response and streams the new one.
 */

import { create } from 'zustand'

export interface AgentMessage {
  agent: string
  content: string
  is_final: boolean
}

interface CouncilState {
  lastQuery: string
  messages: AgentMessage[]
  streaming: boolean
  error: string | null

  setQuery: (q: string) => void
  appendMessage: (msg: AgentMessage) => void
  setStreaming: (v: boolean) => void
  setError: (e: string | null) => void
  clear: () => void
}

export const useCouncilStore = create<CouncilState>((set) => ({
  lastQuery: '',
  messages: [],
  streaming: false,
  error: null,

  setQuery: (q) => set({ lastQuery: q, messages: [], error: null }),
  appendMessage: (msg) =>
    set((s) => ({
      messages: [
        ...s.messages.filter((m) => m.agent !== msg.agent),
        msg,
      ],
    })),
  setStreaming: (v) => set({ streaming: v }),
  setError: (e) => set({ error: e, streaming: false }),
  clear: () =>
    set({ lastQuery: '', messages: [], streaming: false, error: null }),
}))
