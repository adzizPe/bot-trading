import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { mockApi, renderRoute, safetyStatus } from '../test/utils'

describe('demo trading dashboard', () => {
  beforeEach(() => {
    mockApi()
  })

  it('uses the authenticated cookie session for demo data', async () => {
    renderRoute('/demo')
    expect(await screen.findByText('DEMO ACCOUNT ONLY')).toBeInTheDocument()
    await waitFor(() => expect(api.demoStatus).toHaveBeenCalled())
    expect(sessionStorage.length).toBe(0)
  })

  it('shows demo-only state, execution retcode, positions, and deals', async () => {
    renderRoute('/demo')
    expect(await screen.findByText('DEMO ACCOUNT ONLY')).toBeInTheDocument()
    expect(await screen.findByText('10009 · Done')).toBeInTheDocument()
    expect(screen.getAllByText('XAUUSDm · BUY').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Break-even' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('renders safety, guardian, circuit breaker, heartbeat, health, and emergency status', async () => {
    renderRoute('/demo')

    expect(await screen.findByText('Safety Status')).toBeInTheDocument()
    expect(screen.getByText('Guardian Status')).toBeInTheDocument()
    expect(screen.getByText('Circuit Breaker')).toBeInTheDocument()
    expect(screen.getByText('Heartbeat')).toBeInTheDocument()
    expect(screen.getByText('Health')).toBeInTheDocument()
    expect(screen.getByText('Emergency Stop')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('CLOSED')).toBeInTheDocument())
    expect(screen.getAllByText('HEALTHY')).toHaveLength(3)
    expect(screen.getByText('INACTIVE')).toBeInTheDocument()
  })

  it('disables start and position actions when global safety is degraded', async () => {
    vi.mocked(api.safetyStatus).mockResolvedValue({
      ...safetyStatus,
      allowed: false,
      heartbeat_status: 'DEGRADED',
      guardians: {
        ...safetyStatus.guardians,
        HeartbeatMonitor: {
          allowed: false,
          reason: 'System heartbeat is degraded',
          details: { status: 'DEGRADED' },
        },
      },
    })
    renderRoute('/demo')

    expect(await screen.findByRole('status')).toHaveTextContent('Trading actions are disabled')
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled()
    expect(await screen.findByRole('button', { name: 'Break-even' })).toBeDisabled()
    expect(await screen.findByRole('button', { name: 'Close' })).toBeDisabled()
  })

  it('protects emergency stop with typed confirmation', async () => {
    const user = userEvent.setup()
    renderRoute('/demo')
    await user.click(await screen.findByRole('button', { name: 'Emergency stop' }))
    const confirm = screen.getByRole('button', { name: 'Konfirmasi' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Confirmation text'), 'EMERGENCY STOP')
    await user.click(confirm)
    await waitFor(() => expect(api.demoEmergencyStop).toHaveBeenCalledTimes(1))
  })

  it('never renders editable volume, symbol, SL, or TP fields', async () => {
    renderRoute('/demo')
    await screen.findByText('Execution history')
    expect(screen.queryByLabelText(/^Volume$/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^Symbol$/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/stop.loss|take.profit|\bSL\b|\bTP\b/i)).not.toBeInTheDocument()
  })
})
