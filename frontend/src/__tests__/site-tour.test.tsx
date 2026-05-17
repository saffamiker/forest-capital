/**
 * Site tour — behaviour tests.
 *
 * react-joyride is mocked with a stub <Joyride> that records the props
 * SiteTour passes it. That lets the tests read run / stepIndex and drive
 * the onEvent handler directly, without a real Joyride DOM (Joyride v3
 * relies on portals and Floating UI, which jsdom renders unreliably).
 *
 * tourBus is left REAL: SiteTour registers its starter on mount, and the
 * "retake" / "What's New" paths exercise that registration end to end.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import type { Props, EventData } from 'react-joyride'
import { ACTIONS, EVENTS, STATUS } from 'react-joyride'
import type { ChangelogEntry, UnseenChangelogResponse } from '../types/changelog'

// vi.hoisted — the mock factory below runs hoisted, so the capture slot
// must be hoisted too.
const cap = vi.hoisted(() => ({ props: null as Props | null }))

vi.mock('react-joyride', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-joyride')>()
  return {
    ...actual,
    Joyride: (props: Props) => { cap.props = props; return null },
  }
})

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

// Imported after the mocks are declared — vitest hoists vi.mock anyway.
import SiteTour from '../components/SiteTour'
import WhatsNewModal from '../components/WhatsNewModal'
import { startTour } from '../lib/tourBus'

const TOUR_VER = 2

function unseen(over: Partial<UnseenChangelogResponse>): UnseenChangelogResponse {
  return { entries: [], has_tour_update: false, tour_version: TOUR_VER, ...over }
}

function sampleEntry(): ChangelogEntry {
  return {
    id: 1, version: 32, released_at: '2026-05-17T00:00:00Z',
    title: 'Site Tour', description: 'Guided walkthrough.',
    academic_rationale: 'Connects every feature to a grading criterion.',
    tour_step_id: 'welcome',
  }
}

/** Resolves the GET /api/v1/changelog/unseen mock to a given payload. */
function mockUnseen(payload: UnseenChangelogResponse): void {
  mockedAxios.get = vi.fn().mockResolvedValue({ data: payload })
}

/** Invokes SiteTour's onEvent handler with a minimal EventData payload. */
function fireTourEvent(fields: Partial<EventData>): void {
  const handler = cap.props?.onEvent
  if (!handler) throw new Error('Joyride received no onEvent handler')
  act(() => { handler(fields as EventData, undefined as never) })
}

beforeEach(() => {
  cap.props = null
  sessionStorage.clear()
  mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
  mockUnseen(unseen({}))
})

describe('SiteTour auto-start', () => {
  it('auto-starts when last_tour_version_seen < TOUR_VERSION', async () => {
    // has_tour_update true is exactly the < TOUR_VERSION condition, and no
    // changelog entries means no What's New modal would overlap the tour.
    mockUnseen(unseen({ has_tour_update: true, entries: [] }))
    render(<MemoryRouter><SiteTour /></MemoryRouter>)

    await waitFor(() => expect(cap.props?.run).toBe(true))
    expect(cap.props?.stepIndex).toBe(0)
  })

  it('does not auto-start when last_tour_version_seen >= TOUR_VERSION', async () => {
    // has_tour_update false — the user has already seen this tour version.
    mockUnseen(unseen({ has_tour_update: false, entries: [] }))
    render(<MemoryRouter><SiteTour /></MemoryRouter>)

    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalled())
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(cap.props?.run).toBe(false)
  })

  it('does not auto-start while the What\'s New modal would show', async () => {
    // A pending tour update, but unseen changelog entries exist — the modal
    // shows, so the tour waits and is launched from the modal instead.
    mockUnseen(unseen({ has_tour_update: true, entries: [sampleEntry()] }))
    render(<MemoryRouter><SiteTour /></MemoryRouter>)

    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalled())
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(cap.props?.run).toBe(false)
  })
})

describe('SiteTour completion and skip', () => {
  it('completion marks the tour version seen', async () => {
    mockUnseen(unseen({ has_tour_update: true, entries: [] }))
    render(<MemoryRouter><SiteTour /></MemoryRouter>)
    await waitFor(() => expect(cap.props?.run).toBe(true))

    fireTourEvent({
      type: EVENTS.TOUR_END, action: ACTIONS.NEXT, index: 14,
      status: STATUS.FINISHED, size: 15,
    })

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/v1/changelog/mark-seen', { tour_version_seen: TOUR_VER },
    )
    // A completed tour is not recorded as skipped.
    expect(sessionStorage.getItem('fc_tour_skipped')).toBeNull()
  })

  it('skip marks the tour version seen and sets the session skip flag', async () => {
    mockUnseen(unseen({ has_tour_update: true, entries: [] }))
    render(<MemoryRouter><SiteTour /></MemoryRouter>)
    await waitFor(() => expect(cap.props?.run).toBe(true))

    fireTourEvent({
      type: EVENTS.STEP_AFTER, action: ACTIONS.SKIP, index: 3,
      status: STATUS.RUNNING, size: 15,
    })

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/v1/changelog/mark-seen', { tour_version_seen: TOUR_VER },
    )
    expect(sessionStorage.getItem('fc_tour_skipped')).toBe('1')
  })
})

describe('startTour() — the Settings "Retake Site Tour" trigger', () => {
  it('force-starts the tour even when it has already been seen', async () => {
    // has_tour_update false — no auto-start. This is the mechanism the
    // Settings → Account "Retake Site Tour" button invokes on click.
    mockUnseen(unseen({ has_tour_update: false, entries: [] }))
    render(<MemoryRouter><SiteTour /></MemoryRouter>)
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    expect(cap.props?.run).toBe(false)

    act(() => { startTour() })

    expect(cap.props?.run).toBe(true)
    expect(cap.props?.stepIndex).toBe(0)
  })
})

describe('What\'s New modal — "View updated site tour" button', () => {
  it('is active when a tour update is pending and starts the tour', async () => {
    mockUnseen(unseen({ has_tour_update: true, entries: [sampleEntry()] }))
    // SiteTour is mounted alongside so the modal's startTour() reaches a
    // registered starter — the real end-to-end wiring.
    render(
      <MemoryRouter>
        <WhatsNewModal />
        <SiteTour />
      </MemoryRouter>,
    )

    const button = await screen.findByText('View updated site tour')
    expect(button).not.toBeDisabled()

    fireEvent.click(button)

    await waitFor(() => expect(cap.props?.run).toBe(true))
  })
})
