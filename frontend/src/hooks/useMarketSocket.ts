import { useEffect, useRef, useState } from 'react'
import { websocketUrl } from '../api/client'
import type { MarketTick } from '../api/types'

export type SocketState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected' | 'error'

export function useMarketSocket(symbol?: string) {
  const [tick, setTick] = useState<MarketTick | null>(null)
  const [status, setStatus] = useState<SocketState>('connecting')
  const attempts = useRef(0)

  useEffect(() => {
    let socket: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let disposed = false

    const connect = () => {
      if (disposed) return
      setStatus(attempts.current ? 'reconnecting' : 'connecting')
      const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
      socket = new WebSocket(websocketUrl(`/ws/market${query}`))
      socket.onopen = () => {
        attempts.current = 0
        setStatus('connected')
      }
      socket.onmessage = (event) => {
        try {
          const value = JSON.parse(String(event.data)) as MarketTick & { type?: string }
          if (value.type === 'error') {
            setStatus('error')
          } else {
            setTick(value)
          }
        } catch {
          setStatus('error')
        }
      }
      socket.onerror = () => setStatus('error')
      socket.onclose = () => {
        if (disposed) return
        attempts.current += 1
        setStatus('reconnecting')
        timer = setTimeout(connect, Math.min(1000 * 2 ** attempts.current, 15000))
      }
    }

    connect()
    return () => {
      disposed = true
      if (timer) clearTimeout(timer)
      socket?.close(1000, 'Page closed')
      setStatus('disconnected')
    }
  }, [symbol])

  return { tick, status }
}
