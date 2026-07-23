import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  LineSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { BacktestEquity, Candle, PaperEquity } from '../api/types'
import { EmptyState } from './ui'

const chartOptions = {
  layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#94a3b8' },
  grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
  rightPriceScale: { borderColor: '#334155' },
  timeScale: { borderColor: '#334155', timeVisible: true },
}

function useResponsiveChart(build: (chart: IChartApi) => void, dependencies: unknown[]) {
  const reference = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!reference.current) return
    const chart = createChart(reference.current, { ...chartOptions, width: reference.current.clientWidth, height: 300 })
    build(chart)
    chart.timeScale().fitContent()
    const observer = new ResizeObserver(([entry]) => chart.applyOptions({ width: entry.contentRect.width }))
    observer.observe(reference.current)
    return () => { observer.disconnect(); chart.remove() }
    // The caller supplies stable data dependencies for chart recreation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies)
  return reference
}

const timestamp = (value: string) => Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp

export function CandlestickChart({ candles }: { candles: Candle[] }) {
  const reference = useResponsiveChart((chart) => {
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444', borderVisible: false,
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })
    series.setData(candles.map((item) => ({
      time: timestamp(item.timestamp), open: item.open, high: item.high, low: item.low, close: item.close,
    })))
  }, [candles])
  if (!candles.length) return <EmptyState title="Belum ada candle" description="Hubungkan MT5 demo lalu pilih timeframe." />
  return <div className="chart" aria-label="Candlestick chart" ref={reference} />
}
type EquityItem = PaperEquity | BacktestEquity
const pointTime = (point: EquityItem) => timestamp('captured_at' in point ? point.captured_at : point.timestamp)

export function EquityChart({ points }: { points: EquityItem[] }) {
  const reference = useResponsiveChart((chart) => {
    const series = chart.addSeries(AreaSeries, {
      lineColor: '#38bdf8', topColor: 'rgba(56,189,248,.3)', bottomColor: 'rgba(56,189,248,0)',
    })
    series.setData(points.map((point) => ({ time: pointTime(point), value: point.equity })))
  }, [points])
  if (!points.length) return <EmptyState title="Equity curve kosong" description="Snapshot equity akan tampil setelah engine menghasilkan data." />
  return <div className="chart" aria-label="Equity chart" ref={reference} />
}

export function DrawdownChart({ points }: { points: EquityItem[] }) {
  const reference = useResponsiveChart((chart) => {
    const series = chart.addSeries(LineSeries, { color: '#f97316', lineWidth: 2 })
    series.setData(points.map((point) => ({ time: pointTime(point), value: point.drawdown })))
  }, [points])
  if (!points.length) return <EmptyState title="Drawdown curve kosong" description="Belum ada snapshot drawdown." />
  return <div className="chart" aria-label="Drawdown chart" ref={reference} />
}
