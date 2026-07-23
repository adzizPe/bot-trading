export type Direction = 'BUY' | 'SELL' | 'HOLD'
export type SignalStatus = 'CANDIDATE' | 'REJECTED' | 'HOLD'
export type BacktestStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
export type PaperEngineState =
  | 'STOPPED'
  | 'STARTING'
  | 'RUNNING'
  | 'PAUSED'
  | 'RISK_LOCKED'
  | 'ERROR'
  | 'EMERGENCY_STOPPED'

export interface Health {
  status: 'healthy'
  service: string
  environment: string
  database: 'connected'
}

export interface MT5Status {
  state: string
  connected: boolean
  demo_verified: boolean
  configured: boolean
  symbol: string
  last_error: string | null
}

export interface MT5Account {
  login: number
  is_demo: true
  trade_mode: 'demo'
  server: string | null
  company: string | null
  currency: string | null
  leverage: number | null
  balance: number | null
  equity: number | null
  margin: number | null
  margin_free: number | null
  margin_level: number | null
}

export interface MT5Terminal {
  connected: boolean
  name: string | null
  company: string | null
  build: number | null
  trade_allowed: boolean | null
  tradeapi_disabled: boolean | null
  dlls_allowed: boolean | null
  ping_last: number | null
}
export interface MT5Symbol {
  name: string
  description: string | null
  digits: number | null
  point: number | null
  trade_tick_size: number | null
  trade_tick_value: number | null
  volume_min: number | null
  volume_max: number | null
  volume_step: number | null
  spread: number | null
  trade_stops_level: number | null
  trade_freeze_level: number | null
  trade_contract_size: number | null
  visible: boolean | null
  select: boolean | null
}

export interface MarketTick {
  symbol: string
  bid: number
  ask: number
  spread_points: number
  spread_price: number
  timestamp: string
  connection_status: 'connected'
}

export interface Candle {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  tick_volume: number
  spread: number
  real_volume: number
  is_closed: true
}

export interface Indicator {
  symbol: string
  timeframe: string
  candle_time: string
  ema_fast: number
  ema_slow: number
  rsi: number
  atr: number
  market_structure: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  support_levels: number[]
  resistance_levels: number[]
  data_valid: boolean
}

export interface AnalysisSnapshot {
  symbol: string
  cutoff: string
  trend: Indicator
  setup: Indicator
  confirmation: Indicator
  confirmation_candle: 'BULLISH' | 'BEARISH' | 'NONE'
}

export interface ScoreFactor {
  factor: string
  passed: boolean
  weight: number
  points: number
}

export interface Signal {
  signal_id: string
  symbol: string
  direction: Direction
  strategy_name: string
  trend_timeframe: 'H1'
  setup_timeframe: 'M15'
  confirmation_timeframe: 'M5'
  timeframe: string
  entry_reference_price: number
  atr: number
  confidence_score: number
  score_factors: ScoreFactor[]
  reasons: string[]
  rejection_reasons: string[]
  candle_time: string
  created_at: string
  status: SignalStatus
}

export interface RiskSettings {
  settings_id: string
  risk_per_trade_percent: number
  max_daily_loss_percent: number
  max_daily_drawdown_percent: number
  max_consecutive_losses: number
  max_trades_per_day: number
  max_open_positions: number
  minimum_risk_reward: number
  target_risk_reward: number
  maximum_spread_points: number
  cooldown_minutes_after_loss: number
  use_equity_for_risk: boolean
  break_even_enabled: boolean
  trailing_stop_enabled: boolean
  stop_loss_method: 'ATR' | 'SWING' | 'SUPPORT_RESISTANCE'
  atr_multiplier: number
  session_enabled: boolean
  session_start_hour_utc: number
  session_end_hour_utc: number
  session_weekdays: number[]
  updated_at: string
}

