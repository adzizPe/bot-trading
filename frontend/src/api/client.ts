import type {
  AnalysisSnapshot,
  BacktestDetail,
  BacktestEquity,
  BacktestRequest,
  BacktestSummary,
  BacktestTrade,
  Candle,
  DemoDeal,
  DemoExecution,
  DemoOperation,
  DemoOrder,
  DemoPosition,
  DemoReconciliation,
  DemoStatus,
  FullHealth,
  Health,
  Indicator,
  MT5Account,
  MT5Status,
  MT5Symbol,
  MT5Terminal,
  MarketTick,
  PaperAccount,
  PaperEngineStatus,
  PaperEquity,
  PaperPosition,
  PaperStatistics,
  PaperTrade,
  RiskFeasibilityResult,
  RiskSettings,
  RiskStatus,
  SafetyEvent,
  SafetyStatus,
  Signal,
  TradePlan,
} from './types'

const DEFAULT_API = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const ALLOWED_ORIGINS = String(import.meta.env.VITE_API_ALLOWED_ORIGINS || '')
  .split(',').map((value) => value.trim()).filter(Boolean)
const SECRET_PATTERN = /(password|secret|token|authorization|traceback)/gi
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export interface AuthUser {
  user_id: string
  username: string
  role: string
  permissions: string[]
  is_active: boolean
  access_expires_at: string
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface DashboardPreferences {
  refreshInterval: number
  timezone: string
  compact: boolean
}

const defaultPreferences: DashboardPreferences = {
  refreshInterval: 15000,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  compact: false,
}

export function sanitizeMessage(value: unknown): string {
  const raw = value instanceof Error ? value.message : String(value || 'Request failed')
  const singleLine = raw.split('\n')[0].slice(0, 240)
  SECRET_PATTERN.lastIndex = 0
  if (SECRET_PATTERN.test(singleLine)) return 'Sensitive error details were hidden'
  SECRET_PATTERN.lastIndex = 0
  return singleLine.replace(SECRET_PATTERN, '[hidden]')
}

export function loadPreferences(): DashboardPreferences {
  try {
    const stored = JSON.parse(localStorage.getItem('dashboard-preferences') || '{}') as Record<string, unknown>
    const safe = {
      refreshInterval: typeof stored.refreshInterval === 'number' ? Math.max(5000, stored.refreshInterval) : defaultPreferences.refreshInterval,
      timezone: typeof stored.timezone === 'string' ? stored.timezone : defaultPreferences.timezone,
      compact: stored.compact === true,
    }
    if ('apiBaseUrl' in stored || 'websocketUrl' in stored) savePreferences(safe)
    return safe
  } catch {
    return defaultPreferences
  }
}

export function savePreferences(value: DashboardPreferences): void {
  localStorage.setItem('dashboard-preferences', JSON.stringify({
    refreshInterval: Math.max(5000, value.refreshInterval),
    timezone: value.timezone,
    compact: value.compact,
  }))
}

function allowedOrigin(origin: string): boolean {
  return origin === window.location.origin || ALLOWED_ORIGINS.includes(origin)
}

export function apiUrl(path: string): string {
  const base = new URL(DEFAULT_API, window.location.origin)
  if (!/^https?:$/.test(base.protocol) || !allowedOrigin(base.origin)) {
    throw new ApiError('API origin is not allowed by this build', 0)
  }
  const normalizedBase = base.pathname.replace(/\/$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return base.origin === window.location.origin && DEFAULT_API.startsWith('/')
    ? `${normalizedBase}${normalizedPath}`
    : `${base.origin}${normalizedBase}${normalizedPath}`
}

export function websocketUrl(path: string): string {
  const url = new URL(apiUrl(path), window.location.origin)
  if (!allowedOrigin(url.origin)) throw new ApiError('WebSocket origin is not allowed by this build', 0)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

export function readCsrfToken(): string {
  const entry = document.cookie.split(';').map((value) => value.trim()).find((value) => value.startsWith('csrf_token='))
  return entry ? decodeURIComponent(entry.slice('csrf_token='.length)) : ''
}

async function parseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown }
    if (typeof data.detail === 'string') return sanitizeMessage(data.detail)
    if (Array.isArray(data.detail)) return 'Some fields are invalid. Review the form.'
    if (data.detail && typeof data.detail === 'object') {
      const detail = data.detail as Record<string, unknown>
      if (typeof detail.reason === 'string') {
        const guardian = typeof detail.guardian === 'string' ? ` (${detail.guardian})` : ''
        return sanitizeMessage(`${detail.reason}${guardian}`)
      }
    }
  } catch {
    // Deliberately hide non-JSON server output and stack traces.
  }
  return `Request failed (HTTP ${response.status})`
}

