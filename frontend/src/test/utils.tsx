import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import { AppRoutes } from '../App'
import { ApiError, api } from '../api/client'
import type { AnalysisSnapshot, BacktestDetail, BacktestSummary, DemoDeal, DemoExecution, DemoOrder, DemoPosition, DemoStatus, MarketTick, PaperAccount, PaperEngineStatus, PaperPosition, RiskSettings, Signal, TradePlan } from '../api/types'
import { ToastProvider } from '../components/ui'

export const tick: MarketTick = {
  symbol: 'XAUUSDm', bid: 3000.1, ask: 3000.3, spread_points: 20,
  spread_price: 0.2, timestamp: '2026-07-22T10:00:00Z', connection_status: 'connected',
}

export const signal: Signal = {
  signal_id: 'signal-1', symbol: 'XAUUSDm', direction: 'BUY', strategy_name: 'EMA_RSI_ATR_MTF_V1',
  trend_timeframe: 'H1', setup_timeframe: 'M15', confirmation_timeframe: 'M5', timeframe: 'H1/M15/M5',
  entry_reference_price: 3000.2, atr: 3.5, confidence_score: 90, score_factors: [],
  reasons: ['Trend aligned'], rejection_reasons: [], candle_time: '2026-07-22T09:55:00Z',
  created_at: '2026-07-22T10:00:00Z', status: 'CANDIDATE',
}

export const plan: TradePlan = {
  trade_plan_id: 'plan-1', signal_id: 'signal-1', symbol: 'XAUUSDm', direction: 'BUY',
  entry_price: 3000.2, stop_loss: 2997.2, take_profit: 3006.2, stop_distance_price: 3,
  stop_distance_points: 300, risk_percent: 1, risk_amount: 100, position_size_lots: .33,
  risk_reward: 2, spread_points: 20, balance: 10000, equity: 10000,
  calculation_details: { source: 'MT5 demo read-only' }, validation_reasons: ['Risk locks passed'],
  rejection_reasons: [], status: 'APPROVED', created_at: '2026-07-22T10:01:00Z',
}

export const paperStatus: PaperEngineStatus = {
  engine_id: 'paper-1', status: 'STOPPED', last_error: null, started_at: null,
  last_cycle_at: null, updated_at: '2026-07-22T10:00:00Z', scheduler_running: false,
}

export const paperAccount: PaperAccount = {
  account_id: 'account-1', currency: 'USD', initial_balance: 10000, balance: 10100,
  equity: 10120, free_margin: 10120, used_margin: 0, floating_profit_loss: 20,
  realized_profit_loss: 100, total_profit: 150, total_loss: 50,
  created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-22T10:00:00Z',
}
export const position: PaperPosition = {
  position_id: 'position-1', order_id: 'paper-order-1', trade_plan_id: 'plan-1', signal_id: 'signal-1',
  symbol: 'XAUUSDm', direction: 'BUY', volume: .33, entry_price: 3000.2, current_price: 3001.2,
  stop_loss: 2997.2, initial_stop_loss: 2997.2, take_profit: 3006.2, floating_profit_loss: 33,
  commission: 0, swap: 0, risk_amount: 100, point: .001, tick_size: .001, tick_value: .1,
  stop_change_log: [], status: 'OPEN', opened_at: '2026-07-22T10:00:00Z', closed_at: null,
  close_price: null, close_reason: null,
}

export const riskSettings: RiskSettings = {
  settings_id: 'risk-1', risk_per_trade_percent: 1, max_daily_loss_percent: 3,
  max_daily_drawdown_percent: 5, max_consecutive_losses: 3, max_trades_per_day: 5,
  max_open_positions: 1, minimum_risk_reward: 1.5, target_risk_reward: 2,
  maximum_spread_points: 300, cooldown_minutes_after_loss: 30, use_equity_for_risk: true,
  break_even_enabled: false, trailing_stop_enabled: false, stop_loss_method: 'ATR',
  atr_multiplier: 1.5, session_enabled: false, session_start_hour_utc: 0,
  session_end_hour_utc: 24, session_weekdays: [0, 1, 2, 3, 4], updated_at: '2026-07-22T10:00:00Z',
}

const indicator = (timeframe: string) => ({
  symbol: 'XAUUSDm', timeframe, candle_time: '2026-07-22T10:00:00Z', ema_fast: 3001,
  ema_slow: 2999, rsi: 55, atr: 3.5, market_structure: 'BULLISH' as const,
  support_levels: [2990], resistance_levels: [3010], data_valid: true,
})
export const analysis: AnalysisSnapshot = {
  symbol: 'XAUUSDm', cutoff: '2026-07-22T10:00:00Z', trend: indicator('H1'),
  setup: indicator('M15'), confirmation: indicator('M5'), confirmation_candle: 'BULLISH',
}

