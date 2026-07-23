import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Calculator, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { api, hasDemoAdminToken, sanitizeMessage } from '../api/client'
import type { DemoExecution, TradePlan } from '../api/types'
import { ConfirmDialog, DataTable, EmptyState, PageHeader, Panel, StatusBadge, TradePlanCard, dateTime, number, useToast } from '../components/ui'

export function TradePlansPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const plans = useQuery({ queryKey: ['trade-plans'], queryFn: api.tradePlans })
  const signal = useQuery({ queryKey: ['latest-signal'], queryFn: api.latestSignal, retry: false })
  const [selected, setSelected] = useState<TradePlan | null>(null)
  const [paperPlan, setPaperPlan] = useState<TradePlan | null>(null)
  const [demoPlan, setDemoPlan] = useState<TradePlan | null>(null)
  const [lastExecution, setLastExecution] = useState<DemoExecution | null>(null)
  const create = useMutation({
    mutationFn: () => api.createTradePlan(signal.data!.signal_id),
    onSuccess: (plan) => { queryClient.setQueryData<TradePlan[]>(['trade-plans'], (current = []) => [plan, ...current]); notify(`Trade plan ${plan.status}`) },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const openPaper = useMutation({
    mutationFn: (plan: TradePlan) => api.openPaperPosition(plan.trade_plan_id),
    onSuccess: () => { setPaperPlan(null); queryClient.invalidateQueries({ queryKey: ['paper-positions'] }); notify('Paper position berhasil dibuka') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const executeDemo = useMutation({
    mutationFn: (plan: TradePlan) => api.executeDemo(plan.trade_plan_id, `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`),
    onSuccess: (execution) => { setDemoPlan(null); setLastExecution(execution); queryClient.invalidateQueries({ queryKey: ['demo-executions'] }); notify(`Demo execution: ${execution.status}`) },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })
  const requestDemo = (plan: TradePlan) => {
    if (!hasDemoAdminToken()) { notify('Aktifkan sesi admin sementara di halaman Demo Trading terlebih dahulu', 'error'); return }
    setDemoPlan(plan)
  }

  return <div className="page-stack">
    <PageHeader title="Trade plans" description="Risk plan dapat dipakai untuk paper trading atau eksekusi manual pada akun MT5 demo yang terverifikasi." actions={<button className="button button-primary" disabled={!signal.data || signal.data.direction === 'HOLD' || create.isPending} onClick={() => create.mutate()}><Calculator size={16} />{create.isPending ? 'Calculating…' : 'Buat dari latest signal'}</button>} />
    <div className="alert alert-info"><ShieldCheck size={18} />Eksekusi broker hanya tersedia untuk plan APPROVED, mode MANUAL_DEMO, dan akun demo. Volume, SL, TP, serta symbol selalu ditentukan ulang oleh backend.</div>
    {lastExecution && <div className="alert alert-info" role="status">Execution <strong>{lastExecution.status}</strong> · retcode {lastExecution.retcode ?? '—'} · {lastExecution.retcode_message || 'No broker message'}</div>}
    {plans.data?.length ? <DataTable rows={plans.data} rowKey={(item) => item.trade_plan_id} columns={[
      { key: 'time', header: 'Created', cell: (item) => dateTime(item.created_at) },
      { key: 'symbol', header: 'Symbol', cell: (item) => `${item.symbol} · ${item.direction}` },
      { key: 'status', header: 'Status', cell: (item) => <StatusBadge value={item.status} /> },
      { key: 'entry', header: 'Entry / SL / TP', cell: (item) => `${number(item.entry_price, 5)} / ${number(item.stop_loss, 5)} / ${number(item.take_profit, 5)}` },
      { key: 'lot', header: 'Plan lot', cell: (item) => number(item.position_size_lots) },
      { key: 'risk', header: 'Risk / R:R', cell: (item) => `${number(item.risk_amount)} / ${number(item.risk_reward)}` },
      { key: 'detail', header: '', cell: (item) => <button className="button button-ghost" onClick={() => setSelected(item)}>Detail</button> },
    ]} /> : <EmptyState title="Belum ada trade plan" description="Generate candidate signal lalu buat trade plan." />}
    {selected && <div className="dialog-backdrop"><div className="dialog dialog-wide" role="dialog" aria-modal="true" aria-label="Trade plan detail"><TradePlanCard plan={selected} action={selected.status === 'APPROVED' && <div className="engine-actions"><button className="button button-ghost" onClick={() => setPaperPlan(selected)}>Open paper position</button><button className="button button-danger" onClick={() => requestDemo(selected)}>Execute Demo</button></div>} /><Panel title="Calculation details"><pre className="json-detail">{JSON.stringify(selected.calculation_details, null, 2)}</pre></Panel><button className="button button-ghost" onClick={() => setSelected(null)}>Tutup</button></div></div>}
    <ConfirmDialog open={Boolean(paperPlan)} title="Buka paper position?" description="Posisi hanya dibuat pada ledger simulasi. Tidak ada order broker." busy={openPaper.isPending} onCancel={() => setPaperPlan(null)} onConfirm={() => paperPlan && openPaper.mutate(paperPlan)} />
    <ConfirmDialog open={Boolean(demoPlan)} title="Execute MT5 demo order?" description="Backend akan memuat ulang plan, memverifikasi akun demo, menghitung ulang volume dari risiko dan quote terbaru, lalu menjalankan order_check sebelum order_send." destructive confirmationText="EXECUTE DEMO ORDER" busy={executeDemo.isPending} onCancel={() => setDemoPlan(null)} onConfirm={() => demoPlan && executeDemo.mutate(demoPlan)} />
  </div>
}
