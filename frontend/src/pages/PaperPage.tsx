import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Octagon, Pause, Play, Square } from 'lucide-react'
import { useState } from 'react'
import { api, sanitizeMessage } from '../api/client'
import type { PaperPosition, TradePlan } from '../api/types'
import { DrawdownChart, EquityChart } from '../components/charts'
import { ConfirmDialog, DataTable, EmptyState, MetricCard, PnLDisplay, PageHeader, Panel, StatusBadge, dateTime, money, number, useToast } from '../components/ui'

type ConfirmAction = 'stop' | 'emergency-stop' | 'close' | 'open' | null

export function PaperPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const [confirm, setConfirm] = useState<ConfirmAction>(null)
  const [position, setPosition] = useState<PaperPosition | null>(null)
  const [planId, setPlanId] = useState('')
  const interval = 10000
  const status = useQuery({ queryKey: ['paper-status'], queryFn: api.paperStatus, refetchInterval: interval })
  const account = useQuery({ queryKey: ['paper-account'], queryFn: api.paperAccount, refetchInterval: interval })
  const statistics = useQuery({ queryKey: ['paper-statistics'], queryFn: api.paperStatistics, refetchInterval: interval })
  const positions = useQuery({ queryKey: ['paper-positions'], queryFn: api.paperPositions, refetchInterval: interval })
  const trades = useQuery({ queryKey: ['paper-trades'], queryFn: api.paperTrades, refetchInterval: interval })
  const equity = useQuery({ queryKey: ['paper-equity'], queryFn: api.paperEquity, refetchInterval: interval })
  const plans = useQuery({ queryKey: ['trade-plans'], queryFn: api.tradePlans })
  const refresh = () => queryClient.invalidateQueries({ predicate: (query) => String(query.queryKey[0]).startsWith('paper-') })
  const action = useMutation({
    mutationFn: (value: 'start' | 'pause' | 'stop' | 'emergency-stop') => api.paperAction(value),
    onSuccess: (data) => { queryClient.setQueryData(['paper-status'], data); setConfirm(null); refresh(); notify(`Paper engine: ${data.status}`) },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const open = useMutation({
    mutationFn: () => api.openPaperPosition(planId),
    onSuccess: () => { setConfirm(null); setPlanId(''); refresh(); notify('Paper position dibuka') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const close = useMutation({
    mutationFn: () => api.closePaperPosition(position!.position_id),
    onSuccess: () => { setConfirm(null); setPosition(null); refresh(); notify('Paper position ditutup') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const approved = plans.data?.filter((plan) => plan.status === 'APPROVED') || []

  return <div className="page-stack">
    <PageHeader title="Paper trading" description="Lifecycle posisi simulasi dengan quote MT5 demo read-only." actions={<div className="engine-actions">
      <button className="button button-success" disabled={action.isPending || status.data?.status === 'RUNNING'} onClick={() => action.mutate('start')}><Play size={16} />Start</button>
      <button className="button button-ghost" disabled={action.isPending || status.data?.status !== 'RUNNING'} onClick={() => action.mutate('pause')}><Pause size={16} />Pause</button>
      <button className="button button-ghost" disabled={action.isPending} onClick={() => setConfirm('stop')}><Square size={16} />Stop</button>
      <button className="button button-danger" disabled={action.isPending} onClick={() => setConfirm('emergency-stop')}><Octagon size={16} />Emergency stop</button>
    </div>} />
    <div className="metric-grid">
      <MetricCard label="Engine" value={<StatusBadge value={status.data?.status || 'unknown'} />} />
      <MetricCard label="Balance" value={money(account.data?.balance)} />
      <MetricCard label="Equity" value={money(account.data?.equity)} />
      <MetricCard label="Floating PnL" value={account.data ? <PnLDisplay value={account.data.floating_profit_loss} /> : '—'} />
      <MetricCard label="Realized PnL" value={account.data ? <PnLDisplay value={account.data.realized_profit_loss} /> : '—'} />
      <MetricCard label="Win rate" value={`${number(statistics.data?.win_rate)}%`} />
      <MetricCard label="Profit factor" value={number(statistics.data?.profit_factor)} />
      <MetricCard label="Max drawdown" value={money(statistics.data?.maximum_drawdown)} />
    </div>
    <Panel title="Open approved paper plan" subtitle="Hanya membuat posisi paper; tidak ada broker order."><div className="inline-form"><select aria-label="Approved trade plan" value={planId} onChange={(event) => setPlanId(event.target.value)}><option value="">Pilih trade plan</option>{approved.map((plan: TradePlan) => <option key={plan.trade_plan_id} value={plan.trade_plan_id}>{plan.symbol} · {plan.direction} · {plan.trade_plan_id.slice(0, 8)}</option>)}</select><button className="button button-primary" disabled={!planId || open.isPending} onClick={() => setConfirm('open')}>Open paper position</button></div></Panel>
    <div className="two-column"><Panel title="Equity curve"><EquityChart points={equity.data || []} /></Panel><Panel title="Drawdown curve"><DrawdownChart points={equity.data || []} /></Panel></div>
    <Panel title="Open positions" subtitle="Harga dan floating PnL diperbarui oleh paper scheduler.">
      {positions.data?.filter((item) => item.status === 'OPEN').length ? <DataTable rows={positions.data.filter((item) => item.status === 'OPEN')} rowKey={(item) => item.position_id} columns={[
        { key: 'symbol', header: 'Symbol', cell: (item) => `${item.symbol} · ${item.direction}` },
        { key: 'volume', header: 'Volume', cell: (item) => number(item.volume) },
        { key: 'entry', header: 'Entry / Current', cell: (item) => `${number(item.entry_price, 5)} / ${number(item.current_price, 5)}` },
        { key: 'stops', header: 'SL / TP', cell: (item) => `${number(item.stop_loss, 5)} / ${number(item.take_profit, 5)}` },
        { key: 'pnl', header: 'Floating', cell: (item) => <PnLDisplay value={item.floating_profit_loss} /> },
        { key: 'action', header: '', cell: (item) => <button className="button button-ghost" onClick={() => { setPosition(item); setConfirm('close') }}>Close</button> },
      ]} /> : <EmptyState title="Tidak ada posisi terbuka" description="Pilih APPROVED plan untuk membuka paper position." />}
    </Panel>
    <Panel title="Closed trades"><DataTable rows={trades.data || []} rowKey={(item) => item.trade_id} columns={[
      { key: 'closed', header: 'Closed', cell: (item) => dateTime(item.closed_at) },
      { key: 'symbol', header: 'Trade', cell: (item) => `${item.symbol} · ${item.direction}` },
      { key: 'volume', header: 'Volume', cell: (item) => number(item.volume) },
      { key: 'pnl', header: 'Net PnL', cell: (item) => <PnLDisplay value={item.net_profit_loss} /> },
      { key: 'reason', header: 'Reason', cell: (item) => item.close_reason },
    ]} /></Panel>
    <ConfirmDialog open={confirm === 'stop'} title="Stop paper engine?" description="Scheduler paper akan dihentikan. Perilaku posisi mengikuti paper settings backend." busy={action.isPending} onCancel={() => setConfirm(null)} onConfirm={() => action.mutate('stop')} />
    <ConfirmDialog open={confirm === 'emergency-stop'} title="Emergency stop paper engine?" description="Action kritis ini dapat menutup seluruh posisi paper sesuai konfigurasi. Tidak ada order broker." destructive confirmationText="EMERGENCY STOP" busy={action.isPending} onCancel={() => setConfirm(null)} onConfirm={() => action.mutate('emergency-stop')} />
    <ConfirmDialog open={confirm === 'close'} title="Close paper position?" description="Posisi akan ditutup dengan quote simulasi terkini." busy={close.isPending} onCancel={() => { setConfirm(null); setPosition(null) }} onConfirm={() => close.mutate()} />
    <ConfirmDialog open={confirm === 'open'} title="Open paper position?" description="Posisi hanya dibuat pada ledger paper trading." busy={open.isPending} onCancel={() => setConfirm(null)} onConfirm={() => open.mutate()} />
  </div>
}