export const backtest: BacktestSummary = {
  backtest_id: 'backtest-1', symbol: 'XAUUSDm', source: 'MT5', strategy_name: 'EMA_RSI_ATR_MTF_V1',
  status: 'RUNNING', processed_candles: 50, total_candles: 100, progress_percent: 50,
  current_time: '2026-07-20T00:00:00Z', estimated_remaining_seconds: 5,
  cancel_requested: false, error_message: null, created_at: '2026-07-22T10:00:00Z',
  started_at: '2026-07-22T10:00:01Z', completed_at: null, updated_at: '2026-07-22T10:00:05Z',
}
export const backtestDetail: BacktestDetail = {
  ...backtest, configuration: {}, symbol_specification: {}, statistics: null,
}

export const demoStatus: DemoStatus = {
  enabled: true,
  engine: { engine_id: 'default', status: 'RUNNING', last_error: null, updated_at: '2026-07-25T10:00:00Z' },
  broker: { state: 'connected', connected: true, demo_verified: true, configured: true, symbol: 'XAUUSDm', last_error: null },
}

export const demoExecution: DemoExecution = {
  execution_request_id: 'execution-1', idempotency_key: 'ui-test-key',
  trade_plan_id: 'plan-1', signal_id: 'signal-1', symbol: 'XAUUSDm', direction: 'BUY',
  requested_volume: .33, requested_price: 3000.3, requested_sl: 2997.2, requested_tp: 3006.2,
  actual_order_ticket: 1001, actual_deal_ticket: 1002, actual_position_ticket: 1003,
  retcode: 10009, retcode_message: 'Done', broker_comment: 'bot-demo',
  sanitized_request: { symbol: 'XAUUSDm', direction: 'BUY' }, sanitized_response: { retcode_name: 'DONE' },
  status: 'ACCEPTED', reconciliation_required: false,
  created_at: '2026-07-25T10:00:00Z', executed_at: '2026-07-25T10:00:01Z', outcome: 'ACCEPTED',
}

export const demoOrder: DemoOrder = {
  order_id: 'demo-order-1', execution_request_id: 'execution-1', trade_plan_id: 'plan-1',
  broker_order_ticket: 1001, broker_deal_ticket: 1002, symbol: 'XAUUSDm', direction: 'BUY',
  volume: .33, requested_price: 3000.3, fill_price: 3000.3, stop_loss: 2997.2,
  take_profit: 3006.2, status: 'SUBMITTED', outcome: 'ACCEPTED', broker_retcode: 10009,
  reconciliation_required: false, created_at: '2026-07-25T10:00:00Z', updated_at: '2026-07-25T10:00:01Z',
}

export const demoPosition: DemoPosition = {
  position_id: 'demo-position-1', broker_position_ticket: 1003, order_id: 'demo-order-1',
  trade_plan_id: 'plan-1', symbol: 'XAUUSDm', direction: 'BUY', volume: .33,
  entry_price: 3000.3, current_price: 3001, stop_loss: 2997.2, take_profit: 3006.2,
  magic: 9072026, status: 'OPEN', missing_since: null, opened_at: '2026-07-25T10:00:01Z',
  closed_at: null, updated_at: '2026-07-25T10:00:01Z',
}

export const demoDeal: DemoDeal = {
  trade_id: 'demo-deal-1', broker_deal_ticket: 1002, broker_position_ticket: 1003,
  position_id: 'demo-position-1', symbol: 'XAUUSDm', direction: 'BUY', volume: .33,
  price: 3000.3, profit: 10, commission: -1, swap: 0, magic: 9072026,
  executed_at: '2026-07-25T10:00:01Z', created_at: '2026-07-25T10:00:01Z',
}

