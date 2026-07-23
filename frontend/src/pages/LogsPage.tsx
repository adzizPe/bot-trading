import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, sanitizeMessage } from '../api/client'
import { DataTable, EmptyState, PageHeader, StatusBadge, dateTime } from '../components/ui'

type LogRow = { id: string; level: 'INFO' | 'WARNING' | 'ERROR'; module: string; timestamp: string; message: string }

export function LogsPage() {
  const [level, setLevel] = useState('ALL')
  const [module, setModule] = useState('ALL')
  const [date, setDate] = useState('')
  const [search, setSearch] = useState('')
  const [auto, setAuto] = useState(false)
  const backtests = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, refetchInterval: auto ? 10000 : false })
  const mt5 = useQuery({ queryKey: ['mt5-status'], queryFn: api.mt5Status, refetchInterval: auto ? 10000 : false })
  const paper = useQuery({ queryKey: ['paper-status'], queryFn: api.paperStatus, refetchInterval: auto ? 10000 : false })
  const rows = useMemo<LogRow[]>(() => {
    const values: LogRow[] = (backtests.data || []).map((item) => ({
      id: item.backtest_id,
      level: item.status === 'FAILED' ? 'ERROR' : item.status === 'CANCELLED' ? 'WARNING' : 'INFO',
      module: 'BACKTEST', timestamp: item.updated_at,
      message: sanitizeMessage(item.error_message || `Backtest ${item.symbol} ${item.status.toLowerCase()}`),
    }))
    if (mt5.data?.last_error) values.unshift({ id: 'mt5', level: 'ERROR', module: 'MT5', timestamp: new Date().toISOString(), message: sanitizeMessage(mt5.data.last_error) })
    if (paper.data?.last_error) values.unshift({ id: 'paper', level: 'ERROR', module: 'PAPER', timestamp: paper.data.updated_at, message: sanitizeMessage(paper.data.last_error) })
    return values.filter((item) =>
      (level === 'ALL' || item.level === level) && (module === 'ALL' || item.module === module) &&
      (!date || item.timestamp.startsWith(date)) && (!search || item.message.toLowerCase().includes(search.toLowerCase())),
    )
  }, [backtests.data, date, level, module, mt5.data, paper.data, search])

  return <div className="page-stack">
    <PageHeader title="Operational logs" description="Status dan error yang tersedia melalui API publik, dengan sanitasi credential dan stack trace." />
    <div className="alert alert-info">Backend belum menyediakan endpoint global application logs. Halaman ini menampilkan event aman dari MT5 status, paper status, dan backtest jobs.</div>
    <div className="filter-bar">
      <label>Level<select aria-label="Log level" value={level} onChange={(event) => setLevel(event.target.value)}><option>ALL</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></label>
      <label>Module<select aria-label="Log module" value={module} onChange={(event) => setModule(event.target.value)}><option>ALL</option><option>MT5</option><option>PAPER</option><option>BACKTEST</option></select></label>
      <label>Tanggal<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
      <label>Search<input type="search" placeholder="Cari pesan" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      <label className="check-field"><input type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} />Auto refresh</label>
    </div>
    {rows.length ? <DataTable rows={rows} rowKey={(item) => item.id} columns={[
      { key: 'time', header: 'Time', cell: (item) => dateTime(item.timestamp) },
      { key: 'level', header: 'Level', cell: (item) => <StatusBadge value={item.level} /> },
      { key: 'module', header: 'Module', cell: (item) => item.module },
      { key: 'message', header: 'Message', cell: (item) => item.message },
    ]} /> : <EmptyState title="Log tidak ditemukan" description="Tidak ada event API yang cocok dengan filter." />}
  </div>
}
