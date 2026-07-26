import { useQuery } from '@tanstack/react-query'
import { api, loadPreferences, sanitizeMessage } from '../api/client'
import { DataTable, EmptyState, ErrorAlert, LoadingSkeleton, PageHeader, StatusBadge, dateTime } from '../components/ui'

export function AlertsPage() {
  const interval = loadPreferences().refreshInterval
  const query = useQuery({ queryKey: ['monitoring-alerts'], queryFn: api.alerts, refetchInterval: interval, retry: false })
  const alerts = query.data ?? []
  const stale = Boolean(query.data && query.isError)

  return <div className="page-stack">
    <PageHeader title="Monitoring alerts" description="Read-only alert lifecycle and delivery status, refreshed automatically." />
    {query.isPending && <LoadingSkeleton rows={5} />}
    {query.isError && !query.data && <ErrorAlert message={sanitizeMessage(query.error)} retry={() => void query.refetch()} />}
    {stale && <div className="alert alert-warn" role="status">Showing stale alert data while waiting for the next successful refresh.</div>}
    {query.data && (alerts.length ? <DataTable rows={alerts} rowKey={(alert) => alert.alert_id} columns={[
      { key: 'id', header: 'Alert', cell: (alert) => alert.alert_id },
      { key: 'category', header: 'Category', cell: (alert) => alert.category },
      { key: 'severity', header: 'Severity', cell: (alert) => <StatusBadge value={alert.severity} /> },
      { key: 'state', header: 'State', cell: (alert) => <StatusBadge value={alert.state} /> },
      { key: 'active', header: 'Activity', cell: (alert) => <StatusBadge value={alert.active ? 'ACTIVE' : 'INACTIVE'} /> },
      { key: 'occurrences', header: 'Occurrences', cell: (alert) => alert.occurrences },
      { key: 'first', header: 'First observed', cell: (alert) => dateTime(alert.first_observed_at) },
      { key: 'last', header: 'Last observed', cell: (alert) => dateTime(alert.last_observed_at) },
      { key: 'delivery', header: 'Delivery', cell: (alert) => <StatusBadge value={alert.delivery_state} /> },
    ]} /> : <EmptyState title="No alerts" description="No monitoring alerts were returned." />)}
  </div>
}