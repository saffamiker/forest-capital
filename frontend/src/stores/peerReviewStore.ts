/**
 * peerReviewStore — caches the two PeerReview flows across navigation.
 *
 * Item 7 (May 23 2026). The Peer Review page surfaces two
 * harness-gated agent flows that each take 30-60 seconds to
 * complete. Navigating away and back was originally going to lose
 * the result; this store keeps the last verdict + metadata so a
 * round-trip is instant.
 *
 * One slot per flow:
 *   peerReview   — Feature A: upload another team's submission
 *   defensePrep  — Feature B: auto-loaded current draft Q&A
 *
 * Both slots carry the streaming verdict text (accumulated across
 * SSE arbiter_chunk frames), the metadata frame that arrived
 * first (submission_meta / draft_meta), the loading flag, and an
 * optional error. There is intentionally no TTL — the verdict is
 * still useful to read after a session bounce; the user can fire
 * a fresh run when they want one.
 */
import { create } from 'zustand'


export interface PeerReviewSubmissionMeta {
  name: string
  char_count: number
}


export interface DefensePrepDraftMeta {
  title: string
  word_count: number
  updated_at: string | null
}


interface PeerReviewSlot {
  verdict: string
  submissionMeta: PeerReviewSubmissionMeta | null
  loading: boolean
  error: string | null
  startedAt: number | null
  completedAt: number | null
}


interface DefensePrepSlot {
  verdict: string
  draftMeta: DefensePrepDraftMeta | null
  loading: boolean
  error: string | null
  startedAt: number | null
  completedAt: number | null
}


interface PeerReviewState {
  peerReview: PeerReviewSlot
  defensePrep: DefensePrepSlot

  // Mutators — the page consumes these on every SSE frame; the
  // store doesn't drive the fetch itself so a future endpoint
  // shape change only touches the page component.
  startPeerReview: () => void
  setPeerReviewMeta: (meta: PeerReviewSubmissionMeta) => void
  appendPeerReviewChunk: (text: string) => void
  finishPeerReview: () => void
  failPeerReview: (message: string) => void

  startDefensePrep: () => void
  setDefensePrepMeta: (meta: DefensePrepDraftMeta) => void
  appendDefensePrepChunk: (text: string) => void
  finishDefensePrep: () => void
  failDefensePrep: (message: string) => void

  resetPeerReview: () => void
  resetDefensePrep: () => void
}


const _emptyPeer: PeerReviewSlot = {
  verdict: '',
  submissionMeta: null,
  loading: false,
  error: null,
  startedAt: null,
  completedAt: null,
}


const _emptyDefense: DefensePrepSlot = {
  verdict: '',
  draftMeta: null,
  loading: false,
  error: null,
  startedAt: null,
  completedAt: null,
}


export const usePeerReviewStore = create<PeerReviewState>((set) => ({
  peerReview: _emptyPeer,
  defensePrep: _emptyDefense,

  startPeerReview: () =>
    set({
      peerReview: {
        ..._emptyPeer,
        loading: true,
        startedAt: Date.now(),
      },
    }),
  setPeerReviewMeta: (meta) =>
    set((s) => ({
      peerReview: { ...s.peerReview, submissionMeta: meta },
    })),
  appendPeerReviewChunk: (text) =>
    set((s) => ({
      peerReview: { ...s.peerReview, verdict: s.peerReview.verdict + text },
    })),
  finishPeerReview: () =>
    set((s) => ({
      peerReview: {
        ...s.peerReview,
        loading: false,
        completedAt: Date.now(),
      },
    })),
  failPeerReview: (message) =>
    set((s) => ({
      peerReview: {
        ...s.peerReview,
        loading: false,
        error: message,
        completedAt: Date.now(),
      },
    })),

  startDefensePrep: () =>
    set({
      defensePrep: {
        ..._emptyDefense,
        loading: true,
        startedAt: Date.now(),
      },
    }),
  setDefensePrepMeta: (meta) =>
    set((s) => ({
      defensePrep: { ...s.defensePrep, draftMeta: meta },
    })),
  appendDefensePrepChunk: (text) =>
    set((s) => ({
      defensePrep: { ...s.defensePrep, verdict: s.defensePrep.verdict + text },
    })),
  finishDefensePrep: () =>
    set((s) => ({
      defensePrep: {
        ...s.defensePrep,
        loading: false,
        completedAt: Date.now(),
      },
    })),
  failDefensePrep: (message) =>
    set((s) => ({
      defensePrep: {
        ...s.defensePrep,
        loading: false,
        error: message,
        completedAt: Date.now(),
      },
    })),

  resetPeerReview: () => set({ peerReview: _emptyPeer }),
  resetDefensePrep: () => set({ defensePrep: _emptyDefense }),
}))
