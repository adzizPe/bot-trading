import { useQuery } from '@tanstack/react-query'
import { Radio, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api, sanitizeMessage } from '../api/client'
import { CandlestickChart } from '../components/charts'
import { ConnectionIndicator, ErrorAlert, LoadingSkeleton, MetricCard, PageHeader, Panel, PriceDisplay, number } from '../components/ui'
import { useMarketSocket } from '../hooks/useMarketSocket'

const defaultFrames = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']

export function MarketPage() {
  const [timeframe, setTimeframe] = useState('M5')
  const [count, setCount] = useState(200)
  const socket = useMarketSocket()
  const fallbackTick = useQuery({ queryKey: ['tick'], queryFn: () => api.tick(), refetchInterval: 30000, retry: 1 })
  const symbol = useQuery({ queryKey: ['mt5-symbol'], queryFn: api.mt5Symbol, retry: 1 })
  const frames = useQuery({ queryKey: ['timeframes'], queryFn: api.timeframes, staleTime: Infinity })
  const candles = useQuery({
    queryKey: ['candles', timeframe, count],
    queryFn: () => api.candles(timeframe, count),
    retry: 1,
  })
  const tick = socket.tick || fallbackTick.data

  return <div className="page-stack">
    <PageHeader title="Live market" description="Bid/Ask real-time melalui WebSocket dan candle closed dari MT5 demo." actions={<ConnectionIndicator status={socket.status} />} />
    <div className="market-strip large">
      <div><Radio size={18} /><span>Symbol</span><strong>{tick?.symbol || symbol.data?.name || 'XAUUSD'}</strong></div>
      <PriceDisplay label="BID" value={tick?.bid} digits={symbol.data?.digits || 3} />
      <PriceDisplay label="ASK" value={tick?.ask} digits={symbol.data?.digits || 3} />
      <div><span>Spread</span><strong>{number(tick?.spread_points)} pts</strong></div>
    </div>
    {socket.status === 'reconnecting' && <div className="alert alert-warn">WebSocket terputus. Reconnect otomatis sedang berjalan.</div>}
    <div className="metric-grid">
      <MetricCard label="Digits" value={symbol.data?.digits ?? '—'} />
      <MetricCard label="Point" value={number(symbol.data?.point, 8)} />
      <MetricCard label="Tick size / value" value={`${number(symbol.data?.trade_tick_size, 8)} / ${number(symbol.data?.trade_tick_value, 4)}`} />
      <MetricCard label="Volume min / max" value={`${number(symbol.data?.volume_min)} / ${number(symbol.data?.volume_max)}`} />
      <MetricCard label="Volume step" value={number(symbol.data?.volume_step)} />
    </div>
    <Panel title="Candlestick chart" subtitle="Hanya candle yang sudah closed." actions={<div className="toolbar">
      <label>Timeframe<select aria-label="Timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>{(frames.data?.timeframes || defaultFrames).map((frame) => <option key={frame}>{frame}</option>)}</select></label>
      <label>Jumlah candle<select aria-label="Candle count" value={count} onChange={(event) => setCount(Number(event.target.value))}>{[50, 100, 200, 500].map((value) => <option key={value}>{value}</option>)}</select></label>
      <button className="icon-button" aria-label="Refresh candles" onClick={() => candles.refetch()}><RefreshCw /></button>
    </div>}>
      {candles.isLoading ? <LoadingSkeleton rows={5} /> : candles.error ? <ErrorAlert message={sanitizeMessage(candles.error)} retry={() => candles.refetch()} /> : <CandlestickChart candles={candles.data || []} />}
    </Panel>
  </div>
}
