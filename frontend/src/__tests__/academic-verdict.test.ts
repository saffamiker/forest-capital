/**
 * academic-verdict.test.ts — May 22 2026 Molly UAT Groups 2 + 4.
 *
 * Pins the verdict-parsing contract so a future arbiter prompt change
 * cannot silently reduce the verdict to a wall of plain text again.
 *
 * Group 2 root cause: the arbiter prompt was updated to emit two
 * top-level summary lines BEFORE the first `### ` section marker:
 *
 *   **Academic rigour:** Strong
 *   **Portfolio Manager insight:** Developing
 *
 *   ### 1. Data Sufficiency and Methodology
 *   ...
 *
 * The old parseVerdict split on `### ` and treated the pre-section
 * region as a malformed section[0], rendering the literal markdown
 * asterisks as a plain h4 heading with no rating badge. Hence Molly's
 * "renders as plain text" report. The new parseVerdict separates them.
 *
 * Group 4 root cause: section 4 (Priority Areas for Further
 * Investigation) was rendered after sections 1-3, so the most
 * actionable finding sat below the fold for a long verdict. The new
 * extractTopPriority pulls the first numbered item from section 4 so a
 * "Top priority" callout can be rendered above the rubric block.
 */
import { describe, it, expect } from 'vitest'
import {
  parseOverallRatings,
  parseVerdict,
  extractTopPriority,
} from '../lib/academicVerdict'

const SAMPLE_VERDICT = `\
**Academic rigour:** Strong
**Portfolio Manager insight:** Developing

### 1. Data Sufficiency and Methodology
**Rating:** Strong
The methodology section is comprehensive and well-cited.

### 2. Requirements and Rubric Alignment
**Rating:** Developing
The submission covers most rubric criteria but lacks a discussion of
threshold sensitivity.

### 3. Deliverable Quality
**Rating:** Strong
Charts are clear, prose is precise, citations are real.

### 4. Priority Areas for Further Investigation
**Rating:** Developing
1. Quantify the 2022 equity-bond correlation break with explicit
   pre/post values (approx -0.05 and +0.61) and connect it to strategy
   performance differences.
2. Strengthen the FDR result discussion — frame zero significant
   strategies as methodological rigour, not analytical failure.
3. Add a sensitivity table for the Tier 1 thresholds.

### 5. Overall Academic Readiness
**Rating:** Developing
The submission is structurally complete with strong methodology but
needs the central 2022 finding quantified before the midpoint deadline.`


describe('parseOverallRatings', () => {
  it('extracts both top-level ratings when present', () => {
    const out = parseOverallRatings(SAMPLE_VERDICT)
    expect(out).not.toBeNull()
    expect(out!.academic).toBe('Strong')
    expect(out!.pm).toBe('Developing')
  })

  it('returns null when neither line is present (back-compat path)', () => {
    expect(parseOverallRatings('### 1. Header\n**Rating:** Strong\nbody'))
      .toBeNull()
  })

  it('handles only one of the two lines present', () => {
    const out = parseOverallRatings('**Academic rigour:** Needs Work\n')
    expect(out).not.toBeNull()
    expect(out!.academic).toBe('Needs Work')
    expect(out!.pm).toBeUndefined()
  })

  it('is case-insensitive on the label', () => {
    const out = parseOverallRatings('**Academic RIGOUR:** Strong\n')
    expect(out?.academic).toBe('Strong')
  })

  it('trims trailing whitespace', () => {
    const out = parseOverallRatings('**Academic rigour:** Strong   \n')
    expect(out?.academic).toBe('Strong')
  })
})


describe('parseVerdict — overall ratings + sections', () => {
  it('returns the overall ratings and the five sections cleanly', () => {
    const { overall, sections } = parseVerdict(SAMPLE_VERDICT)
    expect(overall).not.toBeNull()
    expect(overall!.academic).toBe('Strong')
    expect(overall!.pm).toBe('Developing')
    expect(sections).toHaveLength(5)
    expect(sections[0].heading).toBe('1. Data Sufficiency and Methodology')
    expect(sections[0].rating).toBe('Strong')
    expect(sections[3].heading).toBe('4. Priority Areas for Further Investigation')
    expect(sections[3].rating).toBe('Developing')
  })

  it('does NOT render the top-level lines as a malformed first section', () => {
    // The bug: the pre-### content used to become sections[0] with
    // heading containing literal `**Academic rigour:** Strong`. With
    // the fix there is no such section — it is extracted into overall.
    const { sections } = parseVerdict(SAMPLE_VERDICT)
    for (const s of sections) {
      expect(s.heading).not.toContain('Academic rigour')
      expect(s.heading).not.toContain('Portfolio Manager')
      expect(s.heading).not.toMatch(/^\*\*/)
    }
  })

  it('back-compat — verdict without top-level lines parses as before', () => {
    const noTopLevel = `\
### 1. Section A
**Rating:** Strong
body A

### 2. Section B
**Rating:** Developing
body B`
    const { overall, sections } = parseVerdict(noTopLevel)
    expect(overall).toBeNull()
    expect(sections).toHaveLength(2)
    expect(sections[0].heading).toBe('1. Section A')
    expect(sections[1].rating).toBe('Developing')
  })

  it('renders the body as markdown content (preserves numbered lists)', () => {
    const { sections } = parseVerdict(SAMPLE_VERDICT)
    const priority = sections[3]
    expect(priority.body).toContain('1. Quantify the 2022')
    expect(priority.body).toContain('2. Strengthen the FDR')
    expect(priority.body).toContain('3. Add a sensitivity table')
  })

  it('handles an in-flight stream where only the top-level lines have arrived', () => {
    // First chunk in: just the two top-level lines, no sections yet.
    // The block must still render the overall ratings — previously the
    // verdict block gated on sections.length > 0 and showed nothing.
    const partial = '**Academic rigour:** Strong\n**Portfolio Manager insight:** Developing\n\n'
    const { overall, sections } = parseVerdict(partial)
    expect(overall).not.toBeNull()
    expect(overall!.academic).toBe('Strong')
    expect(sections).toEqual([])
  })
})


describe('extractTopPriority', () => {
  it('returns the first numbered item from the Priority Areas section', () => {
    const { sections } = parseVerdict(SAMPLE_VERDICT)
    const top = extractTopPriority(sections)
    expect(top).not.toBeNull()
    expect(top).toContain('Quantify the 2022 equity-bond correlation break')
    // Multi-line first item — the wrapped continuation must be captured.
    // The continuation contains a newline-and-indent between "strategy"
    // and "performance", so we check the individual continuation tokens
    // are present rather than a contiguous substring.
    expect(top).toContain('pre/post values')
    expect(top).toContain('performance differences.')
    // Subsequent items must NOT bleed in.
    expect(top).not.toContain('Strengthen the FDR')
    expect(top).not.toContain('Add a sensitivity table')
  })

  it('returns null when there is no Priority Areas section', () => {
    const { sections } = parseVerdict(`### 1. Foo\n**Rating:** Strong\nbody`)
    expect(extractTopPriority(sections)).toBeNull()
  })

  it('returns null when the section has no numbered list', () => {
    const text = `### 4. Priority Areas for Further Investigation
**Rating:** Strong
Nothing actionable surfaced.`
    const { sections } = parseVerdict(text)
    expect(extractTopPriority(sections)).toBeNull()
  })

  it('matches the section heading case-insensitively', () => {
    const text = `### 4. PRIORITY AREAS for further investigation
**Rating:** Strong
1. Item one.`
    const { sections } = parseVerdict(text)
    expect(extractTopPriority(sections)).toBe('Item one.')
  })
})
