/**
 * frontend/src/lib/academicVerdict.ts
 *
 * Pure parsers for the Academic Review arbiter verdict text. Extracted
 * from AcademicReviewButton.tsx on May 22 2026 (Molly UAT Groups 2 + 4)
 * so the parsing rules can be tested in isolation — the verdict's text
 * shape is dictated by the arbiter prompt in
 * backend/agents/academic_review.py, and a future prompt edit MUST trip
 * a parser test rather than silently make the verdict render as plain
 * text in production.
 *
 * The verdict text emitted by the arbiter looks like:
 *
 *   **Academic rigour:** Strong
 *   **Portfolio Manager insight:** Developing
 *
 *   ### 1. Data Sufficiency and Methodology
 *   **Rating:** Strong
 *   <prose>
 *
 *   ### 2. Requirements and Rubric Alignment
 *   ...
 *
 * The OLD parseVerdict split on `^### ` and rendered EVERY split entry
 * as a section. The pre-section content (the two top-level lines) was
 * therefore treated as a malformed first section: `heading` got the
 * literal `**Academic rigour:** Strong` string (rendered as plain h4
 * text — markdown not parsed at the heading level), `rating` was null
 * (no `**Rating:**` line), and the body was the literal PM line. From
 * the user's POV: a chunk of plain markdown text at the top of the
 * verdict with no badges and no clean heading. Hence Molly's "renders
 * as plain text" report.
 *
 * The fix: parseVerdict now returns BOTH the top-level overall
 * ratings AND the rubric sections, and AcademicReviewButton renders
 * the top-level ratings as their own prominent block above the
 * sections. Section 4 (Priority Areas) is also surfaced through a
 * "top priority" callout so Molly's "where do I look first" failure
 * (Group 4) is resolved by construction.
 */

/** The arbiter's qualitative rating scale. The default rubric uses
 *  Strong / Developing / Needs Work; the script rubric substitutes
 *  Incomplete for Developing. RATING_STYLE in the component handles
 *  any unknown rating gracefully so this type stays open-ended. */
export type Rating = string

export interface OverallRatings {
  /** **Academic rigour:** value — methodology, citations, completeness. */
  academic?: Rating
  /** **Portfolio Manager insight:** value — does the document tell a PM
   *  something they did not already know. */
  pm?: Rating
}

export interface VerdictSection {
  heading: string
  rating: Rating | null
  body: string
}

export interface ParsedVerdict {
  overall: OverallRatings | null
  sections: VerdictSection[]
}

/**
 * Extracts the two top-level overall-rating lines from the pre-section
 * region of the verdict text. Returns null when neither line is present
 * (e.g. a back-compat verdict that omits the top-level lines, or an
 * incomplete stream that has not emitted them yet).
 */
export function parseOverallRatings(text: string): OverallRatings | null {
  // Case-insensitive so "Academic Rigour" and "Academic rigour" both
  // match. The label is followed by a colon, optional spaces, then the
  // rating word(s) up to end-of-line. Trailing whitespace is stripped.
  const acad = text.match(/\*\*Academic\s+Rigour:\*\*\s*([^\n]+)/i)
  const pm = text.match(/\*\*Portfolio\s+Manager\s+Insight:\*\*\s*([^\n]+)/i)
  if (!acad && !pm) return null
  const out: OverallRatings = {}
  if (acad) out.academic = acad[1].trim()
  if (pm) out.pm = pm[1].trim()
  return out
}

/**
 * Splits the arbiter markdown into structured rubric sections AND
 * the optional top-level overall ratings.
 *
 * The split key is `^### `; the first part is the pre-section region
 * (top-level ratings, if present). When the pre-section region parses
 * out a non-empty overall block, it is returned separately and NOT
 * rendered as a malformed first "section" — the old behaviour that
 * produced the plain-text appearance Molly reported.
 */
export function parseVerdict(text: string): ParsedVerdict {
  const parts = text.split(/^### /m).map((s) => s.trim())
  let overall: OverallRatings | null = null
  let sectionParts: string[]

  const first = parts[0] ?? ''
  const parsedOverall = parseOverallRatings(first)
  if (parsedOverall) {
    overall = parsedOverall
    sectionParts = parts.slice(1).filter(Boolean)
  } else {
    // No top-level lines — every non-empty part is a section. Mirrors
    // the previous behaviour for back-compat.
    sectionParts = parts.filter(Boolean)
  }

  const sections: VerdictSection[] = sectionParts.map((part) => {
    const lines = part.split('\n')
    const heading = (lines[0] ?? '').trim()
    let rating: Rating | null = null
    const body: string[] = []
    for (const ln of lines.slice(1)) {
      const m = ln.match(/^\*\*Rating:\*\*\s*(.+?)\s*$/)
      if (m && rating === null) {
        rating = m[1].trim()
        continue
      }
      body.push(ln)
    }
    return { heading, rating, body: body.join('\n').trim() }
  })

  return { overall, sections }
}

/**
 * Extracts the FIRST numbered item from the Priority Areas section
 * (rubric section 4) so the verdict can surface a "top priority"
 * callout. Returns null when section 4 is absent (incomplete stream,
 * back-compat verdict) or its body has no numbered list.
 *
 * Pulls everything from "1. " through (but not including) the next
 * "2. " marker so multi-line first items survive intact. Mirrors the
 * arbiter's instruction to produce "a numbered list, ordered by
 * impact".
 */
export function extractTopPriority(sections: VerdictSection[]): string | null {
  // The arbiter labels section 4 exactly: "4. Priority Areas for
  // Further Investigation". The regex is case-insensitive and tolerant
  // of small wording drift ("Priority Area"/"Priority Areas").
  const priority = sections.find((s) =>
    /priority\s+area/i.test(s.heading))
  if (!priority || !priority.body) return null
  // Match "1. <text>" allowing the text to wrap onto subsequent lines
  // until either "2. " (the next item) or end-of-string.
  //
  // Important: NO `m` flag. With multiline mode the `$` in the
  // lookahead matches at line ends, not just end-of-string — the
  // lazy `[\s\S]*?` then stops at the first newline and we lose
  // every continuation line. Without `m`, the `(?:^|\n)` prefix
  // anchors the start at either string-start or a newline (so the
  // section body's leading newline still hits "1." correctly), and
  // `$` only matches end-of-string in the lookahead.
  const m = priority.body.match(
    /(?:^|\n)\s*1\.\s+([\s\S]*?)(?=\n\s*2\.|$)/)
  return m ? m[1].trim() : null
}
