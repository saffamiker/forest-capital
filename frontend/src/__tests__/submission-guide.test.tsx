import { describe, it, expect } from 'vitest'

import {
  daysUntil, deadlineCountdown, compactCountdown, SUBMISSION_DEADLINES,
} from '../components/SubmissionGuides'

// A fixed "now" so the countdown maths is deterministic.
const at = (iso: string) => new Date(`${iso}T12:00:00`)

describe('daysUntil', () => {
  it('counts whole days from today to the deadline', () => {
    expect(daysUntil('2026-05-27', at('2026-05-17'))).toBe(10)
    expect(daysUntil('2026-05-27', at('2026-05-27'))).toBe(0)
    expect(daysUntil('2026-05-27', at('2026-05-28'))).toBe(-1)
  })
})

describe('deadlineCountdown', () => {
  it('is normal-toned with more than five days left', () => {
    const cd = deadlineCountdown('2026-05-27', 'submission', at('2026-05-17'))
    expect(cd.tone).toBe('normal')
    expect(cd.label).toBe('10 days until submission')
  })

  it('turns amber at five days or fewer', () => {
    expect(deadlineCountdown('2026-05-27', 'submission', at('2026-05-22')).tone)
      .toBe('amber')
    expect(deadlineCountdown('2026-05-27', 'submission', at('2026-05-24')).tone)
      .toBe('amber')
  })

  it('turns red at two days or fewer', () => {
    expect(deadlineCountdown('2026-05-27', 'submission', at('2026-05-25')).tone)
      .toBe('red')
    const oneDay = deadlineCountdown('2026-05-27', 'submission',
                                     at('2026-05-26'))
    expect(oneDay.tone).toBe('red')
    expect(oneDay.label).toBe('1 day until submission')   // singular
  })

  it('reads "today" on the deadline day', () => {
    // Molly's only deadline is the July 1st final presentation —
    // June 3rd is a cohort peer-review event, not a submission gate.
    const cd = deadlineCountdown('2026-07-01', 'presentation', at('2026-07-01'))
    expect(cd.tone).toBe('red')
    expect(cd.label).toBe('Presentation today')
  })

  it('reads "Deadline passed", neutral, once the deadline is past', () => {
    const cd = deadlineCountdown('2026-05-27', 'submission', at('2026-05-30'))
    expect(cd.tone).toBe('passed')
    expect(cd.label).toBe('Deadline passed')
  })
})

describe('compactCountdown — labelled chip rendering', () => {
  // Each chip identifies which deliverable it is counting down to.
  // PR #338 retired the midpoint sub-deliverable; only the executive
  // brief countdown variants need pinning here.
  it('prefixes the deliverable label and renders a compact day count', () => {
    const cd = compactCountdown('2026-07-01', 'Executive Brief',
                                at('2026-06-21'))
    expect(cd.tone).toBe('normal')
    expect(cd.label).toBe('Executive Brief: 10 days')
  })

  it('uses singular "day" inside the urgency window', () => {
    const cd = compactCountdown('2026-07-01', 'Executive Brief',
                                at('2026-06-30'))
    expect(cd.tone).toBe('red')
    expect(cd.label).toBe('Executive Brief: 1 day')
  })

  it('reads "<Label>: today" on the deadline day', () => {
    const cd = compactCountdown('2026-07-01', 'Executive Brief',
                                at('2026-07-01'))
    expect(cd.tone).toBe('red')
    expect(cd.label).toBe('Executive Brief: today')
  })

  it('reads "<Label>: passed" after the deadline', () => {
    const cd = compactCountdown('2026-07-01', 'Executive Brief',
                                at('2026-07-05'))
    expect(cd.tone).toBe('passed')
    expect(cd.label).toBe('Executive Brief: passed')
  })
})

describe('SUBMISSION_DEADLINES — flat per-owner schedule', () => {
  // PR #338 retired the midpoint deadline from Bob's guide; only the
  // July 1st executive brief remains. Molly's single July 1st final
  // presentation is unchanged.
  it('emits Bob’s single executive-brief deadline and Molly’s '
    + 'single final-presentation deadline', () => {
      const bob = SUBMISSION_DEADLINES.filter(
        (d) => d.ownerEmail === 'thaob@queens.edu')
      expect(bob.map((d) => d.deadline)).toEqual(['2026-07-01'])
      const molly = SUBMISSION_DEADLINES.filter(
        (d) => d.ownerEmail === 'murdockm@queens.edu')
      expect(molly).toHaveLength(1)
      expect(molly[0]!.deadline).toBe('2026-07-01')
      expect(molly[0]!.label).toBe('Final Presentation')
    })

  it('carries a label per entry so the notification body names the deliverable',
    () => {
      const bobLabels = SUBMISSION_DEADLINES
        .filter((d) => d.ownerEmail === 'thaob@queens.edu')
        .map((d) => d.label)
      expect(bobLabels).toEqual(['Executive Brief'])
    })
})
