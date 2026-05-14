/**
 * frontend/src/__tests__/storyboard.test.tsx
 *
 * Coverage for the Sprint 6 Phase 6 storyboard surface:
 *   - storyboardStore: createDraft, updateSlide, reorderSlides, addSlide,
 *     removeSlide, autosave coordination, saveNamedVersion
 *   - GeminiAssistantPanel: mode-aware rendering, diff display, apply flow
 *
 * Auto-save assertions use vi.useFakeTimers to avoid waiting 30 real
 * seconds per test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, renderHook, screen, act, fireEvent } from '@testing-library/react'
import axios from 'axios'

import { useStoryboardStore } from '../stores/storyboardStore'
import GeminiAssistantPanel from '../components/GeminiAssistantPanel'

vi.mock('axios')
const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
}


function _stubStoryboard(n: number = 3) {
  return {
    slides: Array.from({ length: n }, (_, i) => ({
      id: `slide-${i + 1}`,
      order: i + 1,
      owner: 'Molly' as const,
      timing_mins: 1.0,
      headline: `Slide ${i + 1}`,
      key_point: '',
      chart_ref: null,
      speaker_note: '',
      live_demo: false,
      transition: '',
      ai_draft: true,
    })),
    total_timing_mins: n * 1.0,
    ai_draft: true,
  }
}


beforeEach(() => {
  useStoryboardStore.setState({
    documentId: null, storyboard: null, versions: [],
    loading: false, saving: false, lastSavedAt: null, error: null,
    selectedSlideId: null,
  })
  mockedAxios.post = vi.fn().mockResolvedValue({
    data: {
      document_id: 'doc-uuid',
      storyboard: _stubStoryboard(3),
      persistence: 'saved',
    },
  })
  mockedAxios.patch = vi.fn().mockResolvedValue({ data: { saved_at: 'now' } })
  mockedAxios.get = vi.fn().mockResolvedValue({ data: { versions: [] } })
  mockedAxios.isAxiosError = (() => false) as never
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})


describe('storyboardStore.createDraft()', () => {
  it('populates documentId + storyboard + selects first slide', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    expect(result.current.documentId).toBe('doc-uuid')
    expect(result.current.storyboard?.slides.length).toBe(3)
    expect(result.current.selectedSlideId).toBe('slide-1')
  })

  it('writes active document_id to localStorage for Reports screen', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    expect(localStorage.getItem('fc_active_storyboard_id')).toBe('doc-uuid')
  })

  it('surfaces an error message when persistence is unavailable', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: {
        document_id: null,
        storyboard: _stubStoryboard(3),
        persistence: 'unavailable',
        message: 'Database unavailable',
      },
    })
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    expect(result.current.documentId).toBe(null)
    expect(result.current.error).toBe('Database unavailable')
  })
})


describe('storyboardStore slide mutations', () => {
  it('updateSlide patches the targeted slide only and recomputes total timing', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    act(() => { result.current.updateSlide('slide-2', { timing_mins: 2.5 }) })
    const sb = result.current.storyboard!
    expect(sb.slides[1].timing_mins).toBe(2.5)
    expect(sb.slides[0].timing_mins).toBe(1.0)  // unchanged
    expect(sb.total_timing_mins).toBeCloseTo(4.5, 1)
  })

  it('reorderSlides renumbers orders to 1..N contiguous', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    act(() => { result.current.reorderSlides(['slide-3', 'slide-1', 'slide-2']) })
    const sb = result.current.storyboard!
    expect(sb.slides.map((s) => s.id)).toEqual(['slide-3', 'slide-1', 'slide-2'])
    expect(sb.slides.map((s) => s.order)).toEqual([1, 2, 3])
  })

  it('addSlide inserts after the given order and renumbers', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    act(() => { result.current.addSlide(2) })
    const sb = result.current.storyboard!
    expect(sb.slides.length).toBe(4)
    // The new slide should be at order 3 (between original 2 and 3)
    expect(sb.slides[2].headline).toBe('New slide')
    expect(sb.slides[2].order).toBe(3)
    expect(sb.slides[3].order).toBe(4)
  })

  it('removeSlide shifts following orders down', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    act(() => { result.current.removeSlide('slide-2') })
    const sb = result.current.storyboard!
    expect(sb.slides.length).toBe(2)
    expect(sb.slides.map((s) => s.id)).toEqual(['slide-1', 'slide-3'])
    expect(sb.slides.map((s) => s.order)).toEqual([1, 2])
  })

  it('removeSlide picks a new selection if the deleted slide was selected', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    act(() => { result.current.setSelectedSlide('slide-2') })
    act(() => { result.current.removeSlide('slide-2') })
    expect(result.current.selectedSlideId).toBe('slide-1')
  })
})


describe('storyboardStore auto-save', () => {
  it('debounces auto-save: one PATCH after the timer fires regardless of edit count', async () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })

    act(() => {
      result.current.updateSlide('slide-1', { headline: 'A' })
      result.current.updateSlide('slide-1', { headline: 'AB' })
      result.current.updateSlide('slide-1', { headline: 'ABC' })
    })
    // No PATCH yet — debounced
    expect(mockedAxios.patch).toHaveBeenCalledTimes(0)

    await act(async () => {
      vi.advanceTimersByTime(30_000)
      // Allow the queued microtask from the timeout to resolve
      await Promise.resolve()
    })
    expect(mockedAxios.patch).toHaveBeenCalledTimes(1)
    expect(mockedAxios.patch).toHaveBeenCalledWith(
      '/api/documents/doc-uuid/draft',
      expect.objectContaining({ content: expect.any(Object) }),
    )
  })
})


describe('storyboardStore.saveNamedVersion()', () => {
  it('posts to versions endpoint with content + name + summary', async () => {
    const { result } = renderHook(() => useStoryboardStore())
    await act(async () => { await result.current.createDraft() })
    await act(async () => {
      await result.current.saveNamedVersion('After review', 'tightened slide 5')
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/documents/doc-uuid/versions',
      expect.objectContaining({
        version_name: 'After review',
        change_summary: 'tightened slide 5',
      }),
    )
  })
})


describe('GeminiAssistantPanel', () => {
  it('renders header + textarea', () => {
    render(
      <GeminiAssistantPanel
        documentId="doc-uuid"
        contextType="slide"
        contextContent="Original content"
        onApply={() => undefined}
      />,
    )
    expect(screen.getByTestId('gemini-assistant-panel')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Ask Gemini/i)).toBeInTheDocument()
  })

  it('disables the send button when no document is loaded', () => {
    render(
      <GeminiAssistantPanel
        documentId={null}
        contextType="slide"
        contextContent="x"
        onApply={() => undefined}
      />,
    )
    const button = screen.getByLabelText(/Send to Gemini/i) as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('renders an Apply button after a successful response', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: {
        suggestion: 'New version',
        diff: { removed: ['old'], added: ['new'] },
        explanation: '',
        confidence: 0.7,
      },
    })
    render(
      <GeminiAssistantPanel
        documentId="doc-uuid"
        contextType="slide"
        contextContent="old"
        onApply={() => undefined}
      />,
    )
    const textarea = screen.getByPlaceholderText(/Ask Gemini/i)
    fireEvent.change(textarea, { target: { value: 'tighten' } })
    const sendBtn = screen.getByLabelText(/Send to Gemini/i)
    await act(async () => { fireEvent.click(sendBtn) })
    // After response lands, the Apply button is rendered
    expect(screen.getByText('Apply')).toBeInTheDocument()
  })

  it('calls onApply with the suggestion when Apply is clicked', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: {
        suggestion: 'rewritten content',
        diff: { removed: ['old'], added: ['new'] },
        explanation: '',
        confidence: 0.7,
      },
    })
    const onApply = vi.fn()
    render(
      <GeminiAssistantPanel
        documentId="doc-uuid"
        contextType="slide"
        contextContent="old"
        onApply={onApply}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText(/Ask Gemini/i), {
      target: { value: 'tighten' },
    })
    await act(async () => { fireEvent.click(screen.getByLabelText(/Send to Gemini/i)) })
    fireEvent.click(screen.getByText('Apply'))
    expect(onApply).toHaveBeenCalledWith('rewritten content')
  })

  it('renders the warning when response is out_of_scope', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: {
        suggestion: '',
        diff: { removed: [], added: [] },
        explanation: 'Off-topic request rejected by scope guard',
        confidence: 0,
        out_of_scope: true,
      },
    })
    render(
      <GeminiAssistantPanel
        documentId="doc-uuid"
        contextType="slide"
        contextContent="x"
        onApply={() => undefined}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText(/Ask Gemini/i), {
      target: { value: 'tell me a joke' },
    })
    await act(async () => { fireEvent.click(screen.getByLabelText(/Send to Gemini/i)) })
    expect(screen.getByText(/scope guard/i)).toBeInTheDocument()
  })
})
