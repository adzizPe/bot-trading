import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, setDemoAdminToken } from '../api/client'
import { backtest, backtestDetail, mockApi, paperStatus, position, renderRoute } from '../test/utils'

describe('risk and trade plan flows', () => {
  beforeEach(() => mockApi())

  it('validates risk settings before saving', async () => {
    const user = userEvent.setup()
    renderRoute('/risk')
    const input = await screen.findByLabelText('Risk per trade (%)')
    await user.clear(input)
    await user.type(input, '0')
    await user.click(screen.getByRole('button', { name: 'Simpan pengaturan' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Risk per trade (%) tidak valid')
    expect(api.updateRiskSettings).not.toHaveBeenCalled()
  })

  it('shows paper and guarded manual-demo actions for an approved plan', async () => {
    const user = userEvent.setup()
    setDemoAdminToken('runtime-demo-admin-secret')
    renderRoute('/trade-plans')
    await user.click(await screen.findByRole('button', { name: 'Detail' }))
    expect(screen.getByRole('dialog', { name: 'Trade plan detail' })).toHaveTextContent('Risk locks passed')
    expect(screen.getByRole('button', { name: 'Open paper position' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Execute Demo' }))
    const confirm = screen.getByRole('button', { name: 'Konfirmasi' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Confirmation text'), 'EXECUTE DEMO ORDER')
    await user.click(confirm)
    await waitFor(() => expect(api.executeDemo).toHaveBeenCalledTimes(1))
    const [tradePlanId, idempotencyKey] = vi.mocked(api.executeDemo).mock.calls[0]
    expect(tradePlanId).toBe('plan-1')
    expect(idempotencyKey).toMatch(/^ui-/)
  })
})

describe('paper engine controls', () => {
  beforeEach(() => mockApi())

  it('starts, pauses, and stops through explicit paper endpoints', async () => {
    const user = userEvent.setup()
    let current = paperStatus
    vi.mocked(api.paperStatus).mockImplementation(async () => current)
    vi.mocked(api.paperAction).mockImplementation(async (action) => {
      current = { ...current, status: action === 'start' ? 'RUNNING' : action === 'pause' ? 'PAUSED' : 'STOPPED' }
      return current
    })
    renderRoute('/paper')
    await user.click(await screen.findByRole('button', { name: 'Start' }))
    await waitFor(() => expect(api.paperAction).toHaveBeenCalledWith('start'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(api.paperAction).toHaveBeenCalledWith('pause'))
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Stop paper engine?')
    await user.click(screen.getByRole('button', { name: 'Konfirmasi' }))
    await waitFor(() => expect(api.paperAction).toHaveBeenCalledWith('stop'))
  })

  it('protects emergency stop with typed confirmation', async () => {
    const user = userEvent.setup()
    renderRoute('/paper')
    await user.click(await screen.findByRole('button', { name: 'Emergency stop' }))
    const confirm = screen.getByRole('button', { name: 'Konfirmasi' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Confirmation text'), 'EMERGENCY STOP')
    expect(confirm).toBeEnabled()
    await user.click(confirm)
    await waitFor(() => expect(api.paperAction).toHaveBeenCalledWith('emergency-stop'))
  })

  it('renders open paper positions with non-color PnL labels', async () => {
    vi.mocked(api.paperPositions).mockResolvedValue([position])
    renderRoute('/paper')
    expect(await screen.findByText('XAUUSDm · BUY')).toBeInTheDocument()
    expect(screen.getAllByLabelText('Profit').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })
})
describe('backtest workflow', () => {
  beforeEach(() => mockApi())

  it('validates date range before starting a backtest', async () => {
    const user = userEvent.setup()
    renderRoute('/backtesting')
    const start = await screen.findByLabelText('Start date')
    const end = await screen.findByLabelText('End date')
    fireEvent.change(start, { target: { value: '2026-07-20' } })
    fireEvent.change(end, { target: { value: '2026-07-19' } })
    await user.click(screen.getByRole('button', { name: 'Start backtest' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Start date harus sebelum end date')
    expect(api.startBacktest).not.toHaveBeenCalled()
  })

  it('renders running progress and cancel control', async () => {
    vi.mocked(api.backtests).mockResolvedValue([backtest])
    vi.mocked(api.backtest).mockResolvedValue(backtestDetail)
    renderRoute('/backtesting')
    expect(await screen.findByText('50%')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Backtest progress' })).toBeInTheDocument()
  })

  it('renders completed statistics, curves, trades, and CSV action', async () => {
    const completed = {
      ...backtestDetail,
      status: 'COMPLETED' as const,
      progress_percent: 100,
      completed_at: '2026-07-22T10:05:00Z',
      statistics: { net_profit: 125, total_trades: 1, win_rate: 100, profit_factor: 2, expectancy: 125, maximum_drawdown: 20 },
    }
    vi.mocked(api.backtests).mockResolvedValue([{ ...backtest, status: 'COMPLETED', progress_percent: 100 }])
    vi.mocked(api.backtest).mockResolvedValue(completed)
    vi.mocked(api.backtestTrades).mockResolvedValue([{
      backtest_id: 'backtest-1', trade_id: 'trade-1', position_id: 'position-1', signal_id: 'signal-1',
      trade_plan_id: 'plan-1', symbol: 'XAUUSDm', direction: 'BUY', volume: .1,
      entry_price: 3000, exit_price: 3005, stop_loss: 2997, take_profit: 3006,
      gross_pnl: 130, commission: 5, swap: 0, net_pnl: 125, exit_reason: 'TAKE_PROFIT',
      opened_at: '2026-07-20T00:00:00Z', closed_at: '2026-07-20T01:00:00Z',
    }])
    vi.mocked(api.backtestEquity).mockResolvedValue([{
      backtest_id: 'backtest-1', snapshot_id: 'snapshot-1', timestamp: '2026-07-20T00:00:00Z',
      balance: 10000, equity: 10125, floating_pnl: 0, drawdown: 20,
    }])
    renderRoute('/backtesting')
    expect(await screen.findByText('Net profit')).toBeInTheDocument()
    expect(await screen.findByText('XAUUSDm · BUY')).toBeInTheDocument()
    expect(screen.getByLabelText('Equity chart')).toBeInTheDocument()
    expect(screen.getByLabelText('Drawdown chart')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeInTheDocument()
    expect(screen.getByText(/Past performance does not guarantee/)).toBeInTheDocument()
  })
})
