/**
 * report-writer.test.tsx — Commit 3 contract.
 *
 * Verifies the report writer's frontend pieces:
 *
 *   1. lib/bobBlocks parsing and tokenisation
 *   2. BobBlockBadge interactive states (closed / open / Done)
 *   3. RubricPanel renders criteria, collapses cleanly
 *   4. AcademicReviewPanel renders criterion cards + readiness badge
 *   5. PipelineSteps renders every step with its status pill
 *   6. IterationToolbar enables/disables on selection
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import {
  extractBobBlocks, countBobBlocks, tokenize,
  countWords, wordCountStatus, SECTION_BUDGETS, TOTAL_BUDGET,
} from '../lib/bobBlocks'
import BobBlockBadge from '../components/reportwriter/BobBlockBadge'
import RubricPanel from '../components/reportwriter/RubricPanel'
import AcademicReviewPanel from
  '../components/reportwriter/AcademicReviewPanel'
import PipelineSteps from
  '../components/reportwriter/PipelineSteps'
import IterationToolbar from
  '../components/reportwriter/IterationToolbar'


// ── bobBlocks utility ──────────────────────────────────────────────────────


describe('extractBobBlocks', () => {
  it('finds every marker kind', () => {
    const md =
      '## 1. Data\nThe Sharpe is [DATA REQUIRED — corr_shift] and ' +
      '[CITATION REQUIRED]. [BOB — fill this in] ' +
      '[DATA MISMATCH live=0.5 staged=0.3] ' +
      '[UNVERIFIED NUMBER 99.9] [CITATION UNVERIFIED]'
    const blocks = extractBobBlocks(md)
    expect(blocks).toHaveLength(6)
    const kinds = blocks.map((b) => b.kind).sort()
    expect(kinds).toEqual([
      'BOB', 'CITATION REQUIRED', 'CITATION UNVERIFIED',
      'DATA MISMATCH', 'DATA REQUIRED', 'UNVERIFIED NUMBER',
    ].sort())
  })

  it('strips label from description', () => {
    const blocks = extractBobBlocks('[DATA REQUIRED — corr_shift]')
    expect(blocks[0].description).toBe('corr_shift')
    expect(blocks[0].description.startsWith('DATA REQUIRED')).toBe(false)
  })

  it('returns empty for clean prose', () => {
    expect(extractBobBlocks('clean prose')).toEqual([])
    expect(countBobBlocks('')).toBe(0)
  })

  it('preserves position so the editor can locate each marker', () => {
    const md = 'aa [BOB — x] bb [BOB — y] cc'
    const blocks = extractBobBlocks(md)
    expect(blocks).toHaveLength(2)
    expect(blocks[0].position).toBe(md.indexOf('[BOB — x]'))
    expect(blocks[1].position).toBe(md.indexOf('[BOB — y]'))
  })
})


describe('tokenize', () => {
  it('interleaves text and block tokens losslessly', () => {
    const md = 'before [BOB — x] after'
    const toks = tokenize(md)
    expect(toks).toHaveLength(3)
    expect(toks[0]).toEqual({ kind: 'text', value: 'before ' })
    expect(toks[1].kind).toBe('block')
    expect(toks[2]).toEqual({ kind: 'text', value: ' after' })
  })

  it('returns a single text token for clean prose', () => {
    const toks = tokenize('clean prose')
    expect(toks).toHaveLength(1)
    expect(toks[0]).toEqual({ kind: 'text', value: 'clean prose' })
  })

  it('returns empty array for empty input', () => {
    expect(tokenize('')).toEqual([])
  })
})


describe('word count helpers', () => {
  it('counts words ignoring leading/trailing whitespace', () => {
    expect(countWords('  hello world  ')).toBe(2)
    expect(countWords('')).toBe(0)
    expect(countWords('a b c d e')).toBe(5)
  })

  it('budgets match the backend _SECTION_BUDGETS map', () => {
    expect(SECTION_BUDGETS[1]).toBe(250)
    expect(SECTION_BUDGETS[2]).toBe(300)
    expect(SECTION_BUDGETS[3]).toBe(150)
    expect(SECTION_BUDGETS[4]).toBe(125)
    expect(TOTAL_BUDGET).toBe(825)
  })

  it('green within budget, amber slightly over, red 10% over', () => {
    expect(wordCountStatus(250, 250)).toBe('green')
    expect(wordCountStatus(270, 250)).toBe('amber')
    expect(wordCountStatus(280, 250)).toBe('red')
    expect(wordCountStatus(0, 250)).toBe('green')
  })
})


// ── BobBlockBadge ──────────────────────────────────────────────────────────


describe('BobBlockBadge — non-BOB kinds (legacy collapsed pill)', () => {
  // DATA REQUIRED / CITATION REQUIRED / DATA MISMATCH / UNVERIFIED
  // NUMBER / CITATION UNVERIFIED keep the original collapsed-pill
  // behaviour — they're missing-data flags, not author drafts.
  const block = {
    marker: '[DATA REQUIRED — corr_shift]',
    kind: 'DATA REQUIRED' as const,
    description: 'corr_shift',
    position: 0,
  }

  it('renders the closed badge with kind label', () => {
    render(<BobBlockBadge block={block} onResolve={vi.fn()} />)
    expect(screen.getByTestId('bob-block-badge')).toBeInTheDocument()
    expect(screen.getByText('Missing data')).toBeInTheDocument()
  })

  it('opens the editor on click', () => {
    render(<BobBlockBadge block={block} onResolve={vi.fn()} />)
    fireEvent.click(screen.getByTestId('bob-block-badge'))
    expect(screen.getByTestId('bob-block-badge-open')).toBeInTheDocument()
    expect(screen.getByTestId('bob-block-done')).toBeInTheDocument()
  })

  it('Done blocks until text is entered', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    render(<BobBlockBadge block={block} onResolve={onResolve} />)
    fireEvent.click(screen.getByTestId('bob-block-badge'))
    fireEvent.click(screen.getByTestId('bob-block-done'))
    await waitFor(() => {
      expect(onResolve).not.toHaveBeenCalled()
    })
  })

  it('Done with text POSTs the resolve', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    render(<BobBlockBadge block={block} onResolve={onResolve} />)
    fireEvent.click(screen.getByTestId('bob-block-badge'))
    const ta = screen.getByPlaceholderText('Type your replacement text…')
    fireEvent.change(ta, { target: { value: 'My replacement' } })
    fireEvent.click(screen.getByTestId('bob-block-done'))
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        '[DATA REQUIRED — corr_shift]', 'My replacement')
    })
  })
})


// ── RubricPanel ────────────────────────────────────────────────────────────


describe('RubricPanel', () => {
  const rubric = {
    id: 1, template_id: 'midpoint_check_fna670', version: 1,
    rubric_text: 'raw text',
    criteria: [
      {
        criterion_id: 'clarity_and_rigor',
        section: 'all', description: 'Clarity description',
        weight: null,
        indicators_of_success: ['Active voice', 'No filler'],
      },
      {
        criterion_id: 'analytical_progress',
        section: 'section_2',
        description: 'Progress description',
        weight: null,
        indicators_of_success: [],
      },
    ],
    uploaded_by: 'system', source_filename: 'rubric.pdf',
    uploaded_at: '2026-05-22T00:00:00Z',
  }

  it('renders empty state when rubric is null', () => {
    render(<RubricPanel rubric={null} />)
    expect(screen.getByText(/No rubric uploaded/i)).toBeInTheDocument()
  })

  it('renders the rubric panel collapsed by default', () => {
    render(<RubricPanel rubric={rubric} />)
    expect(screen.getByTestId('rubric-panel')).toBeInTheDocument()
    expect(screen.getByText('Grading rubric')).toBeInTheDocument()
    // Criteria descriptions are inside a collapsed section; the toggle
    // text shows the version.
    expect(screen.getByText('v1')).toBeInTheDocument()
  })

  it('expands to show criteria + indicators on click', () => {
    render(<RubricPanel rubric={rubric} />)
    fireEvent.click(screen.getByText('Grading rubric'))
    expect(screen.getByText('Clarity and rigor')).toBeInTheDocument()
    expect(screen.getByText('Analytical progress')).toBeInTheDocument()
  })
})


// ── AcademicReviewPanel ────────────────────────────────────────────────────


describe('AcademicReviewPanel', () => {
  const review = {
    per_criterion: [
      { criterion_id: 'clarity_and_rigor', score: 'strong',
        evidence: 'Section 1 is clean', gap: '', suggestion: '' },
      { criterion_id: 'analytical_progress', score: 'developing',
        evidence: '', gap: 'Could go deeper on CVaR',
        suggestion: 'Add a sentence on the CVaR implication' },
      { criterion_id: 'results_quality', score: 'strong',
        evidence: 'Section 2 leads with F1', gap: '', suggestion: '' },
      { criterion_id: 'division_of_labor', score: 'needs_work',
        evidence: '', gap: 'Activity block missing',
        suggestion: 'Pull team_activity counts in Section 3' },
    ],
    data_gaps: ['Section 2 missing cvar_ratio'],
    citation_gaps: [],
    thesis_coherence: [],
    tone_violations: [],
    length_compliance: [],
    readiness: 'needs_minor_revision' as const,
    summary: 'Strong overall with minor gaps.',
  }

  it('renders nothing when review is null', () => {
    const { container } = render(<AcademicReviewPanel review={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders all four criterion cards', () => {
    render(<AcademicReviewPanel review={review} />)
    expect(screen.getByText('Clarity and rigor')).toBeInTheDocument()
    expect(screen.getByText('Analytical progress')).toBeInTheDocument()
    expect(screen.getByText('Results quality')).toBeInTheDocument()
    expect(screen.getByText('Division of labor')).toBeInTheDocument()
  })

  it('shows the readiness badge', () => {
    render(<AcademicReviewPanel review={review} />)
    expect(screen.getByTestId('academic-review-readiness')).toHaveTextContent(
      'Needs minor revision')
  })

  it('renders data gaps when present', () => {
    render(<AcademicReviewPanel review={review} />)
    expect(screen.getByText('Data gaps')).toBeInTheDocument()
    expect(screen.getByText(/cvar_ratio/i)).toBeInTheDocument()
  })

  it('hides empty flag lists', () => {
    render(<AcademicReviewPanel review={review} />)
    expect(screen.queryByText('Citation gaps')).not.toBeInTheDocument()
    expect(screen.queryByText('Tone violations')).not.toBeInTheDocument()
  })

  it('shows a loading state when loading is true', () => {
    render(<AcademicReviewPanel review={null} loading />)
    expect(screen.getByText(/Running academic review/i)).toBeInTheDocument()
  })
})


// ── PipelineSteps ──────────────────────────────────────────────────────────


describe('PipelineSteps', () => {
  it('renders each step with its number and status pill', () => {
    const steps = [
      { number: 1, label: 'Stage Findings', status: 'complete' as const },
      { number: 2, label: 'Source Citations', status: 'in_progress' as const },
      { number: 11, label: 'Download', status: 'idle' as const },
    ]
    render(<PipelineSteps steps={steps} />)
    expect(screen.getByTestId('step-1')).toBeInTheDocument()
    expect(screen.getByTestId('step-2')).toBeInTheDocument()
    expect(screen.getByTestId('step-11')).toBeInTheDocument()
    expect(screen.getByText('Stage Findings')).toBeInTheDocument()
  })

  it('renders status pill labels', () => {
    const steps = [
      { number: 1, label: 'X', status: 'complete' as const },
      { number: 2, label: 'Y', status: 'warning' as const },
      { number: 3, label: 'Z', status: 'failed' as const },
    ]
    render(<PipelineSteps steps={steps} />)
    expect(screen.getByText('Complete')).toBeInTheDocument()
    expect(screen.getByText('Warning')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })
})


// ── IterationToolbar ───────────────────────────────────────────────────────


describe('IterationToolbar', () => {
  it('shows the four action buttons', () => {
    render(
      <IterationToolbar
        selectedText="some text"
        onRun={vi.fn()}
        onAccept={vi.fn()} />,
    )
    expect(screen.getByTestId('iteration-rephrase')).toBeInTheDocument()
    expect(screen.getByTestId('iteration-tighten')).toBeInTheDocument()
    expect(screen.getByTestId('iteration-expand')).toBeInTheDocument()
    expect(screen.getByTestId('iteration-ask-the-writer')).toBeInTheDocument()
  })

  it('disables buttons when selectedText is empty', () => {
    render(
      <IterationToolbar
        selectedText=""
        onRun={vi.fn()}
        onAccept={vi.fn()} />,
    )
    expect(screen.getByTestId('iteration-rephrase')).toBeDisabled()
    expect(screen.getByText(/Select text in the editor/i)).toBeInTheDocument()
  })

  it('enables buttons when selectedText is present', () => {
    render(
      <IterationToolbar
        selectedText="some selected text"
        onRun={vi.fn()}
        onAccept={vi.fn()} />,
    )
    expect(screen.getByTestId('iteration-rephrase')).not.toBeDisabled()
  })

  it('clicking Rephrase calls onRun with the action', async () => {
    const onRun = vi.fn().mockResolvedValue({
      original: 'x', rewritten: 'y', word_delta: 0,
      new_unverified_numbers: [], new_unverified_citations: [],
    })
    render(
      <IterationToolbar
        selectedText="some text"
        onRun={onRun}
        onAccept={vi.fn()} />,
    )
    fireEvent.click(screen.getByTestId('iteration-rephrase'))
    await waitFor(() => {
      expect(onRun).toHaveBeenCalledWith('rephrase', undefined)
    })
  })

  it('proposal renders with Accept/Dismiss', async () => {
    const onAccept = vi.fn()
    const onRun = vi.fn().mockResolvedValue({
      original: 'x', rewritten: 'rewritten text', word_delta: 1,
      new_unverified_numbers: [], new_unverified_citations: [],
    })
    render(
      <IterationToolbar
        selectedText="some text"
        onRun={onRun}
        onAccept={onAccept} />,
    )
    fireEvent.click(screen.getByTestId('iteration-rephrase'))
    await waitFor(() => {
      expect(screen.getByTestId('iteration-proposal')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Accept'))
    expect(onAccept).toHaveBeenCalledWith('rewritten text')
  })

  it('proposal flags new unverified numbers as a warning', async () => {
    const onRun = vi.fn().mockResolvedValue({
      original: 'x', rewritten: 'y with 99.9',
      word_delta: 0,
      new_unverified_numbers: [99.9],
      new_unverified_citations: [],
    })
    render(
      <IterationToolbar
        selectedText="some text"
        onRun={onRun}
        onAccept={vi.fn()} />,
    )
    fireEvent.click(screen.getByTestId('iteration-rephrase'))
    await waitFor(() => {
      expect(screen.getByText(/Introduced 1 unverified number/i))
        .toBeInTheDocument()
    })
  })
})
