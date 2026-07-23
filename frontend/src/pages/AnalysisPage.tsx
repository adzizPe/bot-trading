import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, RefreshCw } from 'lucide-react'
import { api, sanitizeMessage } from '../api/client'
import type { Indicator, Signal } from '../api/types'
import { ErrorAlert, LoadingSkeleton, MetricCard, PageHeader, Panel, ReasonList, SignalCard, StatusBadge, number, useToast } from '../components/ui'

function IndicatorCard({ title, value }: { title: string; value: Indicator }) {
  return <article className="panel indicator-card">
    <div className="panel-heading"><div><p className="eyebrow">{title}</p><h3>{value.timeframe}</h3></div><StatusBadge value={value.market_structure} /></div>
    <div className="metric-grid compact">
      <MetricCard label="EMA fast" value={number(value.ema_fast, 5)} />
      <MetricCard label="EMA slow" value={number(value.ema_slow, 5)} />
      <MetricCard label="RSI" value={number(value.rsi)} />
      <MetricCard label="ATR" value={number(value.atr, 5)} />
    </div>
    <div className="levels"><span>Support: {value.support_levels.map((item) => number(item, 5)).join(', ') || '—'}</span><span>Resistance: {value.resistance_levels.map((item) => number(item, 5)).join(', ') || '—'}</span></div>
  </article>
}

export function AnalysisPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const analysis = useQuery({ queryKey: ['analysis'], queryFn: api.analysis, retry: 1 })
  const latest = useQuery({ queryKey: ['latest-signal'], queryFn: api.latestSignal, retry: false })
  const generate = useMutation({
    mutationFn: api.generateSignal,
    onSuccess: (signal) => {
      queryClient.setQueryData(['latest-signal'], signal)
      queryClient.setQueryData<Signal[]>(['signal-history'], (current = []) => [signal, ...current.filter((item) => item.signal_id !== signal.signal_id)])
      notify(`Signal ${signal.direction} berhasil dibuat`)
    },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })

  return <div className="page-stack">
    <PageHeader title="Multi-timeframe analysis" description="EMA, RSI, ATR, structure, support/resistance, dan confirmation dari H1/M15/M5." actions={<div className="page-actions">
      <button className="button button-ghost" onClick={() => analysis.refetch()} disabled={analysis.isFetching}><RefreshCw size={16} />Refresh</button>
      <button className="button button-primary" onClick={() => generate.mutate()} disabled={generate.isPending}><Play size={16} />{generate.isPending ? 'Generating…' : 'Generate signal'}</button>
    </div>} />
    {analysis.isLoading && <LoadingSkeleton rows={4} />}
    {analysis.error && <ErrorAlert message={sanitizeMessage(analysis.error)} retry={() => analysis.refetch()} />}
    {analysis.data && <>
      <div className="analysis-banner"><div><span>Symbol</span><strong>{analysis.data.symbol}</strong></div><div><span>Candle confirmation</span><StatusBadge value={analysis.data.confirmation_candle} /></div><div><span>Cutoff</span><strong>{new Date(analysis.data.cutoff).toLocaleString('id-ID')}</strong></div></div>
      <div className="three-column">
        <IndicatorCard title="Trend" value={analysis.data.trend} />
        <IndicatorCard title="Setup" value={analysis.data.setup} />
        <IndicatorCard title="Confirmation" value={analysis.data.confirmation} />
      </div>
    </>}
    {latest.data && <SignalCard signal={latest.data} />}
    {generate.data && <Panel title="Analysis reasons"><ReasonList title="Rules passed" values={generate.data.reasons} positive /><ReasonList title="Rejected rules" values={generate.data.rejection_reasons} /></Panel>}
  </div>
}
