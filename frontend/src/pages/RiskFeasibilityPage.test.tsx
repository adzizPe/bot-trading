import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { feasibilityResult, mockApi, renderRoute } from '../test/utils'

describe('Risk Feasibility Analyzer page', () => {
  beforeEach(() => mockApi())

  it('renders complete accessible read-only diagnostics and advisory labels', async () => {
    const user = userEvent.setup()
    renderRoute('/risk-feasibility')
    await user.click(await screen.findByRole('button', { name: 'Analyze feasibility' }))

    expect(await screen.findByRole('status')).toHaveTextContent('INFEASIBLE')
    expect(screen.getByText('Raw lot — diagnostic, non-executable')).toBeInTheDocument()
    expect(screen.getAllByText('0.0020000000000000000001 lot')).toHaveLength(2)
    expect(screen.getByText('Floor-normalized lot')).toBeInTheDocument()
    expect(screen.getByText('Effective minimum broker lot')).toBeInTheDocument()
    expect(screen.getByText('Required minimum equity')).toBeInTheDocument()
    expect(screen.getByText('Maximum feasible stop distance')).toBeInTheDocument()
    expect(screen.getByText('DIAGNOSTIC_ONLY')).toBeInTheDocument()
    expect(screen.getByText('NORMALIZED_LOT_BELOW_BROKER_MINIMUM')).toBeInTheDocument()
    expect(screen.getByText('STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM')).toBeInTheDocument()
    expect(screen.getByText('DO_NOT_FORCE_MINIMUM_LOT')).toBeInTheDocument()
    expect(screen.getByLabelText('Advisory disclaimer')).toHaveTextContent('Risk Management and Trade Plan creation remain authoritative')
    expect(document.querySelectorAll('input, select, textarea')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /force|override|create|execute/i })).not.toBeInTheDocument()
  })

  it('shows loading and sanitized transport errors without a feasible conclusion', async () => {
    const user = userEvent.setup()
    let reject!: (error: Error) => void
    vi.mocked(api.riskFeasibility).mockReturnValue(new Promise((_, onReject) => { reject = onReject }))
    renderRoute('/risk-feasibility')
    await user.click(await screen.findByRole('button', { name: 'Analyze feasibility' }))
    expect(screen.getByText('Loading feasibility analysis')).toBeInTheDocument()
    reject(new Error('Backend unavailable'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend unavailable')
    expect(document.body.textContent).not.toContain('FEASIBLE')
  })

  it('renders UNAVAILABLE reasons but never presents them as feasible', async () => {
    const user = userEvent.setup()
    vi.mocked(api.riskFeasibility).mockResolvedValue({
      ...feasibilityResult,
      status: 'UNAVAILABLE', recommendation: 'RETRY_WITH_VALID_FRESH_DATA',
      snapshot_timestamps: { ...feasibilityResult.snapshot_timestamps, tick_at: null, fresh_until: null },
      reasons: [{ code: 'SNAPSHOT_STALE', message: 'The authoritative snapshot is stale.' }],
    })
    renderRoute('/risk-feasibility')
    await user.click(await screen.findByRole('button', { name: 'Analyze feasibility' }))
    expect(await screen.findByText('SNAPSHOT_STALE')).toBeInTheDocument()
    expect(screen.getByText('RETRY_WITH_VALID_FRESH_DATA')).toBeInTheDocument()
    expect(screen.getByText(/must not be interpreted as feasible/i)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('The formula produces a floor-normalized lot')
  })

  it('marks an already-expired response stale and hides all decision diagnostics', async () => {
    const user = userEvent.setup()
    vi.mocked(api.riskFeasibility).mockResolvedValue({
      ...feasibilityResult,
      status: 'FEASIBLE', recommendation: 'PROCEED_TO_AUTHORITATIVE_TRADE_PLAN_FLOW',
      snapshot_timestamps: { ...feasibilityResult.snapshot_timestamps, fresh_until: '2000-01-01T00:00:00Z' },
    })
    renderRoute('/risk-feasibility')
    await user.click(await screen.findByRole('button', { name: 'Analyze feasibility' }))
    expect(await screen.findByText('Stale result — not current')).toBeInTheDocument()
    expect(screen.queryByText('Floor-normalized lot')).not.toBeInTheDocument()
    expect(screen.queryByText('FEASIBLE')).not.toBeInTheDocument()
  })

  it('uses a single signal-only query and does not call protected mutations', async () => {
    const user = userEvent.setup()
    renderRoute('/risk-feasibility')
    await user.click(await screen.findByRole('button', { name: 'Analyze feasibility' }))
    await waitFor(() => expect(api.riskFeasibility).toHaveBeenCalledWith('signal-1', expect.any(AbortSignal)))
    expect(api.createTradePlan).not.toHaveBeenCalled()
    expect(api.updateRiskSettings).not.toHaveBeenCalled()
    expect(api.openPaperPosition).not.toHaveBeenCalled()
    expect(api.executeDemo).not.toHaveBeenCalled()
  })
})

describe('risk feasibility integration boundaries', () => {
  beforeEach(() => mockApi())

  it('renders a fresh FEASIBLE result with an explicit authoritative-flow caveat', async () => {
    const user = userEvent.setup()
    vi.mocked(api.riskFeasibility).mockResolvedValue({
      ...feasibilityResult,
      status: 'FEASIBLE', recommendation: 'PROCEED_TO_AUTHORITATIVE_TRADE_PLAN_FLOW', reasons: [],
    })
    renderRoute('/risk-feasibility')
    await user.click(await screen.findByRole('button', { name: 'Analyze feasibility' }))
    expect(await screen.findByRole('status')).toHaveTextContent('FEASIBLE')
    expect(screen.getByText('PROCEED_TO_AUTHORITATIVE_TRADE_PLAN_FLOW')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Final Trade Plan validation remains authoritative')
  })

  it('expires a displayed result on its fresh-until timer and removes the conclusion', async () => {
    const user = userEvent.setup()
    vi.mocked(api.riskFeasibility).mockResolvedValue({
      ...feasibilityResult,
      snapshot_timestamps: {
        ...feasibilityResult.snapshot_timestamps,
        fresh_until: new Date(Date.now() + 1000).toISOString(),
      },
    })
    renderRoute('/risk-feasibility')
    await user.click(await screen.findByRole('button', { name: 'Analyze feasibility' }))
    expect(await screen.findByText('Floor-normalized lot')).toBeInTheDocument()
    expect(await screen.findByText('Stale result — not current', {}, { timeout: 2500 })).toBeInTheDocument()
    expect(screen.queryByText('Floor-normalized lot')).not.toBeInTheDocument()
  })

  it('does not couple analyzer status or cache to the existing Trade Plan creation control', async () => {
    vi.mocked(api.riskFeasibility).mockResolvedValue({
      ...feasibilityResult,
      status: 'UNAVAILABLE', recommendation: 'RETRY_WITH_VALID_FRESH_DATA',
    })
    renderRoute('/trade-plans')
    const createButton = await screen.findByRole('button', { name: 'Buat dari latest signal' })
    await waitFor(() => expect(createButton).toBeEnabled())
    expect(api.riskFeasibility).not.toHaveBeenCalled()
    expect(api.createTradePlan).not.toHaveBeenCalled()
  })
})
