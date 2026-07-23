import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Landmark, RefreshCw, Shield, WalletCards } from 'lucide-react'
import { api, loadPreferences, sanitizeMessage } from '../api/client'
import { EquityChart } from '../components/charts'
import {
  EmptyState,
  ErrorAlert,
  LoadingSkeleton,
  MetricCard,
  PnLDisplay,
  PageHeader,
  Panel,
  PriceDisplay,
  SignalCard,
  StatusBadge,
  TradePlanCard,
  money,
  number,
} from '../components/ui'

export function OverviewPage() {
  const queryClient = useQueryClient()
  const interval = loadPreferences().refreshInterval
  const common = { refetchInterval: interval, retry: 1 }
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, ...common })
  const mt5 = useQuery({ queryKey: ['mt5-status'], queryFn: api.mt5Status, ...common })
  const tick = useQuery({ queryKey: ['tick'], queryFn: () => api.tick(), ...common, enabled: mt5.data?.connected === true })
  const paperStatus = useQuery({ queryKey: ['paper-status'], queryFn: api.paperStatus, ...common })
  const account = useQuery({ queryKey: ['paper-account'], queryFn: api.paperAccount, ...common })
  const statistics = useQuery({ queryKey: ['paper-statistics'], queryFn: api.paperStatistics, ...common })
  const positions = useQuery({ queryKey: ['paper-positions'], queryFn: api.paperPositions, ...common })
  const equity = useQuery({ queryKey: ['paper-equity'], queryFn: api.paperEquity, ...common })
  const risk = useQuery({ queryKey: ['risk-status'], queryFn: api.riskStatus, ...common })
  const signal = useQuery({ queryKey: ['latest-signal'], queryFn: api.latestSignal, retry: false })
  const plans = useQuery({ queryKey: ['trade-plans'], queryFn: api.tradePlans, retry: 1 })
  const backtests = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, retry: 1 })
  const loading = health.isLoading || mt5.isLoading || account.isLoading
  const firstError = [health.error, mt5.error, account.error].find(Boolean)
  const latestPlan = plans.data?.[0]
  const latestBacktest = backtests.data?.[0]
  const openPositions = positions.data?.filter((item) => item.status === 'OPEN') || []

  return (
    <div className="page-stack">
      <PageHeader title="System overview" description="Snapshot operasional backend, MT5 demo, risk, paper trading, dan backtest." actions={
        <button className="button button-primary" onClick={() => queryClient.invalidateQueries()}><RefreshCw size={16} />Refresh</button>
      } />
      {loading && <LoadingSkeleton rows={3} />}
      {firstError && <ErrorAlert message={sanitizeMessage(firstError)} retry={() => queryClient.invalidateQueries()} />}
      <div className="metric-grid">
        <MetricCard label="Backend" value={<StatusBadge value={health.data?.status || 'unavailable'} />} detail={health.data?.database} icon={<Activity />} />
        <MetricCard label="MT5 demo" value={<StatusBadge value={mt5.data?.connected ? 'connected' : 'disconnected'} />} detail={mt5.data?.demo_verified ? 'Demo verified' : 'No verified account'} icon={<Landmark />} />
        <MetricCard label="Paper engine" value={<StatusBadge value={paperStatus.data?.status || 'unknown'} />} detail={paperStatus.data?.scheduler_running ? 'Scheduler active' : 'Scheduler idle'} icon={<WalletCards />} />
        <MetricCard label="Daily risk" value={<StatusBadge value={risk.data?.risk_locked ? 'locked' : 'available'} />} detail={`${risk.data?.state?.trades_count || 0} trades today`} icon={<Shield />} />
      </div>
      <div className="market-strip">
        <div><span>Active symbol</span><strong>{tick.data?.symbol || mt5.data?.symbol || 'XAUUSD'}</strong></div>
        <PriceDisplay label="BID" value={tick.data?.bid} />
        <PriceDisplay label="ASK" value={tick.data?.ask} />
        <div><span>Spread</span><strong>{number(tick.data?.spread_points)} pts</strong></div>
      </div>
      <div className="metric-grid">
        <MetricCard label="Paper balance" value={money(account.data?.balance)} />
        <MetricCard label="Paper equity" value={money(account.data?.equity)} />
        <MetricCard label="Floating PnL" value={account.data ? <PnLDisplay value={account.data.floating_profit_loss} /> : '—'} />
        <MetricCard label="Realized PnL" value={account.data ? <PnLDisplay value={account.data.realized_profit_loss} /> : '—'} />
        <MetricCard label="Open positions" value={openPositions.length} />
        <MetricCard label="Trades" value={statistics.data?.total_trades ?? '—'} detail={`Win rate ${number(statistics.data?.win_rate)}%`} />
      </div>
      <div className="two-column">
        <Panel title="Paper equity" subtitle="Balance dan equity simulasi; bukan akun broker.">
          <EquityChart points={equity.data || []} />
        </Panel>
        <Panel title="Risk summary" subtitle="Daily controls dari backend.">
          {risk.data?.state ? <div className="key-values vertical">
            <span>Starting balance<strong>{money(risk.data.state.starting_balance)}</strong></span>
            <span>Peak equity<strong>{money(risk.data.state.peak_equity)}</strong></span>
            <span>Realized loss<strong>{money(risk.data.state.realized_loss)}</strong></span>
            <span>Floating drawdown<strong>{money(risk.data.state.floating_drawdown)}</strong></span>
            <span>Consecutive losses<strong>{risk.data.state.consecutive_losses}</strong></span>
          </div> : <EmptyState title="Risk state belum tersedia" description="Hubungkan akun demo agar backend dapat membuat snapshot risk." />}
        </Panel>
      </div>
      <div className="two-column">
        {signal.data ? <SignalCard signal={signal.data} /> : <Panel title="Latest signal"><EmptyState title="Belum ada signal" description="Generate signal dari halaman Analysis." /></Panel>}
        {latestPlan ? <TradePlanCard plan={latestPlan} /> : <Panel title="Latest trade plan"><EmptyState title="Belum ada trade plan" description="Buat plan dari candidate signal. Plan tidak mengirim order." /></Panel>}
      </div>
      <Panel title="Latest backtest" subtitle="Ringkasan job simulasi terbaru.">
        {latestBacktest ? <div className="backtest-summary">
          <div><strong>{latestBacktest.symbol}</strong><span>{latestBacktest.strategy_name}</span></div>
          <StatusBadge value={latestBacktest.status} />
          <div className="progress"><span style={{ width: `${latestBacktest.progress_percent}%` }} /></div>
          <strong>{number(latestBacktest.progress_percent)}%</strong>
        </div> : <EmptyState title="Belum ada backtest" description="Jalankan simulasi historis dari halaman Backtesting." />}
      </Panel>
    </div>
  )
}
