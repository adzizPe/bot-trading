import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { api } from '../api/client'
import { authUser, mockApi, renderRoute } from '../test/utils'

describe('authentication routes and session UI', () => {
  beforeEach(() => mockApi())

  it('redirects unauthenticated protected routes to login', async () => {
    renderRoute('/analysis', null)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('logs in and displays the current username and role', async () => {
    const user = userEvent.setup()
    renderRoute('/login', null)
    await user.type(screen.getByLabelText('Username'), 'operator')
    await user.type(screen.getByLabelText('Password'), 'correct-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(api.login).toHaveBeenCalledWith('operator', 'correct-password'))
    expect(await screen.findByText('operator')).toBeInTheDocument()
    expect(screen.getByText('SUPER_ADMIN')).toBeInTheDocument()
  })

  it('logs out and returns to login', async () => {
    const user = userEvent.setup()
    renderRoute('/', authUser)
    await user.click(await screen.findByRole('button', { name: 'Logout' }))
    await waitFor(() => expect(api.logout).toHaveBeenCalled())
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('renders dedicated unauthorized and forbidden pages', async () => {
    const unauthorized = renderRoute('/unauthorized', null)
    expect(await screen.findByRole('heading', { name: 'Unauthorized' })).toBeInTheDocument()
    unauthorized.unmount()
    renderRoute('/forbidden', null)
    expect(await screen.findByRole('heading', { name: 'Forbidden' })).toBeInTheDocument()
  })
})

describe('permission visibility', () => {
  beforeEach(() => mockApi())
  const readOnlyUser = { ...authUser, role: 'VIEWER', permissions: [] }

  it('shows write controls for matching backend colon permissions', async () => {
    const analysis = renderRoute('/analysis', authUser)
    expect(await screen.findByRole('button', { name: 'Generate signal' })).toBeInTheDocument()
    analysis.unmount()

    const risk = renderRoute('/risk', authUser)
    expect(await screen.findByRole('button', { name: 'Simpan pengaturan' })).toBeInTheDocument()
    risk.unmount()

    renderRoute('/backtesting', authUser)
    expect(await screen.findByRole('button', { name: 'Start backtest' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('keeps read routes accessible while hiding analysis, MT5, and risk writes', async () => {
    const analysis = renderRoute('/analysis', readOnlyUser)
    expect(await screen.findByRole('heading', { name: 'Multi-timeframe analysis' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Generate signal' })).not.toBeInTheDocument()
    analysis.unmount()

    const mt5 = renderRoute('/mt5', readOnlyUser)
    expect(await screen.findByRole('heading', { name: 'MT5 connection' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /connect demo|disconnect/i })).not.toBeInTheDocument()
    mt5.unmount()

    renderRoute('/risk', readOnlyUser)
    expect(await screen.findByRole('heading', { name: 'Risk management' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Simpan pengaturan' })).not.toBeInTheDocument()
  })

  it('hides trade-plan paper/demo actions and all backtest write actions', async () => {
    const user = userEvent.setup()
    const plans = renderRoute('/trade-plans', readOnlyUser)
    expect(await screen.findByRole('heading', { name: 'Trade plans' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Buat dari latest signal' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Detail' }))
    expect(screen.queryByRole('button', { name: 'Open paper position' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Execute Demo' })).not.toBeInTheDocument()
    plans.unmount()

    renderRoute('/backtesting', readOnlyUser)
    expect(await screen.findByRole('heading', { level: 2, name: 'Backtesting' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Start backtest|Cancel|Download CSV/ })).not.toBeInTheDocument()
  })

  it('hides paper, demo-position, and safety controls independently', async () => {
    const paper = renderRoute('/paper', readOnlyUser)
    expect(await screen.findByRole('heading', { name: 'Paper trading' })).toBeInTheDocument()
    for (const action of ['Start', 'Pause', 'Stop', 'Emergency stop', 'Open paper position']) {
      expect(screen.queryByRole('button', { name: action })).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: /^Close$/ })).not.toBeInTheDocument()
    paper.unmount()

    renderRoute('/demo', readOnlyUser)
    expect(await screen.findByRole('heading', { name: 'Demo trading' })).toBeInTheDocument()
    for (const action of ['Start', 'Pause', 'Stop', 'Reconcile', 'Emergency stop', 'Break-even', 'Activate global stop', 'Reset global stop']) {
      expect(screen.queryByRole('button', { name: action })).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: /^Close$/ })).not.toBeInTheDocument()
  })
})
