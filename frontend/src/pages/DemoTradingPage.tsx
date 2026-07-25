import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, HeartPulse, Octagon, Pause, Play, RefreshCw, ShieldAlert, ShieldCheck, Square, Zap } from 'lucide-react'
import { useState } from 'react'
import { api, sanitizeMessage } from '../api/client'
import type { DemoPosition } from '../api/types'
import { usePermission } from '../auth/AuthProvider'
import {
  ConfirmDialog,
  DataTable,
  MetricCard,
  PageHeader,
  Panel,
  PnLDisplay,
  StatusBadge,
  dateTime,
  number,
  useToast,
} from '../components/ui'

type ConfirmAction = 'stop' | 'emergency' | 'safety-emergency' | 'safety-reset' | 'close' | 'break-even' | null

export function DemoTradingPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const canControl = usePermission('demo:execute')
  const canManagePositions = usePermission('demo:position:manage')
  const canEmergency = usePermission('emergency-stop:execute')
  const canReset = usePermission('safety:reset')
  const [safetyReason, setSafetyReason] = useState('')
  const [confirm, setConfirm] = useState<ConfirmAction>(null)
  const [selectedPosition, setSelectedPosition] = useState<DemoPosition | null>(null)
  const interval = 10000
  const safetyInterval = 5000
  const queryOptions = { retry: false, refetchInterval: interval }
  const safetyQueryOptions = { retry: false, refetchInterval: safetyInterval }
  const status = useQuery({ queryKey: ['demo-status'], queryFn: api.demoStatus, ...queryOptions })
  const safety = useQuery({ queryKey: ['safety-status'], queryFn: api.safetyStatus, ...safetyQueryOptions })
  const health = useQuery({ queryKey: ['health-full'], queryFn: api.healthFull, retry: false, refetchInterval: safetyInterval })
  const executions = useQuery({ queryKey: ['demo-executions'], queryFn: api.demoExecutions, ...queryOptions })
  const orders = useQuery({ queryKey: ['demo-orders'], queryFn: api.demoOrders, ...queryOptions })
  const positions = useQuery({ queryKey: ['demo-positions'], queryFn: api.demoPositions, ...queryOptions })
  const deals = useQuery({ queryKey: ['demo-deals'], queryFn: api.demoDeals, ...queryOptions })

  const guardianEntries = Object.values(safety.data?.guardians ?? {})
  const blockedGuardian = guardianEntries.find((guardian) => !guardian.allowed)
  const guardianStatus = !safety.data ? 'unknown' : blockedGuardian ? 'BLOCKED' : 'HEALTHY'
  const circuitStatus = safety.data?.circuit_breaker.state ?? 'unknown'
  const heartbeatStatus = safety.data?.heartbeat_status ?? 'unknown'
  const emergencyActive = safety.data?.emergency.active === true
  const safetyState = !safety.data ? 'unknown' : safety.data.allowed ? 'SAFE' : 'BLOCKED'
  const healthState = health.data?.status ?? 'unknown'
  const safetyBlocked = !safety.data
    || !safety.data.allowed
    || emergencyActive
    || circuitStatus !== 'CLOSED'
    || /DEGRADED|UNHEALTHY/.test(heartbeatStatus)
    || /DEGRADED|UNHEALTHY/.test(healthState)

  const refresh = () => queryClient.invalidateQueries({
    predicate: (query) => /^(demo-|safety-|health-full)/.test(String(query.queryKey[0])),
  })
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
  const safetyEmergency = useMutation({
    mutationFn: () => api.safetyEmergencyStop(safetyReason.trim()),
    onSuccess: (result) => {
      queryClient.setQueryData(['safety-status'], result)
      setConfirm(null)
      setSafetyReason('')
      refresh()
      notify('Global safety emergency stop aktif')
    },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const safetyReset = useMutation({
    mutationFn: api.safetyEmergencyReset,
    onSuccess: (result) => {
      queryClient.setQueryData(['safety-status'], result)
      setConfirm(null)
      refresh()
      notify('Global safety emergency stop di-reset')
    },
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
    <PageHeader title="Demo trading" description="Eksekusi manual XAU/USD pada akun MT5 demo terverifikasi. Auto trading tetap dinonaktifkan." actions={canControl && <div className="engine-actions">
      <button className="button button-success" disabled={action.isPending || safetyBlocked || status.data?.engine.status === 'RUNNING'} title={safetyBlocked ? 'Blocked by global safety state' : undefined} onClick={() => action.mutate('start')}><Play size={16} />Start</button>
      <button className="button button-ghost" disabled={action.isPending || status.data?.engine.status !== 'RUNNING'} onClick={() => action.mutate('pause')}><Pause size={16} />Pause</button>
      <button className="button button-ghost" disabled={action.isPending} onClick={() => setConfirm('stop')}><Square size={16} />Stop</button>
      <button className="button button-ghost" disabled={reconcile.isPending} onClick={() => reconcile.mutate()}><RefreshCw size={16} />Reconcile</button>
      <button className="button button-danger" disabled={emergency.isPending} onClick={() => setConfirm('emergency')}><Octagon size={16} />Emergency stop</button>
    </div>} />
    <div className="alert alert-warn" role="note"><strong>DEMO ACCOUNT ONLY</strong> · Real and unsupported contest accounts are blocked by the backend immediately before order_check and order_send.</div>
    <Panel title="Safety control" subtitle="Status global dipoll setiap 5 detik. Kondisi blocked, emergency, circuit open, atau degraded menonaktifkan aksi pembuka risiko.">
      <div className="metric-grid">
        <MetricCard label="Safety Status" value={<StatusBadge value={safetyState} />} detail={blockedGuardian?.reason ?? undefined} icon={<ShieldCheck />} />
        <MetricCard label="Guardian Status" value={<StatusBadge value={guardianStatus} />} icon={<ShieldAlert />} />
        <MetricCard label="Circuit Breaker" value={<StatusBadge value={circuitStatus} />} icon={<Zap />} />
        <MetricCard label="Heartbeat" value={<StatusBadge value={heartbeatStatus} />} icon={<HeartPulse />} />
        <MetricCard label="Health" value={<StatusBadge value={healthState} />} detail={health.data?.checked_at ? dateTime(health.data.checked_at) : undefined} icon={<Activity />} />
        <MetricCard label="Emergency Stop" value={<StatusBadge value={emergencyActive ? 'ACTIVE' : 'INACTIVE'} />} detail={safety.data?.emergency.reason ?? undefined} icon={<Octagon />} />
      </div>
      {safetyBlocked && <div className="alert alert-warn" role="status">Trading actions are disabled by the current safety or health state.</div>}
      {(safety.error || health.error) && <div className="alert alert-error" role="alert">{sanitizeMessage(safety.error || health.error)}</div>}
      <div className="inline-form">
        {canEmergency && <><label className="field">Emergency reason<input aria-label="Safety emergency reason" value={safetyReason} onChange={(event) => setSafetyReason(event.target.value)} placeholder="Required operational reason" autoComplete="off" /></label>
        <button className="button button-danger" disabled={!safetyReason.trim() || safetyEmergency.isPending} onClick={() => setConfirm('safety-emergency')}><Octagon size={16} />Activate global stop</button></>}
        {canReset && <button className="button button-ghost" disabled={!emergencyActive || safetyReset.isPending} onClick={() => setConfirm('safety-reset')}><RefreshCw size={16} />Reset global stop</button>}
      </div>
    </Panel>
    <>
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
          { key: 'actions', header: '', cell: (item) => canManagePositions ? <div className="engine-actions"><button className="button button-ghost" disabled={safetyBlocked} title={safetyBlocked ? 'Blocked by global safety state' : undefined} onClick={() => choosePosition(item, 'break-even')}>Break-even</button><button className="button button-danger" disabled={safetyBlocked} title={safetyBlocked ? 'Blocked by global safety state' : undefined} onClick={() => choosePosition(item, 'close')}>Close</button></div> : null },
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
    </>
    <ConfirmDialog open={canControl && confirm === 'stop'} title="Stop demo engine?" description="Engine kembali STOPPED dan tidak menerima execution baru. Posisi broker tidak ditutup otomatis." busy={action.isPending} onCancel={() => setConfirm(null)} onConfirm={() => action.mutate('stop')} />
    <ConfirmDialog open={confirm === 'emergency'} title="Emergency stop demo engine?" description="Order baru langsung diblokir. Posisi hanya ditutup bila pengaturan backend yang aman telah diaktifkan." destructive confirmationText="EMERGENCY STOP" busy={emergency.isPending} onCancel={() => setConfirm(null)} onConfirm={() => emergency.mutate()} />
    <ConfirmDialog open={confirm === 'safety-emergency'} title="Activate global safety emergency stop?" description={`Seluruh aksi pembuka risiko akan diblokir. Reason: ${safetyReason.trim()}`} destructive confirmationText="EMERGENCY STOP" busy={safetyEmergency.isPending} onCancel={() => setConfirm(null)} onConfirm={() => safetyEmergency.mutate()} />
    <ConfirmDialog open={confirm === 'safety-reset'} title="Reset global safety emergency stop?" description="Reset hanya menghapus emergency latch. Guardian, circuit breaker, heartbeat, dan health tetap dapat memblokir trading." destructive confirmationText="RESET EMERGENCY STOP" busy={safetyReset.isPending} onCancel={() => setConfirm(null)} onConfirm={() => safetyReset.mutate()} />
    <ConfirmDialog open={confirm === 'close'} title="Close owned demo position?" description="Backend memverifikasi magic number, akun demo, symbol, dan fresh Bid/Ask sebelum mengirim sisi berlawanan." destructive confirmationText="CLOSE DEMO POSITION" busy={close.isPending} onCancel={() => { setConfirm(null); setSelectedPosition(null) }} onConfirm={() => close.mutate()} />
    <ConfirmDialog open={confirm === 'break-even'} title="Move stop to break-even?" description="Stop hanya dapat diperketat pada posisi demo milik aplikasi." busy={breakEven.isPending} onCancel={() => { setConfirm(null); setSelectedPosition(null) }} onConfirm={() => breakEven.mutate()} />
  </div>
}