export function mockApi() {
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'healthy', service: 'Trading Bot', environment: 'test', database: 'connected' })
  vi.spyOn(api, 'mt5Status').mockResolvedValue({ state: 'connected', connected: true, demo_verified: true, configured: true, symbol: 'XAUUSDm', last_error: null })
  vi.spyOn(api, 'tick').mockResolvedValue(tick)
  vi.spyOn(api, 'mt5Account').mockResolvedValue({ login: 12345678, is_demo: true, trade_mode: 'demo', server: 'Broker-Demo', company: 'Broker', currency: 'USD', leverage: 100, balance: 10000, equity: 10000, margin: 0, margin_free: 10000, margin_level: 0 })
  vi.spyOn(api, 'mt5Terminal').mockResolvedValue({ connected: true, name: 'Terminal', company: 'Broker', build: 5000, trade_allowed: true, tradeapi_disabled: false, dlls_allowed: false, ping_last: 10 })
  vi.spyOn(api, 'mt5Symbol').mockResolvedValue({ name: 'XAUUSDm', description: 'Gold', digits: 3, point: .001, trade_tick_size: .001, trade_tick_value: .1, volume_min: .01, volume_max: 100, volume_step: .01, spread: 20, trade_stops_level: 0, trade_freeze_level: 0, trade_contract_size: 100, visible: true, select: true })
  vi.spyOn(api, 'timeframes').mockResolvedValue({ timeframes: ['M1', 'M5', 'H1'] })
  vi.spyOn(api, 'candles').mockResolvedValue([])
  vi.spyOn(api, 'analysis').mockResolvedValue(analysis)
  vi.spyOn(api, 'latestSignal').mockResolvedValue(signal)
  vi.spyOn(api, 'generateSignal').mockResolvedValue(signal)
  vi.spyOn(api, 'riskSettings').mockResolvedValue(riskSettings)
  vi.spyOn(api, 'updateRiskSettings').mockResolvedValue(riskSettings)
  vi.spyOn(api, 'riskStatus').mockResolvedValue({ date: '2026-07-22', account_available: true, demo_verified: true, risk_locked: false, risk_lock_reasons: [], state: null })
  vi.spyOn(api, 'tradePlans').mockResolvedValue([plan])
  vi.spyOn(api, 'tradePlan').mockResolvedValue(plan)
  vi.spyOn(api, 'createTradePlan').mockResolvedValue(plan)
  vi.spyOn(api, 'paperStatus').mockResolvedValue(paperStatus)
  vi.spyOn(api, 'paperAction').mockImplementation(async (value) => ({ ...paperStatus, status: value === 'start' ? 'RUNNING' : value === 'pause' ? 'PAUSED' : value === 'stop' ? 'STOPPED' : 'EMERGENCY_STOPPED' }))
  vi.spyOn(api, 'paperAccount').mockResolvedValue(paperAccount)
  vi.spyOn(api, 'paperStatistics').mockResolvedValue({ total_trades: 1, winning_trades: 1, losing_trades: 0, win_rate: 100, gross_profit: 100, gross_loss: 0, net_profit: 100, profit_factor: 1, average_win: 100, average_loss: 0, expectancy: 100, maximum_drawdown: 0, consecutive_wins: 1, consecutive_losses: 0, current_balance: 10100, current_equity: 10120 })
  vi.spyOn(api, 'paperPositions').mockResolvedValue([position])
  vi.spyOn(api, 'paperTrades').mockResolvedValue([])
  vi.spyOn(api, 'paperEquity').mockResolvedValue([])
  vi.spyOn(api, 'openPaperPosition').mockResolvedValue(position)
  vi.spyOn(api, 'closePaperPosition').mockResolvedValue({ ...position, status: 'CLOSED' })
  vi.spyOn(api, 'backtests').mockResolvedValue([backtest])
  vi.spyOn(api, 'startBacktest').mockResolvedValue(backtest)
  vi.spyOn(api, 'backtest').mockResolvedValue(backtestDetail)
  vi.spyOn(api, 'cancelBacktest').mockResolvedValue({ ...backtest, status: 'CANCELLED' })
  vi.spyOn(api, 'backtestTrades').mockResolvedValue([])
  vi.spyOn(api, 'backtestEquity').mockResolvedValue([])
  vi.spyOn(api, 'backtestReport').mockResolvedValue({ backtest_id: 'backtest-1', report: {}, warnings: [], created_at: '2026-07-22T10:00:00Z' })
  vi.spyOn(api, 'mt5Connect').mockResolvedValue({ state: 'connected', connected: true, demo_verified: true, configured: true, symbol: 'XAUUSDm', last_error: null })
  vi.spyOn(api, 'mt5Disconnect').mockResolvedValue({ state: 'disconnected', connected: false, demo_verified: false, configured: true, symbol: 'XAUUSDm', last_error: null })
  vi.spyOn(api, 'demoStatus').mockResolvedValue(demoStatus)
  vi.spyOn(api, 'demoAction').mockImplementation(async (value) => ({ ...demoStatus.engine, status: value === 'start' ? 'RUNNING' : value === 'pause' ? 'PAUSED' : 'STOPPED' }))
  vi.spyOn(api, 'demoEmergencyStop').mockResolvedValue({ engine: { ...demoStatus.engine, status: 'EMERGENCY_STOPPED' }, close_positions_requested: false, close_positions_effective: false })
  vi.spyOn(api, 'demoExecutions').mockResolvedValue([demoExecution])
  vi.spyOn(api, 'demoOrders').mockResolvedValue([demoOrder])
  vi.spyOn(api, 'demoPositions').mockResolvedValue([demoPosition])
  vi.spyOn(api, 'demoDeals').mockResolvedValue([demoDeal])
  vi.spyOn(api, 'executeDemo').mockResolvedValue(demoExecution)
  vi.spyOn(api, 'closeDemoPosition').mockResolvedValue({ position_id: demoPosition.position_id, outcome: 'ACCEPTED', retcode: 10009 })
  vi.spyOn(api, 'breakEvenDemoPosition').mockResolvedValue({ position_id: demoPosition.position_id, outcome: 'ACCEPTED', retcode: 10009 })
  vi.spyOn(api, 'reconcileDemo').mockResolvedValue({ run_id: 'run-1', status: 'COMPLETED', adopted_count: 0, updated_count: 1, missing_count: 0, trades_count: 1, started_at: '2026-07-25T10:00:00Z', completed_at: '2026-07-25T10:00:01Z' })
}

export function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  window.history.pushState({}, '', path)
  return render(<QueryClientProvider client={client}><ToastProvider><MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter></ToastProvider></QueryClientProvider>)
}

export const noLatestSignal = new ApiError('No signal has been stored', 404)