export interface DailyRiskState {
  state_date: string
  starting_balance: number
  starting_equity: number
  peak_equity: number
  realized_loss: number
  floating_drawdown: number
  consecutive_losses: number
  trades_count: number
  open_positions: number
  cooldown_until: string | null
  risk_locked: boolean
  risk_lock_reasons: string[]
  updated_at: string
}

export interface RiskStatus {
  date: string
  account_available: boolean
  demo_verified: boolean
  risk_locked: boolean
  risk_lock_reasons: string[]
  state: DailyRiskState | null
}

export interface TradePlan {
  trade_plan_id: string
  signal_id: string
  symbol: string
  direction: Direction
  entry_price: number
  stop_loss: number
  take_profit: number
  stop_distance_price: number
  stop_distance_points: number
  risk_percent: number
  risk_amount: number
  position_size_lots: number
  risk_reward: number
  spread_points: number
  balance: number
  equity: number
  calculation_details: Record<string, unknown>
  validation_reasons: string[]
  rejection_reasons: string[]
  status: 'APPROVED' | 'REJECTED'
  created_at: string
}
export interface PaperAccount {
  account_id: string
  currency: string
  initial_balance: number
  balance: number
  equity: number
  free_margin: number
  used_margin: number
  floating_profit_loss: number
  realized_profit_loss: number
  total_profit: number
  total_loss: number
  created_at: string
  updated_at: string
}

export interface PaperEngineStatus {
  engine_id: string
  status: PaperEngineState
  last_error: string | null
  started_at: string | null
  last_cycle_at: string | null
  updated_at: string
  scheduler_running: boolean
}

export interface PaperPosition {
  position_id: string
  order_id: string
  trade_plan_id: string
  signal_id: string
  symbol: string
  direction: Exclude<Direction, 'HOLD'>
  volume: number
  entry_price: number
  current_price: number
  stop_loss: number
  initial_stop_loss: number
  take_profit: number
  floating_profit_loss: number
  commission: number
  swap: number
  risk_amount: number
  point: number
  tick_size: number
  tick_value: number
  stop_change_log: Record<string, unknown>[]
  status: 'OPEN' | 'CLOSED'
  opened_at: string
  closed_at: string | null
  close_price: number | null
  close_reason: string | null
}

export interface PaperTrade {
  trade_id: string
  position_id: string
  trade_plan_id: string
  signal_id: string
  symbol: string
  direction: Exclude<Direction, 'HOLD'>
  volume: number
  entry_price: number
  close_price: number
  gross_profit_loss: number
  commission: number
  swap: number
  net_profit_loss: number
  close_reason: string
  opened_at: string
  closed_at: string
}

export interface PaperStatistics {
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  gross_profit: number
  gross_loss: number
  net_profit: number
  profit_factor: number
  average_win: number
  average_loss: number
  expectancy: number
  maximum_drawdown: number
  consecutive_wins: number
  consecutive_losses: number
  current_balance: number
  current_equity: number
}

export interface PaperEquity {
  snapshot_id: string
  balance: number
  equity: number
  floating_profit_loss: number
  drawdown: number
  captured_at: string
}

export interface BacktestRequest {
  symbol: string
  start_date: string
  end_date: string
  initial_balance: number
  risk_per_trade_percent: number
  maximum_open_positions: number
  spread_mode: 'FIXED' | 'HISTORICAL'
  fixed_spread_points: number
  use_historical_spread: boolean
  slippage_points: number
  commission_per_lot: number
  swap_long_per_lot: number
  swap_short_per_lot: number
  minimum_risk_reward: number
  trading_sessions: { start: string; end: string; weekdays: number[] }[]
  strategy_name: string
  strategy_settings: Record<string, number | boolean | string>
  risk_settings: Record<string, number | boolean | string>
  close_open_positions_at_end: boolean
  same_bar_policy: 'SL_FIRST' | 'TP_FIRST'
  source: 'MT5' | 'CSV'
  csv_path?: string
}

