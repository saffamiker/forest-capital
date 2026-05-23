/**
 * frontend/src/lib/bobBlocks.ts
 *
 * Client-side mirror of tools/report_generator._BOB_BLOCK_RE so the
 * editor can highlight callout points without a server round-trip on
 * every keystroke. Five marker kinds:
 *
 *   [BOB — description]
 *   [DATA REQUIRED — fieldname]
 *   [CITATION REQUIRED]
 *   [DATA MISMATCH live=X staged=Y]
 *   [UNVERIFIED NUMBER 99.9]
 *   [CITATION UNVERIFIED]
 *
 * Bob resolves each block via POST /resolve-bob, which sends the
 * marker text + replacement to the backend. The backend re-runs the
 * post-check; the response includes the new flag_count and bob_blocks.
 */

export type BobBlockKind =
  | 'BOB'
  | 'DATA REQUIRED'
  | 'CITATION REQUIRED'
  | 'DATA MISMATCH'
  | 'UNVERIFIED NUMBER'
  | 'CITATION UNVERIFIED'

export interface BobBlock {
  marker: string         // the full [KIND — description] string
  kind: BobBlockKind
  description: string    // body after the kind label, trimmed
  position: number       // start index in paper_md
}

const KINDS = [
  'DATA REQUIRED',
  'CITATION REQUIRED',
  'DATA MISMATCH',
  'UNVERIFIED NUMBER',
  'CITATION UNVERIFIED',
  'BOB',
] as const

// Pattern matches the longest kind first so 'CITATION UNVERIFIED'
// wins over 'CITATION REQUIRED' tokens of the same prefix.
const _BOB_BLOCK_RE = new RegExp(
  `\\[(${KINDS.join('|')})(?:[^\\[\\]]*)\\]`,
  'g'
)

const SEPARATORS = [' — ', ': ', ' - ', ' '] as const

export function extractBobBlocks(paperMd: string): BobBlock[] {
  if (!paperMd) return []
  const out: BobBlock[] = []
  // Reset regex state in case the global flag is reused across calls.
  const re = new RegExp(_BOB_BLOCK_RE.source, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(paperMd)) !== null) {
    const full = m[0]
    const kind = m[1] as BobBlockKind
    let body = full.slice(1, -1) // drop the surrounding brackets
    for (const sep of SEPARATORS) {
      if (body.startsWith(kind + sep)) {
        body = body.slice(kind.length + sep.length)
        break
      }
    }
    out.push({
      marker: full,
      kind,
      description: body.trim() || kind,
      position: m.index,
    })
  }
  return out
}

export function countBobBlocks(paperMd: string): number {
  return extractBobBlocks(paperMd).length
}

/**
 * Splits a paper_md string into a sequence of (text | block) tokens
 * so a renderer can interleave plain prose with highlighted block
 * components. The text segments preserve every character (including
 * whitespace) so reassembly is lossless.
 */
export type Token =
  | { kind: 'text'; value: string }
  | { kind: 'block'; block: BobBlock }

export function tokenize(paperMd: string): Token[] {
  const blocks = extractBobBlocks(paperMd)
  if (blocks.length === 0) {
    return paperMd ? [{ kind: 'text', value: paperMd }] : []
  }
  const tokens: Token[] = []
  let cursor = 0
  for (const block of blocks) {
    if (block.position > cursor) {
      tokens.push({
        kind: 'text',
        value: paperMd.slice(cursor, block.position),
      })
    }
    tokens.push({ kind: 'block', block })
    cursor = block.position + block.marker.length
  }
  if (cursor < paperMd.length) {
    tokens.push({ kind: 'text', value: paperMd.slice(cursor) })
  }
  return tokens
}

/**
 * Word-count traffic light per section budget (matches the backend's
 * _SECTION_BUDGETS map in template_pipeline.py).
 */
export const SECTION_BUDGETS: Record<number, number> = {
  1: 250,
  2: 300,
  3: 150,
  4: 125,
}
export const TOTAL_BUDGET = 825

export function wordCountStatus(
  words: number, budget: number,
): 'green' | 'amber' | 'red' {
  if (words > budget * 1.10) return 'red'
  if (words > budget) return 'amber'
  return 'green'
}

export function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0
}
