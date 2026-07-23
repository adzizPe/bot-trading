import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Signal } from '../api/types'
import { DataTable, DirectionLabel, EmptyState, PageHeader, SignalCard, StatusBadge, dateTime, number } from '../components/ui'

export function SignalsPage() {
  const latest = useQuery({ queryKey: ['latest-signal'], queryFn: api.latestSignal, retry: false })
  const session = useQuery<Signal[]>({ queryKey: ['signal-history'], queryFn: async () => [], initialData: [], staleTime: Infinity })
  const [direction, setDirection] = useState('ALL')
  const [status, setStatus] = useState('ALL')
  const [date, setDate] = useState('')
  const [selected, setSelected] = useState<Signal | null>(null)
  const rows = useMemo(() => {
    const merged = latest.data ? [latest.data, ...session.data] : session.data
    return Array.from(new Map(merged.map((item) => [item.signal_id, item])).values()).filter((item) =>
      (direction === 'ALL' || item.direction === direction) &&
      (status === 'ALL' || item.status === status) &&
      (!date || item.created_at.startsWith(date)),
    )
  }, [date, direction, latest.data, session.data, status])

  return <div className="page-stack">
    <PageHeader title="Signals" description="Latest persisted signal dan signal yang dibuat selama sesi dashboard ini." />
    <div className="alert alert-info">Backend saat ini hanya mengekspos latest signal; histori penuh dan pagination belum tersedia sebagai endpoint read-only.</div>
    <div className="filter-bar">
      <label>Direction<select aria-label="Filter direction" value={direction} onChange={(event) => setDirection(event.target.value)}><option>ALL</option><option>BUY</option><option>SELL</option><option>HOLD</option></select></label>
      <label>Status<select aria-label="Filter status" value={status} onChange={(event) => setStatus(event.target.value)}><option>ALL</option><option>CANDIDATE</option><option>REJECTED</option><option>HOLD</option></select></label>
      <label>Tanggal<input aria-label="Filter date" type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
    </div>
    {rows.length ? <DataTable rows={rows} rowKey={(item) => item.signal_id} columns={[
      { key: 'direction', header: 'Direction', cell: (item) => <DirectionLabel direction={item.direction} /> },
      { key: 'symbol', header: 'Symbol', cell: (item) => item.symbol },
      { key: 'confidence', header: 'Confidence', cell: (item) => `${number(item.confidence_score)}%` },
      { key: 'status', header: 'Status', cell: (item) => <StatusBadge value={item.status} /> },
      { key: 'time', header: 'Created', cell: (item) => dateTime(item.created_at) },
      { key: 'detail', header: '', cell: (item) => <button className="button button-ghost" onClick={() => setSelected(item)}>Detail</button> },
    ]} /> : <EmptyState title="Signal tidak ditemukan" description="Generate signal di halaman Analysis atau ubah filter." />}
    {selected && <div className="dialog-backdrop"><div className="dialog dialog-wide" role="dialog" aria-modal="true" aria-label="Signal detail"><SignalCard signal={selected} /><button className="button button-ghost" onClick={() => setSelected(null)}>Tutup</button></div></div>}
  </div>
}
