/**
 * UnverifiedPopover.tsx -- June 28 2026 (PR #479).
 *
 * Token-resolution popover for an <unverified> NodeView.
 * Opened by clicking an unverified pill in the editor.
 *
 * Layout
 *   - Header: the raw value the operator is resolving
 *   - Searchable list: every token in the live substitution
 *     table, filtered by token name OR resolved value
 *   - Actions:
 *       (a) "Replace with token" -- replaces the <unverified>
 *           node with a token_value node carrying the chosen
 *           token + its resolved value
 *       (b) "Accept as-is" -- fires the
 *           POST /api/v1/editor/drafts/{id}/accept-unverified
 *           endpoint to log the override + mutates the node
 *           attrs to accepted=true so the visual treatment
 *           shifts to muted
 *       (c) "Cancel" -- closes the popover, leaves node
 *           unchanged
 *
 * Data source
 *   /api/v1/export/data-reference-sheet -- the existing
 *   curated catalog endpoint. Returns every token with its
 *   resolved value, label, source, provenance. Loaded once on
 *   popover mount; cached in component state for the
 *   session.
 */
import { useEffect, useMemo, useState } from 'react'
import type { Editor } from '@tiptap/react'
import { Search, X, CheckCircle, AlertCircle } from 'lucide-react'
import axios from 'axios'


export interface UnverifiedPopoverProps {
  /** The raw numeric the operator is resolving. */
  value: string
  /** TipTap editor reference (passed through from NodeView). */
  editor: Editor
  /** Called when the operator picks a token replacement.
   *  The NodeView rewrites the node type to token_value
   *  carrying these attrs. */
  onReplaceWithToken: (
    token: string, resolved: string,
  ) => void
  /** Called when the operator clicks "Accept as-is". The
   *  NodeView fires the audit endpoint + mutates attrs. */
  onAcceptAsIs: () => void | Promise<void>
  /** Close without changing anything. */
  onCancel: () => void
}


interface CatalogEntry {
  token:    string
  label:    string
  value:    string
  source?:  string | undefined
}


