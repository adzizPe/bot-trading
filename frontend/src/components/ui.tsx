/* eslint-disable react-refresh/only-export-components */
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  CircleOff,
  Info,
  LoaderCircle,
  PlugZap,
  X,
  XCircle,
} from 'lucide-react'
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import type { Direction, Signal, TradePlan } from '../api/types'

export const money = (value: number | null | undefined, currency = 'USD') =>
  value == null
    ? '—'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(value)

export const number = (value: number | null | undefined, digits = 2) =>
  value == null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value)

export const dateTime = (value: string | null | undefined) =>
  value ? new Intl.DateTimeFormat('id-ID', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'

export function StatusBadge({ value }: { value: string }) {
  const normalized = value.toUpperCase()
  const tone = /CONNECTED|HEALTHY|RUNNING|APPROVED|COMPLETED|CANDIDATE|OPEN/.test(normalized)
    ? 'good'
    : /ERROR|FAILED|REJECTED|LOCKED|EMERGENCY|SELL/.test(normalized)
      ? 'bad'
      : /PENDING|STARTING|PAUSED|WARNING|HOLD|BUY/.test(normalized)
        ? 'warn'
        : 'neutral'
  return <span className={`status-badge status-${tone}`}><span aria-hidden>●</span>{normalized}</span>
}
export function MetricCard({
  label,
  value,
  detail,
  icon,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  icon?: ReactNode
}) {
  return (
    <article className="metric-card">
      <div className="metric-label">{icon}<span>{label}</span></div>
      <div className="metric-value">{value}</div>
      {detail && <div className="metric-detail">{detail}</div>}
    </article>
  )
}

export function LoadingSkeleton({ rows = 4 }: { rows?: number }) {
  return <div aria-label="Loading" className="skeleton-wrap">{Array.from({ length: rows }, (_, index) => <div className="skeleton" key={index} />)}</div>
}

export function ErrorAlert({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="alert alert-error" role="alert">
      <XCircle size={18} aria-hidden />
      <span>{message}</span>
      {retry && <button className="button button-ghost" onClick={retry}>Coba lagi</button>}
    </div>
  )
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="empty-state">
      <CircleOff aria-hidden />
      <strong>{title}</strong>
      <span>{description}</span>
    </div>
  )
}

export function ConnectionIndicator({ status }: { status: string }) {
  const active = status === 'connected'
  return (
    <span className={`connection ${active ? 'connection-on' : ''}`}>
      {active ? <PlugZap size={15} aria-hidden /> : <LoaderCircle className={status.includes('connect') ? 'spin' : ''} size={15} aria-hidden />}
      <span>{status}</span>
    </span>
  )
}

export function PriceDisplay({ label, value, digits = 3 }: { label: string; value?: number; digits?: number }) {
  return <div className="price"><span>{label}</span><strong>{value == null ? '—' : value.toFixed(digits)}</strong></div>
}

export function PnLDisplay({ value }: { value: number }) {
  const positive = value >= 0
  return (
    <span className={positive ? 'pnl-profit' : 'pnl-loss'}>
      {positive ? <ArrowUpRight size={16} aria-label="Profit" /> : <ArrowDownRight size={16} aria-label="Loss" />}
      {money(value)}
    </span>
  )
}

