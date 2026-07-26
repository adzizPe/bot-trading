import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { mockApi, renderRoute } from '../test/utils'

describe('monitoring alerts page', () => {
  beforeEach(() => mockApi())

  it('renders read-only alert lifecycle and delivery records', async () => {
    renderRoute('/alerts')
    expect(await screen.findByRole('heading', { name: 'Monitoring alerts' })).toBeInTheDocument()
    expect(await screen.findByText('alert-1')).toBeInTheDocument()
    expect(screen.getByText('CONNECTIVITY')).toBeInTheDocument()
    expect(screen.getByText('DELIVERED')).toBeInTheDocument()
    expect(api.alerts).toHaveBeenCalled()
    expect(within(screen.getByRole('main')).queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('handles loading, error, and empty alert responses', async () => {
    vi.mocked(api.alerts).mockReturnValueOnce(new Promise(() => undefined))
    const loading = renderRoute('/alerts')
    expect(screen.getAllByLabelText('Loading').length).toBeGreaterThan(0)
    loading.unmount()
    vi.mocked(api.alerts).mockRejectedValueOnce(new Error('Alerts unavailable'))
    const failed = renderRoute('/alerts')
    expect(await screen.findByRole('alert')).toHaveTextContent('Alerts unavailable')
    failed.unmount()
    vi.mocked(api.alerts).mockResolvedValueOnce([])
    renderRoute('/alerts')
    expect(await screen.findByText('No alerts')).toBeInTheDocument()
  })
})