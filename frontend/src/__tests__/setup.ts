import '@testing-library/jest-dom'

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
