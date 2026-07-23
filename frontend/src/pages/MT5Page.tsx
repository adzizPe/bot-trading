import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, Unlink } from 'lucide-react'
import { useState } from 'react'
import { api, sanitizeMessage } from '../api/client'
import { ConfirmDialog, ErrorAlert, MetricCard, PageHeader, Panel, StatusBadge, money, number, useToast } from '../components/ui'

const maskLogin = (value?: number) => {
  if (!value) return '—'
  const text = String(value)
  return `${text.slice(0, 2)}${'•'.repeat(Math.max(2, text.length - 4))}${text.slice(-2)}`
}

export function MT5Page() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const [disconnect, setDisconnect] = useState(false)
  const status = useQuery({ queryKey: ['mt5-status'], queryFn: api.mt5Status, refetchInterval: 10000 })
  const enabled = status.data?.connected === true
  const account = useQuery({ queryKey: ['mt5-account'], queryFn: api.mt5Account, enabled, retry: false })
  const terminal = useQuery({ queryKey: ['mt5-terminal'], queryFn: api.mt5Terminal, enabled, retry: false })
  const symbol = useQuery({ queryKey: ['mt5-symbol'], queryFn: api.mt5Symbol, enabled, retry: false })
  const connection = useMutation({
    mutationFn: (action: 'connect' | 'disconnect') => action === 'connect' ? api.mt5Connect() : api.mt5Disconnect(),
    onSuccess: (data) => { queryClient.setQueryData(['mt5-status'], data); queryClient.invalidateQueries(); setDisconnect(false); notify(data.connected ? 'MT5 demo connected' : 'MT5 disconnected') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })

  return <div className="page-stack">
    <PageHeader title="MT5 connection" description="Koneksi hanya menerima akun demo. Password dan secret tidak pernah dikirim atau ditampilkan frontend." actions={enabled ? <button className="button button-danger" onClick={() => setDisconnect(true)}><Unlink size={16} />Disconnect</button> : <button className="button button-primary" onClick={() => connection.mutate('connect')} disabled={connection.isPending}><Link2 size={16} />Connect demo</button>} />
    {status.data?.last_error && <ErrorAlert message={sanitizeMessage(status.data.last_error)} />}
    <div className="metric-grid">
      <MetricCard label="Connection" value={<StatusBadge value={enabled ? 'connected' : status.data?.state || 'disconnected'} />} />
      <MetricCard label="Demo verified" value={<StatusBadge value={status.data?.demo_verified ? 'verified' : 'not verified'} />} />
      <MetricCard label="Active symbol" value={status.data?.symbol || '—'} />
      <MetricCard label="Account type" value={account.data?.is_demo ? 'DEMO' : '—'} detail="Real accounts are rejected" />
    </div>
    <div className="two-column">
      <Panel title="Account info"><div className="key-values vertical">
        <span>Login ID<strong>{maskLogin(account.data?.login)}</strong></span><span>Server<strong>{account.data?.server || '—'}</strong></span><span>Broker<strong>{account.data?.company || '—'}</strong></span><span>Currency<strong>{account.data?.currency || '—'}</strong></span><span>Leverage<strong>{account.data?.leverage ? `1:${account.data.leverage}` : '—'}</strong></span><span>Balance<strong>{money(account.data?.balance, account.data?.currency || 'USD')}</strong></span><span>Equity<strong>{money(account.data?.equity, account.data?.currency || 'USD')}</strong></span>
      </div></Panel>
      <Panel title="Terminal info"><div className="key-values vertical">
        <span>Name<strong>{terminal.data?.name || '—'}</strong></span><span>Company<strong>{terminal.data?.company || '—'}</strong></span><span>Build<strong>{terminal.data?.build || '—'}</strong></span><span>Trade allowed<strong>{terminal.data?.trade_allowed ? 'Yes' : 'No'}</strong></span><span>Trade API disabled<strong>{terminal.data?.tradeapi_disabled ? 'Yes' : 'No'}</strong></span><span>Ping<strong>{number(terminal.data?.ping_last)} μs</strong></span>
      </div></Panel>
    </div>
    <Panel title="Symbol specification"><div className="metric-grid">
      <MetricCard label="Name" value={symbol.data?.name || '—'} detail={symbol.data?.description} />
      <MetricCard label="Digits / point" value={`${symbol.data?.digits ?? '—'} / ${number(symbol.data?.point, 8)}`} />
      <MetricCard label="Tick size / value" value={`${number(symbol.data?.trade_tick_size, 8)} / ${number(symbol.data?.trade_tick_value, 6)}`} />
      <MetricCard label="Volume min / max" value={`${number(symbol.data?.volume_min)} / ${number(symbol.data?.volume_max)}`} />
      <MetricCard label="Volume step" value={number(symbol.data?.volume_step)} />
      <MetricCard label="Stops / freeze" value={`${symbol.data?.trade_stops_level ?? '—'} / ${symbol.data?.trade_freeze_level ?? '—'}`} />
    </div></Panel>
    <ConfirmDialog open={disconnect} title="Disconnect MT5 demo?" description="Market, analysis, dan paper quote akan berhenti sampai koneksi dibuka kembali." busy={connection.isPending} onCancel={() => setDisconnect(false)} onConfirm={() => connection.mutate('disconnect')} />
  </div>
}
