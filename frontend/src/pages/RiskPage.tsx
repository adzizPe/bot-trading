import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Save, ShieldAlert } from 'lucide-react'
import { api, sanitizeMessage } from '../api/client'
import type { RiskSettings } from '../api/types'
import { ConfirmDialog, ErrorAlert, LoadingSkeleton, MetricCard, PageHeader, Panel, StatusBadge, money, useToast } from '../components/ui'

const fields: { key: keyof RiskSettings; label: string; min: number; step?: number }[] = [
  { key: 'risk_per_trade_percent', label: 'Risk per trade (%)', min: 0.01, step: 0.01 },
  { key: 'max_daily_loss_percent', label: 'Max daily loss (%)', min: 0.01, step: 0.01 },
  { key: 'max_daily_drawdown_percent', label: 'Max drawdown (%)', min: 0.01, step: 0.01 },
  { key: 'max_consecutive_losses', label: 'Max consecutive losses', min: 1 },
  { key: 'max_trades_per_day', label: 'Max trades per day', min: 1 },
  { key: 'max_open_positions', label: 'Max open positions', min: 1 },
  { key: 'minimum_risk_reward', label: 'Minimum risk/reward', min: 0.01, step: 0.01 },
  { key: 'target_risk_reward', label: 'Target risk/reward', min: 0.01, step: 0.01 },
  { key: 'maximum_spread_points', label: 'Maximum spread (points)', min: 0, step: 0.01 },
  { key: 'cooldown_minutes_after_loss', label: 'Cooldown (minutes)', min: 0 },
  { key: 'atr_multiplier', label: 'ATR multiplier', min: 0.01, step: 0.01 },
]

export function RiskPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const settings = useQuery({ queryKey: ['risk-settings'], queryFn: api.riskSettings })
  const status = useQuery({ queryKey: ['risk-status'], queryFn: api.riskStatus, refetchInterval: 15000 })
  const [form, setForm] = useState<Partial<RiskSettings>>({})
  const [validation, setValidation] = useState('')
  const [confirm, setConfirm] = useState(false)
  const values = { ...settings.data, ...form }
  const update = useMutation({
    mutationFn: () => api.updateRiskSettings(form),
    onSuccess: (data) => { queryClient.setQueryData(['risk-settings'], data); setConfirm(false); notify('Risk settings berhasil disimpan') },
    onError: (error) => notify(sanitizeMessage(error), 'error'),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const invalid = fields.find(({ key, min }) => typeof values[key] !== 'number' || Number(values[key]) < min)
    if (invalid) { setValidation(`${invalid.label} tidak valid`); return }
    if (values.session_enabled && values.session_start_hour_utc === values.session_end_hour_utc) { setValidation('Jam mulai dan selesai sesi harus berbeda'); return }
    setValidation('')
    setConfirm(true)
  }

  return <div className="page-stack">
    <PageHeader title="Risk management" description="Pengaturan proteksi yang digunakan backend risk planning dan paper trading." />
    <div className="metric-grid">
      <MetricCard label="Risk lock" value={<StatusBadge value={status.data?.risk_locked ? 'locked' : 'available'} />} icon={<ShieldAlert />} />
      <MetricCard label="Daily realized loss" value={money(status.data?.state?.realized_loss)} />
      <MetricCard label="Floating drawdown" value={money(status.data?.state?.floating_drawdown)} />
      <MetricCard label="Trades today" value={status.data?.state?.trades_count ?? '—'} />
      <MetricCard label="Open positions" value={status.data?.state?.open_positions ?? '—'} />
    </div>
    {status.data?.risk_lock_reasons.map((reason) => <ErrorAlert key={reason} message={reason} />)}
    <Panel title="Risk settings" subtitle="Perubahan penting memerlukan konfirmasi.">
      {settings.isLoading ? <LoadingSkeleton rows={6} /> : settings.error ? <ErrorAlert message={sanitizeMessage(settings.error)} retry={() => settings.refetch()} /> : <form className="form-grid" onSubmit={submit} noValidate>
        {fields.map(({ key, label, min, step = 1 }) => <label className="field" key={key}>{label}<input type="number" min={min} step={step} value={String(values[key] ?? '')} onChange={(event) => setForm((current) => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}
        <label className="field">Stop loss method<select value={values.stop_loss_method || 'ATR'} onChange={(event) => setForm((current) => ({ ...current, stop_loss_method: event.target.value as RiskSettings['stop_loss_method'] }))}><option>ATR</option><option>SWING</option><option>SUPPORT_RESISTANCE</option></select></label>
        <label className="check-field"><input type="checkbox" checked={values.use_equity_for_risk ?? true} onChange={(event) => setForm((current) => ({ ...current, use_equity_for_risk: event.target.checked }))} />Use equity for risk</label>
        <label className="check-field"><input type="checkbox" checked={values.session_enabled ?? false} onChange={(event) => setForm((current) => ({ ...current, session_enabled: event.target.checked }))} />Trading session enabled</label>
        <label className="field">Session start UTC<input type="number" min="0" max="23" value={values.session_start_hour_utc ?? 0} onChange={(event) => setForm((current) => ({ ...current, session_start_hour_utc: Number(event.target.value) }))} /></label>
        <label className="field">Session end UTC<input type="number" min="0" max="24" value={values.session_end_hour_utc ?? 24} onChange={(event) => setForm((current) => ({ ...current, session_end_hour_utc: Number(event.target.value) }))} /></label>
        <div className="field">Session weekdays<div className="weekday-grid">{['Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab', 'Min'].map((label, day) => <label className="check-field" key={label}><input type="checkbox" checked={(values.session_weekdays || []).includes(day)} onChange={(event) => setForm((current) => ({ ...current, session_weekdays: event.target.checked ? [...(values.session_weekdays || []), day].sort() : (values.session_weekdays || []).filter((value) => value !== day) }))} />{label}</label>)}</div></div>
        {validation && <div className="form-error" role="alert">{validation}</div>}
        <div className="form-actions"><button className="button button-primary" disabled={update.isPending}><Save size={16} />Simpan pengaturan</button></div>
      </form>}
    </Panel>
    <ConfirmDialog open={confirm} title="Ubah risk settings?" description="Batas risiko baru akan digunakan pada trade plan berikutnya. Pastikan nilainya sudah ditinjau." busy={update.isPending} onCancel={() => setConfirm(false)} onConfirm={() => update.mutate()} />
  </div>
}
