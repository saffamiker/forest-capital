import '@testing-library/jest-dom'

// recharts uses ResizeObserver which jsdom doesn't implement
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

window.ResizeObserver = ResizeObserverStub
