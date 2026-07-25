import { useEffect, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Save } from 'lucide-react'
import { api, loadPreferences, savePreferences, type DashboardPreferences } from '../api/client'
import { PageHeader, Panel, useToast } from '../components/ui'

export function SettingsPage() {
  const { notify } = useToast()
  const [form, setForm] = useState<DashboardPreferences>(loadPreferences)
  const [error, setError] = useState('')
  const mt5 = useQuery({ queryKey: ['mt5-status'], queryFn: api.mt5Status })
  useEffect(() => { document.documentElement.dataset.compact = form.compact ? 'true' : 'false' }, [form.compact])
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (form.refreshInterval < 5000) return setError('Refresh interval minimum 5000 ms')
    setError('')
    savePreferences(form)
    notify('Dashboard settings disimpan')
  }

  return <div className="page-stack">
    <PageHeader title="Dashboard settings" description="Preference tampilan non-secret. API dan WebSocket ditentukan saat build dan tidak dapat diubah saat runtime." />
    <Panel title="General"><form className="form-grid" onSubmit={submit}>
      <label className="field">Active symbol<input value={mt5.data?.symbol || 'XAUUSD'} readOnly aria-describedby="symbol-help" /><small id="symbol-help">Dikelola backend melalui MT5_SYMBOL; tidak menyimpan credential.</small></label>
      <label className="field">Refresh interval (ms)<input type="number" min="5000" step="1000" value={form.refreshInterval} onChange={(event) => setForm({ ...form, refreshInterval: Number(event.target.value) })} /></label>
      <label className="field">Timezone<input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} /></label>
      <label className="check-field"><input type="checkbox" checked={form.compact} onChange={(event) => setForm({ ...form, compact: event.target.checked })} />Compact display density</label>
      <label className="field">Theme<select value="dark" disabled><option>Dark</option></select></label>
      {error && <div className="form-error" role="alert">{error}</div>}
      <div className="form-actions"><button className="button button-primary"><Save size={16} />Save settings</button></div>
    </form></Panel>
    <div className="alert alert-info">API/WS origin is fixed by the build allowlist. Access and refresh tokens remain HttpOnly and are never available to JavaScript or browser storage.</div>
  </div>
}
