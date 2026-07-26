import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { mockApi, renderRoute } from '../test/utils'

describe('system metrics page', () => {
  beforeEach(() => mockApi())

  it('renders read-only component observations from the monitoring endpoint', async () => {
    renderRoute('/system-metrics')
    expect(await screen.findByRole('heading', { name: 'System metrics' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Database' })).toBeInTheDocument()
    expect(screen.getByText('12 ms')).toBeInTheDocument()
    expect(api.systemMetrics).toHaveBeenCalled()
    expect(within(screen.getByRole('main')).queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('renders all required production metric groups generically', async () => {
    const groups = ['CPU', 'RAM', 'Disk', 'SQLite', 'Nginx', 'Backend', 'WebSocket', 'MT5 Connector', 'Heartbeat']
    vi.mocked(api.systemMetrics).mockResolvedValueOnce({
      status: 'HEALTHY', observed_at: new Date().toISOString(), cached: false,
      components: Object.fromEntries(groups.map((name) => [name.toLowerCase(), {
        name, state: 'HEALTHY', observations: [{
          name: `${name}.state`, state: 'HEALTHY', value: 1, unit: 'count', detail: 'READ_ONLY',
        }],
      }])),
    })
    renderRoute('/system-metrics')
    for (const name of groups) expect(await screen.findByRole('heading', { name })).toBeInTheDocument()
  })

  it('handles loading, error, empty, and cached stale results', async () => {
    vi.mocked(api.systemMetrics).mockReturnValueOnce(new Promise(() => undefined))
    const loading = renderRoute('/system-metrics')
    expect(screen.getAllByLabelText('Loading').length).toBeGreaterThan(0)
    loading.unmount()
    vi.mocked(api.systemMetrics).mockRejectedValueOnce(new Error('Metrics unavailable'))
    const failed = renderRoute('/system-metrics')
    expect(await screen.findByRole('alert')).toHaveTextContent('Metrics unavailable')
    failed.unmount()
    vi.mocked(api.systemMetrics).mockResolvedValueOnce({ status: 'UNKNOWN', observed_at: new Date().toISOString(), cached: false, components: {} })
    const empty = renderRoute('/system-metrics')
    expect(await screen.findByText('No metrics available')).toBeInTheDocument()
    empty.unmount()
    vi.mocked(api.systemMetrics).mockResolvedValueOnce({ status: 'HEALTHY', observed_at: new Date().toISOString(), cached: true, components: {} })
    renderRoute('/system-metrics')
    expect(await screen.findByRole('status')).toHaveTextContent(/stale or cached/i)
  })
})