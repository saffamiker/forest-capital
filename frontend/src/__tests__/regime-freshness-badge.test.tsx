/**
 * regime-freshness-badge.test.tsx -- June 28 2026.
 *
 * Behavior pins for the CIO card's freshness badge:
 *   - color-coded by age (green / amber / red)
 *   - relative-time label format
 *   - absolute-UTC tooltip on hover
 *   - graceful no-render when timestamp is null/missing
 *   - re-renders on a 15-second interval (via fake timers)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, cleanup } from '@testing-library/react'

import { RegimeFreshnessBadge } from '../components/RegimeFreshnessBadge'


// Anchor "now" for deterministic relative-time math.
const NOW_MS = Date.UTC(2026, 5, 28, 23, 30, 0)  // 2026-06-28 23:30 UTC


function _isoMinutesAgo(mins: number): string {
  return new Date(NOW_MS - mins * 60_000).toISOString()
}


describe('RegimeFreshnessBadge', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW_MS)
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('renders nothing when computed_at is null', () => {
    const { container } = render(
      <RegimeFreshnessBadge computed_at={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when computed_at is missing', () => {
    const { container } = render(<RegimeFreshnessBadge />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when computed_at is unparseable', () => {
    const { container } = render(
      <RegimeFreshnessBadge computed_at="garbage" />)
    expect(container.firstChild).toBeNull()
  })

  it('uses green tone when age < 5 minutes', () => {
    render(
      <RegimeFreshnessBadge computed_at={_isoMinutesAgo(3)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.dataset.tone).toBe('green')
    expect(badge.dataset.ageMinutes).toBe('3')
    expect(badge.textContent).toContain('Updated 3 minutes ago')
  })

  it('uses amber tone when age is 5-15 minutes', () => {
    render(
      <RegimeFreshnessBadge computed_at={_isoMinutesAgo(8)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.dataset.tone).toBe('amber')
    expect(badge.textContent).toContain('Updated 8 minutes ago')
  })

  it('uses red tone when age >= 15 minutes', () => {
    render(
      <RegimeFreshnessBadge computed_at={_isoMinutesAgo(20)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.dataset.tone).toBe('red')
    expect(badge.textContent).toContain('Updated 20 minutes ago')
  })

  it('shows "just now" at zero age', () => {
    render(
      <RegimeFreshnessBadge computed_at={_isoMinutesAgo(0)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.textContent).toContain('Updated just now')
    expect(badge.dataset.tone).toBe('green')
  })

  it('uses singular "1 minute ago" at age 1', () => {
    render(
      <RegimeFreshnessBadge computed_at={_isoMinutesAgo(1)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.textContent).toContain('Updated 1 minute ago')
  })

  it('formats hours + minutes for ages >= 60 min', () => {
    render(
      <RegimeFreshnessBadge
        computed_at={_isoMinutesAgo(75)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.textContent).toContain('1 hour 15m ago')
  })

  it('handles future timestamps as "just now"', () => {
    const future = new Date(
      NOW_MS + 5 * 60_000).toISOString()
    render(<RegimeFreshnessBadge computed_at={future} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.dataset.ageMinutes).toBe('0')
    expect(badge.textContent).toContain('just now')
  })

  it('absolute UTC tooltip is on the title attribute', () => {
    render(
      <RegimeFreshnessBadge computed_at={_isoMinutesAgo(3)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.getAttribute('title')).toMatch(
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$/)
    expect(badge.getAttribute('title')).toContain(
      '2026-06-28')
    expect(badge.getAttribute('title')).toContain('UTC')
  })

  it('re-renders on the 15-second interval to update label',
     () => {
    render(
      <RegimeFreshnessBadge computed_at={_isoMinutesAgo(4)} />)
    const badge = screen.getByTestId('regime-freshness-badge')
    expect(badge.dataset.ageMinutes).toBe('4')
    expect(badge.dataset.tone).toBe('green')

    // Advance time by 90 seconds -- enough to tick past 5 min
    // boundary AND fire 6 of the 15-second intervals.
    act(() => {
      vi.advanceTimersByTime(90_000)
    })

    const badgeAfter = screen.getByTestId(
      'regime-freshness-badge')
    expect(badgeAfter.dataset.ageMinutes).toBe('5')
    expect(badgeAfter.dataset.tone).toBe('amber')
    expect(badgeAfter.textContent).toContain(
      'Updated 5 minutes ago')
  })

  it('clears the interval on unmount', () => {
    const clearSpy = vi.spyOn(window, 'clearInterval')
    const { unmount } = render(
      <RegimeFreshnessBadge
        computed_at={_isoMinutesAgo(3)} />)
    unmount()
    expect(clearSpy).toHaveBeenCalled()
  })

  it('does not start an interval when computed_at is null',
     () => {
    const setSpy = vi.spyOn(window, 'setInterval')
    render(<RegimeFreshnessBadge computed_at={null} />)
    expect(setSpy).not.toHaveBeenCalled()
  })
})
