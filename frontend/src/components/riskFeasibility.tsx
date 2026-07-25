import { AlertTriangle, CheckCircle2, CircleHelp, Info, ShieldAlert, XCircle } from 'lucide-react'
import type { FeasibilityStatus, RiskFeasibilityResult } from '../api/types'
import { decimalWithUnit } from '../riskFeasibility'
import { MetricCard, Panel, StatusBadge, dateTime } from './ui'

const statusCopy: Record<FeasibilityStatus, string> = {
  FEASIBLE: 'The formula produces a floor-normalized lot that meets the broker minimum. Final Trade Plan validation remains authoritative.',
  INFEASIBLE: 'The configured risk cannot produce an executable broker lot for this snapshot. Do not force the minimum lot.',
  UNAVAILABLE: 'A trustworthy conclusion is unavailable. Retry when authoritative data is valid and fresh.',
}

function StatusIcon({ status }: { status: FeasibilityStatus }) {
  if (status === 'FEASIBLE') return <CheckCircle2 aria-hidden />
  if (status === 'INFEASIBLE') return <XCircle aria-hidden />
  return <CircleHelp aria-hidden />
}

function ValueGrid({ values }: { values: { label: string; value: string; detail?: string }[] }) {
  return <div className="metric-grid">{values.map((item) => (
    <MetricCard key={item.label} label={item.label} value={item.value} detail={item.detail} />
  ))}</div>
}

export function FeasibilityStatusPanel({ result }: { result: RiskFeasibilityResult }) {
  return <section className={`feasibility-status feasibility-${result.status.toLowerCase()}`} role="status" aria-live="polite">
    <StatusIcon status={result.status} />
    <div><StatusBadge value={result.status} /><p>{statusCopy[result.status]}</p></div>
  </section>
}

export function FeasibilityContextPanel({ result }: { result: RiskFeasibilityResult }) {
  const { account, market, units } = result
  return <Panel title="Authoritative source context" subtitle="Read-only account, risk, signal, and broker snapshot values.">
    <ValueGrid values={[
      { label: 'Signal', value: result.source_signal_id, detail: `${result.symbol} · ${result.direction}` },
      { label: 'Balance', value: decimalWithUnit(account.balance, units.currency) },
      { label: 'Equity', value: decimalWithUnit(account.equity, units.currency) },
      { label: `Risk base (${account.risk_base_type})`, value: decimalWithUnit(account.risk_base_value, units.currency) },
      { label: 'Configured risk', value: decimalWithUnit(account.configured_risk_percent, units.percent) },
      { label: 'Entry price', value: decimalWithUnit(market.entry_price, units.price) },
      { label: 'Stop-loss price', value: decimalWithUnit(market.stop_loss_price, units.price) },
      { label: 'Snapshot captured', value: dateTime(result.snapshot_timestamps.captured_at), detail: `Fresh until ${dateTime(result.snapshot_timestamps.fresh_until)}` },
    ]} />
  </Panel>
}

export function PositionSizingDiagnostics({ result }: { result: RiskFeasibilityResult }) {
  const { volume, calculation, units } = result
  return <Panel title="Position-sizing diagnostics" subtitle="The existing zero-origin floor-normalization boundary is shown without rounding up.">
    <ValueGrid values={[
      { label: 'Raw lot — diagnostic, non-executable', value: decimalWithUnit(volume.raw_lot, units.volume) },
      { label: 'Capped lot — diagnostic', value: decimalWithUnit(volume.capped_lot, units.volume) },
      { label: 'Floor-normalized lot', value: decimalWithUnit(volume.normalized_lot, units.volume) },
      { label: 'Configured volume minimum', value: decimalWithUnit(volume.volume_min, units.volume) },
      { label: 'Effective minimum broker lot', value: decimalWithUnit(volume.minimum_broker_lot, units.volume) },
      { label: 'Volume maximum', value: decimalWithUnit(volume.volume_max, units.volume) },
      { label: 'Volume step', value: decimalWithUnit(volume.volume_step, units.volume) },
      { label: 'Risk amount', value: decimalWithUnit(calculation.risk_amount, units.currency) },
      { label: 'Ticks at risk', value: decimalWithUnit(calculation.ticks_at_risk, 'ticks') },
      { label: 'Risk per lot', value: decimalWithUnit(calculation.risk_per_lot, units.tick_derived) },
    ]} />
  </Panel>
}

