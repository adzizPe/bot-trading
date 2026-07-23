import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { clearDemoAdminToken } from '../api/client'

afterEach(() => {
  cleanup()
  clearDemoAdminToken()
  localStorage.clear()
  vi.restoreAllMocks()
})

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverMock)
window.HTMLElement.prototype.scrollIntoView = vi.fn()

vi.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' },
  AreaSeries: {},
  CandlestickSeries: {},
  LineSeries: {},
  createChart: () => ({
    addSeries: () => ({ setData: vi.fn() }),
    timeScale: () => ({ fitContent: vi.fn() }),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  }),
}))

class WebSocketMock {
  static OPEN = 1
  readyState = WebSocketMock.OPEN
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) {
    setTimeout(() => this.onopen?.(), 0)
  }
  close() {}
}
vi.stubGlobal('WebSocket', WebSocketMock)
