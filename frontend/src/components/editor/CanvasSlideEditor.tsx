/**
 * CanvasSlideEditor — the centre panel for a presentation_deck draft.
 *
 * One slide at a time renders on a Konva Stage (960x540, 16:9). Text
 * elements are dragged, resized with a Transformer and inline-edited on
 * double-click via a floating textarea; chart elements show a platform
 * chart PNG and carry an amber "unverified" border until the presenter
 * confirms the chart against current data.
 *
 * The active slide is owned by the parent (DocumentEditor) so the left
 * navigator and this panel always agree on which slide is shown. Every
 * element change flows up through onChange — the parent debounces the
 * auto-save — so switching slides never loses in-flight edits.
 */
import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from 'react'
import axios from 'axios'
import {
  Stage, Layer, Rect, Text, Group, Image as KonvaImage, Transformer,
} from 'react-konva'
import type Konva from 'konva'
import type { KonvaEventObject } from 'konva/lib/Node'
import {
  Type, BarChart3, Bold, Italic, Trash2, Loader2, Sparkles, Plus,
} from 'lucide-react'

import type {
  CanvasChartElement, CanvasDeck, CanvasElement, CanvasSlide,
  CanvasTextElement,
} from '../../types/editor'
import {
  CANVAS_WIDTH, CANVAS_HEIGHT, COLOR_PRESETS, FONT_SIZES,
  konvaFontStyle, newTextElement,
} from './canvasSlide'

interface Props {
  draftId: number
  deck: CanvasDeck
  activeSlideId: number | null
  onChange: (deck: CanvasDeck) => void
  /** Opens the chart picker drawer in the editor's right panel. */
  onRequestChartPicker: () => void
}

