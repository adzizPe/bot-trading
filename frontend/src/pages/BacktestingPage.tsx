import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FlaskConical, XCircle } from 'lucide-react'
import { api, downloadBacktestCsv, sanitizeMessage } from '../api/client'
import type { BacktestRequest, BacktestTrade } from '../api/types'
import { DrawdownChart, EquityChart } from '../components/charts'
import { ConfirmDialog, DataTable, EmptyState, ErrorAlert, MetricCard, PnLDisplay, PageHeader, Panel, StatusBadge, dateTime, money, number, useToast } from '../components/ui'

function dateOffset(days: number) {
  const value = new Date()
  value.setUTCDate(value.getUTCDate() + days)
  return value.toISOString().slice(0, 10)
}

const initialForm: BacktestRequest = {
  symbol: 'XAUUSD', start_date: dateOffset(-30), end_date: dateOffset(-1), initial_balance: 10000,
  risk_per_trade_percent: 1, maximum_open_positions: 1, spread_mode: 'FIXED', fixed_spread_points: 30,
  use_historical_spread: false, slippage_points: 0, commission_per_lot: 0, swap_long_per_lot: 0,
  swap_short_per_lot: 0, minimum_risk_reward: 1.5, trading_sessions: [],
  strategy_name: 'EMA_RSI_ATR_MTF_V1', strategy_settings: {}, risk_settings: {},
  close_open_positions_at_end: true, same_bar_policy: 'SL_FIRST', source: 'MT5',
}

