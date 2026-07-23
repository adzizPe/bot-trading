import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, downloadBacktestCsv, hasDemoAdminToken, loadPreferences, request, sanitizeMessage, savePreferences, setDemoAdminToken } from './client'

describe('central API client', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))

  it('uses the configured API base and parses JSON', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    savePreferences({ apiBaseUrl: '/custom-api', websocketUrl: '', refreshInterval: 5000, timezone: 'UTC', compact: false })
    await expect(request<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true })
    expect(fetch).toHaveBeenCalledWith('/custom-api/health', expect.any(Object))
  })

  it('returns a consistent ApiError and hides sensitive details', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: 'password=hidden' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
    await expect(request('/health')).rejects.toMatchObject({ status: 503, message: 'Sensitive error details were hidden' })
    expect(sanitizeMessage('Traceback: token=abc')).toBe('Sensitive error details were hidden')
  })

  it('persists only whitelisted non-secret dashboard preferences', () => {
    savePreferences({ apiBaseUrl: '/api/v1', websocketUrl: 'ws://localhost/api/v1', refreshInterval: 1000, timezone: 'UTC', compact: true })
    expect(loadPreferences().refreshInterval).toBe(5000)
    expect(localStorage.getItem('dashboard-preferences')).not.toMatch(/password|token|secret/i)
  })

  it('downloads backtest CSV through the read-only export endpoint', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob(['header\n']) } as Response)
    const createObjectURL = vi.fn(() => 'blob:test')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    await downloadBacktestCsv('job-1')
    expect(fetch).toHaveBeenCalledWith('/api/v1/backtests/job-1/export.csv')
    expect(click).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test')
  })

  it('keeps demo admin credential in memory and sends only the allowed execution fields', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ status: 'ACCEPTED' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    setDemoAdminToken('runtime-demo-admin-secret')
    expect(hasDemoAdminToken()).toBe(true)
    await api.executeDemo('plan-1', 'idempotency-1')
    const [, options] = vi.mocked(fetch).mock.calls.at(-1)!
    const headers = new Headers(options?.headers)
    expect(headers.get('X-Admin-Token')).toBe('runtime-demo-admin-secret')
    expect(headers.get('X-Idempotency-Key')).toBe('idempotency-1')
    expect(JSON.parse(String(options?.body))).toEqual({
      trade_plan_id: 'plan-1',
      idempotency_key: 'idempotency-1',
      confirmation_text: 'EXECUTE DEMO ORDER',
    })
    expect(localStorage.getItem('dashboard-preferences') || '').not.toContain('runtime-demo-admin-secret')
    expect(sessionStorage.length).toBe(0)
  })

  it('does not expose any raw MT5 order_send client method', () => {
    const methods = Object.keys(api).join(' ').toLowerCase()
    expect(methods).not.toContain('order_send')
    expect(methods).not.toContain('ordersend')
    expect(methods).not.toContain('brokerorder')
  })
})
