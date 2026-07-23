import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useMarketSocket } from './useMarketSocket'

class ControlledSocket {
  static instances: ControlledSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  close = vi.fn()
  constructor(public url: string) { ControlledSocket.instances.push(this) }
}

function Harness() {
  const { status, tick } = useMarketSocket('XAUUSD')
  return <div><span>{status}</span><span>{tick?.bid}</span></div>
}

describe('market websocket', () => {
  const original = globalThis.WebSocket
  beforeEach(() => { vi.useFakeTimers(); ControlledSocket.instances = []; vi.stubGlobal('WebSocket', ControlledSocket) })
  afterEach(() => { vi.useRealTimers(); vi.stubGlobal('WebSocket', original) })

  it('renders ticks, reconnects with backoff, and closes safely on unmount', () => {
    const view = render(<Harness />)
    expect(ControlledSocket.instances).toHaveLength(1)
    act(() => ControlledSocket.instances[0].onopen?.())
    expect(screen.getByText('connected')).toBeInTheDocument()
    act(() => ControlledSocket.instances[0].onmessage?.({ data: JSON.stringify({ symbol: 'XAUUSD', bid: 3000.1, ask: 3000.2, spread_points: 10, spread_price: .1, timestamp: '2026-07-22T00:00:00Z', connection_status: 'connected' }) } as MessageEvent))
    expect(screen.getByText('3000.1')).toBeInTheDocument()
    act(() => ControlledSocket.instances[0].onclose?.())
    expect(screen.getByText('reconnecting')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(2500))
    expect(ControlledSocket.instances).toHaveLength(2)
    view.unmount()
    expect(ControlledSocket.instances[1].close).toHaveBeenCalledWith(1000, 'Page closed')
  })
})