type AuthFailureListener = (status: 401 | 403) => void
const authFailureListeners = new Set<AuthFailureListener>()
export function subscribeAuthFailure(listener: AuthFailureListener): () => void {
  authFailureListeners.add(listener)
  return () => authFailureListeners.delete(listener)
}
function emitAuthFailure(status: 401 | 403) {
  authFailureListeners.forEach((listener) => listener(status))
}

async function fetchResponse(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers)
  const method = (options.method || 'GET').toUpperCase()
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCsrfToken()
    if (csrf) headers.set('X-CSRF-Token', csrf)
  }
  try {
    return await fetch(apiUrl(path), { ...options, method, headers, credentials: 'include' })
  } catch (error) {
    throw new ApiError(sanitizeMessage(error), 0)
  }
}

let refreshFlight: Promise<AuthUser> | null = null
async function refreshCookies(): Promise<AuthUser> {
  if (!refreshFlight) {
    refreshFlight = (async () => {
      const response = await fetchResponse('/auth/refresh', { method: 'POST' })
      if (!response.ok) throw new ApiError(await parseError(response), response.status)
      return (await response.json()) as AuthUser
    })().finally(() => { refreshFlight = null })
  }
  return refreshFlight
}

function isRefreshable(path: string): boolean {
  return path !== '/auth/login' && path !== '/auth/refresh'
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response = await fetchResponse(path, options)
  if (response.status === 401 && isRefreshable(path)) {
    try {
      await refreshCookies()
      response = await fetchResponse(path, options)
    } catch {
      emitAuthFailure(401)
      throw new ApiError('Your session has expired', 401)
    }
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) emitAuthFailure(response.status)
    throw new ApiError(await parseError(response), response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const get = <T>(path: string) => request<T>(path)
const post = <T>(path: string, body?: unknown, headers?: HeadersInit) => request<T>(path, {
  method: 'POST', headers,
  body: body === undefined ? undefined : JSON.stringify(body),
})
const put = <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body: JSON.stringify(body) })

