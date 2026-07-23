import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import { analysis, mockApi, noLatestSignal, renderRoute, signal, tick } from './test/utils'

describe('dashboard shell and core rendering', () => {
  beforeEach(() => mockApi())

  it('routes to every primary dashboard area through semantic navigation', async () => {
    renderRoute('/analysis')
    expect(await screen.findByRole('heading', { name: 'Multi-timeframe analysis' })).toBeInTheDocument()
    const labels = ['Overview', 'Market', 'Analysis', 'Signals', 'Risk Management', 'Trade Plans', 'Paper Trading', 'Backtesting', 'MT5 Connection', 'Logs', 'Settings']
    for (const label of labels) expect(screen.getByRole('link', { name: label })).toBeInTheDocument()
  })

  it('renders a loading state while overview data is pending', () => {
    vi.mocked(api.health).mockReturnValue(new Promise(() => undefined))
    renderRoute('/')
    expect(screen.getAllByLabelText('Loading').length).toBeGreaterThan(0)
  })

  it('renders a sanitized error state with retry', async () => {
    vi.mocked(api.health).mockRejectedValue(new Error('Backend unavailable'))
    renderRoute('/')
    expect((await screen.findAllByText('Backend unavailable', {}, { timeout: 4000 })).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Coba lagi' })).toBeInTheDocument()
  })

  it('renders an empty signals state when no latest signal exists', async () => {
    vi.mocked(api.latestSignal).mockRejectedValue(noLatestSignal)
    renderRoute('/signals')
    expect(await screen.findByText('Signal tidak ditemukan')).toBeInTheDocument()
  })

  it('renders MT5 demo status and masked login without secrets', async () => {
    renderRoute('/mt5')
    expect(await screen.findByText('Demo verified')).toBeInTheDocument()
    expect(await screen.findByText(/12.*78/)).toBeInTheDocument()
    expect(screen.queryByText('12345678')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('super-secret')
  })

  it('renders market tick bid ask and spread', async () => {
    vi.mocked(api.tick).mockResolvedValue(tick)
    renderRoute('/market')
    expect(await screen.findByText(tick.bid.toFixed(3))).toBeInTheDocument()
    expect(screen.getByText(tick.ask.toFixed(3))).toBeInTheDocument()
    expect(screen.getByText('20 pts')).toBeInTheDocument()
  })

  it('renders multi-timeframe analysis values', async () => {
    vi.mocked(api.analysis).mockResolvedValue(analysis)
    renderRoute('/analysis')
    expect(await screen.findByText('Candle confirmation')).toBeInTheDocument()
    expect(screen.getByText('H1')).toBeInTheDocument()
    expect(screen.getAllByText('BULLISH').length).toBeGreaterThan(0)
  })

  it('renders signal direction, confidence, and reasons', async () => {
    const user = userEvent.setup()
    vi.mocked(api.latestSignal).mockResolvedValue(signal)
    renderRoute('/signals')
    expect(await screen.findByText('BUY ▲')).toBeInTheDocument()
    expect(screen.getByText('90%')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Detail' }))
    expect(await screen.findByText('Trend aligned')).toBeInTheDocument()
  })

  it('exposes a mobile navigation control and usable responsive navigation', () => {
    Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true })
    renderRoute('/')
    expect(screen.getByRole('button', { name: 'Open navigation' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })

  it('never renders backend secret-like errors', async () => {
    vi.mocked(api.mt5Status).mockResolvedValue({ state: 'error', connected: false, demo_verified: false, configured: true, symbol: 'XAUUSD', last_error: 'password=super-secret\nTraceback: details' })
    renderRoute('/mt5')
    await waitFor(() => expect(screen.getByText('Sensitive error details were hidden')).toBeInTheDocument())
    expect(document.body.textContent).not.toContain('super-secret')
    expect(document.body.textContent).not.toContain('Traceback')
  })
})
