import '@testing-library/jest-dom'
import { vi } from 'vitest'

// Konva's Node build (konva/lib/index-node.js) require()s the native
// 'canvas' package, which is not installed and not needed for jsdom
// tests. Mocking react-konva globally means the real module — and the
// `import 'konva'` inside it — never loads, so the optional 'canvas'
// peer dependency is never resolved in the test environment. Without
// this, every test file that transitively imports the canvas editor
// (via DocumentEditor / the router) fails at module-resolution time.
//
// The mock components are plain divs that forward children, text and
// the click/pointer handlers, and never forward refs — so the canvas
// editor's Konva-ref effects all bail safely. The canvas editor's own
// behaviour is covered by canvas-editor.test.tsx.
vi.mock('react-konva', async () => {
  const React = await import('react')
  interface KonvaStub {
    children?: React.ReactNode
    text?: string
    onClick?: () => void
    onDblClick?: () => void
    onMouseDown?: () => void
  }
  const box = (name: string) => (p: KonvaStub) =>
    React.createElement(
      'div',
      { 'data-konva': name, onClick: p.onClick,
        onDoubleClick: p.onDblClick, onMouseDown: p.onMouseDown },
      p.children ?? p.text ?? null)
  return {
    Stage: box('stage'), Layer: box('layer'), Group: box('group'),
    Rect: box('rect'), Text: box('text'), Image: box('image'),
    Transformer: box('transformer'),
  }
})

// recharts uses ResizeObserver which jsdom doesn't implement
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

window.ResizeObserver = ResizeObserverStub

// jsdom does not implement URL.createObjectURL / revokeObjectURL.
// Stub them so vi.spyOn(URL, '...') can replace the implementation in tests
// that exercise blob download flows (TableExportButton, ChartExportButton).
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:mock-url'
}
if (typeof URL.revokeObjectURL !== 'function') {
  URL.revokeObjectURL = () => {}
}