export function BacktestingPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const [form, setForm] = useState(initialForm)
  const [validation, setValidation] = useState('')
  const [activeId, setActiveId] = useState('')
  const [trade, setTrade] = useState<BacktestTrade | null>(null)
  const [cancel, setCancel] = useState(false)
  const list = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, refetchInterval: 10000 })
  const selectedId = activeId || list.data?.[0]?.backtest_id || ''
  const detail = useQuery({ queryKey: ['backtest', selectedId], queryFn: () => api.backtest(selectedId), enabled: Boolean(selectedId), refetchInterval: 3000 })
  const terminal = detail.data && ['COMPLETED', 'FAILED', 'CANCELLED'].includes(detail.data.status)
  const trades = useQuery({ queryKey: ['backtest-trades', selectedId], queryFn: () => api.backtestTrades(selectedId), enabled: Boolean(selectedId && terminal) })
  const equity = useQuery({ queryKey: ['backtest-equity', selectedId], queryFn: () => api.backtestEquity(selectedId), enabled: Boolean(selectedId && terminal) })
  const report = useQuery({ queryKey: ['backtest-report', selectedId], queryFn: () => api.backtestReport(selectedId), enabled: Boolean(selectedId && detail.data?.status === 'COMPLETED'), retry: false })
  const start = useMutation({
    mutationFn: () => api.startBacktest(form),
    onSuccess: (job) => { setActiveId(job.backtest_id); queryClient.invalidateQueries({ queryKey: ['backtests'] }); notify('Backtest masuk antrean') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const cancelJob = useMutation({
    mutationFn: () => api.cancelBacktest(selectedId),
    onSuccess: (job) => { queryClient.setQueryData(['backtest', selectedId], job); setCancel(false); notify('Cancel backtest diminta') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const download = useMutation({ mutationFn: () => downloadBacktestCsv(selectedId), onSuccess: () => notify('CSV berhasil diunduh'), onError: (error) => notify(sanitizeMessage(error), 'error') })
  const stats = detail.data?.statistics
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!form.symbol.trim()) return setValidation('Symbol wajib diisi')
    if (new Date(form.start_date) >= new Date(form.end_date)) return setValidation('Start date harus sebelum end date')
    if (form.initial_balance <= 0 || form.risk_per_trade_percent <= 0) return setValidation('Balance dan risk harus lebih besar dari nol')
    if (form.source === 'CSV' && !form.csv_path) return setValidation('CSV path wajib untuk source CSV')
    setValidation('')
    start.mutate()
  }
  const active = useMemo(() => list.data?.find((item) => item.backtest_id === selectedId), [selectedId, list.data])

  return <div className="page-stack">
    <PageHeader title="Backtesting" description="Simulasi historis read-only dengan anti-look-ahead, biaya, risk parity, dan export CSV." />
    <div className="alert alert-warn">Past performance does not guarantee future results. Semua hasil adalah simulasi, bukan transaksi MT5.</div>
    <Panel title="New backtest" subtitle="Gunakan data MT5 demo read-only atau CSV server."><form className="form-grid" onSubmit={submit} noValidate>
      <label className="field">Symbol<input value={form.symbol} onChange={(event) => setForm({ ...form, symbol: event.target.value })} /></label>
      <label className="field">Start date<input aria-label="Start date" type="date" value={form.start_date} onChange={(event) => setForm({ ...form, start_date: event.target.value })} /></label>
      <label className="field">End date<input aria-label="End date" type="date" value={form.end_date} onChange={(event) => setForm({ ...form, end_date: event.target.value })} /></label>
      <label className="field">Initial balance<input type="number" min="1" value={form.initial_balance} onChange={(event) => setForm({ ...form, initial_balance: Number(event.target.value) })} /></label>
      <label className="field">Strategy<input value={form.strategy_name} onChange={(event) => setForm({ ...form, strategy_name: event.target.value })} /></label>
      <label className="field">Risk per trade (%)<input type="number" min="0.01" max="100" step="0.01" value={form.risk_per_trade_percent} onChange={(event) => setForm({ ...form, risk_per_trade_percent: Number(event.target.value) })} /></label>
      <label className="field">Maximum open positions<input type="number" min="1" value={form.maximum_open_positions} onChange={(event) => setForm({ ...form, maximum_open_positions: Number(event.target.value) })} /></label>
      <label className="field">Minimum risk/reward<input type="number" min="0.01" step="0.01" value={form.minimum_risk_reward} onChange={(event) => setForm({ ...form, minimum_risk_reward: Number(event.target.value) })} /></label>
      <label className="field">Spread mode<select value={form.spread_mode} onChange={(event) => setForm({ ...form, spread_mode: event.target.value as BacktestRequest['spread_mode'], use_historical_spread: event.target.value === 'HISTORICAL' })}><option>FIXED</option><option>HISTORICAL</option></select></label>
      <label className="field">Fixed spread (points)<input type="number" min="0" disabled={form.spread_mode === 'HISTORICAL'} value={form.fixed_spread_points} onChange={(event) => setForm({ ...form, fixed_spread_points: Number(event.target.value) })} /></label>
      <label className="field">Slippage (points)<input type="number" min="0" value={form.slippage_points} onChange={(event) => setForm({ ...form, slippage_points: Number(event.target.value) })} /></label>
      <label className="field">Commission / lot<input type="number" min="0" value={form.commission_per_lot} onChange={(event) => setForm({ ...form, commission_per_lot: Number(event.target.value) })} /></label>
      <label className="field">Source<select value={form.source} onChange={(event) => setForm({ ...form, source: event.target.value as 'MT5' | 'CSV', csv_path: undefined })}><option>MT5</option><option>CSV</option></select></label>
      {form.source === 'CSV' && <label className="field">CSV server path<input value={form.csv_path || ''} onChange={(event) => setForm({ ...form, csv_path: event.target.value })} /></label>}
      <label className="check-field"><input type="checkbox" checked={form.close_open_positions_at_end} onChange={(event) => setForm({ ...form, close_open_positions_at_end: event.target.checked })} />Close positions at end</label>
      {validation && <div className="form-error" role="alert">{validation}</div>}
      <div className="form-actions"><button className="button button-primary" disabled={start.isPending}><FlaskConical size={16} />{start.isPending ? 'Starting…' : 'Start backtest'}</button></div>
    </form></Panel>
    <Panel title="Backtest runs"><DataTable rows={list.data || []} rowKey={(item) => item.backtest_id} columns={[
      { key: 'created', header: 'Created', cell: (item) => dateTime(item.created_at) },
      { key: 'symbol', header: 'Symbol', cell: (item) => item.symbol },
      { key: 'status', header: 'Status', cell: (item) => <StatusBadge value={item.status} /> },
      { key: 'progress', header: 'Progress', cell: (item) => `${number(item.progress_percent)}%` },
      { key: 'detail', header: '', cell: (item) => <button className="button button-ghost" onClick={() => setActiveId(item.backtest_id)}>View</button> },
    ]} /></Panel>
    {selectedId && <Panel title={`Run ${selectedId.slice(0, 8)}`} subtitle={active?.strategy_name || detail.data?.strategy_name} actions={<div className="page-actions">
      {detail.data && ['PENDING', 'RUNNING'].includes(detail.data.status) && <button className="button button-danger" onClick={() => setCancel(true)}><XCircle size={16} />Cancel</button>}
      {detail.data?.status === 'COMPLETED' && <button className="button button-primary" onClick={() => download.mutate()} disabled={download.isPending}><Download size={16} />Download CSV</button>}
    </div>}>
      {detail.error && <ErrorAlert message={sanitizeMessage(detail.error)} retry={() => detail.refetch()} />}
      {detail.data && <>
        <div className="backtest-status"><StatusBadge value={detail.data.status} /><div className="progress" role="progressbar" aria-label="Backtest progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={detail.data.progress_percent}><span style={{ width: `${detail.data.progress_percent}%` }} /></div><strong>{number(detail.data.progress_percent)}%</strong></div>
        {detail.data.error_message && <ErrorAlert message={sanitizeMessage(detail.data.error_message)} />}
        {stats && <div className="metric-grid">
          <MetricCard label="Net profit" value={<PnLDisplay value={Number(stats.net_profit || 0)} />} />
          <MetricCard label="Total trades" value={number(stats.total_trades)} />
          <MetricCard label="Win rate" value={`${number(stats.win_rate)}%`} />
          <MetricCard label="Profit factor" value={number(stats.profit_factor)} />
          <MetricCard label="Expectancy" value={money(stats.expectancy)} />
          <MetricCard label="Max drawdown" value={money(stats.maximum_drawdown)} />
        </div>}
      </>}
    </Panel>}
    {detail.data?.status === 'COMPLETED' && <>
      <div className="two-column"><Panel title="Equity curve"><EquityChart points={equity.data || []} /></Panel><Panel title="Drawdown curve"><DrawdownChart points={equity.data || []} /></Panel></div>
      {report.data?.warnings.map((warning) => <div className="alert alert-warn" key={warning}>{warning}</div>)}
      <Panel title="Backtest trades"><DataTable rows={trades.data || []} rowKey={(item) => item.trade_id} columns={[
        { key: 'closed', header: 'Closed', cell: (item) => dateTime(item.closed_at) },
        { key: 'trade', header: 'Trade', cell: (item) => `${item.symbol} · ${item.direction}` },
        { key: 'volume', header: 'Volume', cell: (item) => number(item.volume) },
        { key: 'prices', header: 'Entry / Exit', cell: (item) => `${number(item.entry_price, 5)} / ${number(item.exit_price, 5)}` },
        { key: 'pnl', header: 'Net PnL', cell: (item) => <PnLDisplay value={item.net_pnl} /> },
        { key: 'reason', header: 'Exit', cell: (item) => item.exit_reason },
        { key: 'detail', header: '', cell: (item) => <button className="button button-ghost" onClick={() => setTrade(item)}>Detail</button> },
      ]} /></Panel>
    </>}
    {!selectedId && <EmptyState title="Pilih backtest" description="Mulai run baru atau pilih histori run." />}
    {trade && <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true" aria-label="Backtest trade detail"><h2>{trade.symbol} · {trade.direction}</h2><div className="key-values vertical"><span>Entry<strong>{number(trade.entry_price, 5)}</strong></span><span>Exit<strong>{number(trade.exit_price, 5)}</strong></span><span>Stop loss<strong>{number(trade.stop_loss, 5)}</strong></span><span>Take profit<strong>{number(trade.take_profit, 5)}</strong></span><span>Commission<strong>{money(trade.commission)}</strong></span><span>Swap<strong>{money(trade.swap)}</strong></span><span>Net PnL<strong><PnLDisplay value={trade.net_pnl} /></strong></span></div><button className="button button-ghost" onClick={() => setTrade(null)}>Tutup</button></div></div>}
    <ConfirmDialog open={cancel} title="Cancel backtest?" description="Job akan berhenti secara cooperative dan hasil parsial ditandai CANCELLED." destructive busy={cancelJob.isPending} onCancel={() => setCancel(false)} onConfirm={() => cancelJob.mutate()} />
  </div>
}