export function UnverifiedPopover(
  props: UnverifiedPopoverProps,
): React.ReactElement {
  const [tokens, setTokens] = useState<CatalogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selectedToken, setSelectedToken] = (
    useState<CatalogEntry | null>(null))
  const [accepting, setAccepting] = useState(false)

  // Load the catalog on mount.
  useEffect(() => {
    setLoading(true)
    setError(null)
    axios.get<{
      categories: Record<string, {
        label:   string
        entries: Array<{
          token: string; label: string; value: string;
          source?: string
        }>
      }>
    }>('/api/v1/export/data-reference-sheet')
      .then((res) => {
        const all: CatalogEntry[] = []
        for (const cat of Object.values(res.data.categories || {})) {
          for (const e of (cat.entries || [])) {
            all.push({
              token:  e.token,
              label:  e.label,
              value:  e.value,
              source: e.source,
            })
          }
        }
        setTokens(all)
        // Auto-select on exact value match -- a value that's
        // ALREADY in the table just needs the operator to
        // click "Replace" to swap.
        const exact = all.find(
          (e) => e.value === props.value
              || e.value === props.value.replace(/%$/, '')
              || e.value === props.value + '%')
        if (exact) setSelectedToken(exact)
      })
      .catch((err) => {
        setError(
          err?.response?.data?.detail
          || err?.message
          || 'Failed to load token catalog.')
      })
      .finally(() => setLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const filtered = useMemo(() => {
    if (!search.trim()) return tokens
    const q = search.trim().toLowerCase()
    return tokens.filter((e) =>
      e.token.toLowerCase().includes(q)
      || (e.label || '').toLowerCase().includes(q)
      || (e.value || '').toLowerCase().includes(q))
  }, [tokens, search])

  const handleReplace = (): void => {
    if (!selectedToken) return
    props.onReplaceWithToken(
      selectedToken.token, selectedToken.value)
  }

  const handleAccept = async (): Promise<void> => {
    setAccepting(true)
    try {
      await props.onAcceptAsIs()
    } finally {
      setAccepting(false)
    }
  }

  return (
    <div
      data-testid="unverified-popover"
      className="absolute z-40 top-full left-0 mt-1 w-96
                 rounded-md border border-slate-600 bg-slate-900
                 shadow-xl p-3 text-2xs text-slate-200"
      onClick={(e: React.MouseEvent) => e.stopPropagation()}>
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="text-xs font-medium text-slate-100">
            Resolve unverified value
          </div>
          <div className="font-mono text-red-300 mt-0.5">
            {props.value}
          </div>
        </div>
        <button
          type="button"
          aria-label="Cancel"
          data-testid="unverified-popover-cancel"
          onClick={() => props.onCancel()}
          className="text-slate-400 hover:text-slate-100">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Search */}
      <div className="relative mb-2">
        <Search
          className="w-3 h-3 absolute left-2 top-1.5
                     text-slate-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search tokens by name, label, or value..."
          data-testid="unverified-popover-search"
          className="w-full pl-6 pr-2 py-1 bg-slate-800
                     border border-slate-700 rounded text-2xs
                     text-slate-100 placeholder-slate-500
                     focus:outline-none focus:border-slate-500"
        />
      </div>

      {/* Token list */}
      <div
        className="max-h-56 overflow-y-auto border border-slate-700
                   rounded bg-slate-950"
        data-testid="unverified-popover-token-list">
        {loading ? (
          <div className="px-2 py-3 text-center text-slate-500">
            Loading catalog…
          </div>
        ) : error ? (
          <div className="px-2 py-3 text-amber-400">
            <AlertCircle
              className="inline w-3 h-3 mr-1 -mt-0.5" />
            {error}
          </div>
        ) : filtered.length === 0 ? (
          <div className="px-2 py-3 text-center text-slate-500">
            No tokens match the filter.
          </div>
        ) : (
          filtered.map((e) => {
            const isSelected = (
              selectedToken?.token === e.token)
            return (
              <button
                key={e.token}
                type="button"
                data-testid={
                  `unverified-token-option-${
                    e.token.replace(/[{}]/g, '')}`}
                onClick={() => setSelectedToken(e)}
                className={(
                  'block w-full text-left px-2 py-1 border-b '
                  + 'border-slate-800 last:border-b-0 '
                  + (isSelected
                    ? 'bg-electric/20 text-slate-100'
                    : 'hover:bg-slate-800 text-slate-300'))}>
                <div className="flex items-center justify-between">
                  <span className="font-mono">{e.token}</span>
                  <span className="text-slate-100 font-medium">
                    {e.value || '—'}
                  </span>
                </div>
                {e.label ? (
                  <div className="text-slate-500 leading-snug
                                  mt-0.5">
                    {e.label}
                  </div>
                ) : null}
              </button>
            )
          })
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 mt-3">
        <button
          type="button"
          data-testid="unverified-popover-accept"
          disabled={accepting}
          onClick={() => { void handleAccept() }}
          className={(
            'px-2 py-1 rounded border text-2xs '
            + 'border-slate-600 text-slate-300 '
            + 'hover:bg-slate-800 disabled:opacity-50')}>
          {accepting ? 'Logging…' : 'Accept as-is'}
        </button>
        <button
          type="button"
          data-testid="unverified-popover-replace"
          disabled={!selectedToken}
          onClick={() => handleReplace()}
          className={(
            'px-2 py-1 rounded text-2xs font-medium '
            + 'bg-electric/30 text-slate-100 '
            + 'border border-electric/50 '
            + 'hover:bg-electric/40 disabled:opacity-30 '
            + 'disabled:cursor-not-allowed')}>
          <CheckCircle
            className="inline w-3 h-3 mr-1 -mt-0.5" />
          Replace with token
        </button>
      </div>
    </div>
  )
}
