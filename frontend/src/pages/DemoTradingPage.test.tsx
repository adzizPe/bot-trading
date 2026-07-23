import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { api, setDemoAdminToken } from '../api/client'
import { mockApi, renderRoute } from '../test/utils'

describe('demo trading dashboard', () => {
  beforeEach(() => mockApi())

  it('keeps admin access runtime-only and blocks queries before activation', async () => {
    const user = userEvent.setup()
    renderRoute('/demo')
    expect(await screen.findByText('Admin session belum aktif')).toBeInTheDocument()
    expect(api.demoStatus).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText('Demo admin token'), 'runtime-demo-admin-secret')
    await user.click(screen.getByRole('button', { name: 'Aktifkan sesi admin' }))
    await waitFor(() => expect(api.demoStatus).toHaveBeenCalled())
    expect(localStorage.getItem('dashboard-preferences') || '').not.toContain('runtime-demo-admin-secret')
    expect(sessionStorage.length).toBe(0)
  })

  it('shows demo-only state, execution retcode, positions, and deals', async () => {
    setDemoAdminToken('runtime-demo-admin-secret')
    renderRoute('/demo')
    expect(await screen.findByText('DEMO ACCOUNT ONLY')).toBeInTheDocument()
    expect(await screen.findByText('10009 · Done')).toBeInTheDocument()
    expect(screen.getAllByText('XAUUSDm · BUY').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Break-even' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('protects emergency stop with typed confirmation', async () => {
    const user = userEvent.setup()
    setDemoAdminToken('runtime-demo-admin-secret')
    renderRoute('/demo')
    await user.click(await screen.findByRole('button', { name: 'Emergency stop' }))
    const confirm = screen.getByRole('button', { name: 'Konfirmasi' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Confirmation text'), 'EMERGENCY STOP')
    await user.click(confirm)
    await waitFor(() => expect(api.demoEmergencyStop).toHaveBeenCalledTimes(1))
  })

  it('never renders editable volume, symbol, SL, or TP fields', async () => {
    setDemoAdminToken('runtime-demo-admin-secret')
    renderRoute('/demo')
    await screen.findByText('Execution history')
    expect(screen.queryByLabelText(/^Volume$/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^Symbol$/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/stop.loss|take.profit|\bSL\b|\bTP\b/i)).not.toBeInTheDocument()
  })
})