export interface BacktestSummary {
  backtest_id: string
  symbol: string
  source: string
  strategy_name: string
  status: BacktestStatus
  processed_candles: number
  total_candles: number
  progress_percent: number
  current_time: string | null
  estimated_remaining_seconds: number | null
  cancel_requested: boolean
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface BacktestDetail extends BacktestSummary {
  configuration: Record<string, unknown>
  symbol_specification: Record<string, unknown> | null
  statistics: Record<string, number | null> | null
}

export interface BacktestTrade {
  backtest_id: string
  trade_id: string
  position_id: string
  signal_id: string | null
  trade_plan_id: string
  symbol: string
  direction: string
  volume: number
  entry_price: number
  exit_price: number
  stop_loss: number
  take_profit: number
  gross_pnl: number
  commission: number
  swap: number
  net_pnl: number
  exit_reason: string
  opened_at: string
  closed_at: string
}

export interface BacktestEquity {
  backtest_id: string
  snapshot_id: string
  timestamp: string
  balance: number
  equity: number
  floating_pnl: number
  drawdown: number
}


export type DemoEngineState =
  | 'STOPPED'
  | 'STARTING'
  | 'RUNNING'
  | 'PAUSED'
  | 'RISK_LOCKED'
  | 'CONNECTION_LOST'
  | 'ERROR'
  | 'EMERGENCY_STOPPED'

export interface DemoStatus {
  enabled: boolean
  engine: { engine_id: string; status: DemoEngineState; last_error: string | null; updated_at: string }
  broker: MT5Status
}

export interface DemoExecution {
  execution_request_id: string
  idempotency_key: string
  trade_plan_id: string
  signal_id: string
  symbol: string
  direction: Exclude<Direction, 'HOLD'>
  requested_volume: number | null
  requested_price: number | null
  requested_sl: number
  requested_tp: number
  actual_order_ticket: number | null
  actual_deal_ticket: number | null
  actual_position_ticket: number | null
  retcode: number | null
  retcode_message: string | null
  broker_comment: string | null
  sanitized_request: Record<string, unknown>
  sanitized_response: Record<string, unknown> | null
  status: 'RESERVED' | 'ACCEPTED' | 'REJECTED' | 'UNKNOWN'
  reconciliation_required: boolean
  created_at: string
  executed_at: string | null
  outcome?: string | null
}

export interface DemoOrder {
  order_id: string
  execution_request_id: string
  trade_plan_id: string
  broker_order_ticket: number | null
  broker_deal_ticket: number | null
  symbol: string
  direction: Exclude<Direction, 'HOLD'>
  volume: number
  requested_price: number
  fill_price: number | null
  stop_loss: number
  take_profit: number
  status: string
  outcome: string
  broker_retcode: number | null
  reconciliation_required: boolean
  created_at: string
  updated_at: string
}

export interface DemoPosition {
  position_id: string
  broker_position_ticket: number
  order_id: string | null
  trade_plan_id: string | null
  symbol: string
  direction: Exclude<Direction, 'HOLD'>
  volume: number
  entry_price: number
  current_price: number
  stop_loss: number
  take_profit: number
  magic: number
  status: string
  missing_since: string | null
  opened_at: string
  closed_at: string | null
  updated_at: string
}

export interface DemoDeal {
  trade_id: string
  broker_deal_ticket: number
  broker_position_ticket: number | null
  position_id: string | null
  symbol: string
  direction: Exclude<Direction, 'HOLD'>
  volume: number
  price: number
  profit: number
  commission: number
  swap: number
  magic: number
  executed_at: string
  created_at: string
}

export interface DemoOperation {
  position_id?: string | null
  outcome: string
  retcode?: number | null
  retcode_name?: string | null
  retcode_message?: string | null
  reconciliation_required?: boolean
  order?: number | null
  deal?: number | null
}

export interface DemoReconciliation {
  run_id: string
  status: string
  adopted_count: number
  updated_count: number
  missing_count: number
  trades_count: number
  started_at: string
  completed_at: string | null
}
