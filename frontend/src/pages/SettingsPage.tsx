import { useEffect, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Save } from 'lucide-react'
import { api, loadPreferences, savePreferences, type DashboardPreferences } from '../api/client'
import { PageHeader, Panel, useToast } from '../components/ui'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { notify } = useToast()
  const [form, setForm] = useState<DashboardPreferences>(loadPreferences)
  const [error, setError] = useState('')
  const mt5 = useQuery({ queryKey: ['mt5-status'], queryFn: api.mt5Status })
  useEffect(() => { document.documentElement.dataset.compact = form.compact ? 'true' : 'false' }, [form.compact])
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!(form.apiBaseUrl.startsWith('/') || /^https?:\/\//.test(form.apiBaseUrl))) return setError('API base URL harus relative path atau HTTP(S) URL')
    if (form.websocketUrl && !(/^wss?:\/\//.test(form.websocketUrl) || form.websocketUrl.startsWith('/'))) return setError('WebSocket URL harus kosong, relative path, WS, atau WSS URL')
    if (form.refreshInterval < 5000) return setError('Refresh interval minimum 5000 ms')
    setError('')
    savePreferences(form)
    queryClient.invalidateQueries()
    notify('Dashboard settings disimpan')
  }

  return <div className="page-stack">
    <PageHeader title="Dashboard settings" description="Preference non-secret untuk koneksi UI, refresh, timezone, dan tampilan." />
    <Panel title="General"><form className="form-grid" onSubmit={submit}>
      <label className="field">Active symbol<input value={mt5.data?.symbol || 'XAUUSD'} readOnly aria-describedby="symbol-help" /><small id="symbol-help">Dikelola backend melalui MT5_SYMBOL; tidak menyimpan credential.</small></label>
      <label className="field">API base URL<input value={form.apiBaseUrl} onChange={(event) => setForm({ ...form, apiBaseUrl: event.target.value })} /></label>
      <label className="field">WebSocket URL<input placeholder="Auto from API URL" value={form.websocketUrl} onChange={(event) => setForm({ ...form, websocketUrl: event.target.value })} /></label>
      <label className="field">Refresh interval (ms)<input type="number" min="5000" step="1000" value={form.refreshInterval} onChange={(event) => setForm({ ...form, refreshInterval: Number(event.target.value) })} /></label>
      <label className="field">Timezone<input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} /></label>
      <label className="check-field"><input type="checkbox" checked={form.compact} onChange={(event) => setForm({ ...form, compact: event.target.checked })} />Compact display density</label>
      <label className="field">Theme<select value="dark" disabled><option>Dark</option></select></label>
      {error && <div className="form-error" role="alert">{error}</div>}
      <div className="form-actions"><button className="button button-primary"><Save size={16} />Save settings</button></div>
    </form></Panel>
    <Panel title="Telegram"><div className="empty-state"><strong>Integration unavailable</strong><span>Backend tidak menyediakan endpoint Telegram pada Milestone 8. Secret tidak boleh dimasukkan atau disimpan di browser.</span><button className="button button-ghost" disabled>Configure Telegram</button></div></Panel>
    <div className="alert alert-info">Tidak ada password, token, MT5 credential, atau secret yang disimpan di localStorage/sessionStorage.</div>
  </div>
}
