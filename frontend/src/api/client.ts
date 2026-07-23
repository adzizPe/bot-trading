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
  RiskSettings,
  RiskStatus,
  Signal,
  TradePlan,
} from './types'

const DEFAULT_API = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const SECRET_PATTERN = /(password|secret|token|authorization|traceback)/gi

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface DashboardPreferences {
  apiBaseUrl: string
  websocketUrl: string
  refreshInterval: number
  timezone: string
  compact: boolean
}
const defaultPreferences: DashboardPreferences = {
  apiBaseUrl: DEFAULT_API,
  websocketUrl: '',
  refreshInterval: 15000,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  compact: false,
}

export function sanitizeMessage(value: unknown): string {
  const raw = value instanceof Error ? value.message : String(value || 'Request failed')
  const singleLine = raw.split('\n')[0].slice(0, 240)
  return SECRET_PATTERN.test(singleLine)
    ? 'Sensitive error details were hidden'
    : singleLine.replace(SECRET_PATTERN, '[hidden]')
}

export function loadPreferences(): DashboardPreferences {
  try {
    const stored = JSON.parse(localStorage.getItem('dashboard-preferences') || '{}') as Partial<DashboardPreferences>
    return {
      apiBaseUrl: typeof stored.apiBaseUrl === 'string' ? stored.apiBaseUrl : defaultPreferences.apiBaseUrl,
      websocketUrl: typeof stored.websocketUrl === 'string' ? stored.websocketUrl : '',
      refreshInterval: typeof stored.refreshInterval === 'number' ? stored.refreshInterval : 15000,
      timezone: typeof stored.timezone === 'string' ? stored.timezone : defaultPreferences.timezone,
      compact: stored.compact === true,
    }
  } catch {
    return defaultPreferences
  }
}

export function savePreferences(value: DashboardPreferences): void {
  const safe: DashboardPreferences = {
    apiBaseUrl: value.apiBaseUrl,
    websocketUrl: value.websocketUrl,
    refreshInterval: Math.max(5000, value.refreshInterval),
    timezone: value.timezone,
    compact: value.compact,
  }
  localStorage.setItem('dashboard-preferences', JSON.stringify(safe))
}

export function apiUrl(path: string): string {
  return `${loadPreferences().apiBaseUrl.replace(/\/$/, '')}${path}`
}

export function websocketUrl(path: string): string {
  const configured = loadPreferences().websocketUrl.trim()
  if (configured) return `${configured.replace(/\/$/, '')}${path}`
  const base = apiUrl(path)
  const url = new URL(base, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

async function parseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown }
    if (typeof data.detail === 'string') return sanitizeMessage(data.detail)
    if (Array.isArray(data.detail)) return 'Some fields are invalid. Review the form.'
  } catch {
    // Deliberately hide non-JSON server output and stack traces.
  }
  return `Request failed (HTTP ${response.status})`
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(apiUrl(path), { ...options, headers })
  } catch (error) {
    throw new ApiError(sanitizeMessage(error), 0)
  }
  if (!response.ok) throw new ApiError(await parseError(response), response.status)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const get = <T>(path: string) => request<T>(path)
const post = <T>(path: string, body?: unknown) => request<T>(path, {
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
})
const put = <T>(path: string, body: unknown) => request<T>(path, {
  method: 'PUT',
  body: JSON.stringify(body),
})

let demoAdminToken = ''

export function setDemoAdminToken(token: string): void {
  demoAdminToken = token.trim()
}

export function clearDemoAdminToken(): void {
  demoAdminToken = ''
}

export function hasDemoAdminToken(): boolean {
  return demoAdminToken.length > 0
}

const demoRequest = <T>(path: string, options: RequestInit = {}) => {
  if (!demoAdminToken) throw new ApiError('Demo admin session is not active', 401)
  const headers = new Headers(options.headers)
  headers.set('X-Admin-Token', demoAdminToken)
  return request<T>(path, { ...options, headers })
}
const demoGet = <T>(path: string) => demoRequest<T>(path)
const demoPost = <T>(path: string, body?: unknown, idempotencyKey?: string) => {
  const headers = new Headers()
  if (idempotencyKey) headers.set('X-Idempotency-Key', idempotencyKey)
  return demoRequest<T>(path, {
    method: 'POST', headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const api = {
  health: () => get<Health>('/health'),
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
  demoStatus: () => demoGet<DemoStatus>('/demo/status'),
  demoAction: (action: 'start' | 'pause' | 'stop') => demoPost<DemoStatus['engine']>(`/demo/${action}`),
  demoEmergencyStop: () => demoPost<{ engine: DemoStatus['engine']; close_positions_requested: boolean; close_positions_effective: boolean }>('/demo/emergency-stop', { close_positions: false }),
  demoExecutions: () => demoGet<DemoExecution[]>('/demo/executions?limit=100&offset=0'),
  demoOrders: () => demoGet<DemoOrder[]>('/demo/orders?limit=100&offset=0'),
  demoPositions: () => demoGet<DemoPosition[]>('/demo/positions?limit=100'),
  demoDeals: () => demoGet<DemoDeal[]>('/demo/deals?limit=100'),
  executeDemo: (tradePlanId: string, idempotencyKey: string) => demoPost<DemoExecution>('/demo/execute', {
    trade_plan_id: tradePlanId,
    idempotency_key: idempotencyKey,
    confirmation_text: 'EXECUTE DEMO ORDER',
  }, idempotencyKey),
  closeDemoPosition: (positionId: string) => demoPost<DemoOperation>(`/demo/positions/${encodeURIComponent(positionId)}/close`),
  breakEvenDemoPosition: (positionId: string) => demoPost<DemoOperation>(`/demo/positions/${encodeURIComponent(positionId)}/break-even`),
  reconcileDemo: () => demoPost<DemoReconciliation>('/demo/reconcile'),
}

export async function downloadBacktestCsv(id: string): Promise<void> {
  const response = await fetch(apiUrl(`/backtests/${encodeURIComponent(id)}/export.csv`))
  if (!response.ok) throw new ApiError(await parseError(response), response.status)
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
