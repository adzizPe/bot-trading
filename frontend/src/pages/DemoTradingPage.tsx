import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Octagon, Pause, Play, RefreshCw, Square } from 'lucide-react'
import { useState } from 'react'
import {
  api,
  clearDemoAdminToken,
  hasDemoAdminToken,
  sanitizeMessage,
  setDemoAdminToken,
} from '../api/client'
import type { DemoPosition } from '../api/types'
import {
  ConfirmDialog,
  DataTable,
  EmptyState,
  MetricCard,
  PageHeader,
  Panel,
  PnLDisplay,
  StatusBadge,
  dateTime,
  number,
  useToast,
} from '../components/ui'

type ConfirmAction = 'stop' | 'emergency' | 'close' | 'break-even' | null

export function DemoTradingPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const [token, setToken] = useState('')
  const [authenticated, setAuthenticated] = useState(hasDemoAdminToken())
  const [confirm, setConfirm] = useState<ConfirmAction>(null)
  const [selectedPosition, setSelectedPosition] = useState<DemoPosition | null>(null)
  const interval = 10000
  const queryOptions = { enabled: authenticated, retry: false, refetchInterval: interval }
  const status = useQuery({ queryKey: ['demo-status'], queryFn: api.demoStatus, ...queryOptions })
  const executions = useQuery({ queryKey: ['demo-executions'], queryFn: api.demoExecutions, ...queryOptions })
  const orders = useQuery({ queryKey: ['demo-orders'], queryFn: api.demoOrders, ...queryOptions })
  const positions = useQuery({ queryKey: ['demo-positions'], queryFn: api.demoPositions, ...queryOptions })
  const deals = useQuery({ queryKey: ['demo-deals'], queryFn: api.demoDeals, ...queryOptions })
  const refresh = () => queryClient.invalidateQueries({ predicate: (query) => String(query.queryKey[0]).startsWith('demo-') })
  const activate = () => {
    if (token.trim().length < 16) { notify('Admin token minimal 16 karakter', 'error'); return }
    setDemoAdminToken(token)
    setToken('')
    setAuthenticated(true)
    notify('Sesi admin aktif hanya di memori tab ini')
  }
  const deactivate = () => {
    clearDemoAdminToken()
    setAuthenticated(false)
    queryClient.removeQueries({ predicate: (query) => String(query.queryKey[0]).startsWith('demo-') })
    notify('Sesi admin dihapus dari memori')
  }
  const action = useMutation({
    mutationFn: (value: 'start' | 'pause' | 'stop') => api.demoAction(value),
    onSuccess: (engine) => { setConfirm(null); refresh(); notify(`Demo engine: ${engine.status}`) },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const reconcile = useMutation({
    mutationFn: api.reconcileDemo,
    onSuccess: (run) => { refresh(); notify(`Reconciliation ${run.status}`) },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const emergency = useMutation({
    mutationFn: api.demoEmergencyStop,
    onSuccess: () => { setConfirm(null); refresh(); notify('Demo engine EMERGENCY_STOPPED') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const close = useMutation({
    mutationFn: () => api.closeDemoPosition(selectedPosition!.position_id),
    onSuccess: (result) => { setConfirm(null); setSelectedPosition(null); refresh(); notify(`Close result: ${result.outcome}`) },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const breakEven = useMutation({
    mutationFn: () => api.breakEvenDemoPosition(selectedPosition!.position_id),
    onSuccess: (result) => { setConfirm(null); setSelectedPosition(null); refresh(); notify(`Break-even: ${result.outcome}`) },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })

  const choosePosition = (position: DemoPosition, actionName: ConfirmAction) => {
    setSelectedPosition(position)
    setConfirm(actionName)
  }

  return <div className="page-stack">
    <PageHeader title="Demo trading" description="Eksekusi manual XAU/USD pada akun MT5 demo terverifikasi. Auto trading tetap dinonaktifkan." actions={authenticated && <div className="engine-actions">
      <button className="button button-success" disabled={action.isPending || status.data?.engine.status === 'RUNNING'} onClick={() => action.mutate('start')}><Play size={16} />Start</button>
      <button className="button button-ghost" disabled={action.isPending || status.data?.engine.status !== 'RUNNING'} onClick={() => action.mutate('pause')}><Pause size={16} />Pause</button>
      <button className="button button-ghost" disabled={action.isPending} onClick={() => setConfirm('stop')}><Square size={16} />Stop</button>
      <button className="button button-ghost" disabled={reconcile.isPending} onClick={() => reconcile.mutate()}><RefreshCw size={16} />Reconcile</button>
      <button className="button button-danger" disabled={emergency.isPending} onClick={() => setConfirm('emergency')}><Octagon size={16} />Emergency stop</button>
    </div>} />
    <div className="alert alert-warn" role="note"><strong>DEMO ACCOUNT ONLY</strong> · Real and unsupported contest accounts are blocked by the backend immediately before order_check and order_send.</div>
    <Panel title="Temporary admin session" subtitle="Token disimpan hanya di memori JavaScript dan hilang saat tab dimuat ulang. Token tidak pernah masuk localStorage atau sessionStorage.">
      <div className="inline-form">
        <label className="field">Demo admin token<input aria-label="Demo admin token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label>
        <button className="button button-primary" disabled={!token || authenticated} onClick={activate}><KeyRound size={16} />Aktifkan sesi admin</button>
        <button className="button button-ghost" disabled={!authenticated} onClick={deactivate}>Hapus sesi</button>
      </div>
    </Panel>
    {!authenticated ? <EmptyState title="Admin session belum aktif" description="Masukkan token runtime untuk mengakses seluruh endpoint demo trading." /> : <>
      <div className="metric-grid">
        <MetricCard label="Engine" value={<StatusBadge value={status.data?.engine.status || 'unknown'} />} />
        <MetricCard label="Mode" value="MANUAL_DEMO" />
        <MetricCard label="Demo verified" value={<StatusBadge value={status.data?.broker.demo_verified ? 'verified' : 'blocked'} />} />
        <MetricCard label="Active positions" value={positions.data?.filter((item) => item.status === 'OPEN').length ?? '—'} />
        <MetricCard label="Executions" value={executions.data?.length ?? '—'} />
      </div>
      {status.error && <div className="alert alert-error" role="alert">{sanitizeMessage(status.error)}</div>}
      <Panel title="Execution history" subtitle="Request dan response telah disanitasi oleh backend; credential tidak ditampilkan.">
        <DataTable rows={executions.data || []} rowKey={(item) => item.execution_request_id} columns={[
          { key: 'time', header: 'Executed', cell: (item) => dateTime(item.executed_at || item.created_at) },
          { key: 'plan', header: 'Plan', cell: (item) => item.trade_plan_id.slice(0, 12) },
          { key: 'trade', header: 'Trade', cell: (item) => `${item.symbol} · ${item.direction} · ${number(item.requested_volume)}` },
          { key: 'status', header: 'Status', cell: (item) => <StatusBadge value={item.status} /> },
          { key: 'retcode', header: 'Retcode', cell: (item) => `${item.retcode ?? '—'} · ${item.retcode_message || '—'}` },
          { key: 'ticket', header: 'Order / Deal / Position', cell: (item) => `${item.actual_order_ticket ?? '—'} / ${item.actual_deal_ticket ?? '—'} / ${item.actual_position_ticket ?? '—'}` },
        ]} />
      </Panel>
      <Panel title="Active orders">
        <DataTable rows={orders.data || []} rowKey={(item) => item.order_id} columns={[
          { key: 'time', header: 'Created', cell: (item) => dateTime(item.created_at) },
          { key: 'trade', header: 'Trade', cell: (item) => `${item.symbol} · ${item.direction}` },
          { key: 'volume', header: 'Volume', cell: (item) => number(item.volume) },
          { key: 'price', header: 'Request / Fill', cell: (item) => `${number(item.requested_price, 5)} / ${number(item.fill_price, 5)}` },
          { key: 'status', header: 'Status', cell: (item) => <StatusBadge value={item.status} /> },
          { key: 'ticket', header: 'Ticket', cell: (item) => item.broker_order_ticket ?? '—' },
        ]} />
      </Panel>
      <Panel title="Active positions" subtitle="Hanya posisi milik magic number aplikasi yang tersedia untuk pengelolaan.">
        <DataTable rows={(positions.data || []).filter((item) => item.status !== 'CLOSED')} rowKey={(item) => item.position_id} columns={[
          { key: 'opened', header: 'Opened', cell: (item) => dateTime(item.opened_at) },
          { key: 'trade', header: 'Trade', cell: (item) => `${item.symbol} · ${item.direction}` },
          { key: 'volume', header: 'Volume', cell: (item) => number(item.volume) },
          { key: 'price', header: 'Entry / Current', cell: (item) => `${number(item.entry_price, 5)} / ${number(item.current_price, 5)}` },
          { key: 'stops', header: 'SL / TP', cell: (item) => `${number(item.stop_loss, 5)} / ${number(item.take_profit, 5)}` },
          { key: 'actions', header: '', cell: (item) => <div className="engine-actions"><button className="button button-ghost" onClick={() => choosePosition(item, 'break-even')}>Break-even</button><button className="button button-danger" onClick={() => choosePosition(item, 'close')}>Close</button></div> },
        ]} />
      </Panel>
      <Panel title="Deal history">
        <DataTable rows={deals.data || []} rowKey={(item) => item.trade_id} columns={[
          { key: 'time', header: 'Executed', cell: (item) => dateTime(item.executed_at) },
          { key: 'trade', header: 'Deal', cell: (item) => `${item.symbol} · ${item.direction}` },
          { key: 'volume', header: 'Volume', cell: (item) => number(item.volume) },
          { key: 'price', header: 'Price', cell: (item) => number(item.price, 5) },
          { key: 'pnl', header: 'PnL', cell: (item) => <PnLDisplay value={item.profit + item.commission + item.swap} /> },
          { key: 'ticket', header: 'Ticket', cell: (item) => item.broker_deal_ticket },
        ]} />
      </Panel>
    </>}
    <ConfirmDialog open={confirm === 'stop'} title="Stop demo engine?" description="Engine kembali STOPPED dan tidak menerima execution baru. Posisi broker tidak ditutup otomatis." busy={action.isPending} onCancel={() => setConfirm(null)} onConfirm={() => action.mutate('stop')} />
    <ConfirmDialog open={confirm === 'emergency'} title="Emergency stop demo engine?" description="Order baru langsung diblokir. Posisi hanya ditutup bila pengaturan backend yang aman telah diaktifkan." destructive confirmationText="EMERGENCY STOP" busy={emergency.isPending} onCancel={() => setConfirm(null)} onConfirm={() => emergency.mutate()} />
    <ConfirmDialog open={confirm === 'close'} title="Close owned demo position?" description="Backend memverifikasi magic number, akun demo, symbol, dan fresh Bid/Ask sebelum mengirim sisi berlawanan." destructive confirmationText="CLOSE DEMO POSITION" busy={close.isPending} onCancel={() => { setConfirm(null); setSelectedPosition(null) }} onConfirm={() => close.mutate()} />
    <ConfirmDialog open={confirm === 'break-even'} title="Move stop to break-even?" description="Stop hanya dapat diperketat pada posisi demo milik aplikasi." busy={breakEven.isPending} onCancel={() => { setConfirm(null); setSelectedPosition(null) }} onConfirm={() => breakEven.mutate()} />
  </div>
}