export default function CanvasSlideEditor({
  draftId, deck, activeSlideId, onChange, onRequestChartPicker,
}: Props) {
  const slides = useMemo(() => deck.slides ?? [], [deck.slides])
  const slide: CanvasSlide | undefined =
    slides.find((s) => s.id === activeSlideId) ?? slides[0]

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [verifyPopupId, setVerifyPopupId] = useState<string | null>(null)

  const stageRef = useRef<Konva.Stage | null>(null)
  const transformerRef = useRef<Konva.Transformer | null>(null)
  const areaRef = useRef<HTMLDivElement | null>(null)
  const editTextRef = useRef<HTMLTextAreaElement | null>(null)

  // Switching slides resets every per-slide interaction. The element
  // edits themselves are already committed (onChange runs on each
  // change), so this only clears the selection/edit overlay state.
  useEffect(() => {
    setSelectedId(null)
    setEditingId(null)
    setVerifyPopupId(null)
  }, [activeSlideId])

  // Scale the 960x540 stage to fit the available panel area.
  const [scale, setScale] = useState(1)
  useLayoutEffect(() => {
    const el = areaRef.current
    if (!el) return
    const measure = () => {
      const w = el.clientWidth - 32
      const h = el.clientHeight - 32
      setScale(Math.max(0.2, Math.min(1, w / CANVAS_WIDTH, h / CANVAS_HEIGHT)))
    }
    measure()
    const obs = new ResizeObserver(measure)
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // ── deck mutation, scoped to the active slide ──────────────────────
  const updateSlide = useCallback((patch: Partial<CanvasSlide>) => {
    if (!slide) return
    onChange({
      slides: slides.map((s) => (s.id === slide.id ? { ...s, ...patch } : s)),
    })
  }, [slide, slides, onChange])

  const updateElement = useCallback(
    (elId: string, patch: Partial<CanvasElement>) => {
      if (!slide) return
      updateSlide({
        elements: slide.elements.map((e) =>
          (e.id === elId ? { ...e, ...patch } as CanvasElement : e)),
      })
    }, [slide, updateSlide])

  const deleteElement = (elId: string) => {
    if (!slide) return
    updateSlide({ elements: slide.elements.filter((e) => e.id !== elId) })
    setSelectedId(null)
    setEditingId(null)
  }

  const addTextElement = () => {
    if (!slide) return
    const el = newTextElement()
    updateSlide({ elements: [...slide.elements, el] })
    setSelectedId(el.id)
  }

  // ── Transformer attaches to the selected, unlocked, non-editing node ─
  useEffect(() => {
    const tr = transformerRef.current
    const stage = stageRef.current
    if (!tr || !stage) return
    const sel = slide?.elements.find((e) => e.id === selectedId)
    if (!sel || sel.locked || editingId) {
      tr.nodes([])
    } else {
      const node = stage.findOne('#' + selectedId)
      tr.nodes(node ? [node] : [])
    }
    tr.getLayer()?.batchDraw()
  }, [selectedId, editingId, slide])

  // Focus the inline textarea when text editing begins.
  useEffect(() => {
    if (editingId) {
      const ta = editTextRef.current
      if (ta) { ta.focus(); ta.select() }
    }
  }, [editingId])

  const onStagePointerDown = (e: KonvaEventObject<MouseEvent | TouchEvent>) => {
    const onEmpty = e.target === e.target.getStage()
      || e.target.name() === 'slide-bg'
    if (onEmpty) {
      setSelectedId(null)
      setEditingId(null)
      setVerifyPopupId(null)
    }
  }

  const onDragEnd =
    (el: CanvasElement) => (e: KonvaEventObject<DragEvent>) => {
      updateElement(el.id, { x: e.target.x(), y: e.target.y() })
    }

  const onTransformEnd =
    (el: CanvasElement) => (e: KonvaEventObject<Event>) => {
      const node = e.target
      const scaleX = node.scaleX()
      const scaleY = node.scaleY()
      node.scaleX(1)
      node.scaleY(1)
      updateElement(el.id, {
        x: node.x(),
        y: node.y(),
        width: Math.max(40, el.width * scaleX),
        height: Math.max(30, el.height * scaleY),
      })
    }

  const onChartClick = (el: CanvasChartElement) => {
    setSelectedId(el.id)
    setVerifyPopupId(el.verified ? null : el.id)
  }

  const selectedEl = slide?.elements.find((e) => e.id === selectedId) ?? null
  const editingEl = (slide?.elements.find(
    (e) => e.id === editingId && e.type === 'text') ?? null) as
    CanvasTextElement | null
  const verifyEl = (slide?.elements.find(
    (e) => e.id === verifyPopupId && e.type === 'chart') ?? null) as
    CanvasChartElement | null

  const stageW = CANVAS_WIDTH * scale
  const stageH = CANVAS_HEIGHT * scale

  if (!slide) {
    return (
      <div className="flex-1 flex items-center justify-center
                      text-sm text-muted italic" data-testid="canvas-slide-editor">
        This deck draft has no slides.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full" data-testid="canvas-slide-editor">
      {/* Toolbar */}
      <div data-testid="canvas-toolbar"
        className="flex flex-wrap items-center gap-1.5 px-3 py-2
                   border-b border-border bg-navy-900 shrink-0">
        <button type="button" onClick={addTextElement}
          className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                     border border-electric/40 text-electric
                     hover:bg-electric/10">
          <Type className="w-3.5 h-3.5" /> Text
        </button>
        <button type="button" onClick={onRequestChartPicker}
          className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                     border border-electric/40 text-electric
                     hover:bg-electric/10">
          <BarChart3 className="w-3.5 h-3.5" /> Chart
        </button>

        {selectedEl?.type === 'text' && (
          <TextFormatBar el={selectedEl}
            onPatch={(p) => updateElement(selectedEl.id, p)} />
        )}

        {selectedEl && (
          <button type="button" onClick={() => deleteElement(selectedEl.id)}
            className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                       border border-danger/40 text-danger
                       hover:bg-danger/10 ml-auto">
            <Trash2 className="w-3.5 h-3.5" /> Delete element
          </button>
        )}
      </div>

      {/* Canvas */}
      <div ref={areaRef}
        className="flex-1 min-h-0 overflow-auto flex items-center
                   justify-center bg-navy-950 p-4">
        <div className="relative shadow-lg"
          style={{ width: stageW, height: stageH }}>
          <Stage ref={stageRef} width={stageW} height={stageH}
            scaleX={scale} scaleY={scale}
            onMouseDown={onStagePointerDown}
            onTouchStart={onStagePointerDown}>
            <Layer>
              <Rect name="slide-bg" x={0} y={0}
                width={CANVAS_WIDTH} height={CANVAS_HEIGHT}
                fill={slide.background || '#FFFFFF'} />

              {slide.elements.map((el) => {
                if (el.type === 'text') {
                  return (
                    <Text key={el.id} id={el.id} name="element"
                      x={el.x} y={el.y} width={el.width} height={el.height}
                      text={el.content} fontSize={el.fontSize}
                      fontFamily="Inter, sans-serif"
                      fontStyle={konvaFontStyle(el)} fill={el.color}
                      visible={editingId !== el.id}
                      draggable={!el.locked}
                      onClick={() => setSelectedId(el.id)}
                      onTap={() => setSelectedId(el.id)}
                      onDblClick={() => {
                        setSelectedId(el.id)
                        setEditingId(el.id)
                      }}
                      onDblTap={() => {
                        setSelectedId(el.id)
                        setEditingId(el.id)
                      }}
                      onDragEnd={onDragEnd(el)}
                      onTransformEnd={onTransformEnd(el)} />
                  )
                }
                return (
                  <CanvasChartNode key={el.id} element={el}
                    draggable={!el.locked}
                    onSelect={() => onChartClick(el)}
                    onDragEnd={onDragEnd(el)}
                    onTransformEnd={onTransformEnd(el)} />
                )
              })}

              <Transformer ref={transformerRef}
                rotateEnabled={false}
                boundBoxFunc={(oldBox, newBox) =>
                  (newBox.width < 40 || newBox.height < 30 ? oldBox : newBox)} />
            </Layer>
          </Stage>

          {/* Inline text editor — a textarea over the selected element. */}
          {editingEl && (
            <textarea ref={editTextRef}
              value={editingEl.content}
              onChange={(e) =>
                updateElement(editingEl.id, { content: e.target.value })}
              onBlur={() => setEditingId(null)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setEditingId(null)
              }}
              style={{
                position: 'absolute',
                left: editingEl.x * scale,
                top: editingEl.y * scale,
                width: editingEl.width * scale,
                height: editingEl.height * scale,
                fontSize: editingEl.fontSize * scale,
                fontFamily: 'Inter, sans-serif',
                fontWeight: editingEl.fontWeight,
                fontStyle: editingEl.fontStyle === 'italic'
                  ? 'italic' : 'normal',
                color: editingEl.color,
                lineHeight: 1.2,
                padding: 0, margin: 0, border: '1px solid #3b82f6',
                background: 'rgba(255,255,255,0.92)', resize: 'none',
                outline: 'none', overflow: 'hidden',
              }} />
          )}

          {/* Verify-chart popup. */}
          {verifyEl && (
            <div role="dialog" aria-label="Verify chart"
              className="absolute z-20 w-56 card p-3 text-xs
                         border border-warning/50"
              style={{
                left: Math.min((verifyEl.x + verifyEl.width / 2) * scale,
                  stageW - 224),
                top: (verifyEl.y + verifyEl.height / 2) * scale,
              }}>
              <p className="text-slate-300 mb-2">
                Verify this chart reflects current platform data.
              </p>
              <div className="flex gap-2">
                <button type="button"
                  onClick={() => {
                    updateElement(verifyEl.id, { verified: true })
                    setVerifyPopupId(null)
                  }}
                  className="flex-1 text-2xs bg-success/15 text-success
                             border border-success/40 rounded py-1
                             hover:bg-success/25">
                  Mark as Verified
                </button>
                <button type="button" onClick={() => setVerifyPopupId(null)}
                  className="text-2xs text-muted hover:text-white px-2">
                  Later
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Speaker notes */}
      <SpeakerNotes draftId={draftId} slide={slide}
        onChange={(notes) => updateSlide({ speaker_notes: notes })} />
    </div>
  )
}

// ── Text format controls (shown when a text element is selected) ───────
function TextFormatBar({
  el, onPatch,
}: {
  el: CanvasTextElement
  onPatch: (patch: Partial<CanvasTextElement>) => void
}) {
  return (
    <div className="flex items-center gap-1.5 pl-1.5 ml-1.5
                    border-l border-border">
      <select value={el.fontSize}
        onChange={(e) => onPatch({ fontSize: Number(e.target.value) })}
        aria-label="Font size"
        className="bg-navy-800 border border-border rounded text-2xs
                   text-white px-1 py-1">
        {FONT_SIZES.map((s) => <option key={s} value={s}>{s}px</option>)}
      </select>
      <button type="button" aria-label="Bold" aria-pressed={el.fontWeight === 'bold'}
        onClick={() => onPatch({
          fontWeight: el.fontWeight === 'bold' ? 'normal' : 'bold' })}
        className={`p-1 rounded border ${el.fontWeight === 'bold'
          ? 'border-electric text-electric bg-electric/10'
          : 'border-border text-muted hover:text-white'}`}>
        <Bold className="w-3.5 h-3.5" />
      </button>
      <button type="button" aria-label="Italic"
        aria-pressed={el.fontStyle === 'italic'}
        onClick={() => onPatch({
          fontStyle: el.fontStyle === 'italic' ? 'normal' : 'italic' })}
        className={`p-1 rounded border ${el.fontStyle === 'italic'
          ? 'border-electric text-electric bg-electric/10'
          : 'border-border text-muted hover:text-white'}`}>
        <Italic className="w-3.5 h-3.5" />
      </button>
      <div className="flex items-center gap-1">
        {COLOR_PRESETS.map((c) => (
          <button key={c} type="button"
            aria-label={`Colour ${c}`}
            onClick={() => onPatch({ color: c })}
            className={`w-4 h-4 rounded border ${el.color === c
              ? 'border-electric' : 'border-border'}`}
            style={{ background: c }} />
        ))}
        <input type="text" value={el.color}
          onChange={(e) => onPatch({ color: e.target.value })}
          aria-label="Hex colour"
          className="w-16 bg-navy-800 border border-border rounded
                     text-2xs text-white px-1 py-1 font-mono" />
      </div>
    </div>
  )
}

// ── Chart element — a Konva Group with the rendered chart PNG ──────────
function CanvasChartNode({
  element, draggable, onSelect, onDragEnd, onTransformEnd,
}: {
  element: CanvasChartElement
  draggable: boolean
  onSelect: () => void
  onDragEnd: (e: KonvaEventObject<DragEvent>) => void
  onTransformEnd: (e: KonvaEventObject<Event>) => void
}) {
  const [img, setImg] = useState<HTMLImageElement | null>(null)
  const w = Math.round(element.width)
  const h = Math.round(element.height)

  useEffect(() => {
    let cancelled = false
    let url: string | null = null
    setImg(null)
    void (async () => {
      try {
        const res = await axios.get(
          `/api/v1/charts/render/${element.chartKey}`,
          { params: { width: w, height: h, theme: 'light' },
            responseType: 'blob' })
        if (cancelled) return
        url = URL.createObjectURL(res.data as Blob)
        const im = new window.Image()
        im.onload = () => { if (!cancelled) setImg(im) }
        im.src = url
      } catch { /* leave img null — the loading rect stays shown */ }
    })()
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url) }
  }, [element.chartKey, w, h])

  return (
    <Group id={element.id} name="element"
      x={element.x} y={element.y} draggable={draggable}
      onClick={onSelect} onTap={onSelect}
      onDragEnd={onDragEnd} onTransformEnd={onTransformEnd}>
      {img ? (
        <KonvaImage image={img}
          width={element.width} height={element.height} />
      ) : (
        <>
          <Rect width={element.width} height={element.height}
            fill="#f4f4f6" />
          <Text width={element.width} height={element.height}
            text="Loading chart…" fontSize={14} fill="#888"
            align="center" verticalAlign="middle" />
        </>
      )}
      {!element.verified && (
        <Rect width={element.width} height={element.height}
          stroke="#f59e0b" strokeWidth={2} listening={false} />
      )}
    </Group>
  )
}

// ── Speaker notes panel (below the canvas) ─────────────────────────────
function SpeakerNotes({
  draftId, slide, onChange,
}: {
  draftId: number
  slide: CanvasSlide
  onChange: (notes: string) => void
}) {
  const [points, setPoints] = useState<string[]>([])
  const [genLoading, setGenLoading] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)

  // The title and every text element form the slide's textual context.
  const slideText = [
    slide.title,
    ...slide.elements
      .filter((e): e is CanvasTextElement => e.type === 'text')
      .map((e) => e.content),
  ].filter(Boolean).join('\n')

  const generateTalkingPoints = async () => {
    setGenLoading(true)
    setGenError(null)
    try {
      const res = await axios.post(`/api/documents/${draftId}/assistant`, {
        message: 'Generate 4-6 concise speaker-note talking points that '
          + 'explain this slide to a non-technical audience. Do not read '
          + 'the slide verbatim — expand on it. One point per line.',
        context_content: slideText,
        context_type: 'slide',
      })
      const text: string = res.data?.suggestion || res.data?.explanation || ''
      const lines = text.split('\n')
        .map((l) => l.replace(/^[-*\d.\s]+/, '').trim())
        .filter(Boolean)
      setPoints(lines.slice(0, 6))
      if (lines.length === 0) setGenError('No talking points returned.')
    } catch {
      setGenError('Could not generate talking points.')
    } finally {
      setGenLoading(false)
    }
  }

  const insertPoint = (p: string) => {
    const notes = slide.speaker_notes.trim()
    onChange(notes ? `${notes}\n• ${p}` : `• ${p}`)
    setPoints((prev) => prev.filter((x) => x !== p))
  }

  return (
    <div className="shrink-0 border-t border-border bg-navy-900 px-3 py-2.5">
      <label className="text-2xs text-muted uppercase tracking-wide">
        Speaker notes
      </label>
      <textarea value={slide.speaker_notes}
        onChange={(e) => onChange(e.target.value)}
        rows={3} placeholder="Write your speaker notes here…"
        className="w-full bg-navy-800 border border-border rounded text-sm
                   text-white px-2 py-1.5 mt-0.5 mb-1 resize-y" />
      <button type="button" onClick={generateTalkingPoints} disabled={genLoading}
        className="text-2xs flex items-center gap-1 text-electric
                   hover:underline disabled:opacity-50">
        {genLoading
          ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Generating…</>
          : <><Sparkles className="w-3.5 h-3.5" /> Generate Talking Points</>}
      </button>
      {genError && <p className="text-2xs text-danger mt-1">{genError}</p>}
      {points.length > 0 && (
        <div className="mt-1.5 space-y-1">
          {points.map((p, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <button type="button" onClick={() => insertPoint(p)}
                aria-label="Insert talking point"
                className="text-electric hover:text-white shrink-0 mt-0.5">
                <Plus className="w-3.5 h-3.5" />
              </button>
              <span className="text-slate-300">{p}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
