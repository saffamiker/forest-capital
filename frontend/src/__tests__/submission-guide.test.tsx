import { describe, it, expect } from 'vitest'

import { daysUntil, deadlineCountdown } from '../components/SubmissionGuides'

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
    const cd = deadlineCountdown('2026-06-03', 'presentation', at('2026-06-03'))
    expect(cd.tone).toBe('red')
    expect(cd.label).toBe('Presentation today')
  })

  it('reads "Deadline passed", neutral, once the deadline is past', () => {
    const cd = deadlineCountdown('2026-05-27', 'submission', at('2026-05-30'))
    expect(cd.tone).toBe('passed')
    expect(cd.label).toBe('Deadline passed')
  })
})