export function ThresholdDiagnostics({ result }: { result: RiskFeasibilityResult }) {
  const { calculation, market, units } = result
  const equityDetail = calculation.required_minimum_equity_applicability === 'APPLICABLE'
    ? 'Applicable because the authoritative risk base is equity.'
    : 'Hypothetical only; balance is the authoritative risk base.'
  return <Panel title="Advisory thresholds" subtitle="Mathematical boundaries from this snapshot; they are not approval or instructions to modify Strategy.">
    <ValueGrid values={[
      { label: `Required minimum risk base (${calculation.required_minimum_risk_base_type})`, value: decimalWithUnit(calculation.required_minimum_risk_base, units.currency) },
      { label: 'Required minimum equity', value: decimalWithUnit(calculation.required_minimum_equity, units.currency), detail: equityDetail },
      { label: 'Actual stop distance', value: decimalWithUnit(market.stop_distance, units.price), detail: decimalWithUnit(market.stop_distance_points, units.point) },
      { label: 'Maximum feasible stop distance', value: decimalWithUnit(calculation.maximum_stop_distance, units.price), detail: decimalWithUnit(calculation.maximum_stop_distance_points, units.point) },
      { label: `Boundary stop-loss (${result.direction})`, value: decimalWithUnit(calculation.boundary_stop_loss_price, units.price) },
    ]} />
  </Panel>
}

export function MinimumLotRiskPanel({ result }: { result: RiskFeasibilityResult }) {
  const { calculation, units } = result
  return <Panel title="Minimum-lot risk estimate" subtitle="Hypothetical consequence only. This analyzer never selects or submits the minimum lot." actions={<span className="diagnostic-label">{calculation.minimum_lot_label}</span>}>
    <ValueGrid values={[
      { label: 'Estimated risk amount', value: decimalWithUnit(calculation.minimum_lot_estimated_risk_amount, units.currency) },
      { label: 'Estimated risk percent', value: decimalWithUnit(calculation.minimum_lot_estimated_risk_percent, units.percent) },
      { label: 'Risk excess amount', value: decimalWithUnit(calculation.minimum_lot_risk_delta_amount, units.currency) },
      { label: 'Risk excess percent', value: decimalWithUnit(calculation.minimum_lot_risk_delta_percent, units.percent) },
    ]} />
    <div className="alert alert-warn"><AlertTriangle size={18} aria-hidden />Minimum lot is not a safe recommendation when it exceeds configured risk. No force, override, create, or execute action is available.</div>
  </Panel>
}

export function FeasibilityReasonList({ result }: { result: RiskFeasibilityResult }) {
  return <Panel title="Reasons and recommendation" subtitle="Stable backend diagnostic codes are displayed in their authoritative order.">
    {result.reasons.length ? <ol className="diagnostic-reasons">{result.reasons.map((reason) => (
      <li key={reason.code}><code>{reason.code}</code><span>{reason.message}</span></li>
    ))}</ol> : <p className="muted-copy">No blocking diagnostic reason was reported.</p>}
    <div className="recommendation"><strong>Recommendation</strong><code>{result.recommendation}</code></div>
  </Panel>
}

export function AdvisoryNotice({ result }: { result: RiskFeasibilityResult }) {
  return <aside className="advisory-notice" aria-label="Advisory disclaimer">
    <ShieldAlert aria-hidden />
    <div><strong>Advisory only — no trading state changed</strong><p>{result.disclaimer}</p><p>Risk Management and Trade Plan creation remain authoritative. This result does not change whether any existing action is enabled or disabled.</p></div>
  </aside>
}

export function UnavailableNotice() {
  return <div className="alert alert-warn" role="status"><Info size={18} aria-hidden />The analysis is current, but its status is UNAVAILABLE. It must not be interpreted as feasible.</div>
}