type Column<T> = { key: string; header: string; cell: (row: T) => ReactNode }
export function DataTable<T>({ rows, columns, rowKey }: { rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string }) {
  if (!rows.length) return <EmptyState title="Belum ada data" description="Data akan muncul setelah tersedia dari backend." />
  return (
    <div className="table-scroll">
      <table>
        <thead><tr>{columns.map((column) => <th key={column.key}>{column.header}</th>)}</tr></thead>
        <tbody>{rows.map((row) => <tr key={rowKey(row)}>{columns.map((column) => <td key={column.key}>{column.cell(row)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}
export function ConfirmDialog({
  open,
  title,
  description,
  destructive = false,
  confirmationText,
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean
  title: string
  description: string
  destructive?: boolean
  confirmationText?: string
  busy?: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const [typed, setTyped] = useState('')
  if (!open) return null
  const allowed = !confirmationText || typed === confirmationText
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}>
      <div aria-modal="true" className="dialog" role="dialog" aria-labelledby="dialog-title">
        <div className={`dialog-icon ${destructive ? 'danger' : ''}`}><AlertTriangle aria-hidden /></div>
        <h2 id="dialog-title">{title}</h2>
        <p>{description}</p>
        {confirmationText && (
          <label className="field">Ketik <strong>{confirmationText}</strong> untuk melanjutkan
            <input aria-label="Confirmation text" value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" />
          </label>
        )}
        <div className="dialog-actions">
          <button className="button button-ghost" onClick={onCancel} disabled={busy}>Batal</button>
          <button className={destructive ? 'button button-danger' : 'button button-primary'} onClick={onConfirm} disabled={!allowed || busy}>
            {busy ? 'Memproses…' : 'Konfirmasi'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function SignalCard({ signal }: { signal: Signal }) {
  return (
    <article className="panel signal-card">
      <div className="panel-heading"><div><p className="eyebrow">Latest signal</p><h3>{signal.symbol}</h3></div><StatusBadge value={signal.direction} /></div>
      <div className="metric-grid compact">
        <MetricCard label="Confidence" value={`${number(signal.confidence_score)}%`} />
        <MetricCard label="Status" value={<StatusBadge value={signal.status} />} />
        <MetricCard label="Entry ref." value={number(signal.entry_reference_price, 5)} />
        <MetricCard label="ATR" value={number(signal.atr, 5)} />
      </div>
      <ReasonList title="Alasan" values={signal.reasons} positive />
      <ReasonList title="Penolakan" values={signal.rejection_reasons} />
      <small>{dateTime(signal.created_at)}</small>
    </article>
  )
}

export function TradePlanCard({ plan, action }: { plan: TradePlan; action?: ReactNode }) {
  return (
    <article className="panel trade-plan-card">
      <div className="panel-heading"><div><p className="eyebrow">Trade plan</p><h3>{plan.symbol} · {plan.direction}</h3></div><StatusBadge value={plan.status} /></div>
      <div className="key-values">
        <span>Entry<strong>{number(plan.entry_price, 5)}</strong></span>
        <span>Stop loss<strong>{number(plan.stop_loss, 5)}</strong></span>
        <span>Take profit<strong>{number(plan.take_profit, 5)}</strong></span>
        <span>Lot<strong>{number(plan.position_size_lots)}</strong></span>
        <span>Risk<strong>{money(plan.risk_amount)}</strong></span>
        <span>R:R<strong>{number(plan.risk_reward)}</strong></span>
      </div>
      <ReasonList title="Validasi" values={plan.validation_reasons} positive />
      <ReasonList title="Penolakan" values={plan.rejection_reasons} />
      {action}
    </article>
  )
}

export function ReasonList({ title, values, positive = false }: { title: string; values: string[]; positive?: boolean }) {
  if (!values.length) return null
  return <div className="reason-list"><strong>{title}</strong>{values.map((value) => <span key={value}>{positive ? <CheckCircle2 size={14} /> : <X size={14} />}{value}</span>)}</div>
}
type Toast = { id: number; type: 'success' | 'error' | 'info'; message: string }
type ToastContextValue = { notify: (message: string, type?: Toast['type']) => void }
const ToastContext = createContext<ToastContextValue | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const notify = useCallback((message: string, type: Toast['type'] = 'success') => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, type, message }])
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4000)
  }, [])
  const value = useMemo(() => ({ notify }), [notify])
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {toasts.map((toast) => (
          <div className={`toast toast-${toast.type}`} key={toast.id}>
            {toast.type === 'success' ? <CheckCircle2 size={17} /> : toast.type === 'error' ? <XCircle size={17} /> : <Info size={17} />}
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const value = useContext(ToastContext)
  if (!value) throw new Error('useToast must be used inside ToastProvider')
  return value
}

export function DirectionLabel({ direction }: { direction: Direction }) {
  return <StatusBadge value={direction === 'BUY' ? 'BUY ▲' : direction === 'SELL' ? 'SELL ▼' : 'HOLD —'} />
}
export function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return <div className="page-header"><div><p className="eyebrow">XAU/USD · DEMO ENVIRONMENT</p><h2>{title}</h2><p>{description}</p></div>{actions && <div className="page-actions">{actions}</div>}</div>
}

export function Panel({ title, subtitle, children, actions }: { title: string; subtitle?: string; children: ReactNode; actions?: ReactNode }) {
  return <section className="panel"><div className="panel-heading"><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>{actions}</div>{children}</section>
}
