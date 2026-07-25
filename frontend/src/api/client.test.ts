import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, apiUrl, downloadBacktestCsv, loadPreferences, request, sanitizeMessage, savePreferences, websocketUrl } from './client'

const authUser = {
  user_id: 'u1', username: 'alice', role: 'OPERATOR', permissions: ['analysis:generate'],
  is_active: true, access_expires_at: '2099-01-01T00:00:00Z',
}

describe('cookie-authenticated API client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    document.cookie = 'csrf_token=; Max-Age=0; path=/'
  })

  it('uses only the build-time API base with credentialed requests', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    await expect(request<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true })
    expect(apiUrl('/health')).toBe('/api/v1/health')
    expect(fetch).toHaveBeenCalledWith('/api/v1/health', expect.objectContaining({ credentials: 'include', method: 'GET' }))
  })

  it('adds CSRF to unsafe requests and never exposes cookie tokens in storage', async () => {
    document.cookie = 'csrf_token=csrf-value; path=/'
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(authUser), { status: 200 }))
    await api.login('alice', 'correct-password')
    const [, options] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(options?.headers)
    expect(options).toMatchObject({ credentials: 'include', method: 'POST' })
    expect(headers.get('X-CSRF-Token')).toBe('csrf-value')
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('refresh_token')).toBeNull()
  })

  it('loads the current user and ignores the successful logout JSON body', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(authUser), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
    await expect(api.me()).resolves.toEqual(authUser)
    await expect(api.logout()).resolves.toBeUndefined()
    expect(vi.mocked(fetch).mock.calls[1][0]).toBe('/api/v1/auth/logout')
  })

  it('accepts the flat AuthUser response from refresh', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(authUser), { status: 200 }))
    await expect(api.refreshAuth()).resolves.toEqual(authUser)
  })

  it('refreshes once before retrying logout so an expired access session can be revoked', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'expired' }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(authUser), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))

    await expect(api.logout()).resolves.toBeUndefined()
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/auth/logout', '/api/v1/auth/refresh', '/api/v1/auth/logout',
    ])
  })

  it('uses one refresh flight for concurrent 401 responses and retries once', async () => {
    let refreshCalls = 0
    const attempts = new Map<string, number>()
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/auth/refresh')) {
        refreshCalls += 1
        await Promise.resolve()
        return new Response(JSON.stringify(authUser), { status: 200 })
      }
      const count = attempts.get(url) || 0
      attempts.set(url, count + 1)
      return count === 0
        ? new Response(JSON.stringify({ detail: 'expired' }), { status: 401 })
        : new Response(JSON.stringify({ ok: true }), { status: 200 })
    })
    await expect(Promise.all([request('/one'), request('/two')])).resolves.toEqual([{ ok: true }, { ok: true }])
    expect(refreshCalls).toBe(1)
    expect(attempts.get('/api/v1/one')).toBe(2)
    expect(attempts.get('/api/v1/two')).toBe(2)
  })

  it('does not loop when refresh is unauthorized', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'expired' }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'invalid refresh' }), { status: 401 }))
    await expect(request('/protected')).rejects.toMatchObject({ status: 401 })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('surfaces forbidden responses without attempting a refresh', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: 'forbidden' }), { status: 403 }))
    await expect(request('/protected')).rejects.toMatchObject({ status: 403 })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('never sends credentials to an arbitrary saved runtime origin', async () => {
    localStorage.setItem('dashboard-preferences', JSON.stringify({
      apiBaseUrl: 'https://evil.example/api', websocketUrl: 'wss://evil.example/ws',
    }))
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    loadPreferences()
    await request('/health')
    expect(fetch).toHaveBeenCalledWith('/api/v1/health', expect.objectContaining({ credentials: 'include' }))
  })

  it('keeps only display preferences and removes legacy saved URLs', () => {
    localStorage.setItem('dashboard-preferences', JSON.stringify({
      apiBaseUrl: 'https://evil.example/api', websocketUrl: 'wss://evil.example/ws',
      refreshInterval: 1000, timezone: 'UTC', compact: true,
    }))
    expect(loadPreferences()).toEqual({ refreshInterval: 5000, timezone: 'UTC', compact: true })
    expect(localStorage.getItem('dashboard-preferences')).not.toMatch(/apiBaseUrl|websocketUrl|evil/)
    savePreferences({ refreshInterval: 7000, timezone: 'Asia/Jakarta', compact: false })
    expect(localStorage.getItem('dashboard-preferences')).toBe('{"refreshInterval":7000,"timezone":"Asia/Jakarta","compact":false}')
  })

  it('derives a cookie-only WebSocket URL from the allowed API origin', () => {
    const url = new URL(websocketUrl('/ws/market?symbol=XAUUSD'))
    expect(url.origin.replace(/^ws/, 'http')).toBe(window.location.origin)
    expect(url.pathname).toBe('/api/v1/ws/market')
    expect(url.searchParams.get('symbol')).toBe('XAUUSD')
    expect(url.search).not.toMatch(/token/i)
  })

  it('downloads CSV with credentials through the same client path', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob(['header\n']) } as Response)
    const createObjectURL = vi.fn(() => 'blob:test')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    await downloadBacktestCsv('job-1')
    expect(fetch).toHaveBeenCalledWith('/api/v1/backtests/job-1/export.csv', expect.objectContaining({ credentials: 'include' }))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test')
  })

  it('sanitizes secret-like backend errors', () => {
    expect(sanitizeMessage('Traceback: token=abc')).toBe('Sensitive error details were hidden')
  })

  it('has no legacy admin-token API surface', () => {
    expect(Object.keys(api).join(' ')).not.toMatch(/admin.*token|token.*admin/i)
  })
})