export const api = {
  login: (username: string, password: string) => post<AuthUser>('/auth/login', { username, password }),
  refreshAuth: () => refreshCookies(),
  logout: async () => { await post<unknown>('/auth/logout') },
  me: () => get<AuthUser>('/auth/me'),
  health: () => get<Health>('/health'),
  healthFull: () => get<FullHealth>('/health/full'),
  safetyStatus: () => get<SafetyStatus>('/safety/status'),
  safetyEmergencyStop: (reason: string) => post<SafetyStatus>('/safety/emergency-stop', { reason, confirmation_text: 'EMERGENCY STOP' }),
  safetyEmergencyReset: () => post<SafetyStatus>('/safety/emergency-reset', { confirmation_text: 'RESET EMERGENCY STOP' }),
  safetyEvents: () => get<SafetyEvent[]>('/safety/events?limit=100'),
  mt5Status: () => get<MT5Status>('/mt5/status'),
  mt5Connect: () => post<MT5Status>('/mt5/connect'),
  mt5Disconnect: () => post<MT5Status>('/mt5/disconnect'),
  mt5Account: () => get<MT5Account>('/mt5/account'),
  mt5Terminal: () => get<MT5Terminal>('/mt5/terminal'),
  mt5Symbol: () => get<MT5Symbol>('/mt5/symbol'),
  tick: (symbol?: string) => get<MarketTick>(`/market/tick${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''}`),
  candles: (timeframe: string, count: number, symbol?: string) => {
    const query = new URLSearchParams({ timeframe, count: String(count) })
    if (symbol) query.set('symbol', symbol)
    return get<Candle[]>(`/market/candles?${query}`)
  },
  timeframes: () => get<{ timeframes: string[] }>('/market/timeframes'),
  indicator: (timeframe = 'M15') => get<Indicator>(`/analysis/indicators?timeframe=${timeframe}`),
  analysis: () => get<AnalysisSnapshot>('/analysis/multi-timeframe'),
  latestSignal: () => get<Signal>('/analysis/latest-signal'),
  generateSignal: () => post<Signal>('/analysis/signal', {}),
  riskSettings: () => get<RiskSettings>('/risk/settings'),
  updateRiskSettings: (body: Partial<RiskSettings>) => put<RiskSettings>('/risk/settings', body),
  riskStatus: () => get<RiskStatus>('/risk/status'),
  riskFeasibility: (signalId: string, signal?: AbortSignal) => request<RiskFeasibilityResult>(
    `/risk/feasibility?signal_id=${encodeURIComponent(signalId)}`, { method: 'GET', signal, cache: 'no-store' },
  ),
  tradePlans: () => get<TradePlan[]>('/risk/trade-plans?limit=100&offset=0'),
  tradePlan: (id: string) => get<TradePlan>(`/risk/trade-plans/${encodeURIComponent(id)}`),
  createTradePlan: (signalId: string) => post<TradePlan>('/risk/trade-plan', { signal_id: signalId }),
  paperStatus: () => get<PaperEngineStatus>('/paper/status'),
  paperAction: (action: 'start' | 'pause' | 'stop' | 'emergency-stop') => post<PaperEngineStatus>(`/paper/${action}`),
  paperAccount: () => get<PaperAccount>('/paper/account'),
  paperStatistics: () => get<PaperStatistics>('/paper/statistics'),
  paperPositions: () => get<PaperPosition[]>('/paper/positions?limit=200'),
  paperTrades: () => get<PaperTrade[]>('/paper/trades?limit=200'),
  paperEquity: () => get<PaperEquity[]>('/paper/equity-curve?limit=1000'),
  openPaperPosition: (tradePlanId: string) => post<PaperPosition>('/paper/open', { trade_plan_id: tradePlanId }),
  closePaperPosition: (positionId: string) => post<PaperPosition>(`/paper/positions/${encodeURIComponent(positionId)}/close`),
  backtests: () => get<BacktestSummary[]>('/backtests?limit=100&offset=0'),
  startBacktest: (body: BacktestRequest) => post<BacktestSummary>('/backtests', body),
  backtest: (id: string) => get<BacktestDetail>(`/backtests/${encodeURIComponent(id)}`),
  cancelBacktest: (id: string) => post<BacktestSummary>(`/backtests/${encodeURIComponent(id)}/cancel`),
  backtestTrades: (id: string) => get<BacktestTrade[]>(`/backtests/${encodeURIComponent(id)}/trades?limit=10000`),
  backtestEquity: (id: string) => get<BacktestEquity[]>(`/backtests/${encodeURIComponent(id)}/equity-curve?limit=10000`),
  backtestReport: (id: string) => get<{ backtest_id: string; report: Record<string, unknown>; warnings: string[]; created_at: string }>(`/backtests/${encodeURIComponent(id)}/report`),
  demoStatus: () => get<DemoStatus>('/demo/status'),
  demoAction: (action: 'start' | 'pause' | 'stop') => post<DemoStatus['engine']>(`/demo/${action}`),
  demoEmergencyStop: () => post<{ engine: DemoStatus['engine']; close_positions_requested: boolean; close_positions_effective: boolean }>('/demo/emergency-stop', { close_positions: false }),
  demoExecutions: () => get<DemoExecution[]>('/demo/executions?limit=100&offset=0'),
  demoOrders: () => get<DemoOrder[]>('/demo/orders?limit=100&offset=0'),
  demoPositions: () => get<DemoPosition[]>('/demo/positions?limit=100'),
  demoDeals: () => get<DemoDeal[]>('/demo/deals?limit=100'),
  executeDemo: (tradePlanId: string, idempotencyKey: string) => post<DemoExecution>('/demo/execute', {
    trade_plan_id: tradePlanId, idempotency_key: idempotencyKey, confirmation_text: 'EXECUTE DEMO ORDER',
  }, { 'X-Idempotency-Key': idempotencyKey }),
  closeDemoPosition: (positionId: string) => post<DemoOperation>(`/demo/positions/${encodeURIComponent(positionId)}/close`),
  breakEvenDemoPosition: (positionId: string) => post<DemoOperation>(`/demo/positions/${encodeURIComponent(positionId)}/break-even`),
  reconcileDemo: () => post<DemoReconciliation>('/demo/reconcile'),
}

export async function downloadBacktestCsv(id: string): Promise<void> {
  let response = await fetchResponse(`/backtests/${encodeURIComponent(id)}/export.csv`, { headers: { Accept: 'text/csv' } })
  if (response.status === 401) {
    try {
      await refreshCookies()
      response = await fetchResponse(`/backtests/${encodeURIComponent(id)}/export.csv`, { headers: { Accept: 'text/csv' } })
    } catch {
      emitAuthFailure(401)
      throw new ApiError('Your session has expired', 401)
    }
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) emitAuthFailure(response.status)
    throw new ApiError(await parseError(response), response.status)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `backtest-${id}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
