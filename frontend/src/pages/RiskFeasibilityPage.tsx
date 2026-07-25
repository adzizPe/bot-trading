import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Calculator, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useReducer, useRef } from 'react'
import { api, sanitizeMessage } from '../api/client'
import {
  AdvisoryNotice,
  FeasibilityContextPanel,
  FeasibilityReasonList,
  FeasibilityStatusPanel,
  MinimumLotRiskPanel,
  PositionSizingDiagnostics,
  ThresholdDiagnostics,
  UnavailableNotice,
} from '../components/riskFeasibility'
import { EmptyState, ErrorAlert, LoadingSkeleton, PageHeader, Panel, StatusBadge, dateTime } from '../components/ui'
import { feasibilityViewReducer, initialFeasibilityState } from '../riskFeasibility'

export function RiskFeasibilityPage() {
  const latestSignal = useQuery({ queryKey: ['latest-signal'], queryFn: api.latestSignal, retry: false })
  const candidate = latestSignal.data?.status === 'CANDIDATE' && latestSignal.data.direction !== 'HOLD'
    ? latestSignal.data
    : null
  const activeSignalId = candidate?.signal_id ?? null
  const [state, dispatch] = useReducer(feasibilityViewReducer, {
    ...initialFeasibilityState,
    activeSignalId,
  })
  const generation = useRef(0)
  const activeRequest = useRef<AbortController | null>(null)

  useEffect(() => {
    activeRequest.current?.abort()
    dispatch({ type: 'SIGNAL_CHANGED', signalId: activeSignalId })
  }, [activeSignalId])

  useEffect(() => () => activeRequest.current?.abort(), [])

  useEffect(() => {
    const result = state.result
    if (!result || (state.phase !== 'current' && state.phase !== 'unavailable')) return
    const freshUntil = result.snapshot_timestamps.fresh_until
    if (!freshUntil) return
    const expiry = Date.parse(freshUntil)
    if (!Number.isFinite(expiry)) {
      dispatch({ type: 'CLOCK_TICK', now: Date.now() })
      return
    }
    const delay = Math.min(2_147_483_647, Math.max(0, expiry - Date.now() + 1))
    const timer = window.setTimeout(() => dispatch({ type: 'CLOCK_TICK', now: Date.now() }), delay)
    return () => window.clearTimeout(timer)
  }, [state.phase, state.result])

  const analyze = useCallback(() => {
    if (!activeSignalId) return
    activeRequest.current?.abort()
    const controller = new AbortController()
    activeRequest.current = controller
    const requestGeneration = ++generation.current
    dispatch({ type: 'REQUEST_STARTED', signalId: activeSignalId, generation: requestGeneration })
    api.riskFeasibility(activeSignalId, controller.signal).then(
      (result) => dispatch({
        type: 'REQUEST_SUCCEEDED', signalId: activeSignalId,
        generation: requestGeneration, result, now: Date.now(),
      }),
      (error) => dispatch({
        type: 'REQUEST_FAILED', signalId: activeSignalId,
        generation: requestGeneration, message: sanitizeMessage(error),
      }),
    )
  }, [activeSignalId])

  const result = state.result
  return <div className="page-stack risk-feasibility-page">
    <PageHeader title="Risk Feasibility Analyzer" description="Read-only advisory diagnostics for the latest candidate signal. It cannot create a Trade Plan, change risk settings, or execute an order." actions={
      <button className="button button-primary" disabled={!candidate || state.phase === 'loading'} onClick={analyze}>
        {state.phase === 'loading' ? <RefreshCw className="spin" size={16} aria-hidden /> : <Calculator size={16} aria-hidden />}
        {state.phase === 'loading' ? 'Analyzing…' : 'Analyze feasibility'}
      </button>
    } />

    {latestSignal.isLoading && <LoadingSkeleton rows={2} />}
    {latestSignal.isError && <ErrorAlert message={sanitizeMessage(latestSignal.error)} retry={() => latestSignal.refetch()} />}
    {!latestSignal.isLoading && !latestSignal.isError && !candidate && <EmptyState title="No candidate signal available" description="Analyzer requires an existing BUY or SELL candidate. It does not generate or modify signals." />}
    {candidate && <Panel title="Active candidate context" subtitle="The analyzer sends only this signal identifier; all calculation values come from authoritative backend sources.">
      <div className="candidate-summary">
        <span><small>Signal ID</small><strong>{candidate.signal_id}</strong></span>
        <span><small>Symbol</small><strong>{candidate.symbol}</strong></span>
        <span><small>Direction</small><StatusBadge value={candidate.direction} /></span>
        <span><small>Created</small><strong>{dateTime(candidate.created_at)}</strong></span>
      </div>
    </Panel>}

    {candidate && state.phase === 'idle' && <EmptyState title="Ready for read-only analysis" description="Run the analyzer to request a fresh advisory snapshot. No plan, order, or persisted result will be created." />}
    {state.phase === 'loading' && <div role="status" aria-live="polite"><span className="sr-only">Loading feasibility analysis</span><LoadingSkeleton rows={5} /></div>}
    {state.phase === 'error' && <ErrorAlert message={state.error || 'Feasibility analysis failed'} retry={analyze} />}
    {state.phase === 'stale' && <div className="stale-state" role="status"><AlertTriangle aria-hidden /><div><strong>Stale result — not current</strong><p>{state.staleReason} No feasibility conclusion is displayed. Run a fresh analysis.</p></div></div>}

    {result && (state.phase === 'current' || state.phase === 'unavailable') && <>
      <FeasibilityStatusPanel result={result} />
      {state.phase === 'unavailable' && <UnavailableNotice />}
      <FeasibilityContextPanel result={result} />
      <PositionSizingDiagnostics result={result} />
      <div className="two-column feasibility-columns">
        <ThresholdDiagnostics result={result} />
        <MinimumLotRiskPanel result={result} />
      </div>
      <FeasibilityReasonList result={result} />
      <AdvisoryNotice result={result} />
    </>}
  </div>
}
