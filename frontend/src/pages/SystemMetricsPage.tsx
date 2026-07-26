import { useQuery } from '@tanstack/react-query'
import { api, loadPreferences, sanitizeMessage } from '../api/client'
import { DataTable, EmptyState, ErrorAlert, LoadingSkeleton, MetricCard, PageHeader, Panel, StatusBadge, dateTime } from '../components/ui'
import type { MonitoringObservation } from '../api/types'

const displayValue = (row: MonitoringObservation) => row.value == null ? '—' : `${String(row.value)}${row.unit ? ` ${row.unit}` : ''}`

export function SystemMetricsPage() {
  const interval = loadPreferences().refreshInterval
  const query = useQuery({ queryKey: ['system-metrics'], queryFn: api.systemMetrics, refetchInterval: interval, retry: false })
  const components = Object.entries(query.data?.components ?? {})
  const stale = Boolean(query.data && (query.data.cached || query.isError))

  return <div className="page-stack">
    <PageHeader title="System metrics" description="Read-only operational observations, refreshed automatically." />
    {query.isPending && <LoadingSkeleton rows={5} />}
    {query.isError && !query.data && <ErrorAlert message={sanitizeMessage(query.error)} retry={() => void query.refetch()} />}
    {stale && <div className="alert alert-warn" role="status">Showing stale or cached metrics while waiting for a fresh observation.</div>}
    {query.data && <>
      <div className="metric-grid">
        <MetricCard label="Overall status" value={<StatusBadge value={query.data.status} />} />
        <MetricCard label="Observed at" value={dateTime(query.data.observed_at)} />
        <MetricCard label="Source" value={query.data.cached ? 'Cached' : 'Live'} />
      </div>
      {!components.length ? <EmptyState title="No metrics available" description="No monitoring components were returned." /> : components.map(([key, component]) =>
        <Panel key={key} title={component.name} subtitle={key} actions={<StatusBadge value={component.state} />}>
          {component.observations.length ? <DataTable rows={component.observations} rowKey={(row) => `${row.name}-${row.detail}`} columns={[
            { key: 'name', header: 'Observation', cell: (row) => row.name },
            { key: 'state', header: 'State', cell: (row) => <StatusBadge value={row.state} /> },
            { key: 'value', header: 'Value', cell: displayValue },
            { key: 'detail', header: 'Detail', cell: (row) => row.detail || '—' },
          ]} /> : <EmptyState title="No observations" description="This component has no observations." />}
        </Panel>)}
    </>}
  </div>
}