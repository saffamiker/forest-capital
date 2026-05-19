/**
 * canvas-editor.test.tsx — the Konva canvas presentation editor.
 *
 * react-konva needs a real <canvas>; it is mocked so the editor renders
 * in jsdom (the mock components are plain divs and never forward refs,
 * so the editor's Konva-ref effects all bail safely). axios is mocked
 * for the chart-render and assistant calls.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { ReactNode } from 'react'

interface KonvaMockProps {
  children?: ReactNode
  text?: string
  onClick?: () => void
  onDblClick?: () => void
  onMouseDown?: () => void
}

vi.mock('react-konva', () => ({
  Stage: ({ children, onMouseDown }: KonvaMockProps) => (
    <div data-konva="stage" onMouseDown={onMouseDown}>{children}</div>
  ),
  Layer: ({ children }: KonvaMockProps) => (
    <div data-konva="layer">{children}</div>
  ),
  Rect: () => null,
  Text: ({ text, onClick, onDblClick }: KonvaMockProps) => (
    <div data-konva="text" onClick={onClick} onDoubleClick={onDblClick}>
      {text}
    </div>
  ),
  Group: ({ children, onClick }: KonvaMockProps) => (
    <div data-konva="group" onClick={onClick}>{children}</div>
  ),
  Image: () => null,
  Transformer: () => null,
}))

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import axios from 'axios'
import CanvasSlideEditor from '../components/editor/CanvasSlideEditor'
import ChartPicker from '../components/editor/ChartPicker'
import {
  newTextElement, newChartElement, deckToText, konvaFontStyle,
} from '../components/editor/canvasSlide'
import type { CanvasDeck } from '../types/editor'

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

function makeDeck(): CanvasDeck {
  return {
    slides: [{
      id: 1, title: 'Opening', background: '#FFFFFF', speaker_notes: '',
      elements: [{
        id: 'el_001', type: 'text', x: 60, y: 40, width: 840, height: 80,
        content: 'Opening title', fontSize: 36, fontWeight: 'bold',
        fontStyle: 'normal', color: '#1B2A4A', locked: false,
      }],
    }],
  }
}

beforeEach(() => {
  mockedAxios.get.mockReset()
  mockedAxios.post.mockReset()
  mockedAxios.get.mockResolvedValue({ data: new Blob() })
})

// ── canvasSlide helpers — pure logic ──────────────────────────────────────────

describe('canvasSlide helpers', () => {
  it('newTextElement builds a text element with brand defaults', () => {
    const el = newTextElement()
    expect(el.type).toBe('text')
    expect(el.content).toBeTruthy()
    expect(el.fontSize).toBeGreaterThan(0)
    expect(el.locked).toBe(false)
  })

  it('newChartElement carries the chart key and starts unverified', () => {
    const el = newChartElement('risk_return')
    expect(el.type).toBe('chart')
    expect(el.chartKey).toBe('risk_return')
    expect(el.verified).toBe(false)
  })

  it('konvaFontStyle combines weight and style', () => {
    expect(konvaFontStyle({ fontWeight: 'bold', fontStyle: 'italic' } as never))
      .toBe('bold italic')
    expect(konvaFontStyle({ fontWeight: 'normal' } as never)).toBe('normal')
  })

  it('deckToText projects titles and text element content', () => {
    const text = deckToText(makeDeck().slides)
    expect(text).toContain('Opening')
    expect(text).toContain('Opening title')
  })
})

// ── CanvasSlideEditor — the canvas centre panel ───────────────────────────────

describe('CanvasSlideEditor', () => {
  const noop = () => {}

  it('renders the toolbar and the active slide on a Konva stage', () => {
    render(<CanvasSlideEditor draftId={1} deck={makeDeck()} activeSlideId={1}
      onChange={noop} onRequestChartPicker={noop} />)
    expect(screen.getByRole('button', { name: 'Text' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Chart' })).toBeInTheDocument()
    // The slide's text element renders inside the (mocked) stage.
    expect(screen.getByText('Opening title')).toBeInTheDocument()
  })

  it('shows an empty-state message when the deck has no slides', () => {
    render(<CanvasSlideEditor draftId={1} deck={{ slides: [] }}
      activeSlideId={null} onChange={noop} onRequestChartPicker={noop} />)
    expect(screen.getByText(/no slides/i)).toBeInTheDocument()
  })

  it('adds a text element when [Text] is clicked', () => {
    const onChange = vi.fn()
    render(<CanvasSlideEditor draftId={1} deck={makeDeck()} activeSlideId={1}
      onChange={onChange} onRequestChartPicker={noop} />)
    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    expect(onChange).toHaveBeenCalled()
    const next = onChange.mock.calls[0][0] as CanvasDeck
    expect(next.slides[0].elements).toHaveLength(2)
  })

  it('opens the chart picker when [Chart] is clicked', () => {
    const onRequestChartPicker = vi.fn()
    render(<CanvasSlideEditor draftId={1} deck={makeDeck()} activeSlideId={1}
      onChange={noop} onRequestChartPicker={onRequestChartPicker} />)
    fireEvent.click(screen.getByRole('button', { name: 'Chart' }))
    expect(onRequestChartPicker).toHaveBeenCalledTimes(1)
  })

  it('shows AI Layout always and AI Copy only with a text element selected', () => {
    render(<CanvasSlideEditor draftId={1} deck={makeDeck()} activeSlideId={1}
      onChange={noop} onRequestChartPicker={noop} />)
    expect(screen.getByRole('button', { name: 'AI Layout' }))
      .toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'AI Copy' })).toBeNull()
    // Selecting the text element reveals AI Copy and the delete control.
    fireEvent.click(screen.getByText('Opening title'))
    expect(screen.getByRole('button', { name: 'AI Copy' }))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete element' }))
      .toBeInTheDocument()
  })

  it('edits the speaker notes through onChange', () => {
    const onChange = vi.fn()
    render(<CanvasSlideEditor draftId={1} deck={makeDeck()} activeSlideId={1}
      onChange={onChange} onRequestChartPicker={noop} />)
    fireEvent.change(
      screen.getByPlaceholderText('Write your speaker notes here…'),
      { target: { value: 'rehearsal line' } })
    const next = onChange.mock.calls[0][0] as CanvasDeck
    expect(next.slides[0].speaker_notes).toBe('rehearsal line')
  })

  it('AI Layout reviews and applies a suggested layout', async () => {
    const onChange = vi.fn()
    mockedAxios.post.mockResolvedValue({ data: { suggestion:
      '[{"id":"el_001","x":300,"y":200,"width":400,"height":120}]' } })
    render(<CanvasSlideEditor draftId={1} deck={makeDeck()} activeSlideId={1}
      onChange={onChange} onRequestChartPicker={noop} />)
    fireEvent.click(screen.getByRole('button', { name: 'AI Layout' }))
    const overlay = await screen.findByTestId('ai-suggestion-overlay')
    expect(overlay).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    const next = onChange.mock.calls.at(-1)?.[0] as CanvasDeck
    expect(next.slides[0].elements[0].x).toBe(300)
  })
})

// ── ChartPicker — the right-panel chart drawer ────────────────────────────────

describe('ChartPicker', () => {
  beforeEach(() => {
    mockedAxios.get.mockImplementation((url: string) => {
      if (url.includes('/charts/available')) {
        return Promise.resolve({ data: [{
          key: 'risk_return', label: 'Risk vs Return',
          description: 'Return against volatility.', category: 'performance',
        }] })
      }
      return Promise.resolve({ data: new Blob() })
    })
  })

  it('lists the available charts and selects one on click', async () => {
    const onSelect = vi.fn()
    render(<ChartPicker onSelect={onSelect} onClose={() => {}} />)
    const card = await screen.findByText('Risk vs Return')
    fireEvent.click(card)
    expect(onSelect).toHaveBeenCalledWith('risk_return')
  })

  it('closes when the close button is clicked', async () => {
    const onClose = vi.fn()
    render(<ChartPicker onSelect={() => {}} onClose={onClose} />)
    await screen.findByText('Risk vs Return')
    fireEvent.click(screen.getByLabelText('Close chart picker'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
