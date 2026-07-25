import type { RiskFeasibilityResult } from './api/types'

export type FeasibilityViewPhase = 'idle' | 'loading' | 'current' | 'unavailable' | 'error' | 'stale'

export interface FeasibilityViewState {
  phase: FeasibilityViewPhase
  activeSignalId: string | null
  generation: number
  result: RiskFeasibilityResult | null
  error: string | null
  staleReason: string | null
}

export type FeasibilityViewEvent =
  | { type: 'SIGNAL_CHANGED'; signalId: string | null }
  | { type: 'REQUEST_STARTED'; signalId: string; generation: number }
  | { type: 'REQUEST_SUCCEEDED'; signalId: string; generation: number; result: RiskFeasibilityResult; now: number }
  | { type: 'REQUEST_FAILED'; signalId: string; generation: number; message: string }
  | { type: 'CLOCK_TICK'; now: number }

export const initialFeasibilityState: FeasibilityViewState = {
  phase: 'idle', activeSignalId: null, generation: 0, result: null, error: null, staleReason: null,
}

export function isResultFresh(result: RiskFeasibilityResult, now: number): boolean {
  const value = result.snapshot_timestamps.fresh_until
  if (!value) return false
  const freshUntil = Date.parse(value)
  return Number.isFinite(freshUntil) && now <= freshUntil
}

export function feasibilityViewReducer(
  state: FeasibilityViewState,
  event: FeasibilityViewEvent,
): FeasibilityViewState {
  if (event.type === 'SIGNAL_CHANGED') {
    if (state.activeSignalId === event.signalId) return state
    return { ...initialFeasibilityState, activeSignalId: event.signalId, generation: state.generation }
  }
  if (event.type === 'REQUEST_STARTED') {
    if (event.signalId !== state.activeSignalId || event.generation <= state.generation) return state
    return { ...state, phase: 'loading', generation: event.generation, result: null, error: null, staleReason: null }
  }
  if (event.type === 'REQUEST_FAILED') {
    if (event.generation !== state.generation || event.signalId !== state.activeSignalId) return state
    return { ...state, phase: 'error', result: null, error: event.message, staleReason: null }
  }
  if (event.type === 'REQUEST_SUCCEEDED') {
    if (event.generation !== state.generation || event.signalId !== state.activeSignalId) return state
    if (event.result.source_signal_id !== state.activeSignalId) {
      return { ...state, phase: 'stale', result: null, error: null, staleReason: 'Result source no longer matches the active signal.' }
    }
    if (event.result.status === 'UNAVAILABLE') {
      return { ...state, phase: 'unavailable', result: event.result, error: null, staleReason: null }
    }
    if (!isResultFresh(event.result, event.now)) {
      return { ...state, phase: 'stale', result: null, error: null, staleReason: 'Snapshot expired before it could be displayed.' }
    }
    return {
      ...state,
      phase: 'current',
      result: event.result,
      error: null,
      staleReason: null,
    }
  }
  if (state.result && !isResultFresh(state.result, event.now)) {
    return { ...state, phase: 'stale', result: null, error: null, staleReason: 'Snapshot is stale. Run a fresh analysis.' }
  }
  return state
}

const PLAIN_DECIMAL = /^([+-]?)(\d+)(?:\.(\d+))?$/

export function formatDecimalString(value: string | null | undefined): string {
  if (value == null) return 'Unavailable'
  const match = PLAIN_DECIMAL.exec(value)
  if (!match) return value
  const [, sign, integer, fraction] = match
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${sign}${grouped}${fraction === undefined ? '' : `.${fraction}`}`
}

export function decimalWithUnit(
  value: string | null | undefined,
  unit: string,
): string {
  return value == null ? 'Unavailable' : `${formatDecimalString(value)} ${unit}`
}
